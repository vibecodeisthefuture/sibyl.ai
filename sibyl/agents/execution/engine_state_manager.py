"""
Engine State Manager — tracks capital allocation, exposure, and circuit breakers.

PURPOSE:
    Maintains the `engine_state` table with live capital data for SGE and ACE.
    Other agents (Order Executor, Position Lifecycle Manager) read this state
    to make risk-aware decisions.

WHAT IT TRACKS:
    - total_capital:     Total assigned to each engine (SGE=70%, ACE=30%)
    - deployed_capital:  Sum of open position values
    - available_capital: total - deployed (how much can be used for new trades)
    - unrealized_pnl:    Mark-to-market P&L across all open positions
    - circuit_breaker:   CLEAR / WARNING / TRIGGERED
    - daily_pnl:         Running daily P&L (resets at midnight UTC)

CIRCUIT BREAKER:
    If an engine's drawdown exceeds the threshold (SGE=-10%, ACE=-18%),
    the circuit breaker is TRIGGERED and no new orders can be placed.
    The Position Lifecycle Manager's Stop Guard handles the triggering;
    this agent just reads and surfaces the current state.

POLLING: Every 15 seconds.
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.engine_state_manager")


class EngineStateManager(BaseAgent):
    """Keeps engine_state table current with capital and risk metrics."""

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="engine_state_manager", db=db, config=config)
        self._sge_allocation: float = 0.70
        self._ace_allocation: float = 0.30

    @property
    def poll_interval(self) -> float:
        """Run every 15 seconds to keep capital state fresh."""
        return float(self.config.get("polling", {}).get(
            "position_sync_interval_seconds", 15
        ))

    async def start(self) -> None:
        """Load engine allocations and initialize engine_state rows."""
        from sibyl.core.config import load_yaml
        try:
            sge = load_yaml("sge_config.yaml")
            self._sge_allocation = float(sge.get("engine", {}).get("capital_allocation_pct", 0.70))
        except FileNotFoundError:
            pass
        try:
            ace = load_yaml("ace_config.yaml")
            self._ace_allocation = float(ace.get("engine", {}).get("capital_allocation_pct", 0.30))
        except FileNotFoundError:
            pass

        # Ensure engine_state rows exist for both engines
        for engine in ("SGE", "ACE"):
            existing = await self.db.fetchone(
                "SELECT engine FROM engine_state WHERE engine = ?", (engine,)
            )
            if not existing:
                await self.db.execute(
                    "INSERT INTO engine_state (engine) VALUES (?)",
                    (engine,),
                )
        await self.db.commit()
        self.logger.info(
            "Engine State Manager started (SGE=%.0f%%, ACE=%.0f%%)",
            self._sge_allocation * 100, self._ace_allocation * 100,
        )

    async def run_cycle(self) -> None:
        """Recompute capital metrics for both engines from live position data."""
        for engine in ("SGE", "ACE"):
            await self._update_engine(engine)
        await self.db.commit()

    async def stop(self) -> None:
        self.logger.info("Engine State Manager stopped")

    async def _update_engine(self, engine: str) -> None:
        """Recompute deployed capital, unrealized PnL, and available capital."""
        # Sum up all open positions for this engine
        row = await self.db.fetchone(
            """SELECT
                 COALESCE(SUM(size * entry_price), 0) as deployed,
                 COALESCE(SUM(CASE
                   WHEN side = 'YES' THEN (current_price - entry_price) * size
                   WHEN side = 'NO'  THEN (entry_price - current_price) * size
                   ELSE 0
                 END), 0) as unrealized_pnl,
                 COUNT(*) as position_count
               FROM positions
               WHERE engine = ? AND status = 'OPEN'""",
            (engine,),
        )

        deployed = float(row["deployed"]) if row else 0.0
        unrealized = float(row["unrealized_pnl"]) if row else 0.0

        # Get current total capital from engine_state
        state = await self.db.fetchone(
            "SELECT total_capital FROM engine_state WHERE engine = ?", (engine,)
        )
        total = float(state["total_capital"]) if state else 0.0
        available = total - deployed
        exposure_pct = (deployed / total) if total > 0 else 0.0

        await self.db.execute(
            """UPDATE engine_state SET
                 deployed_capital = ?,
                 available_capital = ?,
                 exposure_pct = ?,
                 daily_pnl = ?,
                 updated_at = datetime('now')
               WHERE engine = ?""",
            (deployed, available, exposure_pct, unrealized, engine),
        )
