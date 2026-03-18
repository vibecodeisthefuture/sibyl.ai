"""
Signal Generator — converts raw detection events into scored trading signals.

PURPOSE:
    The Market Intelligence Agent detects unusual activity (whales, volume spikes,
    orderbook changes) but doesn't make trading decisions.  The Signal Generator
    takes those raw detection events and produces SCORED SIGNALS that the Signal
    Router can then send to the appropriate trading engine (SGE or ACE).

DATA FLOW:
    MarketIntelligenceAgent._detection_queue
        → SignalGenerator.run_cycle() reads and clears the queue
        → Creates Signal records with confidence + EV scores
        → Writes to `signals` table with status = PENDING
        → SignalRouter reads PENDING signals (next step)

SIGNAL SCORING PROCESS:

    1. GROUP: Group detection events by market_id within a 15-minute window.

    2. COMPOSITE SCORING: If ≥2 different modes triggered on the same market,
       it's a high-conviction composite signal (e.g., WHALE + VOLUME_SURGE).
       Single-mode detections get a base confidence of 0.55.
       Multi-mode gets 0.70+ (boosted by cross-platform divergence data).

    3. SIGNAL TYPE CLASSIFICATION: Maps detection modes to SignalType enums:
       - WHALE alone → MOMENTUM
       - VOLUME_SURGE alone → VOLUME_SURGE
       - SPREAD_EXPANSION/LIQUIDITY_VACUUM → LIQUIDITY_VACUUM
       - WHALE + VOLUME → COMPOSITE_HIGH_CONVICTION
       - WALL_APPEARED → MEAN_REVERSION
       - Cross-platform divergence present → ARBITRAGE

    4. EV ESTIMATION:
       ev = (confidence × potential_upside) - ((1-confidence) × risk)
       Where potential_upside is derived from how far the current price
       is from 0.50 (maximum uncertainty).

    5. CROSS-PLATFORM ENRICHMENT: Checks `system_state` for active divergence
       alerts on the same market.  If found, boosts confidence by 0.10.

CONFIGURATION:
    Read from `market_intelligence_config.yaml` → composite section:
        window_minutes: 15
        high_conviction_modes_required: 2
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

if TYPE_CHECKING:
    from sibyl.agents.intelligence.market_intelligence import MarketIntelligenceAgent

logger = logging.getLogger("sibyl.agents.signal_generator")


class SignalGenerator(BaseAgent):
    """Consumes detection events and produces scored trading signals.

    This agent holds a reference to the MarketIntelligenceAgent so it can
    read and clear its detection queue each cycle.

    Usage:
        intel_agent = MarketIntelligenceAgent(db, config)
        sig_gen = SignalGenerator(db, config, intel_agent=intel_agent)
        intel_agent.schedule()
        sig_gen.schedule()
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: dict[str, Any],
        intel_agent: MarketIntelligenceAgent | None = None,
    ) -> None:
        """Initialize the Signal Generator.

        Args:
            db:          Shared DatabaseManager.
            config:      System config dict.
            intel_agent: Reference to MarketIntelligenceAgent (for reading detections).
                         Can be None for testing — use inject_detections() instead.
        """
        super().__init__(name="signal_generator", db=db, config=config)
        self._intel_agent = intel_agent
        self._composite_config: dict[str, Any] = {}

        # Injected detections for testing (bypasses intel_agent dependency)
        self._injected_detections: list[dict[str, Any]] = []

    @property
    def poll_interval(self) -> float:
        """Run every 5 seconds — process detections as fast as they appear."""
        return 5.0

    async def start(self) -> None:
        """Load composite scoring configuration."""
        from sibyl.core.config import load_yaml
        try:
            mi_config = load_yaml("market_intelligence_config.yaml")
            self._composite_config = mi_config.get("composite", {})
        except FileNotFoundError:
            self._composite_config = {}
        self.logger.info("Signal Generator started")

    async def run_cycle(self) -> None:
        """Read detection events, score them, and write signals to the database."""

        # Get detection events from MarketIntelligenceAgent (or injected for testing)
        if self._injected_detections:
            detections = list(self._injected_detections)
            self._injected_detections.clear()
        elif self._intel_agent:
            detections = self._intel_agent.get_and_clear_detections()
        else:
            return  # No source of detections

        if not detections:
            return  # Nothing to process this cycle

        self.logger.info("Processing %d detection events", len(detections))

        # Step 1: Group detections by market_id
        by_market: dict[str, list[dict]] = defaultdict(list)
        for det in detections:
            by_market[det["market_id"]].append(det)

        # Step 2: Score and create signals for each market
        signals_created = 0
        min_modes_for_composite = int(
            self._composite_config.get("high_conviction_modes_required", 2)
        )

        for market_id, market_detections in by_market.items():
            signal = await self._score_and_create_signal(
                market_id, market_detections, min_modes_for_composite
            )
            if signal:
                signals_created += 1

        if signals_created:
            self.logger.info("Created %d new signals from %d detections",
                             signals_created, len(detections))

    async def stop(self) -> None:
        self.logger.info("Signal Generator stopped")

    # ── Scoring Logic ─────────────────────────────────────────────────

    async def _score_and_create_signal(
        self,
        market_id: str,
        detections: list[dict],
        min_modes_for_composite: int,
    ) -> dict | None:
        """Score a group of detections on a single market and write a signal.

        Args:
            market_id:              Which market triggered.
            detections:             All detection events for this market.
            min_modes_for_composite: How many different modes needed for composite.

        Returns:
            The signal dict that was written to DB, or None if filtered out.
        """
        # Identify which unique modes triggered
        modes_triggered = list(set(det["mode"] for det in detections))
        is_composite = len(modes_triggered) >= min_modes_for_composite

        # ── Determine signal type ─────────────────────────────────────
        signal_type = self._classify_signal_type(modes_triggered, is_composite)

        # ── Compute base confidence ───────────────────────────────────
        if is_composite:
            # Multiple modes = high conviction
            confidence = 0.55 + (len(modes_triggered) * 0.08)
        else:
            # Single mode = moderate confidence
            confidence = 0.55

        # ── Cross-platform divergence boost ───────────────────────────
        # Check if there's an active arbitrage divergence alert for this market
        divergence_boost = await self._check_divergence_boost(market_id)
        if divergence_boost > 0:
            confidence += divergence_boost
            # If divergence exists and we have detections, consider it arbitrage
            if signal_type not in ("ARBITRAGE", "HIGH_CONVICTION_ARB"):
                signal_type = "ARBITRAGE"

        # Cap confidence at 0.95 (never 100% certain)
        confidence = min(confidence, 0.95)

        # ── EV estimation ─────────────────────────────────────────────
        ev_estimate = await self._estimate_ev(market_id, confidence)

        # ── Write signal to database ──────────────────────────────────
        modes_str = ",".join(modes_triggered)
        reasoning = self._build_reasoning(detections, modes_triggered, is_composite)

        await self.db.execute(
            """INSERT INTO signals
               (market_id, signal_type, confidence, ev_estimate, status,
                detection_modes_triggered, reasoning)
               VALUES (?, ?, ?, ?, 'PENDING', ?, ?)""",
            (market_id, signal_type, confidence, ev_estimate, modes_str, reasoning),
        )
        await self.db.commit()

        return {
            "market_id": market_id,
            "signal_type": signal_type,
            "confidence": confidence,
            "ev_estimate": ev_estimate,
            "modes": modes_triggered,
        }

    @staticmethod
    def _classify_signal_type(modes: list[str], is_composite: bool) -> str:
        """Map detection modes to a signal type string.

        This determines which trading engines can receive the signal
        (SGE and ACE have different signal whitelists).

        Args:
            modes:        List of triggered mode names.
            is_composite: True if ≥2 different modes triggered.

        Returns:
            SignalType string (matches the signal_whitelist in engine configs).
        """
        mode_set = set(modes)

        # Composite signals (multiple modes confirmed)
        if is_composite:
            return "COMPOSITE_HIGH_CONVICTION"

        # Single-mode classification
        if "WHALE" in mode_set:
            return "MOMENTUM"
        if "VOLUME_SURGE" in mode_set:
            return "VOLUME_SURGE"
        if "LIQUIDITY_VACUUM" in mode_set:
            return "LIQUIDITY_VACUUM"
        if "SPREAD_EXPANSION" in mode_set:
            return "LIQUIDITY_VACUUM"
        if "WALL_APPEARED" in mode_set:
            return "MEAN_REVERSION"
        if "WALL_DISAPPEARED" in mode_set:
            return "MOMENTUM"

        return "VOLUME_SURGE"  # Default fallback

    async def _check_divergence_boost(self, market_id: str) -> float:
        """Check for cross-platform price divergence alerts on this market.

        If the CrossPlatformSyncAgent has flagged a divergence involving
        this market_id, we boost the signal confidence by 0.10.

        Returns:
            0.10 if a divergence alert exists for this market, 0.0 otherwise.
        """
        try:
            row = await self.db.fetchone(
                """SELECT key FROM system_state
                   WHERE key LIKE ?
                     AND updated_at > datetime('now', '-30 minutes')""",
                (f"%{market_id}%",),
            )
            return 0.10 if row else 0.0
        except Exception:
            return 0.0

    async def _estimate_ev(self, market_id: str, confidence: float) -> float:
        """Estimate the expected value of trading this signal.

        Simple EV formula:
            current_price = most recent YES price
            If price < 0.50: potential_upside = 1.0 - price (buying YES)
            If price > 0.50: potential_upside = price     (buying NO idea)

            ev = (confidence × upside) - ((1-confidence) × downside)

        Returns:
            EV as a float (positive = profitable, negative = avoid).
        """
        try:
            row = await self.db.fetchone(
                """SELECT yes_price FROM prices
                   WHERE market_id = ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (market_id,),
            )
            if not row:
                return 0.0

            price = float(row["yes_price"])

            # Model the trade as buying at current price, selling at resolution
            # If price < 0.50, we'd buy YES (upside = 1.0 - price)
            # If price > 0.50, we'd buy NO  (upside = price)
            if price < 0.50:
                potential_profit = 1.0 - price
                potential_loss = price
            else:
                potential_profit = price
                potential_loss = 1.0 - price

            ev = (confidence * potential_profit) - ((1.0 - confidence) * potential_loss)
            return round(ev, 4)

        except Exception:
            return 0.0

    @staticmethod
    def _build_reasoning(
        detections: list[dict], modes: list[str], is_composite: bool
    ) -> str:
        """Build a human-readable reasoning string for the signal.

        This is stored in the `reasoning` column of the signals table
        for debugging and post-mortem analysis.
        """
        parts = []
        if is_composite:
            parts.append(f"COMPOSITE: {len(modes)} modes triggered ({', '.join(modes)})")
        else:
            parts.append(f"Single mode: {modes[0]}")

        # Add the most relevant detail from each detection
        for det in detections[:3]:  # Max 3 details to keep it concise
            mode = det["mode"]
            details = det.get("details", {})
            if mode == "WHALE":
                parts.append(f"Whale: {details.get('multiplier', 0):.1f}× avg size")
            elif mode == "VOLUME_SURGE":
                parts.append(f"Volume: z={details.get('zscore', 0):.2f}")
            elif mode == "SPREAD_EXPANSION":
                parts.append(f"Spread: {details.get('normalized_spread', 0):.3f}")
            elif mode == "LIQUIDITY_VACUUM":
                parts.append(f"Depth: ${details.get('total_depth', 0):.0f}")
            elif mode in ("WALL_APPEARED", "WALL_DISAPPEARED"):
                parts.append(f"Wall at {details.get('price', 0):.2f}")

        return " | ".join(parts)

    # ── Testing API ───────────────────────────────────────────────────

    def inject_detections(self, detections: list[dict[str, Any]]) -> None:
        """Inject detection events for testing (bypasses intel_agent).

        Usage in tests:
            sig_gen = SignalGenerator(db, config, intel_agent=None)
            sig_gen.inject_detections([{"market_id": "X", "mode": "WHALE", ...}])
            await sig_gen.run_cycle()
        """
        self._injected_detections.extend(detections)
