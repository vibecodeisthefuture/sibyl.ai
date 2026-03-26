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
import os
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

        # ── Initialize Kalshi client for live balance sync ───────────────
        if self._mode == "live":
            from sibyl.clients.kalshi_client import KalshiClient
            key_id = os.environ.get("KALSHI_KEY_ID")
            key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
            if key_id and key_path:
                tier = self.config.get("platforms", {}).get("kalshi", {}).get(
                    "tier", "basic"
                )
                self._kalshi_client = KalshiClient(
                    key_id=key_id,
                    private_key_path=key_path,
                    tier=tier,
                )
                self.logger.info("Kalshi client initialized for live balance sync (tier=%s)", tier)
            else:
                self.logger.warning(
                    "Live mode but KALSHI_KEY_ID / KALSHI_PRIVATE_KEY_PATH not set — "
                    "falling back to paper balance"
                )

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
                    "INITIAL ALLOCATION: %s → $%.2f (%.0f%% of $%.2f)",
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
                    "REBALANCE: %s $%.2f → $%.2f (drift=%.1f%%, delta=$%.2f)",
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
        """Compute paper balance: starting balance + realized P&L.

        This is a simple model: you start with $X and all closed positions
        add/subtract from that balance.
        """
        # Sum all realized P&L from closed/stopped positions
        row = await self.db.fetchone(
            """SELECT COALESCE(SUM(pnl), 0) as total_pnl
               FROM positions WHERE status IN ('CLOSED', 'STOPPED')"""
        )
        realized_pnl = float(row["total_pnl"]) if row else 0.0
        return self._paper_balance + realized_pnl

    async def _get_live_balance(self) -> float:
        """Fetch real Kalshi balance, with rate-limiting and discrepancy checks.

        Returns the cached tracked balance if the sync interval hasn't elapsed.
        On successful sync, checks for discrepancies between Kalshi's balance
        and Sibyl's tracked total (could indicate external deposit/withdrawal).
        """
        now = time.monotonic()

        # Rate-limit balance API calls
        if (now - self._last_balance_sync_ts) < self._balance_sync_interval:
            # Use tracked balance from system_state
            row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = 'portfolio_total_balance'"
            )
            if row:
                return float(row["value"])
            # Fallback to paper balance if no tracked balance yet
            return await self._get_paper_balance()

        # Sync with Kalshi
        try:
            kalshi_balance = await self._kalshi_client.get_balance()
            self._last_balance_sync_ts = now

            if kalshi_balance is None:
                self.logger.warning("Kalshi balance sync returned None — using tracked")
                return await self._get_paper_balance()

            # Check for discrepancy with tracked balance
            tracked_row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = 'portfolio_total_balance'"
            )
            if tracked_row:
                tracked = float(tracked_row["value"])
                if tracked > 0:
                    discrepancy = abs(kalshi_balance - tracked) / tracked
                    if discrepancy > self._discrepancy_alert_pct:
                        self.logger.warning(
                            "BALANCE DISCREPANCY: Kalshi=$%.2f, Tracked=$%.2f (%.1f%% diff) "
                            "— possible external deposit/withdrawal",
                            kalshi_balance, tracked, discrepancy * 100,
                        )

            self.logger.debug("Kalshi balance synced: $%.2f", kalshi_balance)
            return kalshi_balance

        except Exception:
            self.logger.exception("Failed to sync Kalshi balance — using tracked")
            return await self._get_paper_balance()

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
