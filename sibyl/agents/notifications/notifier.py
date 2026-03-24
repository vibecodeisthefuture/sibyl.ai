"""
Notifier Agent — push alerts via ntfy.sh for trading events.

PURPOSE:
    Sends real-time push notifications to your phone/desktop for critical
    trading events.  Uses ntfy.sh, a free, open-source pub/sub service.

WHAT TRIGGERS A NOTIFICATION:
    1. SIGNAL ROUTED:   A new signal was routed to SGE or ACE for execution.
    2. POSITION OPENED: A new position was opened (paper or live).
    3. POSITION CLOSED: A position was closed (stop-loss, take-profit, resolution).
    4. CIRCUIT BREAKER: An engine's circuit breaker tripped (WARNING or TRIGGERED).
    5. DRAWDOWN ALERT:  Portfolio drawdown exceeded a threshold (CAUTION or CRITICAL).
    6. DAILY SUMMARY:   End-of-day P&L recap (optional, disabled by default).

HOW IT WORKS:
    The Notifier polls the database every 10 seconds for "unnotified" events.
    Each event type has a dedicated detector method that queries the relevant
    table(s) and checks for new rows since the last poll.

    When an event is detected:
        1. Format a human-readable message with key metrics.
        2. POST it to ntfy.sh (or a self-hosted ntfy server).
        3. Mark the event as notified (via system_state tracker keys).

NTFY.SH BASICS:
    - Subscribe: Install ntfy app → subscribe to your topic name.
    - Send:      POST to https://ntfy.sh/<topic> with message in body.
    - Free tier: 250 messages/day per IP — more than enough for trading alerts.
    - Self-host: Set NTFY_SERVER in .env to your own ntfy instance URL.

CONFIGURATION:
    .env:
        NTFY_TOPIC=sibyl-health        ← Your topic (like a channel name)
        NTFY_SERVER=https://ntfy.sh    ← Server URL (default: public ntfy.sh)
    config/system_config.yaml:
        notifications.enabled: true     ← Master switch

POLLING: Every 10 seconds.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.notifier")

# ── Priority tags for ntfy.sh ─────────────────────────────────────────
# ntfy supports 5 priority levels (1–5).  We map our event types:
PRIORITY_MAP = {
    "signal":          "3",  # Default — informational
    "position_open":   "3",  # Default — new position opened
    "position_close":  "3",  # Default — position closed normally
    "stop_loss":       "4",  # High — stop triggered, money lost
    "circuit_breaker": "5",  # Urgent — engine halted
    "drawdown":        "5",  # Urgent — significant portfolio drawdown
    "daily_summary":   "2",  # Low — end-of-day recap
}

# ── Emoji tags per event type (shown in ntfy app) ─────────────────────
TAG_MAP = {
    "signal":          "chart_with_upwards_trend",
    "position_open":   "rocket",
    "position_close":  "checkered_flag",
    "stop_loss":       "rotating_light",
    "circuit_breaker": "octagonal_sign",
    "drawdown":        "warning",
    "daily_summary":   "bar_chart",
}


class Notifier(BaseAgent):
    """Sends push notifications for trading events via ntfy.sh."""

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="notifier", db=db, config=config)
        self._enabled: bool = False
        self._ntfy_url: str = ""
        self._http_client: httpx.AsyncClient | None = None

        # Tracking: last-seen IDs for each event type.
        # Prevents re-notifying on the same event after restart.
        self._last_signal_id: int = 0
        self._last_position_id: int = 0
        self._last_execution_id: int = 0
        self._last_drawdown_level: str = "CLEAR"
        self._last_circuit_sge: str = "CLEAR"
        self._last_circuit_ace: str = "CLEAR"

    @property
    def poll_interval(self) -> float:
        """Poll every 10 seconds — fast enough for trading, light on DB."""
        return 10.0

    async def start(self) -> None:
        """Initialize ntfy.sh client and restore last-seen cursors."""
        # ── Check master switch ──────────────────────────────────────────
        notif_config = self.config.get("notifications", {})
        self._enabled = notif_config.get("enabled", False)

        if not self._enabled:
            self.logger.info("Notifications DISABLED in config — Notifier idle")
            return

        # ── Build ntfy URL from env ──────────────────────────────────────
        server = os.environ.get(
            "NTFY_SERVER",
            notif_config.get("ntfy_server", "https://ntfy.sh"),
        )
        topic = os.environ.get("NTFY_TOPIC", "sibyl-health")

        if not topic:
            self.logger.warning("NTFY_TOPIC not set — Notifier disabled")
            self._enabled = False
            return

        self._ntfy_url = f"{server.rstrip('/')}/{topic}"
        self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

        # ── Restore cursors from system_state ────────────────────────────
        # This prevents re-sending old notifications after a restart.
        for key, attr in [
            ("notifier_last_signal_id", "_last_signal_id"),
            ("notifier_last_position_id", "_last_position_id"),
            ("notifier_last_execution_id", "_last_execution_id"),
        ]:
            row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            )
            if row:
                setattr(self, attr, int(row["value"]))

        self.logger.info(
            "Notifier started → %s (cursors: signal=%d, position=%d)",
            self._ntfy_url, self._last_signal_id, self._last_position_id,
        )

    async def run_cycle(self) -> None:
        """Check for new events and send notifications."""
        if not self._enabled:
            return

        await self._check_new_signals()
        await self._check_new_positions()
        await self._check_closed_positions()
        await self._check_circuit_breakers()
        await self._check_drawdown()

    async def stop(self) -> None:
        """Persist cursors and close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
        self.logger.info("Notifier stopped")

    # ── Event Detectors ────────────────────────────────────────────────

    async def _check_new_signals(self) -> None:
        """Detect new ROUTED signals (status = 'ROUTED') since last cursor."""
        rows = await self.db.fetchall(
            """SELECT s.id, s.market_id, s.signal_type, s.confidence,
                      s.ev_estimate, s.routed_to, m.title
               FROM signals s
               JOIN markets m ON s.market_id = m.id
               WHERE s.id > ? AND s.status = 'ROUTED'
               ORDER BY s.id""",
            (self._last_signal_id,),
        )
        for row in rows:
            title = f"Signal → {row['routed_to']}: {row['signal_type']}"
            body = (
                f"{row['title']}\n"
                f"Confidence: {float(row['confidence']):.0%} | "
                f"EV: {float(row['ev_estimate'] or 0):.1%}"
            )
            await self._send(title, body, "signal")
            self._last_signal_id = int(row["id"])

        if rows:
            await self._save_cursor("notifier_last_signal_id", self._last_signal_id)

    async def _check_new_positions(self) -> None:
        """Detect newly OPENED positions since last cursor."""
        rows = await self.db.fetchall(
            """SELECT p.id, p.market_id, p.engine, p.side, p.size,
                      p.entry_price, m.title
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.id > ? AND p.status = 'OPEN'
               ORDER BY p.id""",
            (self._last_position_id,),
        )
        for row in rows:
            cost = float(row["size"]) * float(row["entry_price"])
            title = f"Position OPENED: {row['engine']} {row['side']}"
            body = (
                f"{row['title']}\n"
                f"Size: {int(row['size'])} @ ${float(row['entry_price']):.2f} "
                f"(${cost:.2f})"
            )
            await self._send(title, body, "position_open")
            self._last_position_id = int(row["id"])

        if rows:
            await self._save_cursor("notifier_last_position_id", self._last_position_id)

    async def _check_closed_positions(self) -> None:
        """Detect positions that were recently CLOSED or STOPPED."""
        rows = await self.db.fetchall(
            """SELECT p.id, p.market_id, p.engine, p.side, p.pnl,
                      p.status, p.entry_price, p.current_price, m.title
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status IN ('CLOSED', 'STOPPED')
                 AND p.closed_at >= datetime('now', '-15 seconds')
               ORDER BY p.id"""
        )
        for row in rows:
            pnl = float(row["pnl"] or 0)
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            event_type = "stop_loss" if row["status"] == "STOPPED" else "position_close"
            verb = "STOPPED" if row["status"] == "STOPPED" else "CLOSED"

            title = f"Position {verb}: {row['engine']} ({pnl_str})"
            body = (
                f"{row['title']}\n"
                f"{row['side']} | Entry: ${float(row['entry_price']):.2f} → "
                f"${float(row['current_price'] or 0):.2f} | P&L: {pnl_str}"
            )
            await self._send(title, body, event_type)

    async def _check_circuit_breakers(self) -> None:
        """Detect circuit breaker state changes for SGE/ACE."""
        for engine in ("SGE", "ACE"):
            row = await self.db.fetchone(
                "SELECT circuit_breaker FROM engine_state WHERE engine = ?",
                (engine,),
            )
            if not row:
                continue

            current = row["circuit_breaker"]
            prev = self._last_circuit_sge if engine == "SGE" else self._last_circuit_ace

            # Only notify on state CHANGES (not every cycle)
            if current != prev and current in ("WARNING", "TRIGGERED"):
                title = f"CIRCUIT BREAKER: {engine} → {current}"
                body = f"The {engine} engine circuit breaker is now {current}."
                await self._send(title, body, "circuit_breaker")

            if engine == "SGE":
                self._last_circuit_sge = current
            else:
                self._last_circuit_ace = current

    async def _check_drawdown(self) -> None:
        """Detect drawdown level escalations."""
        row = await self.db.fetchone(
            "SELECT value FROM system_state WHERE key = 'risk_drawdown_level'"
        )
        if not row:
            return

        current = row["value"]
        # Only notify on escalation (not when recovering to CLEAR)
        if current != self._last_drawdown_level and current in ("CAUTION", "CRITICAL"):
            dd_row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = 'risk_drawdown_pct'"
            )
            dd_pct = float(dd_row["value"]) * 100 if dd_row else 0

            title = f"DRAWDOWN {current}: {dd_pct:.1f}%"
            body = f"Portfolio drawdown is now {dd_pct:.1f}% from the high-water mark."
            await self._send(title, body, "drawdown")

        self._last_drawdown_level = current

    # ── ntfy.sh Transport ──────────────────────────────────────────────

    async def _send(self, title: str, body: str, event_type: str) -> None:
        """Send a push notification via ntfy.sh HTTP POST.

        ntfy.sh API:
            POST https://ntfy.sh/<topic>
            Headers:
                Title:    Notification title
                Priority: 1-5 (min to max)
                Tags:     Comma-separated emoji names
            Body:         Plain text message

        Args:
            title:      Notification title (shown as heading).
            body:       Notification body (shown as detail text).
            event_type: Key from PRIORITY_MAP / TAG_MAP.
        """
        if not self._http_client:
            return

        priority = PRIORITY_MAP.get(event_type, "3")
        tags = TAG_MAP.get(event_type, "bell")

        try:
            resp = await self._http_client.post(
                self._ntfy_url,
                content=body,
                headers={
                    "Title": title,
                    "Priority": priority,
                    "Tags": tags,
                },
            )
            if resp.status_code == 200:
                self.logger.debug("Notification sent: %s", title)
            else:
                self.logger.warning(
                    "ntfy.sh returned %d: %s", resp.status_code, resp.text[:200],
                )
        except Exception:
            self.logger.exception("Failed to send notification: %s", title)

    # ── Cursor Persistence ─────────────────────────────────────────────

    async def _save_cursor(self, key: str, value: int) -> None:
        """Persist a last-seen cursor to system_state."""
        await self.db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES (?, ?, datetime('now'))""",
            (key, str(value)),
        )
        await self.db.commit()
