"""
Portfolio Allocator — capital allocation, rebalancing, and balance synchronization.

PURPOSE:
    The Allocator is Sibyl's "treasurer."  It controls how much capital each
    trading engine (SGE / ACE) is allowed to deploy, syncs with the real Kalshi
    account balance, and rebalances when drift exceeds thresholds.

RESPONSIBILITIES:
    1. BALANCE SYNC:   Pull actual Kalshi balance (live) or use paper balance.
    2. RESERVE:        Hold a configurable % of total capital as cash reserve.
    3. CAPITAL SPLITS: Distribute allocable capital between SGE (70%) and ACE (30%).
    4. REBALANCE:      When an engine's actual allocation drifts from its target
                       by more than the threshold, redistribute capital.
    5. BOOKKEEPING:    Write updated totals to `engine_state` table so all other
                       agents can see how much capital they have.

FLOW (each cycle):
    1. Fetch real balance from Kalshi (live mode) or use tracked balance (paper mode).
    2. Subtract cash_reserve_pct → allocable_capital.
    3. Compute target capital per engine: allocable × engine_split.
    4. Compare targets to current engine_state.total_capital.
    5. If drift > threshold → rebalance (subject to cooldown + max_rebalance_pct).
    6. Write updated engine_state rows.

WHY THE ALLOCATOR IS SEPARATE FROM EngineStateManager:
    EngineStateManager reads POSITION DATA and computes deployed/available.
    The Allocator reads BALANCE DATA and sets the total_capital ceiling.
    Separation of concerns: Allocator decides the budget, EngineStateManager
    tracks how that budget is being spent.

CONFIGURATION:
    config/portfolio_allocator_config.yaml

POLLING: Every 60 seconds (configurable).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.allocator")


class PortfolioAllocator(BaseAgent):
    """Manages capital allocation between SGE and ACE engines.

    This agent is the single source of truth for how much capital each
    engine is allowed to use.  All other agents read engine_state.total_capital
    to know their budget — this agent is the only one that WRITES that value.
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: dict[str, Any],
        mode: str = "paper",
    ) -> None:
        """Initialize the Portfolio Allocator.

        Args:
            db:     Shared DatabaseManager.
            config: System config dict (system_config.yaml).
            mode:   "paper" (simulated balance) or "live" (real Kalshi balance).
        """
        super().__init__(name="portfolio_allocator", db=db, config=config)
        self._mode = mode

        # Allocator-specific config (loaded in start())
        self._alloc_config: dict[str, Any] = {}

        # Engine capital splits (default: SGE=70%, ACE=30%)
        self._splits: dict[str, float] = {"SGE": 0.70, "ACE": 0.30}

        # Blitz partition (Sprint 14): sub-engine of SGE
        self._blitz_enabled: bool = False
        self._blitz_pct_of_sge: float = 0.20

        # Cash reserve percentage (default: 5%)
        self._cash_reserve_pct: float = 0.05

        # Rebalance state
        self._last_rebalance_ts: float = 0.0
        self._drift_threshold: float = 0.05
        self._cooldown_seconds: float = 300.0
        self._max_rebalance_pct: float = 0.10

        # Balance sync state
        self._paper_balance: float = 500.0
        self._last_balance_sync_ts: float = 0.0
        self._balance_sync_interval: float = 120.0
        self._discrepancy_alert_pct: float = 0.02

        # Kalshi client (initialized in start() for live mode)
        self._kalshi_client = None

    @property
    def poll_interval(self) -> float:
        """Run every 60 seconds — balance changes are not high-frequency."""
        return float(self._alloc_config.get("allocator", {}).get(
            "poll_interval_seconds", 60
        ))

    async def start(self) -> None:
        """Load allocator config and initialize Kalshi client if live mode."""
        from sibyl.core.config import load_yaml

        # ── Load allocator config ────────────────────────────────────────
        try:
            self._alloc_config = load_yaml("portfolio_allocator_config.yaml")
        except FileNotFoundError:
            self.logger.warning("portfolio_allocator_config.yaml not found — using defaults")
            self._alloc_config = {}

        alloc = self._alloc_config.get("allocator", {})

        # Engine splits
        splits_raw = alloc.get("engine_splits", {"SGE": 0.70, "ACE": 0.30})
        self._splits = {k: float(v) for k, v in splits_raw.items()}

        # Cash reserve
        self._cash_reserve_pct = float(alloc.get("cash_reserve_pct", 0.05))

        # Rebalance settings
        rebal = alloc.get("rebalance", {})
        self._drift_threshold = float(rebal.get("drift_threshold_pct", 0.05))
        self._cooldown_seconds = float(rebal.get("cooldown_seconds", 300))
        self._max_rebalance_pct = float(rebal.get("max_rebalance_pct", 0.10))

        # Balance sync settings
        bsync = alloc.get("balance_sync", {})
        self._paper_balance = float(bsync.get("paper_starting_balance_usd", 500.0))
        self._balance_sync_interval = float(bsync.get("sync_interval_seconds", 120))
        self._discrepancy_alert_pct = float(bsync.get("discrepancy_alert_pct", 0.02))

        # ── Sprint 23D: Use shared Kalshi client ─────────────────────────
        # Always initialize the Kalshi client (paper + live) so startup sync works.
        from sibyl.clients.kalshi_client import get_shared_kalshi_client
        shared = get_shared_kalshi_client(self.config)
        if shared.is_authenticated:
            self._kalshi_client = shared
            self.logger.info(
                "Portfolio Allocator using shared Kalshi client (mode=%s)", self._mode
            )
        else:
            if self._mode == "live":
                self.logger.warning(
                    "Live mode but Kalshi credentials not set — "
                    "falling back to paper balance"
                )

        # ── Sprint 32: Startup balance sync from authoritative Kalshi source ──
        # Always query the real Kalshi account at startup so system_state
        # reflects the actual account state, not stale cached values.
        # This also allows the risk_dashboard to set a correct HWM at startup.
        await self._startup_sync_kalshi()

        # ── Load Blitz partition config (Sprint 14) ──────────────────────
        try:
            from sibyl.core.config import load_yaml as load_yaml_2
            sge_cfg = load_yaml_2("sge_config.yaml")
            blitz = sge_cfg.get("blitz", {})
            self._blitz_enabled = blitz.get("enabled", False)
            self._blitz_pct_of_sge = float(blitz.get("capital_pct_of_sge", 0.20))
        except (FileNotFoundError, Exception):
            self._blitz_enabled = False

        # ── Seed initial allocation ──────────────────────────────────────
        await self._run_allocation_cycle()

        blitz_info = ""
        if self._blitz_enabled:
            blitz_info = f", Blitz={self._blitz_pct_of_sge * 100:.0f}% of SGE"
        self.logger.info(
            "Portfolio Allocator started (mode=%s, SGE=%.0f%%, ACE=%.0f%%, reserve=%.0f%%%s)",
            self._mode,
            self._splits.get("SGE", 0.70) * 100,
            self._splits.get("ACE", 0.30) * 100,
            self._cash_reserve_pct * 100,
            blitz_info,
        )

    async def run_cycle(self) -> None:
        """Run one allocation cycle: sync balance → compute splits → update engine_state."""
        await self._run_allocation_cycle()

    async def stop(self) -> None:
        """Graceful shutdown — close Kalshi client if open."""
        if self._kalshi_client:
            await self._kalshi_client.close()
        self.logger.info("Portfolio Allocator stopped")

    # ── Core Allocation Logic ──────────────────────────────────────────

    async def _run_allocation_cycle(self) -> None:
        """Full allocation cycle: balance → reserve → splits → rebalance → write."""

        # ── Step 1: Get total portfolio balance ──────────────────────────
        total_balance = await self._get_total_balance()
        if total_balance <= 0:
            self.logger.debug("Total balance is $0 — nothing to allocate")
            return

        # ── Step 2: Subtract cash reserve ────────────────────────────────
        reserve = total_balance * self._cash_reserve_pct
        allocable = total_balance - reserve

        # Sprint 25: In live mode, cap allocable by actual Kalshi cash.
        # total_balance includes position exposure, but we can only deploy
        # what's actually available as cash on Kalshi.
        if self._mode == "live":
            cash_row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = 'portfolio_cash_available'"
            )
            if cash_row:
                actual_cash = float(cash_row["value"])
                cash_allocable = max(actual_cash - reserve, 0.0)
                if cash_allocable < allocable:
                    self.logger.debug(
                        "Cash cap: allocable $%.2f -> $%.2f (cash=$%.2f, exposure locked)",
                        allocable, cash_allocable, actual_cash,
                    )
                    allocable = cash_allocable

        # ── Step 3: Compute target allocation per engine ─────────────────
        targets: dict[str, float] = {}
        for engine, split in self._splits.items():
            targets[engine] = allocable * split

        # ── Step 4: Check drift and rebalance if needed ──────────────────
        now = time.monotonic()
        cooldown_elapsed = (now - self._last_rebalance_ts) >= self._cooldown_seconds

        for engine, target in targets.items():
            current = await self._get_engine_total(engine)

            if current <= 0:
                # First allocation — set directly to target
                await self._set_engine_total(engine, target)
                self.logger.info(
                    "INITIAL ALLOCATION: %s -> $%.2f (%.0f%% of $%.2f)",
                    engine, target, self._splits[engine] * 100, allocable,
                )
                self._last_rebalance_ts = now
                continue

            # Compute drift: how far actual is from target, as % of allocable
            drift = abs(current - target) / allocable if allocable > 0 else 0

            if drift > self._drift_threshold and cooldown_elapsed:
                # Rebalance — but cap the movement
                delta = target - current
                max_move = allocable * self._max_rebalance_pct
                capped_delta = max(min(delta, max_move), -max_move)
                new_total = current + capped_delta

                await self._set_engine_total(engine, new_total)
                self._last_rebalance_ts = now

                self.logger.info(
                    "REBALANCE: %s $%.2f -> $%.2f (drift=%.1f%%, delta=$%.2f)",
                    engine, current, new_total, drift * 100, capped_delta,
                )
            elif drift <= self._drift_threshold:
                # No rebalance needed — but still update target in case
                # balance changed (deposits, realized P&L)
                await self._set_engine_total(engine, target)

        # ── Step 4b: Blitz sub-engine allocation (Sprint 14) ──────────────
        # Blitz gets a fixed percentage of SGE's ORIGINAL TARGET capital.
        # Sprint 19 fix: Compute Blitz from the target (not the just-set value)
        # to prevent repeated carve-out that erodes SGE capital each cycle.
        # SGE target is split ONCE: 80% stays in SGE, 20% goes to Blitz.
        if self._blitz_enabled:
            sge_target_full = targets.get("SGE", 0.0)
            blitz_target = sge_target_full * self._blitz_pct_of_sge
            sge_standard = sge_target_full - blitz_target

            # Ensure SGE_BLITZ engine_state row exists
            existing = await self.db.fetchone(
                "SELECT engine FROM engine_state WHERE engine = 'SGE_BLITZ'"
            )
            if not existing:
                await self.db.execute(
                    "INSERT INTO engine_state (engine, circuit_breaker) "
                    "VALUES ('SGE_BLITZ', 'CLEAR')"
                )

            await self._set_engine_total("SGE_BLITZ", blitz_target)
            await self._set_engine_total("SGE", sge_standard)

            self.logger.debug(
                "Blitz allocation: $%.2f (%.0f%% of SGE target $%.2f), SGE standard: $%.2f",
                blitz_target, self._blitz_pct_of_sge * 100, sge_target_full, sge_standard,
            )

        # ── Step 5: Write portfolio-level state ──────────────────────────
        await self.db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES ('portfolio_total_balance', ?, datetime('now'))""",
            (str(round(total_balance, 2)),),
        )
        await self.db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES ('portfolio_cash_reserve', ?, datetime('now'))""",
            (str(round(reserve, 2)),),
        )
        await self.db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES ('portfolio_allocable', ?, datetime('now'))""",
            (str(round(allocable, 2)),),
        )

        # ── Step 6: Track per-category exposure (Sprint 11) ────────────
        # Write category exposure data to system_state for policy enforcement
        # and dashboard consumption.
        await self._track_category_exposure()

        await self.db.commit()

    # ── Category Exposure Tracking (Sprint 11) ─────────────────────────

    async def _track_category_exposure(self) -> None:
        """Compute and persist per-category exposure for policy enforcement.

        Writes a JSON-encoded dict to system_state under key
        'category_exposure' with per-engine, per-category deployed capital.
        This is consumed by the PolicyEngine's capital cap checks.
        """
        import json

        rows = await self.db.fetchall(
            """SELECT p.engine, m.category, SUM(p.size * p.entry_price) as deployed
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status = 'OPEN'
               GROUP BY p.engine, m.category"""
        )

        exposure = {}
        for row in rows:
            engine = row["engine"]
            category = row["category"] or "Unknown"
            deployed = float(row["deployed"])
            if engine not in exposure:
                exposure[engine] = {}
            exposure[engine][category] = round(deployed, 2)

        await self.db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES ('category_exposure', ?, datetime('now'))""",
            (json.dumps(exposure),),
        )

    # ── Balance Sync ───────────────────────────────────────────────────

    async def _startup_sync_kalshi(self) -> None:
        """Sprint 32: Query Kalshi API at startup and write authoritative balance.

        Runs unconditionally (paper + live) so system_state always reflects the
        real account state before the first allocation cycle and before the risk
        dashboard loads its HWM.  This prevents the stale-HWM DRAWDOWN HALT that
        occurred when cached values from a previous session were orders of magnitude
        higher than the real account (inflated paper balance era).

        On failure the existing system_state values are left in place and a warning
        is logged — the system can still start with the cached values.
        """
        if not (self._kalshi_client and self._kalshi_client.is_authenticated):
            self.logger.warning(
                "Startup Kalshi sync skipped — no authenticated client"
            )
            return

        try:
            pv = await self._kalshi_client.get_portfolio_value()
            if pv is None:
                self.logger.warning(
                    "Startup Kalshi sync returned None — keeping cached system_state"
                )
                return

            cash = round(pv["cash"], 2)
            exposure = round(pv["position_exposure"], 2)
            total = round(pv["total_value"], 2)

            for key, val in [
                ("portfolio_cash_available", str(cash)),
                ("portfolio_position_exposure", str(exposure)),
                ("portfolio_total_balance", str(total)),
            ]:
                await self.db.execute(
                    "INSERT INTO system_state (key, value, updated_at) "
                    "VALUES (?, ?, datetime('now')) "
                    "ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')",
                    (key, val, val),
                )
            await self.db.commit()

            # ── Reconcile open positions against Kalshi ───────────────────
            # In paper mode, any position the DB thinks is OPEN must be
            # reconciled against real Kalshi exposure.  If Kalshi shows $0
            # exposure (all positions resolved), close all DB-open positions
            # so the correlation tracker and dedup logic start clean.
            if exposure == 0.0:
                result = await self.db.execute(
                    "UPDATE positions SET status = 'CLOSED', "
                    "closed_at = datetime('now') WHERE status = 'OPEN'"
                )
                closed_count = result.rowcount if result else 0
                if closed_count:
                    await self.db.commit()
                    self.logger.info(
                        "Startup position reconcile: closed %d stale DB-open "
                        "positions (Kalshi exposure=$0)",
                        closed_count,
                    )

            self.logger.info(
                "Startup Kalshi sync: cash=$%.2f, exposure=$%.2f, total=$%.2f",
                cash, exposure, total,
            )
        except Exception:
            self.logger.exception(
                "Startup Kalshi sync failed — keeping cached system_state"
            )

    async def _get_total_balance(self) -> float:
        """Get the total portfolio balance.

        PAPER MODE: Starts at paper_starting_balance_usd, then tracks P&L.
            total = paper_starting_balance + sum(all realized P&L)

        LIVE MODE:  Calls KalshiClient.get_balance() (rate-limited).
            Falls back to paper mode if API call fails.
        """
        if self._mode == "live" and self._kalshi_client:
            return await self._get_live_balance()
        return await self._get_paper_balance()

    async def _get_paper_balance(self) -> float:
        """Compute paper balance, anchored to real Kalshi balance when available.

        Sprint 31 (B-NEW-3): Old formula summed ALL historical closed-position
        P&L across sessions, inflating balance ~12x. Now:
          1. Seed from cached Kalshi balance if available (Option C from audit).
          2. Hard-cap at 150% of real Kalshi cash to prevent cross-session drift.
          3. Fall back to config paper_starting_balance only if no Kalshi data.
        """
        # Try to anchor to actual Kalshi balance
        kalshi_row = await self.db.fetchone(
            "SELECT value FROM system_state WHERE key = 'portfolio_cash_available'"
        )
        if kalshi_row:
            kalshi_cash = float(kalshi_row["value"])
            # Use Kalshi cash as the base, plus exposure
            exposure_row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = 'portfolio_position_exposure'"
            )
            exposure = float(exposure_row["value"]) if exposure_row else 0.0
            real_total = kalshi_cash + exposure
            # Hard cap: never exceed 150% of real total (auditor rec 3.3)
            return min(self._paper_balance, real_total * 1.5) if real_total > 0 else self._paper_balance

        # No Kalshi data — use config starting balance (no cross-session accumulation)
        return self._paper_balance

    async def _get_live_balance(self) -> float:
        """Fetch real Kalshi portfolio value (cash + position exposure).

        Sprint 25: Uses get_portfolio_value() to track both cash and exposure.
        Writes three keys to system_state:
            portfolio_cash_available   — actual Kalshi cash (for balance gate)
            portfolio_position_exposure — capital locked in open positions
            portfolio_total_balance     — cash + exposure (for HWM / drawdown)

        The ALLOCATOR uses cash_available to cap engine budgets.
        The RISK DASHBOARD uses total_balance for drawdown tracking.
        The EXECUTOR reads cash_available for pre-execution balance gate.
        """
        now = time.monotonic()

        # Rate-limit API calls
        if (now - self._last_balance_sync_ts) < self._balance_sync_interval:
            row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = 'portfolio_total_balance'"
            )
            if row:
                return float(row["value"])
            # Sprint 30-B: No cached value yet — return safe starting balance
            # rather than _get_paper_balance() which accumulates cross-session P&L.
            return self._paper_balance

        # Sync with Kalshi — full portfolio value
        try:
            pv = await self._kalshi_client.get_portfolio_value()
            self._last_balance_sync_ts = now

            if pv is None:
                self.logger.warning("Kalshi portfolio sync returned None — using tracked")
                return await self._get_paper_balance()

            cash = pv["cash"]
            exposure = pv["position_exposure"]
            total = pv["total_value"]

            # Write all three values to system_state
            for key, val in [
                ("portfolio_cash_available", cash),
                ("portfolio_position_exposure", exposure),
                ("portfolio_total_balance", total),
            ]:
                await self.db.execute(
                    "INSERT INTO system_state (key, value, updated_at) "
                    "VALUES (?, ?, datetime('now')) "
                    "ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')",
                    (key, str(round(val, 2)), str(round(val, 2))),
                )
            await self.db.commit()

            self.logger.debug(
                "Portfolio sync: cash=$%.2f, exposure=$%.2f, total=$%.2f",
                cash, exposure, total,
            )
            return total

        except Exception:
            self.logger.exception("Failed to sync Kalshi portfolio — using tracked")
            # Sprint 30-B: Prefer cached system_state over _get_paper_balance()
            # to avoid returning inflated cross-session P&L accumulation.
            cached = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = 'portfolio_total_balance'"
            )
            if cached:
                return float(cached["value"])
            return self._paper_balance

    # ── Engine State Helpers ───────────────────────────────────────────

    async def _get_engine_total(self, engine: str) -> float:
        """Read an engine's current total_capital from engine_state."""
        row = await self.db.fetchone(
            "SELECT total_capital FROM engine_state WHERE engine = ?", (engine,)
        )
        return float(row["total_capital"]) if row else 0.0

    async def _set_engine_total(self, engine: str, total: float) -> None:
        """Write an engine's total_capital to engine_state.

        Also recalculates available_capital based on deployed positions.
        """
        # Get current deployed capital for this engine
        row = await self.db.fetchone(
            """SELECT COALESCE(SUM(size * entry_price), 0) as deployed
               FROM positions WHERE engine = ? AND status = 'OPEN'""",
            (engine,),
        )
        deployed = float(row["deployed"]) if row else 0.0
        available = max(total - deployed, 0.0)

        await self.db.execute(
            """UPDATE engine_state SET
                 total_capital = ?,
                 available_capital = ?,
                 updated_at = datetime('now')
               WHERE engine = ?""",
            (round(total, 2), round(available, 2), engine),
        )
