"""
Signal Router — dispatches scored signals to the appropriate trading engine.

PURPOSE:
    The Signal Generator creates PENDING signals in the database.  The Signal
    Router reads those signals and decides WHERE to send each one:
      - SGE (Stable Growth Engine) — conservative, patient, diversified
      - ACE (Alpha Capture Engine) — aggressive, concentrated, speed-focused
      - BOTH — high-conviction signals that both engines should act on
      - DEFERRED — signals that don't meet minimum thresholds

HOW ROUTING WORKS:

    1. READ: Fetch all signals with status = 'PENDING' from the database.

    2. FILTER: Check if the signal meets minimum confidence and EV thresholds.
       SGE requires: confidence ≥ 0.60, EV ≥ +3%
       ACE requires: confidence ≥ 0.68, EV ≥ +6%
       Signals below BOTH thresholds → DEFERRED.

    3. MATCH WHITELIST: Each engine has a `signal_whitelist` in its config:
       SGE accepts: ARBITRAGE, MEAN_REVERSION, LIQUIDITY_VACUUM
       ACE accepts: MOMENTUM, VOLUME_SURGE, STALE_MARKET, HIGH_CONVICTION_ARB
       If a signal type appears in BOTH whitelists → routed to BOTH.

    4. COMPOSITE HANDLING: COMPOSITE_HIGH_CONVICTION signals that exceed BOTH
       engines' thresholds are always routed to BOTH.

    5. UPDATE: Set `signals.routed_to` and `signals.status` in the database.
       PENDING → ROUTED (if matched) or DEFERRED (if filtered out).

CONFIGURATION:
    Routing rules are derived from:
      - config/sge_config.yaml → signal_whitelist + risk_policy
      - config/ace_config.yaml → signal_whitelist + risk_policy

DATA FLOW:
    signals table (status=PENDING)
        → SignalRouter.run_cycle() reads them
        → Applies thresholds + whitelist matching
        → Updates status to ROUTED or DEFERRED
        → (Sprint 3+) Position Lifecycle Manager picks up ROUTED signals
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.signal_router")


class SignalRouter(BaseAgent):
    """Routes PENDING signals to SGE, ACE, BOTH, or DEFERRED.

    This is the last agent in the Intelligence Layer pipeline.
    After routing, signals wait in the database for the Position Lifecycle
    Manager (Sprint 3) to pick them up and execute trades.

    Category-Aware Routing (Sprint 9):
        The router now loads per-category strategies from
        config/category_strategies.yaml via CategoryStrategyManager.
        When routing a signal, it:
        1. Looks up the market's category from the DB
        2. Applies category-specific confidence/EV modifiers
        3. Uses the category's preferred engine as a tiebreaker
        4. Stores adjusted confidence in signals.confidence_adjusted

    Usage:
        router = SignalRouter(db=db, config=system_config)
        router.schedule()  # Runs in background
    """

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        """Initialize with database and system config.

        Args:
            db:     Shared DatabaseManager.
            config: System config dict (from system_config.yaml).
        """
        super().__init__(name="signal_router", db=db, config=config)

        # Engine configurations — loaded in start()
        self._sge_config: dict[str, Any] = {}
        self._ace_config: dict[str, Any] = {}

        # Whitelists — which signal types each engine accepts
        self._sge_whitelist: set[str] = set()
        self._ace_whitelist: set[str] = set()

        # Threshold values (from engine risk policies)
        self._sge_min_confidence: float = 0.60
        self._sge_min_ev: float = 0.03
        self._ace_min_confidence: float = 0.68
        self._ace_min_ev: float = 0.06

        # Category strategy manager — loaded in start()
        self._category_mgr = None

        # Policy engine — loaded in start() (Sprint 11)
        self._policy = None

    @property
    def poll_interval(self) -> float:
        """Run every 3 seconds — fastest agent in the pipeline for minimal routing latency."""
        return 3.0

    async def start(self) -> None:
        """Load SGE/ACE engine configs, category strategies, and policy engine."""
        from sibyl.core.config import load_yaml
        from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager
        from sibyl.core.policy import PolicyEngine

        try:
            self._sge_config = load_yaml("sge_config.yaml")
        except FileNotFoundError:
            self._sge_config = {}

        try:
            self._ace_config = load_yaml("ace_config.yaml")
        except FileNotFoundError:
            self._ace_config = {}

        # Extract signal whitelists
        self._sge_whitelist = set(self._sge_config.get("signal_whitelist", []))
        self._ace_whitelist = set(self._ace_config.get("signal_whitelist", []))

        # Extract risk policy thresholds
        sge_risk = self._sge_config.get("risk_policy", {})
        ace_risk = self._ace_config.get("risk_policy", {})
        self._sge_min_confidence = float(sge_risk.get("min_confidence", 0.60))
        self._sge_min_ev = float(sge_risk.get("min_ev_threshold", 0.03))
        self._ace_min_confidence = float(ace_risk.get("min_confidence", 0.68))
        self._ace_min_ev = float(ace_risk.get("min_ev_threshold", 0.06))

        # ── Initialize Category Strategy Manager ────────────────────────
        self._category_mgr = CategoryStrategyManager()
        await self._category_mgr.initialize()

        # ── Initialize Policy Engine (Sprint 11) ────────────────────────
        self._policy = PolicyEngine()
        try:
            self._policy.initialize()
            self.logger.info("PolicyEngine loaded for signal routing")
        except FileNotFoundError:
            self.logger.warning(
                "investment_policy_config.yaml not found — policy enforcement disabled"
            )
            self._policy = None

        self.logger.info(
            "Signal Router started (SGE whitelist=%s, ACE whitelist=%s, categories=%d, policy=%s)",
            self._sge_whitelist, self._ace_whitelist,
            len(self._category_mgr.categories),
            "ACTIVE" if self._policy else "DISABLED",
        )

    async def run_cycle(self) -> None:
        """Fetch PENDING signals and route each to the appropriate engine.

        Category-aware routing (Sprint 9):
        1. Look up the market's category from the DB.
        2. Apply category-specific confidence/EV modifiers via CategoryStrategyManager.
        3. Use adjusted values for threshold checks.
        4. Use category's preferred engine as tiebreaker.
        5. Store adjusted confidence in signals.confidence_adjusted.
        """

        # Fetch all unrouted signals WITH market category
        pending = await self.db.fetchall(
            """SELECT s.id, s.market_id, s.signal_type, s.confidence, s.ev_estimate,
                      s.detection_modes_triggered, m.category
               FROM signals s
               JOIN markets m ON s.market_id = m.id
               WHERE s.status = 'PENDING'
               ORDER BY s.confidence DESC"""
        )

        if not pending:
            return  # Nothing to route

        routed_count = 0
        deferred_count = 0

        for signal in pending:
            signal_id = signal["id"]
            signal_type = signal["signal_type"]
            raw_confidence = float(signal["confidence"])
            raw_ev = float(signal["ev_estimate"] or 0)
            category = signal["category"]

            # ── Policy: Classify tier + sports sub-type (Sprint 11) ────
            policy_tier = None
            sports_sub_type = None
            override_flag = 0
            if self._policy and self._policy.initialized:
                tier = self._policy.classify_tier(category or "")
                policy_tier = tier.value if tier else None

                # Sports pre-game/in-game detection
                if category and category.lower() in ("sports", "sports (pre-game)", "sports (in-game)"):
                    # For now, default to PRE_GAME unless market data says otherwise
                    sports_sub_type = "PRE_GAME"

                # Policy: Check signal quality floor
                quality_ok = self._policy.check_signal_quality_floor(
                    category=category or "",
                    confidence=raw_confidence,
                    ev=raw_ev,
                    sports_sub_type=sports_sub_type,
                )
                if not quality_ok:
                    # Check if override eligible (Tier 3 or exceptional signal)
                    tier_cfg = self._policy.get_tier_config(tier) if tier else None
                    if tier_cfg and not tier_cfg.auto_entry:
                        # Tier 3: check override protocol
                        override = self._policy.check_override_eligibility(
                            confidence=raw_confidence, ev=raw_ev,
                            independent_source_count=0,
                        )
                        if not override.eligible:
                            await self.db.execute(
                                """UPDATE signals
                                   SET status = 'DEFERRED', policy_tier = ?,
                                       sports_sub_type = ?
                                   WHERE id = ?""",
                                (policy_tier, sports_sub_type, signal_id),
                            )
                            deferred_count += 1
                            self.logger.debug(
                                "Signal #%d deferred by policy floor (%s, tier=%s)",
                                signal_id, category, policy_tier,
                            )
                            continue

                # Policy: Check no_signal_coverage
                if category and not self._policy.has_signal_coverage(category):
                    await self.db.execute(
                        """UPDATE signals
                           SET status = 'DEFERRED', policy_tier = ?
                           WHERE id = ?""",
                        (policy_tier, signal_id),
                    )
                    deferred_count += 1
                    self.logger.info(
                        "Signal #%d flagged no_signal_coverage (%s)", signal_id, category,
                    )
                    continue

                # Policy: Block Tier 3 auto-entry unless override eligible
                tier_cfg = self._policy.get_tier_config(tier)
                if tier_cfg and not tier_cfg.auto_entry:
                    override = self._policy.check_override_eligibility(
                        confidence=raw_confidence, ev=raw_ev,
                        independent_source_count=0,  # Will be enriched by source data
                    )
                    if not override.eligible:
                        await self.db.execute(
                            """UPDATE signals
                               SET status = 'DEFERRED', policy_tier = ?
                               WHERE id = ?""",
                            (policy_tier, signal_id),
                        )
                        deferred_count += 1
                        self.logger.info(
                            "Signal #%d blocked — Tier 3 restricted, override not eligible (%s)",
                            signal_id, override.reasoning,
                        )
                        continue
                    else:
                        override_flag = 1
                        self.logger.info(
                            "Signal #%d POLICY OVERRIDE — Tier 3 entry approved (%s)",
                            signal_id, override.reasoning,
                        )

            # ── Apply category-specific adjustments ───────────────────
            if self._category_mgr and self._category_mgr.initialized:
                adjusted = self._category_mgr.adjust_signal(
                    category=category,
                    signal_type=signal_type,
                    raw_confidence=raw_confidence,
                    raw_ev=raw_ev,
                )
                confidence = adjusted.confidence
                ev = adjusted.ev
                cat_engine_pref = adjusted.preferred_engine
            else:
                confidence = raw_confidence
                ev = raw_ev
                cat_engine_pref = None

            # ── Sprint 20: Per-category risk profile routing ──────────
            # If the category has a risk profile, use its thresholds
            # instead of the engine-level defaults. This eliminates the
            # dead zone where routing approved but execution rejected.
            cat_profile = None
            if self._policy and self._policy.initialized:
                cat_profile = self._policy.get_category_risk_profile(category or "")
                if cat_profile and cat_profile.get("locked", False):
                    # Category is locked — defer all signals
                    await self.db.execute(
                        """UPDATE signals
                           SET status = 'DEFERRED', policy_tier = ?
                           WHERE id = ?""",
                        (policy_tier, signal_id),
                    )
                    deferred_count += 1
                    self.logger.debug(
                        "Signal #%d deferred — category '%s' is locked",
                        signal_id, category,
                    )
                    continue

            # ── Determine routing destination ─────────────────────────
            destination = self._route_signal(
                signal_type, confidence, ev, cat_engine_pref,
                category_profile=cat_profile,
            )

            # ── Update the signal in the database ─────────────────────
            new_status = "DEFERRED" if destination == "DEFERRED" else "ROUTED"
            await self.db.execute(
                """UPDATE signals
                   SET routed_to = ?, status = ?, confidence_adjusted = ?,
                       policy_tier = ?, sports_sub_type = ?, override_flag = ?
                   WHERE id = ?""",
                (destination, new_status, round(confidence, 4),
                 policy_tier, sports_sub_type, override_flag, signal_id),
            )

            if destination == "DEFERRED":
                deferred_count += 1
            else:
                routed_count += 1
                self.logger.info(
                    "Signal #%d (%s [%s], tier=%s, conf=%.2f→%.2f, ev=%.3f) → %s%s",
                    signal_id, signal_type, category or "?",
                    policy_tier or "?",
                    raw_confidence, confidence, ev, destination,
                    " [OVERRIDE]" if override_flag else "",
                )

        await self.db.commit()

        if routed_count or deferred_count:
            self.logger.info(
                "Routing complete: %d routed, %d deferred",
                routed_count, deferred_count,
            )

    async def stop(self) -> None:
        self.logger.info("Signal Router stopped")

    # ── Routing Logic ─────────────────────────────────────────────────

    def _route_signal(
        self,
        signal_type: str,
        confidence: float,
        ev: float,
        category_engine_pref: str | None = None,
        category_profile: dict | None = None,
    ) -> str:
        """Determine routing destination for a signal.

        Sprint 20 Enhancement: Per-category risk profiles.
        When a category profile exists, its min_confidence/min_ev thresholds
        are used instead of the engine-level defaults. This eliminates the
        dead zone where routing approved (SGE floor 0.03) but execution
        rejected (Tier 2 floor 0.06) for crypto signals.

        Decision logic (in priority order):

        1. If category profile exists, use its thresholds for SGE routing.
        2. If signal doesn't meet ANY engine's minimum thresholds → DEFERRED.
        3. If signal type is on SGE's whitelist AND meets thresholds → SGE.
        4. If signal type is on ACE's whitelist AND meets ACE thresholds → ACE.
        5. COMPOSITE_HIGH_CONVICTION meeting both thresholds → always BOTH.
        6. Category preference as tiebreaker.
        7. Final fallback: route to whichever engine has the lower threshold.

        Args:
            signal_type:          The classified signal type string.
            confidence:           Confidence score (0.0–1.0), already category-adjusted.
            ev:                   Expected value estimate, already category-adjusted.
            category_engine_pref: Category's preferred engine ("SGE" or "ACE"),
                                  used as a tiebreaker. None = no preference.
            category_profile:     Per-category risk profile dict (Sprint 20), or None.

        Returns:
            "SGE", "ACE", "BOTH", or "DEFERRED".
        """
        # Sprint 20: Per-category thresholds override engine defaults
        sge_min_conf = self._sge_min_confidence
        sge_min_ev = self._sge_min_ev
        if category_profile:
            sge_min_conf = float(category_profile.get("min_confidence", sge_min_conf))
            sge_min_ev = float(category_profile.get("min_ev", sge_min_ev))

        # Check which engines' thresholds are met
        meets_sge = confidence >= sge_min_conf and ev >= sge_min_ev
        meets_ace = confidence >= self._ace_min_confidence and ev >= self._ace_min_ev

        # If doesn't meet any threshold → defer
        if not meets_sge and not meets_ace:
            return "DEFERRED"

        # Check which engines' whitelists include this signal type
        on_sge_list = signal_type in self._sge_whitelist
        on_ace_list = signal_type in self._ace_whitelist

        # Composite high conviction → route to both if both thresholds met
        if signal_type == "COMPOSITE_HIGH_CONVICTION" and meets_sge and meets_ace:
            return "BOTH"

        # On both whitelists and meets both thresholds → BOTH
        if on_sge_list and on_ace_list and meets_sge and meets_ace:
            return "BOTH"

        # Matches SGE
        if on_sge_list and meets_sge:
            return "SGE"

        # Matches ACE
        if on_ace_list and meets_ace:
            return "ACE"

        # Meets thresholds but not on either whitelist:
        # Use category preference as tiebreaker (Sprint 9 enhancement)
        if category_engine_pref and meets_sge and meets_ace:
            return category_engine_pref

        # Sprint 20: If category profile exists and meets its thresholds,
        # route to the preferred engine even if not on whitelist
        if category_profile and meets_sge:
            pref = category_engine_pref or "SGE"
            return pref

        # Final fallback: route to whichever threshold is met
        if meets_sge:
            return "SGE"
        if meets_ace:
            return "ACE"

        return "DEFERRED"
