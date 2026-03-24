"""
Tests for the Investment Policy Engine (Sprint 11).

Tests cover all 17 policy sections:
  - Tier classification for all categories (Section 2/3)
  - Signal quality floors per tier (Section 14)
  - Universal avoidance rules (Section 13)
  - Data freshness validation (Section 15)
  - Capital cap enforcement (Section 12)
  - Override protocol eligibility (Section 17)
  - Sports pre-game/in-game detection (Section 5)
  - Multi-category market resolution (Section 16)
  - Full pre-trade gate integration
"""

import time
import pytest

from sibyl.core.policy import (
    PolicyEngine,
    Tier,
    AvoidanceResult,
    OverrideEligibility,
    PreTradeDecision,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def policy_config():
    """Minimal policy config for testing."""
    return {
        "tiers": {
            "tier_1": {
                "name": "Steady",
                "engines": ["SGE"],
                "min_confidence": 0.60,
                "min_ev": 0.03,
                "auto_entry": True,
            },
            "tier_2": {
                "name": "Volatile",
                "engines": ["ACE"],
                "min_confidence": 0.65,
                "min_ev": 0.06,
                "min_signal_count": 20,
                "auto_entry": True,
            },
            "tier_2_ingame": {
                "name": "In-Game",
                "engines": ["ACE"],
                "min_confidence": 0.70,
                "min_ev": 0.08,
                "min_confirmations": 2,
                "auto_entry": True,
            },
            "tier_3": {
                "name": "Restricted",
                "engines": ["ACE"],
                "min_confidence": 0.85,
                "min_ev": 0.15,
                "min_source_confirmations": 3,
                "auto_entry": False,
            },
        },
        "category_tier_map": {
            "Weather": "tier_1",
            "Sports": "tier_2",
            "Sports (Pre-Game)": "tier_2",
            "Sports (In-Game)": "tier_2_ingame",
            "Mentions": "tier_1",
            "Culture & Entertainment": "tier_2",
            "Economics & Macro": "tier_1",
            "Crypto & Digital Assets": "tier_2",
            "Science & Technology": "tier_1",
            "Geopolitics & Legal": "tier_3",
        },
        "capital_caps": {
            "Weather": {"sge": 0.15, "ace": 0.05, "combined": 0.15},
            "Sports (Pre-Game)": {"sge": 0.05, "ace": 0.15, "combined": 0.18},
            "Sports (In-Game)": {"sge": 0.03, "ace": 0.10, "combined": 0.12},
            "Mentions": {"sge": 0.15, "ace": 0.15, "combined": 0.25},
            "Culture & Entertainment": {"sge": 0.05, "ace": 0.20, "combined": 0.22},
            "Economics & Macro": {"sge": 0.20, "ace": 0.10, "combined": 0.25},
            "Crypto & Digital Assets": {"sge": 0.10, "ace": 0.20, "combined": 0.25},
            "Science & Technology": {"sge": 0.15, "ace": 0.15, "combined": 0.25},
            "Geopolitics & Legal": {"sge": 0.00, "ace": 0.10, "combined": 0.10},
        },
        "data_freshness_max_staleness": {
            "weather_forecast": 5400,
            "sports_injury_report": 7200,
            "live_game_state": 60,
            "economic_release": 1800,
            "x_sentiment_window": 300,
            "earnings_consensus": 86400,
            "fda_approval_calendar": 172800,
            "geopolitical_event": 3600,
        },
        "universal_avoidance": {
            "min_open_interest_usd": 1000.0,
            "reject_subjective_resolution": True,
            "reject_no_signal_coverage": True,
            "reject_duplicate_exposure": True,
            "reject_announcement_polls": True,
            "reject_sports_prop_crossover": True,
        },
        "subjective_resolution_keywords": [
            "at the discretion of",
            "as determined by",
        ],
        "override_protocol": {
            "min_confidence": 0.90,
            "min_ev": 0.20,
            "min_independent_sources": 3,
            "max_position_size_multiplier": 0.50,
        },
        "sports": {
            "ingame": {
                "kelly_shrinkage_factor": 0.50,
                "max_wager_pct_of_book": 0.02,
            },
            "ingame_circuit_breakers": {
                "material_event": {
                    "action": "suspend_new_entries",
                    "cooldown_seconds": 5,
                },
                "official_review": {
                    "action": "suspend_new_entries",
                    "cooldown_seconds": 0,
                    "post_review_buffer_seconds": 3,
                },
                "suspicious_activity": {
                    "action": "halt",
                    "cooldown_seconds": -1,
                },
                "rapid_odds_swing": {
                    "action": "suspend_new_entries",
                    "cooldown_seconds": 10,
                },
            },
        },
        "approved_data_sources": {
            "Weather": ["open_meteo", "noaa", "nws"],
            "Sports": ["sportsrc", "api_sports", "balldontlie"],
            "Mentions": ["fred", "bls", "sec_edgar", "congress"],
            "Culture & Entertainment": ["x_sentiment", "reddit", "pytrends"],
            "Economics & Macro": ["fred", "bls", "bea", "cme_fedwatch"],
            "Crypto & Digital Assets": ["coingecko", "glassnode", "feargreed"],
            "Science & Technology": ["openfda", "clinicaltrials"],
            "Geopolitics & Legal": ["courtlistener", "govtrack", "gdelt"],
        },
        "category_engine_permissions": {
            "Weather": {"primary": "SGE", "allowed": ["SGE"]},
            "Sports": {"primary": "ACE", "allowed": ["ACE"]},
            "Geopolitics & Legal": {"primary": "ACE", "allowed": ["ACE"]},
        },
    }


@pytest.fixture
def engine(policy_config):
    """Initialized PolicyEngine for testing."""
    pe = PolicyEngine()
    pe.initialize(config=policy_config)
    return pe


# ── Tier Classification Tests (Section 2/3) ──────────────────────────

class TestTierClassification:
    def test_weather_is_tier_1(self, engine):
        assert engine.classify_tier("Weather") == Tier.TIER_1

    def test_sports_is_tier_2(self, engine):
        assert engine.classify_tier("Sports") == Tier.TIER_2

    def test_sports_ingame_is_tier_2_ingame(self, engine):
        assert engine.classify_tier("Sports (In-Game)") == Tier.TIER_2_INGAME

    def test_geopolitics_is_tier_3(self, engine):
        assert engine.classify_tier("Geopolitics & Legal") == Tier.TIER_3

    def test_economics_is_tier_1(self, engine):
        assert engine.classify_tier("Economics & Macro") == Tier.TIER_1

    def test_crypto_is_tier_2(self, engine):
        assert engine.classify_tier("Crypto & Digital Assets") == Tier.TIER_2

    def test_culture_is_tier_2(self, engine):
        assert engine.classify_tier("Culture & Entertainment") == Tier.TIER_2

    def test_mentions_is_tier_1(self, engine):
        assert engine.classify_tier("Mentions") == Tier.TIER_1

    def test_science_is_tier_1(self, engine):
        assert engine.classify_tier("Science & Technology") == Tier.TIER_1

    def test_unknown_category_returns_unknown(self, engine):
        assert engine.classify_tier("Nonexistent Category") == Tier.UNKNOWN

    def test_case_insensitive_match(self, engine):
        assert engine.classify_tier("weather") == Tier.TIER_1

    def test_all_categories_classified(self, engine):
        categories = [
            "Weather", "Sports", "Sports (In-Game)", "Mentions",
            "Culture & Entertainment", "Economics & Macro",
            "Crypto & Digital Assets", "Science & Technology",
            "Geopolitics & Legal",
        ]
        for cat in categories:
            tier = engine.classify_tier(cat)
            assert tier != Tier.UNKNOWN, f"{cat} should not be UNKNOWN"


# ── Signal Quality Floor Tests (Section 14) ──────────────────────────

class TestSignalQualityFloor:
    def test_tier1_pass(self, engine):
        assert engine.check_signal_quality_floor(
            "Weather", confidence=0.65, ev=0.04
        ) is True

    def test_tier1_fail_confidence(self, engine):
        assert engine.check_signal_quality_floor(
            "Weather", confidence=0.55, ev=0.04
        ) is False

    def test_tier1_fail_ev(self, engine):
        assert engine.check_signal_quality_floor(
            "Weather", confidence=0.65, ev=0.02
        ) is False

    def test_tier2_pass(self, engine):
        assert engine.check_signal_quality_floor(
            "Culture & Entertainment", confidence=0.70, ev=0.08,
            signal_count=25,
        ) is True

    def test_tier2_fail_signal_count(self, engine):
        assert engine.check_signal_quality_floor(
            "Culture & Entertainment", confidence=0.70, ev=0.08,
            signal_count=10,
        ) is False

    def test_tier2_ingame_pass(self, engine):
        assert engine.check_signal_quality_floor(
            "Sports", confidence=0.75, ev=0.10,
            source_confirmations=3,
            sports_sub_type="IN_GAME",
        ) is True

    def test_tier2_ingame_fail_confidence(self, engine):
        assert engine.check_signal_quality_floor(
            "Sports", confidence=0.65, ev=0.10,
            source_confirmations=3,
            sports_sub_type="IN_GAME",
        ) is False

    def test_tier2_ingame_fail_confirmations(self, engine):
        assert engine.check_signal_quality_floor(
            "Sports", confidence=0.75, ev=0.10,
            source_confirmations=1,
            sports_sub_type="IN_GAME",
        ) is False

    def test_tier3_pass(self, engine):
        assert engine.check_signal_quality_floor(
            "Geopolitics & Legal", confidence=0.90, ev=0.20,
            source_confirmations=4,
        ) is True

    def test_tier3_fail_confidence(self, engine):
        assert engine.check_signal_quality_floor(
            "Geopolitics & Legal", confidence=0.80, ev=0.20,
            source_confirmations=4,
        ) is False

    def test_tier3_fail_sources(self, engine):
        assert engine.check_signal_quality_floor(
            "Geopolitics & Legal", confidence=0.90, ev=0.20,
            source_confirmations=2,
        ) is False

    def test_unknown_category_fails(self, engine):
        assert engine.check_signal_quality_floor(
            "Nonexistent", confidence=0.99, ev=0.50
        ) is False


# ── Avoidance Rule Tests (Section 13) ────────────────────────────────

class TestAvoidanceRules:
    def test_liquidity_floor_rejects(self, engine):
        result = engine.check_avoidance_rules({"open_interest": 500})
        assert result.should_avoid is True
        assert "floor" in result.reason.lower()

    def test_liquidity_floor_passes(self, engine):
        result = engine.check_avoidance_rules({"open_interest": 5000})
        assert result.should_avoid is False

    def test_subjective_resolution_rejects(self, engine):
        result = engine.check_avoidance_rules({
            "open_interest": 5000,
            "resolution_criteria": "Winner at the discretion of the committee",
        })
        assert result.should_avoid is True
        assert "subjective" in result.reason.lower()

    def test_clean_resolution_passes(self, engine):
        result = engine.check_avoidance_rules({
            "open_interest": 5000,
            "resolution_criteria": "Resolves YES if Bitcoin is above $100,000 on Dec 31",
        })
        assert result.should_avoid is False

    def test_no_signal_coverage_rejects(self, engine):
        result = engine.check_avoidance_rules({
            "open_interest": 5000,
            "category": "Unknown Category With No Sources",
        })
        assert result.should_avoid is True
        assert "no approved data" in result.reason.lower()

    def test_covered_category_passes(self, engine):
        result = engine.check_avoidance_rules({
            "open_interest": 5000,
            "category": "Weather",
        })
        assert result.should_avoid is False

    def test_duplicate_exposure_rejects(self, engine):
        positions = [
            {"market_id": "MKT1", "engine": "SGE", "status": "OPEN"},
            {"market_id": "MKT1", "engine": "ACE", "status": "OPEN"},
        ]
        result = engine.check_avoidance_rules(
            {"open_interest": 5000, "market_id": "MKT1"},
            existing_positions=positions,
        )
        assert result.should_avoid is True
        assert "duplicate" in result.reason.lower()

    def test_single_engine_passes(self, engine):
        positions = [
            {"market_id": "MKT1", "engine": "SGE", "status": "OPEN"},
        ]
        result = engine.check_avoidance_rules(
            {"open_interest": 5000, "market_id": "MKT1"},
            existing_positions=positions,
        )
        assert result.should_avoid is False

    def test_announcement_poll_rejects(self, engine):
        # Exact phrase "will be named" in the title triggers the poll check
        result = engine.check_avoidance_rules({
            "open_interest": 5000,
            "title": "Who will be named league MVP this season?",
            "category": "Sports",
        })
        assert result.should_avoid is True
        assert result.rule_name == "announcement_poll"


# ── Data Freshness Tests (Section 15) ────────────────────────────────

class TestDataFreshness:
    def test_fresh_weather_data(self, engine):
        now = time.time()
        assert engine.check_data_freshness(
            "weather_forecast", now - 3600, now  # 1 hour ago
        ) is True

    def test_stale_weather_data(self, engine):
        now = time.time()
        assert engine.check_data_freshness(
            "weather_forecast", now - 7200, now  # 2 hours ago, max is 90min
        ) is False

    def test_fresh_game_state(self, engine):
        now = time.time()
        assert engine.check_data_freshness(
            "live_game_state", now - 30, now  # 30 seconds ago
        ) is True

    def test_stale_game_state(self, engine):
        now = time.time()
        assert engine.check_data_freshness(
            "live_game_state", now - 120, now  # 2 minutes ago, max is 60s
        ) is False

    def test_unknown_data_type_uses_default(self, engine):
        now = time.time()
        # Unknown data type should use 1-hour default
        assert engine.check_data_freshness(
            "unknown_type", now - 1800, now  # 30 min ago
        ) is True


# ── Capital Cap Tests (Section 12) ───────────────────────────────────

class TestCapitalCaps:
    def test_within_cap(self, engine):
        assert engine.check_category_cap(
            "SGE", "Weather", current_exposure_pct=0.10, additional_exposure_pct=0.03
        ) is True

    def test_exceeds_cap(self, engine):
        assert engine.check_category_cap(
            "SGE", "Weather", current_exposure_pct=0.14, additional_exposure_pct=0.03
        ) is False

    def test_ace_sports_pregame_cap(self, engine):
        assert engine.get_category_cap("ACE", "Sports (Pre-Game)") == 0.15

    def test_sge_geopolitics_is_zero(self, engine):
        assert engine.get_category_cap("SGE", "Geopolitics & Legal") == 0.00

    def test_combined_cap(self, engine):
        assert engine.get_combined_cap("Economics & Macro") == 0.25

    def test_unknown_category_uses_default(self, engine):
        cap = engine.get_category_cap("SGE", "Totally Unknown")
        assert cap == 0.10  # Default

    def test_exactly_at_cap_is_allowed(self, engine):
        # 15% + 0% = exactly at the 15% SGE cap for Weather
        assert engine.check_category_cap(
            "SGE", "Weather", current_exposure_pct=0.15, additional_exposure_pct=0.0
        ) is True


# ── Override Protocol Tests (Section 17) ─────────────────────────────

class TestOverrideProtocol:
    def test_eligible_override(self, engine):
        result = engine.check_override_eligibility(
            confidence=0.92, ev=0.25, independent_source_count=4,
        )
        assert result.eligible is True
        assert len(result.missing_conditions) == 0

    def test_not_eligible_low_confidence(self, engine):
        result = engine.check_override_eligibility(
            confidence=0.85, ev=0.25, independent_source_count=4,
        )
        assert result.eligible is False
        assert any("confidence" in c for c in result.missing_conditions)

    def test_not_eligible_low_ev(self, engine):
        result = engine.check_override_eligibility(
            confidence=0.92, ev=0.15, independent_source_count=4,
        )
        assert result.eligible is False
        assert any("EV" in c for c in result.missing_conditions)

    def test_not_eligible_few_sources(self, engine):
        result = engine.check_override_eligibility(
            confidence=0.92, ev=0.25, independent_source_count=2,
        )
        assert result.eligible is False
        assert any("sources" in c for c in result.missing_conditions)

    def test_not_eligible_avoidance_violation(self, engine):
        avoidance = AvoidanceResult(
            should_avoid=True, reason="Low liquidity", rule_name="liquidity_floor"
        )
        result = engine.check_override_eligibility(
            confidence=0.92, ev=0.25, independent_source_count=4,
            avoidance_result=avoidance,
        )
        assert result.eligible is False
        assert any("avoidance" in c for c in result.missing_conditions)

    def test_override_position_multiplier(self, engine):
        assert engine.get_override_position_multiplier() == 0.50


# ── Sports Pre-Game / In-Game Tests (Section 5) ─────────────────────

class TestSportsDecoupling:
    def test_pregame_default(self, engine):
        sub = engine.get_sports_sub_type({})
        assert sub == "PRE_GAME"

    def test_ingame_from_flag(self, engine):
        sub = engine.get_sports_sub_type({"is_live": True})
        assert sub == "IN_GAME"

    def test_ingame_from_past_start(self, engine):
        sub = engine.get_sports_sub_type({
            "game_start_time": time.time() - 3600,
        })
        assert sub == "IN_GAME"

    def test_pregame_from_future_start(self, engine):
        sub = engine.get_sports_sub_type({
            "game_start_time": time.time() + 3600,
        })
        assert sub == "PRE_GAME"

    def test_ingame_kelly_shrinkage(self, engine):
        assert engine.get_in_game_kelly_shrinkage() == 0.50

    def test_ingame_max_wager(self, engine):
        assert engine.get_in_game_max_wager_pct() == 0.02

    def test_ingame_circuit_breaker_material_event(self, engine):
        cb = engine.check_in_game_circuit_breaker("material_event")
        assert cb["action"] == "suspend_new_entries"
        assert cb["cooldown_seconds"] == 5

    def test_ingame_circuit_breaker_suspicious(self, engine):
        cb = engine.check_in_game_circuit_breaker("suspicious_activity")
        assert cb["action"] == "halt"
        assert cb["cooldown_seconds"] == -1  # Manual review

    def test_ingame_circuit_breaker_unknown(self, engine):
        cb = engine.check_in_game_circuit_breaker("nonexistent")
        assert cb == {}


# ── Multi-Category Resolution Tests (Section 16) ────────────────────

class TestMultiCategory:
    def test_single_category(self, engine):
        assert engine.resolve_multi_category(["Weather"]) == "Weather"

    def test_empty_categories(self, engine):
        assert engine.resolve_multi_category([]) == ""

    def test_prefers_more_sources(self, engine):
        # Mentions has 4 sources, Geopolitics has 3
        result = engine.resolve_multi_category(["Geopolitics & Legal", "Mentions"])
        assert result == "Mentions"

    def test_prefers_higher_tier_on_tie(self, engine):
        # If two categories have equal source coverage, prefer the more
        # restrictive tier. This tests the tiebreaker logic.
        result = engine.resolve_multi_category(["Weather", "Economics & Macro"])
        # Economics has 4 sources, Weather has 3 → Economics should win
        assert result == "Economics & Macro"


# ── Signal Coverage Tests ────────────────────────────────────────────

class TestSignalCoverage:
    def test_covered_category(self, engine):
        assert engine.has_signal_coverage("Weather") is True

    def test_uncovered_category(self, engine):
        assert engine.has_signal_coverage("Nonexistent") is False

    def test_case_insensitive(self, engine):
        assert engine.has_signal_coverage("weather") is True


# ── Engine Permission Tests ──────────────────────────────────────────

class TestEnginePermissions:
    def test_sge_allowed_for_weather(self, engine):
        assert engine.is_engine_allowed("SGE", "Weather") is True

    def test_ace_not_allowed_for_weather(self, engine):
        assert engine.is_engine_allowed("ACE", "Weather") is False

    def test_ace_allowed_for_sports(self, engine):
        assert engine.is_engine_allowed("ACE", "Sports") is True

    def test_sge_not_allowed_for_sports(self, engine):
        assert engine.is_engine_allowed("SGE", "Sports") is False

    def test_unknown_category_allows_any(self, engine):
        assert engine.is_engine_allowed("SGE", "Brand New Category") is True


# ── Full Pre-Trade Gate Tests ────────────────────────────────────────

class TestPreTradeGate:
    def test_approved_tier1_signal(self, engine):
        decision = engine.pre_trade_gate(
            signal_data={"confidence": 0.70, "ev": 0.05},
            market_data={"category": "Weather", "open_interest": 5000},
            engine="SGE",
        )
        assert decision.approved is True
        assert decision.tier == Tier.TIER_1

    def test_rejected_low_quality(self, engine):
        decision = engine.pre_trade_gate(
            signal_data={"confidence": 0.50, "ev": 0.01},
            market_data={"category": "Weather", "open_interest": 5000},
            engine="SGE",
        )
        assert decision.approved is False
        assert "quality floor" in decision.rejection_reason.lower()

    def test_rejected_avoidance(self, engine):
        decision = engine.pre_trade_gate(
            signal_data={"confidence": 0.70, "ev": 0.05},
            market_data={"category": "Weather", "open_interest": 100},
            engine="SGE",
        )
        assert decision.approved is False
        assert "avoidance" in decision.rejection_reason.lower()

    def test_rejected_capital_cap(self, engine):
        decision = engine.pre_trade_gate(
            signal_data={"confidence": 0.70, "ev": 0.05},
            market_data={"category": "Weather", "open_interest": 5000},
            engine="SGE",
            current_category_exposure_pct=0.14,
            additional_exposure_pct=0.05,
        )
        assert decision.approved is False
        assert "cap" in decision.rejection_reason.lower()

    def test_rejected_engine_permission(self, engine):
        decision = engine.pre_trade_gate(
            signal_data={"confidence": 0.70, "ev": 0.05},
            market_data={"category": "Weather", "open_interest": 5000},
            engine="ACE",  # ACE not allowed for Weather
        )
        assert decision.approved is False
        assert "not permitted" in decision.rejection_reason.lower()

    def test_tier3_blocked_without_override(self, engine):
        # Tier 3 with confidence 0.86 fails the quality floor (min 0.85)
        # but also fails override (needs 0.90). The pre-trade gate rejects
        # either at the quality floor or the Tier 3 block, depending on values.
        decision = engine.pre_trade_gate(
            signal_data={
                "confidence": 0.88, "ev": 0.18,  # Passes Tier 3 floor but not override
                "source_confirmations": 1,
            },
            market_data={
                "category": "Geopolitics & Legal",
                "open_interest": 5000,
            },
            engine="ACE",
        )
        assert decision.approved is False
        assert decision.tier == Tier.TIER_3

    def test_tier3_approved_with_override(self, engine):
        decision = engine.pre_trade_gate(
            signal_data={
                "confidence": 0.92, "ev": 0.25,
                "source_confirmations": 4,
            },
            market_data={
                "category": "Geopolitics & Legal",
                "open_interest": 5000,
            },
            engine="ACE",
        )
        assert decision.approved is True
        assert decision.override_eligible is True

    def test_sports_ingame_detected(self, engine):
        decision = engine.pre_trade_gate(
            signal_data={"confidence": 0.75, "ev": 0.10},
            market_data={
                "category": "Sports",
                "open_interest": 5000,
                "is_live": True,
            },
            engine="ACE",
        )
        assert decision.sports_sub_type == "IN_GAME"
        assert decision.tier == Tier.TIER_2_INGAME


# ── Initialization Tests ─────────────────────────────────────────────

class TestPolicyEngineInit:
    def test_not_initialized_by_default(self):
        pe = PolicyEngine()
        assert pe.initialized is False

    def test_initialized_after_init(self, engine):
        assert engine.initialized is True

    def test_loads_from_config_dict(self, policy_config):
        pe = PolicyEngine()
        pe.initialize(config=policy_config)
        assert pe.initialized is True
        assert pe.classify_tier("Weather") == Tier.TIER_1
