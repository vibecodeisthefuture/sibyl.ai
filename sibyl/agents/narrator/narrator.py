"""
Portfolio Health Narrator — LLM-powered 6-hour digest + alert escalation.

PURPOSE:
    The Narrator is the "voice" of Sibyl.  Every 6 hours (configurable via cron),
    it gathers a snapshot of the entire portfolio state — positions, risk metrics,
    engine health, recent signals, breakout research — and feeds it to Claude Haiku
    for synthesis into a concise, human-readable health digest.

    The digest is pushed as a notification via ntfy.sh, giving the operator a
    periodic "state of the portfolio" update without having to open the dashboard.

ALERT ESCALATION:
    If ≥ N active alerts exist (configurable, default: 2), the Narrator sends an
    IMMEDIATE high-priority push instead of waiting for the next scheduled cycle.
    Active alerts include:
        - Circuit breakers in WARNING or TRIGGERED state
        - Drawdown level at CAUTION or CRITICAL
        - Any position with > 15% unrealized loss

LLM SYNTHESIS:
    The Narrator calls Claude Haiku (fast, cheap) with a structured prompt
    containing the full portfolio snapshot.  Claude returns a 2-4 sentence
    digest summarizing:
        - Overall portfolio health (healthy / stressed / critical)
        - Key positions and their performance
        - Notable risk factors or opportunities
        - Recommended actions (if any)

CONFIGURATION:
    config/narrator_config.yaml:
        schedule_cron:              "0 */6 * * *"
        llm_model:                  "claude-haiku-4-5-20251001"
        max_digest_tokens:          550
        alert_escalation_threshold: 2

POLLING:
    Every 6 hours by default (overridden to 360 seconds poll_interval for the
    BaseAgent loop — the agent checks if it's time to run based on cron).
    Alert escalation checks run every cycle (every 60 seconds).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.narrator")


class Narrator(BaseAgent):
    """Generates LLM-powered portfolio health digests and escalation alerts."""

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="narrator", db=db, config=config)
        self._narrator_config: dict[str, Any] = {}

        # LLM client (Sonar replaces Anthropic Claude)
        self._sonar_llm = None
        self._max_tokens: int = 550

        # ntfy transport
        self._ntfy_url: str = ""
        self._http_client = None

        # Alert escalation
        self._alert_threshold: int = 2
        self._last_escalation_hour: int = -1  # Track to avoid duplicate escalations

        # Digest scheduling
        self._digest_interval_hours: int = 6
        self._last_digest_hour: int = -1

    @property
    def poll_interval(self) -> float:
        """Check every 60 seconds for alert escalation; digest runs on schedule."""
        return 60.0

    async def start(self) -> None:
        """Load config, initialize LLM client and ntfy transport."""
        from sibyl.core.config import load_yaml

        try:
            self._narrator_config = load_yaml("narrator_config.yaml")
        except FileNotFoundError:
            self._narrator_config = {}

        # ── LLM config ────────────────────────────────────────────────────
        self._max_tokens = int(self._narrator_config.get("max_digest_tokens", 550))
        self._alert_threshold = int(self._narrator_config.get("alert_escalation_threshold", 2))
        self._digest_interval_hours = int(
            self._narrator_config.get("digest_interval_hours", 6)
        )

        # ── Initialize Sonar LLM client (replaces Anthropic) ──────────────
        try:
            from sibyl.clients.sonar_llm_client import SonarLLMClient

            sonar = SonarLLMClient()
            if sonar.initialize():
                self._sonar_llm = sonar
                self.logger.info("Narrator Sonar LLM client initialized")
            else:
                self.logger.info("Sonar LLM unavailable — digests will use template fallback")
        except Exception:
            self.logger.warning("Failed to initialize Sonar LLM client — digests disabled")

        # ── Initialize ntfy transport ─────────────────────────────────────
        notif_config = self.config.get("notifications", {})
        server = os.environ.get(
            "NTFY_SERVER",
            notif_config.get("ntfy_server", "https://ntfy.sh"),
        )
        topic = os.environ.get(
            "NTFY_TOPIC",
            self._narrator_config.get("ntfy_topic", "sibyl-health"),
        )

        if topic:
            import httpx
            self._ntfy_url = f"{server.rstrip('/')}/{topic}"
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

        self.logger.info(
            "Narrator started (sonar_llm=%s, escalation_threshold=%d, ntfy=%s)",
            "ready" if self._sonar_llm else "fallback",
            self._alert_threshold,
            "ready" if self._ntfy_url else "disabled",
        )

    async def run_cycle(self) -> None:
        """Check for alert escalation every cycle; run digest on schedule."""
        now = datetime.now(timezone.utc)

        # ── Alert escalation check (every cycle) ─────────────────────────
        active_alerts = await self._count_active_alerts()
        if active_alerts >= self._alert_threshold:
            current_hour = now.hour
            if current_hour != self._last_escalation_hour:
                self._last_escalation_hour = current_hour
                snapshot = await self._gather_snapshot()
                digest = await self._generate_digest(snapshot, escalation=True)
                await self._send_notification(
                    title=f"ALERT ESCALATION: {active_alerts} active alerts",
                    body=digest,
                    priority="5",
                    tags="rotating_light",
                )

        # ── Scheduled digest (every N hours) ──────────────────────────────
        current_hour = now.hour
        if (current_hour % self._digest_interval_hours == 0
                and current_hour != self._last_digest_hour):
            self._last_digest_hour = current_hour
            snapshot = await self._gather_snapshot()
            digest = await self._generate_digest(snapshot, escalation=False)
            await self._send_notification(
                title="Sibyl Portfolio Digest",
                body=digest,
                priority="2",
                tags="bar_chart",
            )

    async def stop(self) -> None:
        """Cleanup LLM and HTTP clients."""
        if self._sonar_llm:
            await self._sonar_llm.close()
        if self._http_client:
            await self._http_client.aclose()
        self.logger.info("Narrator stopped")

    # ── Snapshot Gathering ─────────────────────────────────────────────

    async def _gather_snapshot(self) -> dict[str, Any]:
        """Collect full portfolio state from the database.

        Returns a dict containing:
            - portfolio: balance, cash reserve, allocable
            - positions: list of open positions with P&L
            - risk: drawdown, win rate, Sharpe
            - engines: SGE/ACE state (capital, circuit breakers)
            - signals: recent signals (last 6 hours)
            - alerts: list of active alert descriptions
        """
        snapshot: dict[str, Any] = {}

        # ── Portfolio balance ──────────────────────────────────────────
        portfolio_keys = [
            "portfolio_total_balance",
            "portfolio_cash_reserve",
            "portfolio_allocable",
        ]
        portfolio = {}
        for key in portfolio_keys:
            row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            )
            portfolio[key] = float(row["value"]) if row else 0.0
        snapshot["portfolio"] = portfolio

        # ── Open positions ─────────────────────────────────────────────
        positions = await self.db.fetchall(
            """SELECT p.market_id, p.engine, p.side, p.size, p.entry_price,
                      p.current_price, p.pnl, p.status, m.title
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status = 'OPEN'
               ORDER BY p.opened_at DESC
               LIMIT 20"""
        )
        snapshot["positions"] = [
            {
                "title": r["title"],
                "engine": r["engine"],
                "side": r["side"],
                "size": int(r["size"]),
                "entry": float(r["entry_price"]),
                "current": float(r["current_price"] or r["entry_price"]),
                "pnl": float(r["pnl"] or 0),
            }
            for r in positions
        ]

        # ── Risk metrics ───────────────────────────────────────────────
        risk_keys = [
            "risk_hwm", "risk_drawdown_pct", "risk_drawdown_level",
            "risk_win_rate_7d", "risk_sharpe_30d", "risk_daily_pnl",
        ]
        risk = {}
        for key in risk_keys:
            row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            )
            risk[key] = row["value"] if row else "0"
        snapshot["risk"] = risk

        # ── Engine state ───────────────────────────────────────────────
        engines = {}
        for engine_name in ("SGE", "ACE"):
            row = await self.db.fetchone(
                "SELECT * FROM engine_state WHERE engine = ?", (engine_name,)
            )
            if row:
                engines[engine_name] = {
                    "total_capital": float(row["total_capital"]),
                    "deployed_capital": float(row["deployed_capital"]),
                    "circuit_breaker": row["circuit_breaker"],
                }
        snapshot["engines"] = engines

        # ── Recent signals (last 6 hours) ──────────────────────────────
        signals = await self.db.fetchall(
            """SELECT s.signal_type, s.confidence, s.ev_estimate,
                      s.routed_to, s.status, m.title
               FROM signals s
               JOIN markets m ON s.market_id = m.id
               WHERE s.timestamp >= datetime('now', '-6 hours')
               ORDER BY s.timestamp DESC
               LIMIT 10"""
        )
        snapshot["recent_signals"] = [
            {
                "title": r["title"],
                "type": r["signal_type"],
                "confidence": float(r["confidence"]),
                "routed_to": r["routed_to"],
                "status": r["status"],
            }
            for r in signals
        ]

        # ── Active alerts ──────────────────────────────────────────────
        snapshot["alerts"] = await self._get_active_alerts()

        return snapshot

    async def _count_active_alerts(self) -> int:
        """Count the number of currently active alerts."""
        alerts = await self._get_active_alerts()
        return len(alerts)

    async def _get_active_alerts(self) -> list[str]:
        """Identify all active alerts across the system.

        Active alerts include:
            - Circuit breakers in WARNING or TRIGGERED
            - Drawdown level at CAUTION or CRITICAL
            - Positions with > 15% unrealized loss
        """
        alerts: list[str] = []

        # Circuit breakers
        for engine in ("SGE", "ACE"):
            row = await self.db.fetchone(
                "SELECT circuit_breaker FROM engine_state WHERE engine = ?",
                (engine,),
            )
            if row and row["circuit_breaker"] in ("WARNING", "TRIGGERED"):
                alerts.append(f"{engine} circuit breaker: {row['circuit_breaker']}")

        # Drawdown level
        row = await self.db.fetchone(
            "SELECT value FROM system_state WHERE key = 'risk_drawdown_level'"
        )
        if row and row["value"] in ("CAUTION", "CRITICAL"):
            alerts.append(f"Portfolio drawdown: {row['value']}")

        # Positions with heavy losses
        losing_positions = await self.db.fetchall(
            """SELECT p.market_id, m.title, p.entry_price, p.current_price
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status = 'OPEN'
                 AND p.current_price IS NOT NULL
                 AND p.entry_price > 0
                 AND (p.current_price - p.entry_price) / p.entry_price < -0.15"""
        )
        for pos in losing_positions:
            loss_pct = (
                (float(pos["current_price"]) - float(pos["entry_price"]))
                / float(pos["entry_price"])
            ) * 100
            alerts.append(f"Heavy loss on {pos['title']}: {loss_pct:.1f}%")

        return alerts

    # ── LLM Digest Generation ──────────────────────────────────────────

    async def _generate_digest(
        self, snapshot: dict[str, Any], escalation: bool = False
    ) -> str:
        """Generate a human-readable portfolio digest via Sonar LLM.

        If the LLM is unavailable, falls back to a template-based digest.

        Args:
            snapshot:    Full portfolio state dict from _gather_snapshot().
            escalation:  True if this is an alert escalation (changes tone).

        Returns:
            Digest string (2-5 sentences).
        """
        if not self._sonar_llm:
            return self._fallback_digest(snapshot, escalation)

        context = "ALERT ESCALATION — immediate attention needed" if escalation else "Scheduled digest"

        prompt = f"""You are Sibyl, an autonomous prediction market trading system.
Generate a brief portfolio health digest ({context}).

PORTFOLIO STATE:
{json.dumps(snapshot, indent=2, default=str)}

Rules:
- Keep it to 2-4 sentences
- Lead with overall health status (healthy / stressed / critical)
- Mention total balance and P&L direction
- Flag any active alerts
- If escalation, be direct about what needs attention
- Use plain language, no jargon
- Include specific numbers (balances, percentages)"""

        try:
            text = await self._sonar_llm.generate_digest(prompt)
            if text:
                return text
            self.logger.warning("Sonar returned empty digest — using fallback")
            return self._fallback_digest(snapshot, escalation)

        except Exception:
            self.logger.exception("LLM digest generation failed — using fallback")
            return self._fallback_digest(snapshot, escalation)

    @staticmethod
    def _fallback_digest(snapshot: dict[str, Any], escalation: bool) -> str:
        """Template-based digest when LLM is unavailable.

        Produces a structured summary from the snapshot data without
        requiring an API call.
        """
        portfolio = snapshot.get("portfolio", {})
        risk = snapshot.get("risk", {})
        positions = snapshot.get("positions", [])
        alerts = snapshot.get("alerts", [])

        balance = portfolio.get("portfolio_total_balance", 0)
        drawdown_pct = float(risk.get("risk_drawdown_pct", 0)) * 100
        drawdown_level = risk.get("risk_drawdown_level", "CLEAR")
        win_rate = float(risk.get("risk_win_rate_7d", 0)) * 100
        open_count = len(positions)

        parts = []

        if escalation:
            parts.append(f"ALERT: {len(alerts)} active alert(s).")
            for alert in alerts[:3]:
                parts.append(f"  - {alert}")

        parts.append(
            f"Portfolio: ${balance:.2f} | "
            f"Drawdown: {drawdown_pct:.1f}% ({drawdown_level}) | "
            f"Win rate: {win_rate:.0f}%"
        )
        parts.append(f"Open positions: {open_count}")

        if positions:
            total_pnl = sum(p.get("pnl", 0) for p in positions)
            parts.append(f"Total unrealized P&L: ${total_pnl:+.2f}")

        return "\n".join(parts)

    # ── ntfy Transport ─────────────────────────────────────────────────

    async def _send_notification(
        self, title: str, body: str, priority: str = "2", tags: str = "bar_chart"
    ) -> None:
        """Send a push notification via ntfy.sh.

        Args:
            title:    Notification title.
            body:     Notification body text.
            priority: ntfy priority level (1-5).
            tags:     ntfy emoji tag names.
        """
        if not self._http_client or not self._ntfy_url:
            self.logger.debug("ntfy not configured — skipping notification")
            return

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
                self.logger.info("Digest sent: %s", title)
            else:
                self.logger.warning(
                    "ntfy returned %d: %s", resp.status_code, resp.text[:200]
                )
        except Exception:
            self.logger.exception("Failed to send digest notification")
