"""
Policy Engine — machine-enforceable investment policy for all Sibyl operations.

PURPOSE:
    This module codifies the Kalshi Investment Policy (all 19 sections) into
    pure Python functions that can be called by any Sibyl agent at runtime.
    Every trading decision passes through the PolicyEngine before execution.

WHAT IT ENFORCES:
    1. TIER CLASSIFICATION (Section 2): Maps each category to Tier 1/2/3.
    2. SIGNAL QUALITY FLOORS (Section 14): Min confidence, EV, signal count.
    3. UNIVERSAL AVOIDANCE (Section 13): Liquidity floor, resolution clarity, etc.
    4. DATA FRESHNESS (Section 15): Max staleness per data type.
    5. CAPITAL CAPS (Section 12): Per-engine, per-category exposure limits.
    6. MULTI-CATEGORY RESOLUTION (Section 16): Assign ambiguous markets.
    7. OVERRIDE PROTOCOL (Section 17): Safety valve for exceptional opportunities.
    8. SPORTS DECOUPLING (Section 5): Pre-game vs in-game distinct policies.

DESIGN PRINCIPLES:
    - Pure functions: No database dependency. Takes data in, returns decisions.
    - Config-driven: All thresholds loaded from investment_policy_config.yaml.
    - Testable: Every function can be unit-tested in isolation.
    - Composable: Agents call individual checks or the full pre-trade gate.

USAGE:
    from sibyl.core.policy import PolicyEngine

    policy = PolicyEngine()  # Loads config/investment_policy_config.yaml
    policy.initialize()

    # Check if a signal passes quality floor
    passes = policy.check_signal_quality_floor("Weather", 0.72, 0.05)

    # Check if a market should be avoided
    avoid, reason = policy.check_avoidance_rules(market_data)

    # Full pre-trade gate (combines all checks)
    approved, rejection_reason = policy.pre_trade_gate(signal_data, market_data)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("sibyl.policy")


# ── Data Classes ────────────────────────────────────────────────────────

class Tier(Enum):
    """Policy tier classification."""
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_2_INGAME = "tier_2_ingame"
    TIER_3 = "tier_3"
    UNKNOWN = "unknown"


@dataclass
class TierConfig:
    """Configuration for a single policy tier."""
    name: str
    engines: list[str]
    min_confidence: float
    min_ev: float
    auto_entry: bool = True
    min_signal_count: int | None = None
    min_confirmations: int | None = None
    min_source_confirmations: int | None = None


@dataclass
class CapitalCap:
    """Per-category, per-engine capital allocation cap."""
    sge: float
    ace: float
    combined: float


@dataclass
class AvoidanceResult:
    """Result of an avoidance rule check."""
    should_avoid: bool
    reason: str = ""
    rule_name: str = ""


@dataclass
class OverrideEligibility:
    """Result of override protocol eligibility check."""
    eligible: bool
    reasoning: str = ""
    missing_conditions: list[str] = field(default_factory=list)


@dataclass
class PreTradeDecision:
    """Full pre-trade gate decision."""
    approved: bool
    rejection_reason: str = ""
    tier: Tier = Tier.UNKNOWN
    policy_tier_name: str = ""
    sports_sub_type: str | None = None
    override_eligible: bool = False
    capital_cap_ok: bool = True
    quality_floor_ok: bool = True
    avoidance_ok: bool = True
    freshness_ok: bool = True


# ── Policy Engine ───────────────────────────────────────────────────────

class PolicyEngine:
    """Central policy engine — enforces all investment policy rules.

    This is a pure-logic module. It loads rules from YAML config and
    applies them to input data. No database access, no side effects.

    Typical lifecycle:
        engine = PolicyEngine()
        engine.initialize()
        # ... use engine throughout Sibyl's runtime ...
    """

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._tiers: dict[str, TierConfig] = {}
        self._category_tier_map: dict[str, str] = {}
        self._capital_caps: dict[str, CapitalCap] = {}
        self._freshness_rules: dict[str, int] = {}
        self._avoidance_config: dict[str, Any] = {}
        self._override_config: dict[str, Any] = {}
        self._sports_config: dict[str, Any] = {}
        self._approved_sources: dict[str, list[str]] = {}
        self._engine_permissions: dict[str, dict] = {}
        self._blitz_exemption: dict[str, Any] = {}  # Sprint 14: Section 20
        self._category_risk_profiles: dict[str, dict] = {}  # Sprint 20: per-category risk
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    def initialize(self, config: dict[str, Any] | None = None) -> None:
        """Load and parse the investment policy config.

        Args:
            config: Pre-loaded config dict. If None, loads from YAML file.
        """
        if config is None:
            from sibyl.core.config import load_yaml
            config = load_yaml("investment_policy_config.yaml")

        self._config = config
        self._parse_tiers(config.get("tiers", {}))
        self._category_tier_map = config.get("category_tier_map", {})
        self._parse_capital_caps(config.get("capital_caps", {}))
        self._freshness_rules = config.get("data_freshness_max_staleness", {})
        self._avoidance_config = config.get("universal_avoidance", {})
        self._override_config = config.get("override_protocol", {})
        self._sports_config = config.get("sports", {})
        self._approved_sources = config.get("approved_data_sources", {})
        self._engine_permissions = config.get("category_engine_permissions", {})
        self._blitz_exemption = config.get("blitz_partition_exemption", {})
        self._category_risk_profiles = config.get("per_category_risk_profiles", {})
        self._initialized = True

        blitz_status = "enabled" if self._blitz_exemption.get("enabled") else "disabled"
        logger.info(
            "PolicyEngine initialized: %d tiers, %d categories, %d cap rules, blitz_exemption=%s",
            len(self._tiers),
            len(self._category_tier_map),
            len(self._capital_caps),
            blitz_status,
        )

    # ── Tier Classification (Section 2) ─────────────────────────────────

    def classify_tier(self, category: str) -> Tier:
        """Return the policy tier for a given market category.

        Args:
            category: Kalshi market category string.

        Returns:
            Tier enum value. UNKNOWN if category is not recognized.
        """
        tier_key = self._category_tier_map.get(category)
        if not tier_key:
            # Try case-insensitive match
            for cat_name, t_key in self._category_tier_map.items():
                if cat_name.lower() == category.lower():
                    tier_key = t_key
                    break

        if not tier_key:
            return Tier.UNKNOWN

        try:
            return Tier(tier_key)
        except ValueError:
            return Tier.UNKNOWN

    def get_tier_config(self, tier: Tier) -> TierConfig | None:
        """Get the full configuration for a tier."""
        return self._tiers.get(tier.value)

    # ── Per-Category Risk Profiles (Section 21, Sprint 20) ──────────────

    def get_category_risk_profile(self, category: str) -> dict[str, Any] | None:
        """Get the per-category risk profile, if one exists.

        Per-category profiles override tier-level defaults for quality floors,
        position sizing, stop losses, and other risk parameters.

        Args:
            category: Market category name (case-insensitive).

        Returns:
            Dict of risk parameters, or None if no profile exists.
        """
        profile = self._category_risk_profiles.get(category)
        if profile:
            return profile
        # Case-insensitive fallback
        for cat_name, p in self._category_risk_profiles.items():
            if cat_name.lower() == category.lower():
                return p
        return None

    def is_category_locked(self, category: str) -> bool:
        """Check if a category is locked (disabled) per Sprint 20 pivot.

        Args:
            category: Market category name.

        Returns:
            True if the category has a risk profile with locked=True.
        """
        profile = self.get_category_risk_profile(category)
        if profile:
            return bool(profile.get("locked", False))
        return False

    # ── Signal Quality Floor (Section 14) ───────────────────────────────

    def check_signal_quality_floor(
        self,
        category: str,
        confidence: float,
        ev: float,
        signal_count: int = 0,
        source_confirmations: int = 0,
        sports_sub_type: str | None = None,
    ) -> bool:
        """Check if a signal meets the minimum quality floor for its tier.

        Sprint 20: Per-category risk profiles take precedence over tier defaults.
        If a category has a profile with min_confidence/min_ev, those are used
        instead of the tier's thresholds. This eliminates the dead zone where
        routing and execution had different floors.

        Args:
            category:             Market category.
            confidence:           Signal confidence (0.0-1.0).
            ev:                   Expected value estimate (0.0-1.0).
            signal_count:         Number of signals/tweets in the window (Tier 2).
            source_confirmations: Number of independent confirming sources (Tier 3).
            sports_sub_type:      "PRE_GAME" or "IN_GAME" for sports markets.

        Returns:
            True if the signal meets the quality floor.
        """
        # Sprint 20: Check per-category risk profile FIRST
        profile = self.get_category_risk_profile(category)
        if profile and not profile.get("locked", False):
            min_conf = float(profile.get("min_confidence", 0.50))
            min_ev = float(profile.get("min_ev", 0.01))
            if confidence < min_conf:
                return False
            if ev < min_ev:
                return False
            # Per-category profile passed — skip tier checks
            logger.debug(
                "Category profile '%s': conf=%.2f >= %.2f, ev=%.3f >= %.3f → PASS",
                category, confidence, min_conf, ev, min_ev,
            )
            return True

        # Fallback: use tier-level thresholds
        if sports_sub_type == "IN_GAME":
            tier = Tier.TIER_2_INGAME
        else:
            tier = self.classify_tier(category)

        if tier == Tier.UNKNOWN:
            logger.warning("Unknown category '%s' — failing quality floor", category)
            return False

        tier_cfg = self._tiers.get(tier.value)
        if not tier_cfg:
            return False

        # Core checks: confidence and EV
        if confidence < tier_cfg.min_confidence:
            return False
        if ev < tier_cfg.min_ev:
            return False

        # Tier 2: signal count check
        if tier_cfg.min_signal_count is not None:
            if signal_count < tier_cfg.min_signal_count:
                return False

        # Tier 2 in-game: independent confirmations
        if tier_cfg.min_confirmations is not None:
            if source_confirmations < tier_cfg.min_confirmations:
                return False

        # Tier 3: independent source confirmations
        if tier_cfg.min_source_confirmations is not None:
            if source_confirmations < tier_cfg.min_source_confirmations:
                return False

        return True

    # ── Universal Avoidance Rules (Section 13) ─────────────────────────

    def check_avoidance_rules(
        self,
        market_data: dict[str, Any],
        existing_positions: list[dict[str, Any]] | None = None,
    ) -> AvoidanceResult:
        """Check if a market should be avoided based on universal rules.

        Args:
            market_data: Dict with keys like 'open_interest', 'resolution_criteria',
                         'market_id', 'category', 'signal_type'.
            existing_positions: List of open positions (for duplicate exposure check).

        Returns:
            AvoidanceResult with should_avoid=True and reason if any rule triggers.
        """
        # 1. Liquidity floor
        oi = market_data.get("open_interest", 0)
        min_oi = self._avoidance_config.get("min_open_interest_usd", 1000.0)
        if oi < min_oi:
            return AvoidanceResult(
                should_avoid=True,
                reason=f"Open interest ${oi:.0f} below ${min_oi:.0f} floor",
                rule_name="liquidity_floor",
            )

        # 2. Resolution ambiguity
        if self._avoidance_config.get("reject_subjective_resolution", True):
            resolution = market_data.get("resolution_criteria", "")
            if resolution:
                keywords = self._config.get("subjective_resolution_keywords", [])
                for keyword in keywords:
                    if keyword.lower() in resolution.lower():
                        return AvoidanceResult(
                            should_avoid=True,
                            reason=f"Subjective resolution: contains '{keyword}'",
                            rule_name="resolution_ambiguity",
                        )

        # 3. No signal coverage
        if self._avoidance_config.get("reject_no_signal_coverage", True):
            category = market_data.get("category", "")
            if category and not self.has_signal_coverage(category):
                return AvoidanceResult(
                    should_avoid=True,
                    reason=f"No approved data sources for category '{category}'",
                    rule_name="no_signal_coverage",
                )

        # 4. Duplicate exposure (both engines on same market/direction)
        if self._avoidance_config.get("reject_duplicate_exposure", True):
            if existing_positions:
                market_id = market_data.get("market_id", "")
                engines_on_market = set()
                for pos in existing_positions:
                    if pos.get("market_id") == market_id and pos.get("status") == "OPEN":
                        engines_on_market.add(pos.get("engine"))
                if len(engines_on_market) >= 2:
                    return AvoidanceResult(
                        should_avoid=True,
                        reason=f"Duplicate exposure: both SGE and ACE on {market_id}",
                        rule_name="duplicate_exposure",
                    )

        # 5. Announcement polls
        if self._avoidance_config.get("reject_announcement_polls", True):
            title = market_data.get("title", "").lower()
            announcement_keywords = ["will be named", "will be selected", "will be chosen"]
            for kw in announcement_keywords:
                if kw in title:
                    category = market_data.get("category", "")
                    if category in ("Sports", "Culture & Entertainment"):
                        return AvoidanceResult(
                            should_avoid=True,
                            reason=f"Announcement poll: '{kw}' in title",
                            rule_name="announcement_poll",
                        )

        return AvoidanceResult(should_avoid=False)

    # ── Data Freshness (Section 15) ────────────────────────────────────

    def check_data_freshness(
        self,
        data_type: str,
        data_timestamp: float,
        current_time: float | None = None,
    ) -> bool:
        """Check if data is fresh enough for signal generation.

        Args:
            data_type:      Key matching data_freshness_max_staleness config.
            data_timestamp: Unix timestamp of the data.
            current_time:   Current unix timestamp (default: now).

        Returns:
            True if data is within the freshness window.
        """
        if current_time is None:
            current_time = time.time()

        max_staleness = self._freshness_rules.get(data_type)
        if max_staleness is None:
            # Unknown data type — default to 1 hour
            max_staleness = 3600
            logger.debug("No freshness rule for '%s', using 1h default", data_type)

        age = current_time - data_timestamp
        return age <= max_staleness

    # ── Capital Caps (Section 12) ──────────────────────────────────────

    def check_category_cap(
        self,
        engine: str,
        category: str,
        current_exposure_pct: float,
        additional_exposure_pct: float = 0.0,
    ) -> bool:
        """Check if a new position would exceed the per-category capital cap.

        Args:
            engine:                  "SGE" or "ACE".
            category:                Market category.
            current_exposure_pct:    Current exposure in this category as fraction
                                     of the engine's total capital.
            additional_exposure_pct: The new position's size as fraction of engine capital.

        Returns:
            True if the position is within the cap.
        """
        cap = self._capital_caps.get(category)
        if not cap:
            # Try case-insensitive match
            for cat_name, c in self._capital_caps.items():
                if cat_name.lower() == category.lower():
                    cap = c
                    break

        if not cap:
            # Unknown category — apply a conservative default (10%)
            logger.warning("No capital cap for '%s', using 10%% default", category)
            engine_cap = 0.10
        else:
            engine_cap = cap.sge if engine == "SGE" else cap.ace

        new_total = current_exposure_pct + additional_exposure_pct
        return new_total <= engine_cap

    def get_category_cap(self, engine: str, category: str) -> float:
        """Get the capital cap for a specific engine/category combination.

        Returns:
            Cap as a fraction (e.g., 0.15 = 15%). Returns 0.10 for unknown.
        """
        cap = self._capital_caps.get(category)
        if not cap:
            for cat_name, c in self._capital_caps.items():
                if cat_name.lower() == category.lower():
                    cap = c
                    break
        if not cap:
            return 0.10
        return cap.sge if engine == "SGE" else cap.ace

    def get_combined_cap(self, category: str) -> float:
        """Get the combined capital cap for a category across both engines.

        Returns:
            Combined cap as a fraction. Returns 0.15 for unknown.
        """
        cap = self._capital_caps.get(category)
        if not cap:
            for cat_name, c in self._capital_caps.items():
                if cat_name.lower() == category.lower():
                    cap = c
                    break
        if not cap:
            return 0.15
        return cap.combined

    # ── Sports Pre-Game / In-Game (Section 5) ──────────────────────────

    def get_sports_sub_type(self, market_data: dict[str, Any]) -> str:
        """Determine if a sports market is PRE_GAME or IN_GAME.

        Heuristic:
        - If market has 'is_live' or 'in_game' flag → IN_GAME
        - If game_start_time exists and is in the past → IN_GAME
        - Otherwise → PRE_GAME

        Args:
            market_data: Dict with market metadata.

        Returns:
            "PRE_GAME" or "IN_GAME".
        """
        if market_data.get("is_live") or market_data.get("in_game"):
            return "IN_GAME"

        game_start = market_data.get("game_start_time")
        if game_start:
            try:
                if isinstance(game_start, (int, float)):
                    if game_start < time.time():
                        return "IN_GAME"
            except (ValueError, TypeError):
                pass

        return "PRE_GAME"

    def get_in_game_kelly_shrinkage(self) -> float:
        """Get the Kelly shrinkage factor for in-game sports positions.

        Returns:
            Shrinkage multiplier (default: 0.50).
        """
        ingame = self._sports_config.get("ingame", {})
        return float(ingame.get("kelly_shrinkage_factor", 0.50))

    def get_in_game_max_wager_pct(self) -> float:
        """Get max wager as % of top-of-book for in-game positions.

        Returns:
            Max wager percentage (default: 0.02 = 2%).
        """
        ingame = self._sports_config.get("ingame", {})
        return float(ingame.get("max_wager_pct_of_book", 0.02))

    def check_in_game_circuit_breaker(
        self,
        event_type: str,
    ) -> dict[str, Any]:
        """Get circuit breaker config for an in-game event type.

        Args:
            event_type: One of 'material_event', 'official_review',
                        'suspicious_activity', 'rapid_odds_swing'.

        Returns:
            Dict with 'action' and 'cooldown_seconds', or empty dict.
        """
        breakers = self._sports_config.get("ingame_circuit_breakers", {})
        return breakers.get(event_type, {})

    # ── Multi-Category Resolution (Section 16) ─────────────────────────

    def resolve_multi_category(
        self,
        categories: list[str],
    ) -> str:
        """Assign a multi-category market to the best-fit category.

        Rules (Section 16):
        1. Prefer the category with the strongest data source coverage.
        2. If coverage is equal, prefer the higher (more restrictive) tier.

        Args:
            categories: List of candidate category names.

        Returns:
            The selected category name. Empty string if none valid.
        """
        if not categories:
            return ""
        if len(categories) == 1:
            return categories[0]

        # Score by data source count (proxy for coverage strength)
        scored = []
        for cat in categories:
            sources = self._approved_sources.get(cat, [])
            tier = self.classify_tier(cat)
            # Higher tier number = more restrictive
            tier_rank = {
                Tier.TIER_1: 1,
                Tier.TIER_2: 2,
                Tier.TIER_2_INGAME: 3,
                Tier.TIER_3: 4,
                Tier.UNKNOWN: 0,
            }.get(tier, 0)
            scored.append((cat, len(sources), tier_rank))

        # Sort: most sources first, then highest tier rank for ties
        scored.sort(key=lambda x: (-x[1], -x[2]))
        return scored[0][0]

    # ── Override Protocol (Section 17) ─────────────────────────────────

    def check_override_eligibility(
        self,
        confidence: float,
        ev: float,
        independent_source_count: int,
        avoidance_result: AvoidanceResult | None = None,
    ) -> OverrideEligibility:
        """Check if a signal qualifies for the Policy Override Protocol.

        ALL conditions must be met (Section 17):
        1. Confidence >= 0.90
        2. EV >= 0.20 (20%)
        3. >= 3 independent signal sources
        4. No universal avoidance rules violated

        Args:
            confidence:               Signal confidence score.
            ev:                       Expected value estimate.
            independent_source_count: Number of independent confirming sources.
            avoidance_result:         Result from check_avoidance_rules().

        Returns:
            OverrideEligibility with eligible=True if all conditions met.
        """
        min_conf = float(self._override_config.get("min_confidence", 0.90))
        min_ev = float(self._override_config.get("min_ev", 0.20))
        min_sources = int(self._override_config.get("min_independent_sources", 3))

        missing = []

        if confidence < min_conf:
            missing.append(f"confidence {confidence:.2f} < {min_conf:.2f}")
        if ev < min_ev:
            missing.append(f"EV {ev:.2f} < {min_ev:.2f}")
        if independent_source_count < min_sources:
            missing.append(
                f"sources {independent_source_count} < {min_sources}"
            )
        if avoidance_result and avoidance_result.should_avoid:
            missing.append(f"avoidance rule violated: {avoidance_result.rule_name}")

        eligible = len(missing) == 0
        reasoning = "All override conditions met" if eligible else "; ".join(missing)

        return OverrideEligibility(
            eligible=eligible,
            reasoning=reasoning,
            missing_conditions=missing,
        )

    def get_override_position_multiplier(self) -> float:
        """Get the max position size multiplier for override trades.

        Returns:
            Multiplier (default: 0.50 = 50% of normal max).
        """
        return float(
            self._override_config.get("max_position_size_multiplier", 0.50)
        )

    # ── Signal Coverage Check ──────────────────────────────────────────

    def has_signal_coverage(self, category: str) -> bool:
        """Check if a category has any approved data sources configured.

        Args:
            category: Market category name.

        Returns:
            True if at least one data source is listed for this category.
        """
        sources = self._approved_sources.get(category, [])
        if sources:
            return True
        # Case-insensitive fallback
        for cat_name, src_list in self._approved_sources.items():
            if cat_name.lower() == category.lower():
                return bool(src_list)
        return False

    # ── Engine Permission Check ────────────────────────────────────────

    def is_engine_allowed(self, engine: str, category: str) -> bool:
        """Check if a specific engine is allowed to trade a category.

        Args:
            engine:   "SGE" or "ACE".
            category: Market category.

        Returns:
            True if the engine is permitted.
        """
        perms = self._engine_permissions.get(category)
        if not perms:
            # Fallback case-insensitive
            for cat_name, p in self._engine_permissions.items():
                if cat_name.lower() == category.lower():
                    perms = p
                    break
        if not perms:
            return True  # No restriction defined → allow

        allowed = perms.get("allowed", [])
        return engine in allowed

    # ── Blitz Partition Exemption (Section 20, Sprint 14) ──────────────

    def _is_blitz_exempt(self) -> bool:
        """Check if the Blitz partition exemption is active.

        When enabled, SGE_BLITZ trades bypass: engine category permissions,
        signal type whitelist, execution style requirements, per-category
        capital caps, and tier classification gates. They remain subject to
        universal avoidance rules and their own Blitz-specific risk controls.
        """
        return bool(self._blitz_exemption.get("enabled", False))

    def get_blitz_exemption_config(self) -> dict[str, Any]:
        """Return the full Blitz exemption configuration for external use.

        Used by BlitzScanner and BlitzExecutor to read policy-level Blitz
        parameters (e.g., which avoidance rules still apply).
        """
        return dict(self._blitz_exemption)

    # ── Full Pre-Trade Gate ────────────────────────────────────────────

    def pre_trade_gate(
        self,
        signal_data: dict[str, Any],
        market_data: dict[str, Any],
        engine: str,
        current_category_exposure_pct: float = 0.0,
        additional_exposure_pct: float = 0.0,
        existing_positions: list[dict[str, Any]] | None = None,
    ) -> PreTradeDecision:
        """Run ALL policy checks before allowing a trade.

        This is the single entry point that combines:
        1. Tier classification
        2. Signal quality floor
        3. Avoidance rules
        4. Capital cap check
        5. Engine permission check
        6. Tier 3 auto-entry block (override check)
        7. Sports sub-type handling

        Args:
            signal_data: Dict with 'confidence', 'ev', 'signal_type',
                         'signal_count', 'source_confirmations'.
            market_data: Dict with market metadata (see check_avoidance_rules).
            engine:      "SGE" or "ACE".
            current_category_exposure_pct: Current category exposure.
            additional_exposure_pct:       Proposed position size as fraction.
            existing_positions:            Open positions for duplicate check.

        Returns:
            PreTradeDecision with approved=True if all checks pass.
        """
        category = market_data.get("category", "")
        confidence = float(signal_data.get("confidence", 0))
        ev = float(signal_data.get("ev", 0))

        decision = PreTradeDecision(approved=False)

        # ── 0. Blitz Partition Exemption (Section 20, Sprint 14) ──
        # SGE_BLITZ trades bypass tier classification, engine permissions,
        # signal whitelist, execution style, and per-category caps.
        # They remain subject to: universal avoidance rules (Section 13),
        # and their own Blitz-specific risk controls (in BlitzExecutor).
        if engine == "SGE_BLITZ" and self._is_blitz_exempt():
            # Still enforce universal avoidance rules
            avoidance = self.check_avoidance_rules(market_data, existing_positions)
            if avoidance.should_avoid:
                decision.avoidance_ok = False
                decision.rejection_reason = f"Blitz avoidance: {avoidance.reason}"
                return decision
            decision.approved = True
            decision.tier = self.classify_tier(category)
            decision.policy_tier_name = "Blitz-Exempt"
            logger.debug(
                "BLITZ EXEMPT: %s approved (conf=%.2f, ev=%.3f, category=%s)",
                market_data.get("market_id", "?"), confidence, ev, category,
            )
            return decision

        # ── 1. Tier classification ──────────────────────────────────
        sports_sub = None
        if category in ("Sports", "Sports (Pre-Game)", "Sports (In-Game)"):
            sports_sub = self.get_sports_sub_type(market_data)
            decision.sports_sub_type = sports_sub
            if sports_sub == "IN_GAME":
                decision.tier = Tier.TIER_2_INGAME
            else:
                decision.tier = Tier.TIER_2
        else:
            decision.tier = self.classify_tier(category)

        tier_cfg = self.get_tier_config(decision.tier)
        decision.policy_tier_name = tier_cfg.name if tier_cfg else "Unknown"

        # ── 2. Signal quality floor ─────────────────────────────────
        quality_ok = self.check_signal_quality_floor(
            category=category,
            confidence=confidence,
            ev=ev,
            signal_count=int(signal_data.get("signal_count", 0)),
            source_confirmations=int(signal_data.get("source_confirmations", 0)),
            sports_sub_type=sports_sub,
        )
        decision.quality_floor_ok = quality_ok
        if not quality_ok:
            decision.rejection_reason = (
                f"Signal quality floor not met for {decision.policy_tier_name} "
                f"(conf={confidence:.2f}, ev={ev:.3f})"
            )
            return decision

        # ── 3. Avoidance rules ──────────────────────────────────────
        avoidance = self.check_avoidance_rules(market_data, existing_positions)
        decision.avoidance_ok = not avoidance.should_avoid
        if avoidance.should_avoid:
            decision.rejection_reason = f"Avoidance: {avoidance.reason}"
            return decision

        # ── 4. Capital cap check ────────────────────────────────────
        cap_ok = self.check_category_cap(
            engine, category, current_category_exposure_pct, additional_exposure_pct
        )
        decision.capital_cap_ok = cap_ok
        if not cap_ok:
            cap_val = self.get_category_cap(engine, category)
            decision.rejection_reason = (
                f"Category cap exceeded: {category} on {engine} "
                f"(current={current_category_exposure_pct:.1%}, "
                f"cap={cap_val:.1%})"
            )
            return decision

        # ── 5. Engine permission check ──────────────────────────────
        if not self.is_engine_allowed(engine, category):
            decision.rejection_reason = (
                f"Engine {engine} not permitted for category '{category}'"
            )
            return decision

        # ── 6. Tier 3 auto-entry block ──────────────────────────────
        if tier_cfg and not tier_cfg.auto_entry:
            # Tier 3: check override eligibility
            override = self.check_override_eligibility(
                confidence=confidence,
                ev=ev,
                independent_source_count=int(
                    signal_data.get("source_confirmations", 0)
                ),
                avoidance_result=avoidance,
            )
            decision.override_eligible = override.eligible
            if not override.eligible:
                decision.rejection_reason = (
                    f"Tier 3 restricted — override not eligible: {override.reasoning}"
                )
                return decision
            # Override approved — proceed with reduced sizing
            logger.info(
                "POLICY OVERRIDE approved: %s (conf=%.2f, ev=%.2f, sources=%d)",
                category, confidence, ev,
                signal_data.get("source_confirmations", 0),
            )

        # ── All checks passed ───────────────────────────────────────
        decision.approved = True
        return decision

    # ── Internal Parsing ───────────────────────────────────────────────

    def _parse_tiers(self, tiers_config: dict[str, Any]) -> None:
        """Parse tier definitions from config."""
        for key, cfg in tiers_config.items():
            self._tiers[key] = TierConfig(
                name=cfg.get("name", key),
                engines=cfg.get("engines", []),
                min_confidence=float(cfg.get("min_confidence", 0.5)),
                min_ev=float(cfg.get("min_ev", 0.01)),
                auto_entry=cfg.get("auto_entry", True),
                min_signal_count=cfg.get("min_signal_count"),
                min_confirmations=cfg.get("min_confirmations"),
                min_source_confirmations=cfg.get("min_source_confirmations"),
            )

    def _parse_capital_caps(self, caps_config: dict[str, Any]) -> None:
        """Parse capital allocation caps from config."""
        for category, cap in caps_config.items():
            self._capital_caps[category] = CapitalCap(
                sge=float(cap.get("sge", 0.10)),
                ace=float(cap.get("ace", 0.10)),
                combined=float(cap.get("combined", 0.15)),
            )
