"""
Risk Dashboard — aggregate risk metrics, drawdown tracking, and daily P&L resets.

PURPOSE:
    The Risk Dashboard is Sibyl's "flight recorder."  It continuously computes
    portfolio-wide risk metrics and writes them to the system_state table for
    consumption by the web dashboard and other agents.

RESPONSIBILITIES:
    1. HIGH-WATER MARK:  Track the all-time peak portfolio value.
    2. DRAWDOWN:         Compute current drawdown from HWM and escalate levels.
    3. DAILY P&L:        Track per-engine daily P&L and reset at midnight UTC.
    4. WIN RATE:         Rolling 7-day win rate across all resolved positions.
    5. EXPOSURE:         Aggregate deployed capital and open position count.
    6. SNAPSHOT:         Write all metrics to system_state for the web dashboard.

DRAWDOWN LEVELS:
    - CLEAR:    Drawdown < 5%   → normal operations
    - WARNING:  Drawdown 5–10%  → informational alert
    - CAUTION:  Drawdown 10–20% → reduce new position sizing by 50%
    - CRITICAL: Drawdown > 20%  → halt all new positions

DAILY P&L RESET:
    At midnight UTC (configurable), the Risk Dashboard resets the daily_pnl
    column in engine_state for both SGE and ACE.  This gives each engine a
    fresh daily P&L counter every trading day.

CONFIGURATION:
    config/risk_dashboard_config.yaml

POLLING: Every 30 seconds (configurable).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.risk_dashboard")


class RiskDashboard(BaseAgent):
    """Computes and publishes portfolio-wide risk metrics."""

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="risk_dashboard", db=db, config=config)
        self._rdc: dict[str, Any] = {}  # risk_dashboard_config.yaml

        # Drawdown thresholds
        self._dd_warning: float = 0.05
        self._dd_caution: float = 0.10
        self._dd_critical: float = 0.20

        # Daily reset
        self._reset_hour_utc: int = 0
        self._last_reset_date: str = ""  # ISO date of last daily reset

        # High-water mark (loaded from system_state on start)
        self._hwm: float = 0.0

    @property
    def poll_interval(self) -> float:
        """Run every 30 seconds for near-real-time risk monitoring."""
        return float(self._rdc.get("risk_dashboard", {}).get(
            "poll_interval_seconds", 30
        ))

    async def start(self) -> None:
        """Load config and restore high-water mark from system_state."""
        from sibyl.core.config import load_yaml

        try:
            self._rdc = load_yaml("risk_dashboard_config.yaml")
        except FileNotFoundError:
            self._rdc = {}

        rd = self._rdc.get("risk_dashboard", {})

        # Drawdown thresholds
        dd = rd.get("drawdown", {})
        self._dd_warning = float(dd.get("warning_threshold_pct", 0.05))
        self._dd_caution = float(dd.get("caution_threshold_pct", 0.10))
        self._dd_critical = float(dd.get("critical_threshold_pct", 0.20))

        # Daily reset
        dr = rd.get("daily_reset", {})
        self._reset_hour_utc = int(dr.get("reset_hour_utc", 0))

        # Restore HWM from system_state (survives restarts)
        hwm_row = await self.db.fetchone(
            "SELECT value FROM system_state WHERE key = 'risk_hwm'"
        )
        if hwm_row:
            self._hwm = float(hwm_row["value"])
        self.logger.info(
            "Risk Dashboard started (HWM=$%.2f, drawdown thresholds: "
            "warn=%.0f%%, caution=%.0f%%, critical=%.0f%%)",
            self._hwm, self._dd_warning * 100, self._dd_caution * 100,
            self._dd_critical * 100,
        )

    async def run_cycle(self) -> None:
        """Compute all risk metrics and write snapshot to system_state."""
        # ── Check for daily P&L reset ────────────────────────────────────
        await self._check_daily_reset()

        # ── Fetch portfolio balance ──────────────────────────────────────
        balance_row = await self.db.fetchone(
            "SELECT value FROM system_state WHERE key = 'portfolio_total_balance'"
        )
        total_balance = float(balance_row["value"]) if balance_row else 0.0

        if total_balance <= 0:
            return

        # ── High-Water Mark update ───────────────────────────────────────
        if total_balance > self._hwm:
            self._hwm = total_balance
            await self._write_state("risk_hwm", str(round(self._hwm, 2)))

        # ── Drawdown computation ─────────────────────────────────────────
        drawdown_pct = 0.0
        if self._hwm > 0:
            drawdown_pct = (self._hwm - total_balance) / self._hwm

        drawdown_level = self._classify_drawdown(drawdown_pct)
        await self._write_state("risk_drawdown_pct", str(round(drawdown_pct, 4)))
        await self._write_state("risk_drawdown_level", drawdown_level)

        # Log drawdown escalations
        if drawdown_level != "CLEAR":
            self.logger.warning(
                "DRAWDOWN %s: %.1f%% from HWM ($%.2f → $%.2f)",
                drawdown_level, drawdown_pct * 100, self._hwm, total_balance,
            )

        # ── Exposure metrics ─────────────────────────────────────────────
        exposure_row = await self.db.fetchone(
            """SELECT
                 COALESCE(SUM(size * entry_price), 0) as total_deployed,
                 COUNT(*) as open_count
               FROM positions WHERE status = 'OPEN'"""
        )
        total_deployed = float(exposure_row["total_deployed"]) if exposure_row else 0.0
        open_count = int(exposure_row["open_count"]) if exposure_row else 0

        await self._write_state("risk_total_exposure", str(round(total_deployed, 2)))
        await self._write_state("risk_open_positions", str(open_count))

        # ── Per-engine daily P&L ─────────────────────────────────────────
        for engine in ("SGE", "ACE"):
            state = await self.db.fetchone(
                "SELECT daily_pnl FROM engine_state WHERE engine = ?", (engine,)
            )
            daily_pnl = float(state["daily_pnl"]) if state else 0.0
            await self._write_state(f"risk_daily_pnl_{engine.lower()}", str(round(daily_pnl, 2)))

        # ── 7-day rolling win rate ───────────────────────────────────────
        win_rate = await self._compute_win_rate_7d()
        await self._write_state("risk_win_rate_7d", str(round(win_rate, 4)))

        # ── 30-day Sharpe estimate ───────────────────────────────────────
        sharpe = await self._compute_sharpe_30d()
        await self._write_state("risk_sharpe_30d", str(round(sharpe, 4)))

        await self.db.commit()

    async def stop(self) -> None:
        """Persist final HWM on shutdown."""
        await self._write_state("risk_hwm", str(round(self._hwm, 2)))
        await self.db.commit()
        self.logger.info("Risk Dashboard stopped (HWM=$%.2f)", self._hwm)

    # ── Drawdown Classification ────────────────────────────────────────

    def _classify_drawdown(self, drawdown_pct: float) -> str:
        """Classify current drawdown into risk levels.

        Args:
            drawdown_pct: Current drawdown as a fraction (e.g., 0.12 = 12%).

        Returns:
            One of: "CLEAR", "WARNING", "CAUTION", "CRITICAL"
        """
        if drawdown_pct >= self._dd_critical:
            return "CRITICAL"
        if drawdown_pct >= self._dd_caution:
            return "CAUTION"
        if drawdown_pct >= self._dd_warning:
            return "WARNING"
        return "CLEAR"

    # ── Daily P&L Reset ────────────────────────────────────────────────

    async def _check_daily_reset(self) -> None:
        """Reset daily P&L counters at midnight UTC (or configured hour).

        Uses self._last_reset_date to ensure we only reset once per day,
        even if the agent runs multiple cycles within the reset hour.
        """
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        if now.hour == self._reset_hour_utc and today != self._last_reset_date:
            for engine in ("SGE", "ACE"):
                await self.db.execute(
                    "UPDATE engine_state SET daily_pnl = 0.0 WHERE engine = ?",
                    (engine,),
                )
            await self.db.commit()
            self._last_reset_date = today
            self.logger.info("Daily P&L reset completed for all engines")

    # ── Rolling Win Rate (7 days) ──────────────────────────────────────

    async def _compute_win_rate_7d(self) -> float:
        """Compute 7-day rolling win rate from performance records.

        Win rate = (correct predictions) / (total resolved predictions).
        Returns 0.0 if no resolved positions in the last 7 days.
        """
        row = await self.db.fetchone(
            """SELECT
                 COUNT(*) as total,
                 COALESCE(SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END), 0) as wins
               FROM performance
               WHERE resolved = 1
                 AND resolved_at >= datetime('now', '-7 days')"""
        )
        total = int(row["total"]) if row else 0
        wins = int(row["wins"]) if row else 0
        return (wins / total) if total > 0 else 0.0

    # ── Sharpe Ratio Estimate (30 days) ────────────────────────────────

    async def _compute_sharpe_30d(self) -> float:
        """Estimate 30-day rolling Sharpe ratio from closed position P&L.

        Sharpe = mean(returns) / std(returns).
        Uses daily aggregated P&L from closed positions as "returns."
        Returns 0.0 if fewer than 2 data points.

        NOTE: This is a simplified estimate.  True Sharpe needs risk-free
        rate subtraction and proper annualization.  For prediction markets,
        the risk-free rate is effectively 0 (no interest earned on idle capital).
        """
        rows = await self.db.fetchall(
            """SELECT
                 date(closed_at) as close_date,
                 SUM(pnl) as daily_pnl
               FROM positions
               WHERE status IN ('CLOSED', 'STOPPED')
                 AND closed_at >= datetime('now', '-30 days')
               GROUP BY date(closed_at)
               ORDER BY close_date"""
        )

        if len(rows) < 2:
            return 0.0

        pnls = [float(r["daily_pnl"]) for r in rows]
        mean_pnl = sum(pnls) / len(pnls)

        # Standard deviation
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std_pnl = math.sqrt(variance) if variance > 0 else 0.0

        if std_pnl == 0:
            return 0.0

        return mean_pnl / std_pnl

    # ── Helpers ────────────────────────────────────────────────────────

    async def _write_state(self, key: str, value: str) -> None:
        """Write a key-value pair to system_state."""
        await self.db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES (?, ?, datetime('now'))""",
            (key, value),
        )
