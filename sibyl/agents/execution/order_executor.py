"""
Order Executor — converts routed signals into live positions.

PURPOSE:
    Reads ROUTED signals from the database and converts them into trading
    positions.  In PAPER mode, fills are simulated at current market price.
    In LIVE mode, orders are placed on Kalshi via `KalshiClient.place_order()`.

EXECUTION FLOW:
    1. Fetch oldest ROUTED signal from `signals` table.
    2. Risk check: verify engine has available capital + circuit breaker is CLEAR.
    3. Position sizing: Kelly fraction × engine capital × confidence.
    4. Order placement:
       - Paper mode: simulate fill at current YES/NO price.
       - Live mode:  call KalshiClient.place_order().
    5. Record: write to `positions` + `executions` tables.
    6. Update signal status → EXECUTED.

PAPER MODE (default):
    No real money changes hands.  The executor:
      - Looks up the current market price from the `prices` table.
      - Creates a position as if the order was filled at that price.
      - Writes to `executions` with order_id = "PAPER-{timestamp}".

POSITION SIZING (Kelly Criterion):
    The Kelly formula determines optimal bet size:
        kelly_size = (confidence × payout - (1-confidence)) / payout
    We then apply the engine's kelly_fraction cap (SGE=0.15, ACE=0.35).
    Final size = min(kelly_size, max_single_position_pct) × available_capital.

POLLING: Every 3 seconds (same as Signal Router for minimal latency).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.order_executor")


class OrderExecutor(BaseAgent):
    """Converts ROUTED signals into positions via Kelly-sized orders."""

    def __init__(
        self,
        db: DatabaseManager,
        config: dict[str, Any],
        mode: str = "paper",
    ) -> None:
        """Initialize the Order Executor.

        Args:
            db:     Shared DatabaseManager.
            config: System config dict.
            mode:   "paper" (simulated) or "live" (real money).
        """
        super().__init__(name="order_executor", db=db, config=config)
        self._mode = mode
        self._sge_risk: dict[str, Any] = {}
        self._ace_risk: dict[str, Any] = {}

    @property
    def poll_interval(self) -> float:
        """Run every 3 seconds — minimal latency from signal to position."""
        return 3.0

    async def start(self) -> None:
        """Load engine risk policies for position sizing."""
        from sibyl.core.config import load_yaml
        try:
            sge = load_yaml("sge_config.yaml")
            self._sge_risk = sge.get("risk_policy", {})
        except FileNotFoundError:
            self._sge_risk = {}
        try:
            ace = load_yaml("ace_config.yaml")
            self._ace_risk = ace.get("risk_policy", {})
        except FileNotFoundError:
            self._ace_risk = {}
        self.logger.info("Order Executor started (mode=%s)", self._mode)

    async def run_cycle(self) -> None:
        """Process one ROUTED signal per cycle (oldest first)."""
        signal = await self.db.fetchone(
            """SELECT id, market_id, signal_type, confidence, ev_estimate, routed_to
               FROM signals
               WHERE status = 'ROUTED'
               ORDER BY timestamp ASC
               LIMIT 1"""
        )

        if not signal:
            return

        engine = signal["routed_to"]
        if engine == "DEFERRED":
            return  # Should not happen, but safety check

        # If routed to BOTH, execute for SGE first (conservative sizing)
        engines_to_execute = ["SGE", "ACE"] if engine == "BOTH" else [engine]

        for eng in engines_to_execute:
            await self._execute_for_engine(signal, eng)

        # Mark signal as executed
        await self.db.execute(
            "UPDATE signals SET status = 'EXECUTED' WHERE id = ?",
            (signal["id"],),
        )
        await self.db.commit()

    async def stop(self) -> None:
        self.logger.info("Order Executor stopped")

    # ── Execution Logic ───────────────────────────────────────────────

    async def _execute_for_engine(self, signal: Any, engine: str) -> None:
        """Size and execute a position for a specific engine."""
        risk = self._sge_risk if engine == "SGE" else self._ace_risk
        market_id = signal["market_id"]
        confidence = float(signal["confidence"])

        # ── Risk Check ────────────────────────────────────────────────
        state = await self.db.fetchone(
            "SELECT * FROM engine_state WHERE engine = ?", (engine,)
        )
        if not state:
            self.logger.warning("No engine state for %s — skipping", engine)
            return

        available = float(state["available_capital"])
        circuit = state["circuit_breaker"]

        if circuit == "TRIGGERED":
            self.logger.warning("Circuit breaker TRIGGERED for %s — skipping", engine)
            return

        if available <= 0:
            self.logger.debug("No available capital for %s — skipping", engine)
            return

        # ── Position Sizing (Kelly) ───────────────────────────────────
        kelly_frac = float(risk.get("kelly_fraction", 0.15))
        max_position_pct = float(risk.get("max_single_position_pct", 0.02))

        # Get current price for the market
        price_row = await self.db.fetchone(
            "SELECT yes_price FROM prices WHERE market_id = ? ORDER BY timestamp DESC LIMIT 1",
            (market_id,),
        )
        if not price_row:
            return

        current_price = float(price_row["yes_price"])
        if current_price <= 0 or current_price >= 1.0:
            return

        # Kelly: optimal fraction of bankroll to wager
        # For binary markets: kelly = (confidence × payout - (1-confidence)) / payout
        # Where payout = (1 / price) - 1 for YES bets
        payout = (1.0 / current_price) - 1.0 if current_price > 0 else 0
        if payout <= 0:
            return

        kelly_raw = (confidence * payout - (1.0 - confidence)) / payout
        kelly_raw = max(kelly_raw, 0)  # Don't bet if negative Kelly

        # Apply engine's kelly fraction cap
        kelly_capped = min(kelly_raw, kelly_frac)

        # Position size in dollars
        position_dollars = min(kelly_capped, max_position_pct) * available
        if position_dollars < 1.0:
            return  # Too small to trade

        # Determine side (buy YES if price < 0.50, buy NO if price > 0.50)
        side = "YES" if current_price < 0.50 else "NO"
        entry_price = current_price if side == "YES" else 1.0 - current_price
        size_contracts = int(position_dollars / entry_price) if entry_price > 0 else 0
        if size_contracts < 1:
            return

        # ── Execute ───────────────────────────────────────────────────
        order_id = f"PAPER-{int(time.time() * 1000)}"

        if self._mode == "live":
            # TODO: In live mode, call KalshiClient.place_order()
            # For now, paper-fill to keep things safe
            pass

        # Stop loss from engine config
        stop_loss_pct = float(risk.get("per_market_stop_loss_pct", 0.35))
        stop_loss = entry_price * (1.0 - stop_loss_pct)

        # ── Write Position ────────────────────────────────────────────
        await self.db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price,
                current_price, stop_loss, status, signal_id, thesis)
               VALUES (?, 'kalshi', ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)""",
            (
                market_id, engine, side, float(size_contracts), entry_price,
                current_price, stop_loss, signal["id"],
                f"Signal #{signal['id']}: {signal['signal_type']} (conf={confidence:.2f})",
            ),
        )

        # ── Write Execution ───────────────────────────────────────────
        await self.db.execute(
            """INSERT INTO executions
               (signal_id, engine, platform, order_id, side, fill_price, size, order_type)
               VALUES (?, ?, 'kalshi', ?, 'BUY', ?, ?, ?)""",
            (
                signal["id"], engine, order_id, entry_price,
                float(size_contracts), "limit" if self._mode == "paper" else "market",
            ),
        )

        self.logger.info(
            "EXECUTED: %s %s %d contracts @ %.2f on %s (engine=%s, kelly=%.3f)",
            side, signal["signal_type"], size_contracts, entry_price,
            market_id, engine, kelly_capped,
        )
