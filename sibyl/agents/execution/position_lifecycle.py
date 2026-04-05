"""
Position Lifecycle Manager — monitors and manages all open positions.

PURPOSE:
    Once the Order Executor opens a position, THIS agent takes over.  It runs
    6 sub-routines on overlapping schedules to monitor risk, optimize exits,
    track resolution, and reconcile with the exchange.

THE 6 SUB-ROUTINES:

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

    F — POSITION RECONCILIATION (every 15 min, live mode only — Sprint 22):
        Syncs DB positions against actual Kalshi portfolio via get_positions().
        - Ghost DB positions (DB open, Kalshi empty) → mark GHOST_CLOSED
        - Orphan Kalshi positions (Kalshi open, DB empty) → create tracking entry
        - Pending exits (STOP_PENDING/CLOSE_PENDING) no longer on Kalshi → mark CLOSED

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
    """Monitors open positions with 6 sub-routines on overlapping schedules.

    Sprint 20.5: Now initializes a KalshiClient for LIVE position exits.
    Sprint 22: Added position reconciliation (sub-routine F) and fill-price
    tracking on all exit paths.
    When a sub-routine decides to close a position (stop-loss, take profit,
    or resolution), it places a sell order on Kalshi before updating the DB.
    """

    def __init__(self, db: DatabaseManager, config: dict[str, Any], mode: str = "paper") -> None:
        super().__init__(name="position_lifecycle", db=db, config=config)
        self._plc: dict[str, Any] = {}  # position_lifecycle_config.yaml
        self._kalshi_client = None       # Sprint 20.5: for LIVE sell orders
        self._mode: str = mode           # Sprint 27: mode from system config, not credentials

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
        """Load position lifecycle configuration and Kalshi client."""
        from sibyl.core.config import load_yaml
        try:
            self._plc = load_yaml("position_lifecycle_config.yaml")
        except FileNotFoundError:
            self._plc = {}

        # Sprint 27: Mode enforcement — system config is the single source of truth.
        # Credentials alone do NOT upgrade paper to live.
        from sibyl.clients.kalshi_client import get_shared_kalshi_client
        shared = get_shared_kalshi_client(self.config)
        if self._mode == "live" and shared.is_authenticated:
            self._kalshi_client = shared
            self.logger.info("Position Lifecycle using shared Kalshi client (LIVE mode)")
        elif self._mode == "live" and not shared.is_authenticated:
            self.logger.error("LIVE mode requested but Kalshi not authenticated — falling back to paper")
            self._mode = "paper"
        else:
            self.logger.info("Position Lifecycle in PAPER mode — no Kalshi orders will be placed")

        # Sprint 25: Full Kalshi-source reconciliation on startup in live mode.
        # Ensures DB accurately reflects Kalshi reality before any trading.
        if self._mode == "live" and self._kalshi_client:
            await self._startup_reconciliation()

        self.logger.info("Position Lifecycle Manager started (5 sub-routines active, mode=%s)", self._mode)

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

        # Sub-routine F: Position Reconciliation — every 5 min (Sprint 23B:
        # tightened from 15 min since async fill-and-forget can create orphans faster)
        recon_interval = int(5 * 60 / 5)  # 60 cycles
        if cycle % recon_interval == 0 and self._mode == "live":
            await self._sub_f_position_reconciliation()

    async def stop(self) -> None:
        """Shut down lifecycle manager."""
        # Don't close shared client — other agents may still need it
        self.logger.info("Position Lifecycle Manager stopped")

    # ── Sell Helper (Sprint 20.5) ─────────────────────────────────────

    async def _sell_on_kalshi(self, pos: Any, reason: str) -> tuple[bool, float | None]:
        """Place a sell order on Kalshi and verify the fill.

        Sprint 22: Returns actual fill price so P&L is computed from real
        proceeds, not snapshot prices.  Polls up to 5 times to confirm fill.

        Called by stop guard, exit optimizer, and resolution tracker
        when they decide to close a position. In paper mode, this is a no-op.

        Args:
            pos: Position row dict (must have market_id, side, size).
            reason: Human-readable exit reason for logging.

        Returns:
            (success, actual_fill_price) — success is True if sell filled
            (or paper mode).  actual_fill_price is the real exit price from
            Kalshi, or None if paper mode or unavailable.
        """
        if self._mode != "live" or not self._kalshi_client:
            # Paper mode — DB-only close is sufficient
            return True, None

        market_id = pos["market_id"]
        side = pos["side"].lower()
        size = int(float(pos["size"]))

        try:
            result = await self._kalshi_client.sell_position(
                ticker=market_id,
                side=side,
                size=size,
                order_type="market",  # Market order for fast exit
            )
            if result and "order" in result:
                order_id = result["order"].get("order_id", "unknown")
                order_status = result["order"].get("status", "unknown")
                self.logger.info(
                    "LIVE SELL: %s %d contracts on %s (order=%s, status=%s, reason=%s)",
                    side, size, market_id, order_id, order_status, reason,
                )

                # Sprint 23: Sell fill confirmation — 60s timeout (was 120s),
                # 3s intervals (was 6s).  Sells need confirmation before marking
                # position closed, but 120s was excessive for market orders.
                import asyncio
                actual_fill_price: float | None = None
                fill_confirmed = order_status in ("executed", "filled")

                if fill_confirmed:
                    avg_price = result["order"].get("average_fill_price")
                    if avg_price is not None:
                        actual_fill_price = float(avg_price) / 100.0

                SELL_POLL_ATTEMPTS = 20
                SELL_POLL_INTERVAL = 3  # seconds (total: 60s)

                if not fill_confirmed:
                    for attempt in range(SELL_POLL_ATTEMPTS):
                        await asyncio.sleep(SELL_POLL_INTERVAL)
                        try:
                            confirm = await self._kalshi_client.get_order(order_id)
                            if confirm and "order" in confirm:
                                confirmed_status = confirm["order"].get("status", "unknown")
                                if confirmed_status in ("executed", "filled"):
                                    fill_confirmed = True
                                    avg_price = confirm["order"].get("average_fill_price")
                                    if avg_price is not None:
                                        actual_fill_price = float(avg_price) / 100.0
                                    self.logger.info(
                                        "SELL CONFIRMED [%d/%d]: %s fill_price=%.4f",
                                        attempt + 1, SELL_POLL_ATTEMPTS, order_id,
                                        actual_fill_price or 0,
                                    )
                                    break
                                elif confirmed_status in ("canceled", "expired"):
                                    self.logger.error(
                                        "SELL REJECTED: %s was %s", order_id, confirmed_status,
                                    )
                                    return False, None
                        except Exception as e:
                            self.logger.warning(
                                "Sell confirm attempt %d/%d failed for %s: %s",
                                attempt + 1, SELL_POLL_ATTEMPTS, order_id, e,
                            )

                if not fill_confirmed:
                    self.logger.error(
                        "SELL NOT FILLED after %ds: %s on %s (reason=%s) — "
                        "position remains open on Kalshi",
                        SELL_POLL_ATTEMPTS * SELL_POLL_INTERVAL,
                        order_id, market_id, reason,
                    )
                    return False, None

                # Sprint 29: Record sell execution with fee for accurate P&L tracking
                sell_fee = size * 0.007  # 0.7% per contract sell-side
                try:
                    await self.db.execute(
                        """INSERT INTO executions
                           (signal_id, position_id, engine, platform, order_id,
                            side, fill_price, size, order_type, fee_amount)
                           VALUES (?, ?, ?, 'kalshi', ?, 'SELL', ?, ?, 'market', ?)""",
                        (
                            pos["signal_id"], pos["id"], pos["engine"],
                            order_id, actual_fill_price or 0.0, float(size), sell_fee,
                        ),
                    )
                except Exception:
                    self.logger.debug("Failed to record sell execution for %s", market_id)

                return True, actual_fill_price
            else:
                self.logger.error(
                    "LIVE SELL FAILED for %s (reason=%s) — result: %s",
                    market_id, reason, result,
                )
                return False, None
        except Exception:
            self.logger.exception(
                "LIVE SELL EXCEPTION for %s (reason=%s)", market_id, reason,
            )
            return False, None

    # ── Fresh Price Helper (Sprint 20.5) ──────────────────────────────

    async def _get_fresh_price(self, market_id: str) -> float | None:
        """Get the latest price from the prices table (updated every 5s by KalshiMonitor).

        Sprint 20.5: Stop Guard and other sub-routines should use this instead
        of the stale positions.current_price field, which is only updated every
        90-300 seconds by the EV Monitor.
        """
        price_row = await self.db.fetchone(
            "SELECT yes_price FROM prices WHERE market_id = ? ORDER BY timestamp DESC LIMIT 1",
            (market_id,),
        )
        if price_row and price_row["yes_price"] is not None:
            return float(price_row["yes_price"])
        return None

    # ── SUB-ROUTINE A: Stop Guard ─────────────────────────────────────

    async def _sub_a_stop_guard(self) -> None:
        """Check all OPEN positions against their stop_loss prices.

        If current_price has moved against us past stop_loss → sell on Kalshi + close in DB.
        If 3+ stops in 15 minutes for one engine → trigger circuit breaker.

        Sprint 20.5: Now reads fresh price from prices table (5s updates)
        instead of stale positions.current_price (300s updates), and places
        a sell order on Kalshi before closing the position in the DB.
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
            # Sprint 20.5: Use fresh price from prices table, not stale positions.current_price
            fresh_price = await self._get_fresh_price(pos["market_id"])
            current = fresh_price if fresh_price is not None else (
                float(pos["current_price"]) if pos["current_price"] else None
            )
            stop = float(pos["stop_loss"])

            if current is None:
                continue

            # Also update positions.current_price while we're at it
            if fresh_price is not None:
                await self.db.execute(
                    "UPDATE positions SET current_price = ? WHERE id = ?",
                    (fresh_price, pos["id"]),
                )

            # For YES positions: stop if price drops below stop_loss
            # For NO positions: stop if price rises above (1 - stop_loss)
            stopped = False
            if pos["side"] == "YES" and current <= stop:
                stopped = True
            elif pos["side"] == "NO" and current >= (1.0 - stop):
                stopped = True

            if stopped:
                # Sprint 22: Sell on Kalshi FIRST, get actual fill price
                sold, fill_price = await self._sell_on_kalshi(pos, reason="STOP_LOSS")

                # Sprint 22: Use actual fill price for P&L if available
                exit_price = fill_price if fill_price else current
                pnl = self._compute_pnl_with_price(pos, exit_price)
                status = "STOPPED" if sold else "STOP_PENDING"
                # Only set closed_at if actually sold — pending exits are NOT closed
                await self.db.execute(
                    """UPDATE positions SET
                         status = ?, pnl = ?, current_price = ?,
                         closed_at = CASE WHEN ? THEN datetime('now') ELSE closed_at END
                       WHERE id = ?""",
                    (status, pnl, exit_price, sold, pos["id"]),
                )
                self.logger.warning(
                    "STOP LOSS %s: position #%d on %s (engine=%s, pnl=%.2f, sold=%s)",
                    "HIT" if sold else "PENDING",
                    pos["id"], pos["market_id"], pos["engine"], pnl, sold,
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

        # Sprint 30-A: Auto-reset circuit breaker when the stop window expires.
        # The trigger loop above only ever writes TRIGGERED; this block clears it
        # once no stops remain inside the cb_window.
        triggered = await self.db.fetchall(
            "SELECT engine FROM engine_state WHERE circuit_breaker = 'TRIGGERED'"
        )
        for row in triggered:
            eng = row["engine"]
            now_ts = datetime.now(timezone.utc).timestamp()
            cutoff = now_ts - (cb_window * 60)
            self._recent_stops[eng] = [
                t for t in self._recent_stops[eng] if t > cutoff
            ]
            if len(self._recent_stops[eng]) < cb_count:
                await self.db.execute(
                    "UPDATE engine_state SET circuit_breaker = 'CLEAR' WHERE engine = ?",
                    (eng,),
                )
                self.logger.info(
                    "Circuit breaker CLEARED for %s (window expired, stops in window: %d)",
                    eng, len(self._recent_stops[eng]),
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
                    "EV SHIFT on position #%d (%s): %.3f -> %.3f (delta=%.3f)",
                    pos["id"], engine, old_ev, new_ev, ev_shift,
                )

        await self.db.commit()

    # ── SUB-ROUTINE C: Exit Optimizer ─────────────────────────────────

    async def _sub_c_exit_optimizer(self) -> None:
        """Determine when to close positions.

        Three exit triggers:
        1. EV Capture: >80% of estimated profit has been realized → take profit.
        2. Momentum Stall: price hasn't moved >0.5% in 4 consecutive checks.
        3. Time Decay (Sprint 26): market closes within N minutes AND position
           is not in profit → exit to avoid settlement loss. LT3 showed
           positions from 3+ weeks ago still open, accumulating losses.

        Sprint 20.5: Uses fresh prices from prices table and places sell
        orders on Kalshi before closing in DB.
        """
        ev_capture_thresh = float(self._plc.get("exit_optimizer", {}).get(
            "ev_capture_threshold", 0.80
        ))
        # Sprint 26: Time-based exit — close losing positions before settlement
        expiry_exit_minutes = float(self._plc.get("exit_optimizer", {}).get(
            "expiry_exit_minutes", 15
        ))
        # Sprint 29: Momentum stall exit — close positions with no price movement.
        # Locks capital in stagnant markets until settlement.
        stall_cycles = int(self._plc.get("exit_optimizer", {}).get(
            "momentum_stall_cycles", 4
        ))
        stall_threshold = float(self._plc.get("exit_optimizer", {}).get(
            "momentum_stall_threshold", 0.005
        ))
        # Sprint 31: Min holding period by timeframe (auditor rec 3.5).
        min_hold_cfg = self._plc.get("exit_optimizer", {}).get("min_hold_seconds", {})

        positions = await self.db.fetchall(
            """SELECT p.id, p.market_id, p.engine, p.side, p.size, p.entry_price,
                      p.current_price, p.ev_current, p.signal_id, p.opened_at,
                      s.timeframe
               FROM positions p
               LEFT JOIN signals s ON p.signal_id = s.id
               WHERE p.status = 'OPEN'"""
        )

        for pos in positions:
            # Sprint 20.5: Use fresh price
            fresh_price = await self._get_fresh_price(pos["market_id"])
            current = fresh_price if fresh_price is not None else (
                float(pos["current_price"]) if pos["current_price"] else None
            )
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
                    # Sprint 22: Sell on Kalshi FIRST, get actual fill price
                    sold, fill_price = await self._sell_on_kalshi(pos, reason="EV_CAPTURE")
                    exit_price = fill_price if fill_price else current
                    pnl = self._compute_pnl_with_price(pos, exit_price)
                    status = "CLOSED" if sold else "CLOSE_PENDING"
                    await self.db.execute(
                        """UPDATE positions SET
                             status = ?, pnl = ?, current_price = ?,
                             closed_at = CASE WHEN ? THEN datetime('now') ELSE closed_at END
                           WHERE id = ?""",
                        (status, pnl, exit_price, sold, pos["id"]),
                    )
                    self.logger.info(
                        "EXIT (EV capture %.0f%%): position #%d on %s (pnl=%.2f, sold=%s)",
                        capture_ratio * 100, pos["id"], pos["market_id"], pnl, sold,
                    )
                    continue  # Already closed — skip time-decay check

            # Sprint 26: Time-based exit — close losing positions nearing expiry.
            # Markets approaching settlement converge to 0 or 100. Being on the
            # wrong side near expiry means maximum loss. Exit early to limit damage.
            if profit < 0:
                mkt_row = await self.db.fetchone(
                    "SELECT close_date FROM markets WHERE id = ?",
                    (pos["market_id"],),
                )
                if mkt_row and mkt_row["close_date"]:
                    try:
                        close_dt = datetime.fromisoformat(
                            mkt_row["close_date"].replace("Z", "+00:00")
                        )
                        mins_left = (close_dt - datetime.now(timezone.utc)).total_seconds() / 60
                        if 0 < mins_left <= expiry_exit_minutes:
                            sold, fill_price = await self._sell_on_kalshi(
                                pos, reason="TIME_DECAY_EXIT"
                            )
                            exit_price = fill_price if fill_price else current
                            pnl = self._compute_pnl_with_price(pos, exit_price)
                            status = "CLOSED" if sold else "CLOSE_PENDING"
                            await self.db.execute(
                                """UPDATE positions SET
                                     status = ?, pnl = ?, current_price = ?,
                                     closed_at = CASE WHEN ? THEN datetime('now') ELSE closed_at END
                                   WHERE id = ?""",
                                (status, pnl, exit_price, sold, pos["id"]),
                            )
                            self.logger.info(
                                "EXIT (time decay, %.0f min to close): position #%d on %s "
                                "(pnl=%.2f, sold=%s)",
                                mins_left, pos["id"], pos["market_id"], pnl, sold,
                            )
                    except (ValueError, TypeError):
                        pass

            # Sprint 31: Holding period minimum — skip momentum stall check if
            # position hasn't been held long enough for its timeframe.
            # Monthly positions stalling after 20s is not a true stall.
            timeframe = pos["timeframe"] or "daily"
            min_hold = float(min_hold_cfg.get(timeframe, 0))
            if min_hold > 0 and pos["opened_at"]:
                try:
                    opened_dt = datetime.fromisoformat(pos["opened_at"])
                    if opened_dt.tzinfo is None:
                        opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                    held_seconds = (datetime.now(timezone.utc) - opened_dt).total_seconds()
                    if held_seconds < min_hold:
                        # Still within minimum hold — skip stall check entirely
                        if current is not None:
                            await self.db.execute(
                                "INSERT OR REPLACE INTO system_state (key, value, updated_at) "
                                "VALUES (?, ?, datetime('now'))",
                                (f"stall_prev_price_{pos['id']}", str(current)),
                            )
                        continue
                except (ValueError, TypeError):
                    pass

            # Sprint 29: Momentum stall exit — free capital from stagnant positions.
            # Track consecutive cycles where price change < threshold.
            # After N stall cycles, exit to redeploy capital productively.
            stall_key = f"stall_count_{pos['id']}"
            prev_price_key = f"stall_prev_price_{pos['id']}"
            prev_row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = ?", (prev_price_key,)
            )
            prev_price = float(prev_row["value"]) if prev_row else None

            if prev_price is not None and current is not None:
                price_change = abs(current - prev_price)
                if price_change < stall_threshold:
                    # Increment stall counter
                    count_row = await self.db.fetchone(
                        "SELECT value FROM system_state WHERE key = ?", (stall_key,)
                    )
                    stall_count = int(count_row["value"]) + 1 if count_row else 1
                    await self.db.execute(
                        "INSERT OR REPLACE INTO system_state (key, value, updated_at) "
                        "VALUES (?, ?, datetime('now'))",
                        (stall_key, str(stall_count)),
                    )
                    if stall_count >= stall_cycles:
                        sold, fill_price = await self._sell_on_kalshi(
                            pos, reason="MOMENTUM_STALL"
                        )
                        exit_price = fill_price if fill_price else current
                        pnl = self._compute_pnl_with_price(pos, exit_price)
                        status = "CLOSED" if sold else "CLOSE_PENDING"
                        await self.db.execute(
                            """UPDATE positions SET
                                 status = ?, pnl = ?, current_price = ?,
                                 closed_at = CASE WHEN ? THEN datetime('now') ELSE closed_at END
                               WHERE id = ?""",
                            (status, pnl, exit_price, sold, pos["id"]),
                        )
                        self.logger.info(
                            "EXIT (momentum stall, %d cycles): position #%d on %s "
                            "(pnl=%.2f, sold=%s)",
                            stall_count, pos["id"], pos["market_id"], pnl, sold,
                        )
                        # Clean up stall tracking keys
                        await self.db.execute(
                            "DELETE FROM system_state WHERE key IN (?, ?)",
                            (stall_key, prev_price_key),
                        )
                        continue
                else:
                    # Price moved — reset stall counter
                    await self.db.execute(
                        "DELETE FROM system_state WHERE key = ?", (stall_key,),
                    )

            # Update previous price for next cycle's stall check
            if current is not None:
                await self.db.execute(
                    "INSERT OR REPLACE INTO system_state (key, value, updated_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (prev_price_key, str(current)),
                )

        await self.db.commit()

    # ── SUB-ROUTINE D: Resolution Tracker ─────────────────────────────

    async def _sub_d_resolution_tracker(self) -> None:
        """Detect markets converging to resolution and close positions.

        A market is "resolving" when:
        - YES price > 85% → almost certainly resolving YES
        - YES price < 15% → almost certainly resolving NO

        Sprint 20.5: Places sell order on Kalshi before closing in DB.
        For markets near resolution, selling at market captures nearly
        the full value without waiting for settlement.
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
            fresh_price = await self._get_fresh_price(pos["market_id"])
            if fresh_price is None:
                continue

            yes_price = fresh_price

            # Check if market is converging to resolution
            resolved_direction = None
            if yes_price >= yes_thresh:
                resolved_direction = "YES"
            elif yes_price <= no_thresh:
                resolved_direction = "NO"

            if resolved_direction:
                # Sprint 22: Sell on Kalshi with fill verification
                sold, fill_price = await self._sell_on_kalshi(
                    pos, reason=f"RESOLUTION_{resolved_direction}"
                )
                exit_price = fill_price if fill_price else yes_price
                pnl = self._compute_pnl_with_price(pos, exit_price)

                # Determine if our bet was correct
                correct = (
                    (pos["side"] == "YES" and resolved_direction == "YES")
                    or (pos["side"] == "NO" and resolved_direction == "NO")
                )

                # Close position — only set closed_at if actually sold
                status = "CLOSED" if sold else "CLOSE_PENDING"
                await self.db.execute(
                    """UPDATE positions SET
                         status = ?, pnl = ?, current_price = ?,
                         closed_at = CASE WHEN ? THEN datetime('now') ELSE closed_at END
                       WHERE id = ?""",
                    (status, pnl, exit_price, sold, pos["id"]),
                )

                # Write performance record
                await self.db.execute(
                    """INSERT INTO performance
                       (signal_id, position_id, engine, resolved, correct, pnl, resolved_at)
                       VALUES (?, ?, ?, 1, ?, ?, datetime('now'))""",
                    (pos["signal_id"], pos["id"], pos["engine"], 1 if correct else 0, pnl),
                )

                self.logger.info(
                    "RESOLVED: position #%d on %s -> %s (correct=%s, pnl=%.2f, sold=%s)",
                    pos["id"], pos["market_id"], resolved_direction, correct, pnl, sold,
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

    # ── Sprint 25: Startup Reconciliation ───────────────────────────

    async def _startup_reconciliation(self) -> None:
        """Full Kalshi-source position sync on startup.

        Unlike sub-routine F (which runs periodically and finds ghosts/orphans),
        this runs ONCE at startup and treats Kalshi as the source of truth.
        It handles the case where the DB was populated by paper testing or
        stale sessions.
        """
        try:
            # Fetch ALL Kalshi positions (paginated)
            all_kalshi: list[dict] = []
            cursor = None
            while True:
                data = await self._kalshi_client.get_positions(
                    limit=100, cursor=cursor, settlement_status="unsettled"
                )
                positions = data.get("market_positions", [])
                all_kalshi.extend(positions)
                cursor = data.get("cursor")
                if not cursor or not positions:
                    break

            # Build ticker -> position map (only positions with actual exposure)
            kalshi_by_ticker: dict[str, dict] = {}
            for kp in all_kalshi:
                ticker = kp.get("ticker", "")
                exposure = float(kp.get("market_exposure_dollars", 0))
                if ticker and exposure > 0:
                    kalshi_by_ticker[ticker] = kp

            # Fetch all DB OPEN positions
            db_positions = await self.db.fetchall(
                "SELECT id, market_id, side, size FROM positions "
                "WHERE status IN ('OPEN', 'STOP_PENDING', 'CLOSE_PENDING') "
                "AND platform = 'kalshi'"
            )
            db_tickers = {pos["market_id"] for pos in db_positions}

            ghost_count = 0
            orphan_count = 0
            synced_count = 0

            # Close DB positions that don't exist on Kalshi
            for pos in db_positions:
                if pos["market_id"] not in kalshi_by_ticker:
                    await self.db.execute(
                        "UPDATE positions SET status = 'GHOST_CLOSED', pnl = 0, "
                        "closed_at = datetime('now') WHERE id = ?",
                        (pos["id"],),
                    )
                    ghost_count += 1
                else:
                    synced_count += 1

            # Create DB records for Kalshi positions missing from DB
            for ticker, kp in kalshi_by_ticker.items():
                if ticker not in db_tickers:
                    pos_fp = float(kp.get("position_fp", 0))
                    if pos_fp == 0:
                        continue
                    side = "YES" if pos_fp > 0 else "NO"
                    size = abs(pos_fp)
                    exposure = float(kp.get("market_exposure_dollars", 0))
                    avg_price = exposure / size if size > 0 else 0.50

                    await self.db.execute(
                        "INSERT INTO positions "
                        "(market_id, platform, engine, side, size, "
                        "entry_price, current_price, stop_loss, "
                        "status, signal_id, thesis) "
                        "VALUES (?, 'kalshi', 'SGE', ?, ?, ?, ?, 0, "
                        "'OPEN', NULL, 'STARTUP_SYNC: imported from Kalshi')",
                        (ticker, side, size, round(avg_price, 4), round(avg_price, 4)),
                    )
                    orphan_count += 1

            if ghost_count > 0 or orphan_count > 0:
                await self.db.commit()

            # Sprint 27: Enhanced settled-position reconciliation.
            # Fetch recently settled Kalshi positions to properly record P&L
            # for positions that settled while Sibyl was offline.
            settled_count = 0
            try:
                settled_kalshi: list[dict] = []
                s_cursor = None
                while True:
                    s_data = await self._kalshi_client.get_positions(
                        limit=100, cursor=s_cursor, settlement_status="settled"
                    )
                    s_page = s_data.get("market_positions", []) if isinstance(s_data, dict) else []
                    settled_kalshi.extend(s_page)
                    s_cursor = s_data.get("cursor") if isinstance(s_data, dict) else None
                    if not s_cursor or not s_page:
                        break

                # Cross-reference: DB OPEN positions that settled on Kalshi
                for pos in db_positions:
                    if pos["market_id"] in kalshi_by_ticker:
                        continue  # Still active on Kalshi
                    # Check if it's in the settled list with P&L info
                    for sk in settled_kalshi:
                        if sk.get("ticker") == pos["market_id"]:
                            rpnl = float(sk.get("realized_pnl_dollars", 0))
                            await self.db.execute(
                                "UPDATE positions SET status = 'SETTLED', pnl = ?, "
                                "closed_at = datetime('now') WHERE id = ? AND status != 'SETTLED'",
                                (rpnl, pos["id"]),
                            )
                            settled_count += 1
                            break
            except Exception:
                self.logger.debug("Settled position query failed — using fallback")

            # Fallback: Mark remaining stale positions by checking market close dates
            stale_open = await self.db.fetchall(
                "SELECT p.id, p.market_id FROM positions p "
                "LEFT JOIN markets m ON p.market_id = m.id "
                "WHERE p.status = 'OPEN' AND p.platform = 'kalshi' "
                "AND (m.close_date < datetime('now') OR m.status = 'closed')"
            )
            for sp in stale_open:
                if sp["market_id"] not in kalshi_by_ticker:
                    await self.db.execute(
                        "UPDATE positions SET status = 'SETTLED', pnl = 0, "
                        "closed_at = datetime('now') WHERE id = ?",
                        (sp["id"],),
                    )
                    settled_count += 1

            if settled_count > 0:
                await self.db.commit()

            self.logger.info(
                "Startup reconciliation: %d Kalshi positions, %d synced, "
                "%d ghosts closed, %d orphans imported, %d settled purged",
                len(kalshi_by_ticker), synced_count, ghost_count, orphan_count,
                settled_count,
            )

        except Exception:
            self.logger.exception("Startup reconciliation failed")

    # ── SUB-ROUTINE F: Position Reconciliation (Sprint 22) ──────────

    async def _sub_f_position_reconciliation(self) -> None:
        """Reconcile DB positions against actual Kalshi portfolio.

        Sprint 22: Runs every 15 minutes in live mode.  Detects two types
        of discrepancy that cause ghost trades and phantom P&L:

        1. GHOST DB POSITIONS: DB says OPEN, Kalshi has no position
           → Order never filled or was cancelled.  Mark as GHOST_CLOSED.

        2. ORPHAN KALSHI POSITIONS: Kalshi has position, DB says nothing
           → Fill confirmation failed.  Create DB record to track it.

        Also reconciles STOP_PENDING / CLOSE_PENDING positions — if Kalshi
        shows no position for them, they expired at settlement.
        """
        if not self._kalshi_client:
            return

        try:
            # Sprint 27: Paginated fetch — handles accounts with >100 positions.
            # Previously only fetched first page (100 positions), missing orphans.
            kalshi_positions: list[dict] = []
            cursor = None
            while True:
                positions_data = await self._kalshi_client.get_positions(
                    limit=100, cursor=cursor, settlement_status="unsettled"
                )
                if isinstance(positions_data, dict):
                    page = positions_data.get("market_positions", [])
                    kalshi_positions.extend(page)
                    cursor = positions_data.get("cursor")
                    if not cursor or not page:
                        break
                elif isinstance(positions_data, list):
                    kalshi_positions = positions_data
                    break
                else:
                    self.logger.warning("Position reconciliation: unexpected response type")
                    return

            # Build set of Kalshi ticker -> position data
            kalshi_by_ticker: dict[str, dict] = {}
            for kp in kalshi_positions:
                if isinstance(kp, dict):
                    ticker = kp.get("ticker") or kp.get("market_ticker", "")
                    exposure = float(kp.get("market_exposure_dollars", 0))
                    if ticker and exposure > 0:
                        kalshi_by_ticker[ticker] = kp

            # Fetch DB open/pending positions
            db_positions = await self.db.fetchall(
                """SELECT id, market_id, side, size, entry_price, status
                   FROM positions
                   WHERE status IN ('OPEN', 'STOP_PENDING', 'CLOSE_PENDING')
                     AND platform = 'kalshi'"""
            )

            ghost_count = 0
            orphan_count = 0
            pending_resolved = 0
            db_tickers = set()

            for pos in db_positions:
                mid = pos["market_id"]
                db_tickers.add(mid)

                if mid in kalshi_by_ticker:
                    # Position exists on both sides — consistent
                    pass
                else:
                    # DB says open, Kalshi says nothing
                    if pos["status"] == "OPEN":
                        # Ghost position — order never actually filled
                        self.logger.error(
                            "GHOST POSITION DETECTED: DB position #%d on %s "
                            "(side=%s, size=%.0f) has no Kalshi counterpart — "
                            "marking as GHOST_CLOSED with zero P&L",
                            pos["id"], mid, pos["side"], pos["size"],
                        )
                        await self.db.execute(
                            """UPDATE positions SET
                                 status = 'GHOST_CLOSED', pnl = 0,
                                 closed_at = datetime('now')
                               WHERE id = ?""",
                            (pos["id"],),
                        )
                        ghost_count += 1
                    elif pos["status"] in ("STOP_PENDING", "CLOSE_PENDING"):
                        # Pending exit resolved (position settled or expired)
                        self.logger.info(
                            "PENDING RESOLVED: position #%d on %s — "
                            "no longer on Kalshi, marking CLOSED",
                            pos["id"], mid,
                        )
                        await self.db.execute(
                            """UPDATE positions SET
                                 status = 'CLOSED', closed_at = datetime('now')
                               WHERE id = ?""",
                            (pos["id"],),
                        )
                        pending_resolved += 1

            # Check for orphan Kalshi positions (Kalshi has it, DB doesn't)
            for ticker, kp in kalshi_by_ticker.items():
                if ticker not in db_tickers:
                    self.logger.warning(
                        "ORPHAN KALSHI POSITION: %s exists on Kalshi "
                        "(traded=%s) but has no DB record — "
                        "creating tracking entry",
                        ticker, kp.get("total_traded", "?"),
                    )
                    # Determine side and size from Kalshi data
                    yes_count = kp.get("yes_count", 0) or 0
                    no_count = kp.get("no_count", 0) or 0
                    side = "YES" if yes_count > no_count else "NO"
                    size = max(yes_count, no_count)
                    avg_price = float(kp.get("average_price", 50)) / 100.0

                    await self.db.execute(
                        """INSERT INTO positions
                           (market_id, platform, engine, side, size,
                            entry_price, current_price, stop_loss,
                            status, signal_id, thesis)
                           VALUES (?, 'kalshi', 'SGE', ?, ?, ?, ?, 0,
                                   'OPEN', NULL,
                                   'ORPHAN: discovered by position reconciliation')""",
                        (ticker, side, float(size), avg_price, avg_price),
                    )
                    orphan_count += 1

            if ghost_count > 0 or orphan_count > 0 or pending_resolved > 0:
                await self.db.commit()

            self.logger.info(
                "Position reconciliation: %d DB open, %d Kalshi open, "
                "%d ghosts closed, %d orphans created, %d pending resolved",
                len(db_positions), len(kalshi_by_ticker),
                ghost_count, orphan_count, pending_resolved,
            )

        except Exception:
            self.logger.exception("Position reconciliation failed")

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _compute_pnl(pos: Any) -> float:
        """Compute realized P&L for a position using stored current_price.

        Sprint 29: Includes roundtrip fee deduction (buy + sell side).
        Kalshi taker fee ~0.7c per contract per side = 1.4c roundtrip.
        """
        current = float(pos["current_price"]) if pos["current_price"] else 0
        entry = float(pos["entry_price"])
        size = float(pos["size"])
        fee = size * 0.014  # Roundtrip fee (0.7% buy + 0.7% sell)
        if pos["side"] == "YES":
            return (current - entry) * size - fee
        else:
            return (entry - current) * size - fee

    @staticmethod
    def _compute_pnl_with_price(
        pos: Any, current_price: float, is_settlement: bool = False
    ) -> float:
        """Compute realized P&L using an explicit current price.

        Sprint 20.5: Used by sub-routines that have already fetched a fresh
        price from the prices table, avoiding use of the stale stored value.

        Sprint 29: Fee-adjusted P&L. Settlement exits pay only buy-side fee
        (no sell transaction). Active sells pay roundtrip fee.
        """
        entry = float(pos["entry_price"])
        size = float(pos["size"])
        # Buy-side fee always applies; sell-side only on active exits
        fee_per_side = 0.007  # 0.7% per contract per side
        fee = size * fee_per_side * (1 if is_settlement else 2)
        if pos["side"] == "YES":
            return (current_price - entry) * size - fee
        else:
            return (entry - current_price) * size - fee
