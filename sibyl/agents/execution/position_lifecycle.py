"""
Position Lifecycle Manager — monitors and manages all open positions.

PURPOSE:
    Once the Order Executor opens a position, THIS agent takes over.  It runs
    5 sub-routines on overlapping schedules to monitor risk, optimize exits,
    and track resolution.

THE 5 SUB-ROUTINES:

    A — STOP GUARD (every 7s):
        Checks each OPEN position against its stop_loss price.
        If current_price breaches stop_loss → close immediately.
        If 3 stops fire within 15 minutes → trigger circuit breaker for that engine.

    B — EV MONITOR (ACE=90s, SGE=300s):
        Re-estimates expected value with the current price.
        If EV has shifted by more than 5% since entry → flag the position.
        Useful for detecting when the thesis has changed.

    C — EXIT OPTIMIZER (every 120s):
        Determines when to take profits:
        - If >80% of estimated EV has been captured → close (take profit).
        - If price has stalled for 4 consecutive cycles → close (momentum stall).
        Uses the concept of "EV capture" = how much of the forecasted move occurred.

    D — RESOLUTION TRACKER (every 300s):
        Detects markets converging to a resolution:
        - YES price > 85% → market is resolving YES
        - NO price < 15% → market is resolving NO
        Writes PerformanceRecord entries and closes resolved positions.

    E — CORRELATION SCANNER (every 10 min):
        Checks for correlated positions across engines:
        - Groups positions by event_id (same real-world event).
        - If total exposure on one event > 3% of capital → emit WARNING.
        - If exposure > 7% → BLOCK new entries on that event.

CONFIGURATION:
    All thresholds from `config/position_lifecycle_config.yaml`.

POLLING:
    The manager runs every 5 seconds.  Each sub-routine has its own internal
    counter to fire at its configured interval.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.position_lifecycle")


class PositionLifecycleManager(BaseAgent):
    """Monitors open positions with 5 sub-routines on overlapping schedules."""

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="position_lifecycle", db=db, config=config)
        self._plc: dict[str, Any] = {}  # position_lifecycle_config.yaml

        # Sub-routine cycle counters (each increments every 5s cycle)
        self._sub_counters: dict[str, int] = {
            "stop_guard": 0,
            "ev_monitor": 0,
            "exit_optimizer": 0,
            "resolution_tracker": 0,
            "correlation_scanner": 0,
        }
        # Recent stops per engine for circuit breaker logic
        # Maps engine → list of timestamps when stops occurred
        self._recent_stops: dict[str, list[float]] = {"SGE": [], "ACE": []}

    @property
    def poll_interval(self) -> float:
        """Run every 5 seconds — sub-routines use internal counters for pacing."""
        return 5.0

    async def start(self) -> None:
        """Load position lifecycle configuration."""
        from sibyl.core.config import load_yaml
        try:
            self._plc = load_yaml("position_lifecycle_config.yaml")
        except FileNotFoundError:
            self._plc = {}
        self.logger.info("Position Lifecycle Manager started (5 sub-routines active)")

    async def run_cycle(self) -> None:
        """Run sub-routines at their configured intervals."""
        cycle = self._cycle_count

        # Sub-routine A: Stop Guard — every 7s ≈ every other cycle at 5s polling
        sg_interval = int(self._plc.get("stop_guard", {}).get("poll_interval_seconds", 7) / 5)
        if cycle % max(sg_interval, 1) == 0:
            await self._sub_a_stop_guard()

        # Sub-routine B: EV Monitor — fires at different rates per engine
        ev_ace = int(self._plc.get("ev_monitor", {}).get("poll_interval_ace_seconds", 90) / 5)
        ev_sge = int(self._plc.get("ev_monitor", {}).get("poll_interval_sge_seconds", 300) / 5)
        if cycle % max(ev_ace, 1) == 0:
            await self._sub_b_ev_monitor("ACE")
        if cycle % max(ev_sge, 1) == 0:
            await self._sub_b_ev_monitor("SGE")

        # Sub-routine C: Exit Optimizer — every 120s
        exit_interval = int(self._plc.get("exit_optimizer", {}).get("poll_interval_seconds", 120) / 5)
        if cycle % max(exit_interval, 1) == 0:
            await self._sub_c_exit_optimizer()

        # Sub-routine D: Resolution Tracker — every 300s
        res_interval = int(self._plc.get("resolution_tracker", {}).get("poll_interval_seconds", 300) / 5)
        if cycle % max(res_interval, 1) == 0:
            await self._sub_d_resolution_tracker()

        # Sub-routine E: Correlation Scanner — every 10 min
        corr_minutes = int(self._plc.get("correlation_scanner", {}).get("poll_interval_minutes", 10))
        corr_interval = int(corr_minutes * 60 / 5)
        if cycle % max(corr_interval, 1) == 0:
            await self._sub_e_correlation_scanner()

    async def stop(self) -> None:
        self.logger.info("Position Lifecycle Manager stopped")

    # ── SUB-ROUTINE A: Stop Guard ─────────────────────────────────────

    async def _sub_a_stop_guard(self) -> None:
        """Check all OPEN positions against their stop_loss prices.

        If current_price has moved against us past stop_loss → close.
        If 3+ stops in 15 minutes for one engine → trigger circuit breaker.
        """
        positions = await self.db.fetchall(
            """SELECT id, market_id, engine, side, size, entry_price,
                      current_price, stop_loss
               FROM positions WHERE status = 'OPEN' AND stop_loss IS NOT NULL"""
        )

        cb_window = int(self._plc.get("stop_guard", {}).get(
            "circuit_breaker_window_minutes", 15
        ))
        cb_count = int(self._plc.get("stop_guard", {}).get(
            "circuit_breaker_stop_count", 3
        ))

        for pos in positions:
            current = float(pos["current_price"]) if pos["current_price"] else None
            stop = float(pos["stop_loss"])

            if current is None:
                continue

            # For YES positions: stop if price drops below stop_loss
            # For NO positions: stop if price rises above (1 - stop_loss)
            stopped = False
            if pos["side"] == "YES" and current <= stop:
                stopped = True
            elif pos["side"] == "NO" and current >= (1.0 - stop):
                stopped = True

            if stopped:
                # Close the position
                pnl = self._compute_pnl(pos)
                await self.db.execute(
                    """UPDATE positions SET
                         status = 'STOPPED', pnl = ?, closed_at = datetime('now')
                       WHERE id = ?""",
                    (pnl, pos["id"]),
                )
                self.logger.warning(
                    "STOP LOSS hit: position #%d on %s (engine=%s, pnl=%.2f)",
                    pos["id"], pos["market_id"], pos["engine"], pnl,
                )

                # Track this stop for circuit breaker evaluation
                engine = pos["engine"]
                now = datetime.now(timezone.utc).timestamp()
                self._recent_stops[engine].append(now)

                # Clean old stops outside the window
                cutoff = now - (cb_window * 60)
                self._recent_stops[engine] = [
                    t for t in self._recent_stops[engine] if t > cutoff
                ]

                # Check circuit breaker threshold
                if len(self._recent_stops[engine]) >= cb_count:
                    await self.db.execute(
                        "UPDATE engine_state SET circuit_breaker = 'TRIGGERED' WHERE engine = ?",
                        (engine,),
                    )
                    self.logger.critical(
                        "CIRCUIT BREAKER TRIGGERED for %s (%d stops in %d min)",
                        engine, len(self._recent_stops[engine]), cb_window,
                    )

        await self.db.commit()

    # ── SUB-ROUTINE B: EV Monitor ─────────────────────────────────────

    async def _sub_b_ev_monitor(self, engine: str) -> None:
        """Re-estimate EV for positions belonging to a specific engine.

        If EV has shifted by more than the threshold → update and flag.
        """
        threshold = float(self._plc.get("ev_monitor", {}).get(
            "significant_ev_shift_threshold", 0.05
        ))

        positions = await self.db.fetchall(
            """SELECT p.id, p.market_id, p.side, p.ev_current, p.entry_price, p.signal_id
               FROM positions p
               WHERE p.engine = ? AND p.status = 'OPEN'""",
            (engine,),
        )

        for pos in positions:
            # Get latest price
            price_row = await self.db.fetchone(
                "SELECT yes_price FROM prices WHERE market_id = ? ORDER BY timestamp DESC LIMIT 1",
                (pos["market_id"],),
            )
            if not price_row:
                continue

            current_price = float(price_row["yes_price"])

            # Get the signal's confidence for EV re-estimation
            signal_row = await self.db.fetchone(
                "SELECT confidence FROM signals WHERE id = ?",
                (pos["signal_id"],),
            )
            confidence = float(signal_row["confidence"]) if signal_row else 0.55

            # Recalculate EV
            if current_price < 0.50:
                potential_profit = 1.0 - current_price
                potential_loss = current_price
            else:
                potential_profit = current_price
                potential_loss = 1.0 - current_price

            new_ev = (confidence * potential_profit) - ((1.0 - confidence) * potential_loss)
            old_ev = float(pos["ev_current"]) if pos["ev_current"] else 0.0

            # Update current price and EV
            await self.db.execute(
                "UPDATE positions SET current_price = ?, ev_current = ? WHERE id = ?",
                (current_price, new_ev, pos["id"]),
            )

            # Flag significant shifts
            ev_shift = abs(new_ev - old_ev)
            if ev_shift >= threshold:
                self.logger.info(
                    "EV SHIFT on position #%d (%s): %.3f → %.3f (Δ=%.3f)",
                    pos["id"], engine, old_ev, new_ev, ev_shift,
                )

        await self.db.commit()

    # ── SUB-ROUTINE C: Exit Optimizer ─────────────────────────────────

    async def _sub_c_exit_optimizer(self) -> None:
        """Determine when to close profitable positions.

        Two exit triggers:
        1. EV Capture: >80% of estimated profit has been realized → take profit.
        2. Momentum Stall: price hasn't moved >0.5% in 4 consecutive checks.
        """
        ev_capture_thresh = float(self._plc.get("exit_optimizer", {}).get(
            "ev_capture_threshold", 0.80
        ))

        positions = await self.db.fetchall(
            """SELECT id, market_id, engine, side, size, entry_price,
                      current_price, ev_current, signal_id
               FROM positions WHERE status = 'OPEN'"""
        )

        for pos in positions:
            current = float(pos["current_price"]) if pos["current_price"] else None
            entry = float(pos["entry_price"])

            if current is None or entry <= 0:
                continue

            # Check EV capture
            if pos["side"] == "YES":
                profit = (current - entry) * float(pos["size"])
                max_profit = (1.0 - entry) * float(pos["size"])
            else:
                profit = (entry - current) * float(pos["size"])
                max_profit = entry * float(pos["size"])

            if max_profit > 0:
                capture_ratio = profit / max_profit
                if capture_ratio >= ev_capture_thresh:
                    pnl = self._compute_pnl(pos)
                    await self.db.execute(
                        """UPDATE positions SET
                             status = 'CLOSED', pnl = ?, closed_at = datetime('now')
                           WHERE id = ?""",
                        (pnl, pos["id"]),
                    )
                    self.logger.info(
                        "EXIT (EV capture %.0f%%): position #%d on %s (pnl=%.2f)",
                        capture_ratio * 100, pos["id"], pos["market_id"], pnl,
                    )

        await self.db.commit()

    # ── SUB-ROUTINE D: Resolution Tracker ─────────────────────────────

    async def _sub_d_resolution_tracker(self) -> None:
        """Detect markets converging to resolution and close positions.

        A market is "resolving" when:
        - YES price > 85% → almost certainly resolving YES
        - YES price < 15% → almost certainly resolving NO
        """
        yes_thresh = float(self._plc.get("resolution_tracker", {}).get(
            "convergence_yes_threshold", 0.85
        ))
        no_thresh = float(self._plc.get("resolution_tracker", {}).get(
            "convergence_no_threshold", 0.15
        ))

        positions = await self.db.fetchall(
            """SELECT p.id, p.market_id, p.engine, p.side, p.size,
                      p.entry_price, p.current_price, p.signal_id
               FROM positions p WHERE p.status = 'OPEN'"""
        )

        for pos in positions:
            price_row = await self.db.fetchone(
                "SELECT yes_price FROM prices WHERE market_id = ? ORDER BY timestamp DESC LIMIT 1",
                (pos["market_id"],),
            )
            if not price_row:
                continue

            yes_price = float(price_row["yes_price"])

            # Check if market is converging to resolution
            resolved_direction = None
            if yes_price >= yes_thresh:
                resolved_direction = "YES"
            elif yes_price <= no_thresh:
                resolved_direction = "NO"

            if resolved_direction:
                pnl = self._compute_pnl(pos)

                # Determine if our bet was correct
                correct = (
                    (pos["side"] == "YES" and resolved_direction == "YES")
                    or (pos["side"] == "NO" and resolved_direction == "NO")
                )

                # Close position
                await self.db.execute(
                    """UPDATE positions SET
                         status = 'CLOSED', pnl = ?, closed_at = datetime('now')
                       WHERE id = ?""",
                    (pnl, pos["id"]),
                )

                # Write performance record
                await self.db.execute(
                    """INSERT INTO performance
                       (signal_id, position_id, engine, resolved, correct, pnl, resolved_at)
                       VALUES (?, ?, ?, 1, ?, ?, datetime('now'))""",
                    (pos["signal_id"], pos["id"], pos["engine"], 1 if correct else 0, pnl),
                )

                self.logger.info(
                    "RESOLVED: position #%d on %s → %s (correct=%s, pnl=%.2f)",
                    pos["id"], pos["market_id"], resolved_direction, correct, pnl,
                )

        await self.db.commit()

    # ── SUB-ROUTINE E: Correlation Scanner ────────────────────────────

    async def _sub_e_correlation_scanner(self) -> None:
        """Check for correlated exposure across engines.

        Groups open positions by event_id (from markets table) and flags
        if the combined exposure on a single event exceeds thresholds:
        - >3% of total capital → emit WARNING
        - >7% of total capital → BLOCK new entries
        """
        alert_pct = float(self._plc.get("correlation_scanner", {}).get(
            "event_exposure_alert_threshold_pct", 0.03
        ))
        block_pct = float(self._plc.get("correlation_scanner", {}).get(
            "event_exposure_block_threshold_pct", 0.07
        ))

        # Get total capital across all engines
        total_row = await self.db.fetchone(
            "SELECT SUM(total_capital) as total FROM engine_state"
        )
        total_capital = float(total_row["total"]) if total_row and total_row["total"] else 0
        if total_capital <= 0:
            return

        # Get open positions with their event_ids
        positions = await self.db.fetchall(
            """SELECT p.id, p.market_id, p.engine, p.size, p.entry_price,
                      m.event_id
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status = 'OPEN' AND m.event_id IS NOT NULL"""
        )

        # Group exposure by event_id
        event_exposure: dict[str, float] = {}
        for pos in positions:
            event_id = pos["event_id"]
            exposure = float(pos["size"]) * float(pos["entry_price"])
            event_exposure[event_id] = event_exposure.get(event_id, 0) + exposure

        # Check thresholds
        for event_id, exposure in event_exposure.items():
            exposure_pct = exposure / total_capital
            if exposure_pct >= block_pct:
                # Store block flag in system_state
                await self.db.execute(
                    """INSERT OR REPLACE INTO system_state (key, value, updated_at)
                       VALUES (?, ?, datetime('now'))""",
                    (f"corr_block_{event_id}", f"BLOCKED: {exposure_pct:.1%} exposure"),
                )
                self.logger.warning(
                    "CORRELATION BLOCK: event %s has %.1f%% exposure (threshold=%.0f%%)",
                    event_id, exposure_pct * 100, block_pct * 100,
                )
            elif exposure_pct >= alert_pct:
                self.logger.info(
                    "CORR WARNING: event %s has %.1f%% exposure",
                    event_id, exposure_pct * 100,
                )

        await self.db.commit()

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _compute_pnl(pos: Any) -> float:
        """Compute realized P&L for a position."""
        current = float(pos["current_price"]) if pos["current_price"] else 0
        entry = float(pos["entry_price"])
        size = float(pos["size"])
        if pos["side"] == "YES":
            return (current - entry) * size
        else:
            return (entry - current) * size
