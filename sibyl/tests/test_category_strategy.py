"""
Tests for Sprint 9 Category Strategy system.

Tests:
    - CategoryStrategyManager initialization and config loading
    - Per-category signal adjustment (confidence, EV, sizing)
    - Routing preference per category
    - Signal weight application per category
    - Default fallback for unknown categories
    - SignalRouter category-aware routing integration
    - Category exposure limits
    - Correlation penalty calculations
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ═══════════════════════════════════════════════════════════════════════════
# CategoryStrategyManager Unit Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_category_strategy_manager_init():
    """Manager should start uninitialized with empty strategies."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    mgr = CategoryStrategyManager()
    assert mgr.initialized is False
    assert mgr.categories == []


def test_category_strategy_manager_load(event_loop):
    """Manager should load all 10 categories from config."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()
        assert mgr.initialized is True
        assert len(mgr.categories) == 12
        # Verify all Kalshi categories present
        cats = set(mgr.categories)
        assert "Politics" in cats
        assert "Sports" in cats
        assert "Culture" in cats
        assert "Crypto" in cats
        assert "Climate" in cats
        assert "Economics" in cats
        assert "Mentions" in cats
        assert "Companies" in cats
        assert "Financials" in cats
        assert "Tech & Science" in cats
        assert "Weather" in cats
        assert "Geopolitics & Legal" in cats

    event_loop.run_until_complete(_test())


def test_category_strategy_politics_adjusts_confidence(event_loop):
    """Politics should slightly discount confidence (polls lie)."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        adjusted = mgr.adjust_signal(
            category="Politics",
            signal_type="WHALE",
            raw_confidence=0.80,
            raw_ev=0.10,
        )
        # Politics confidence_modifier = 0.95
        # WHALE weight in Politics = 0.9
        # adj_confidence = 0.80 * 0.95 = 0.76
        # weighted = 0.76 * (1 + (0.9 - 1.0) * 0.3) = 0.76 * 0.97 = 0.7372
        assert adjusted.confidence < 0.80  # Should be discounted
        assert adjusted.preferred_engine == "SGE"

    event_loop.run_until_complete(_test())


def test_category_strategy_sports_boosts_whale_signals(event_loop):
    """Sports should boost WHALE signal confidence (sharp money)."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        adjusted = mgr.adjust_signal(
            category="Sports",
            signal_type="WHALE",
            raw_confidence=0.80,
            raw_ev=0.10,
        )
        # Sports confidence_modifier = 1.05, WHALE weight = 1.4
        # adj_confidence = 0.80 * 1.05 = 0.84
        # weighted = 0.84 * (1 + (1.4 - 1.0) * 0.3) = 0.84 * 1.12 = 0.9408
        assert adjusted.confidence > 0.80  # Should be boosted
        assert adjusted.signal_weight == 1.4
        assert adjusted.preferred_engine == "ACE"

    event_loop.run_until_complete(_test())


def test_category_strategy_culture_small_sizing(event_loop):
    """Culture should use small position sizing (high uncertainty)."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        adjusted = mgr.adjust_signal(
            category="Culture",
            signal_type="SENTIMENT",
            raw_confidence=0.75,
            raw_ev=0.08,
        )
        assert adjusted.size_scale == 0.6  # Smaller positions
        assert adjusted.strategy_type == "sentiment_momentum"

    event_loop.run_until_complete(_test())


def test_category_strategy_crypto_momentum(event_loop):
    """Crypto should be momentum-focused with high whale weight."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        strat = mgr.get_strategy("Crypto")
        assert strat.strategy_type == "technical_momentum"
        assert strat.signal_weights.get("WHALE") == 1.5
        assert strat.signal_weights.get("MEAN_REVERSION") == 0.6
        assert strat.correlation_penalty == 0.10

    event_loop.run_until_complete(_test())


def test_category_strategy_economics_ev_premium(event_loop):
    """Economics should get EV premium (high liquidity + clear catalysts)."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        adjusted = mgr.adjust_signal(
            category="Economics",
            signal_type="ARBITRAGE",
            raw_confidence=0.70,
            raw_ev=0.08,
        )
        # ev_modifier = 1.10 → EV boosted
        assert adjusted.ev > 0.08
        # ARBITRAGE weight = 1.3 → confidence boosted
        assert adjusted.confidence > 0.70

    event_loop.run_until_complete(_test())


def test_category_strategy_climate_long_horizon(event_loop):
    """Climate should have long time horizon and low volatility."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        strat = mgr.get_strategy("Climate")
        assert strat.time_horizon == "long"
        assert strat.volatility_profile == "low"
        assert strat.preferred_engine == "SGE"

    event_loop.run_until_complete(_test())


def test_category_strategy_mentions_tiny_sizing(event_loop):
    """Mentions should use tiny position sizing (pure alpha capture)."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        strat = mgr.get_strategy("Mentions")
        assert strat.position_size_scale == 0.5
        assert strat.max_exposure_pct == 0.06
        assert strat.confidence_modifier == 0.80  # Heavy discount

    event_loop.run_until_complete(_test())


def test_category_strategy_unknown_returns_defaults(event_loop):
    """Unknown categories should return default strategy."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        strat = mgr.get_strategy("NonexistentCategory")
        assert strat.name == "_defaults"
        assert strat.confidence_modifier == 1.0
        assert strat.preferred_engine == "SGE"

    event_loop.run_until_complete(_test())


def test_category_strategy_none_returns_defaults(event_loop):
    """None category should return default strategy."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        strat = mgr.get_strategy(None)
        assert strat.name == "_defaults"

    event_loop.run_until_complete(_test())


def test_category_strategy_case_insensitive(event_loop):
    """Category lookup should be case-insensitive."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        strat_lower = mgr.get_strategy("politics")
        strat_upper = mgr.get_strategy("Politics")
        assert strat_lower.name == strat_upper.name

    event_loop.run_until_complete(_test())


def test_category_exposure_limits(event_loop):
    """Each category should have a defined max exposure limit."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        # Verify all categories have exposure limits
        for cat in mgr.categories:
            exposure = mgr.get_max_exposure(cat)
            assert 0.0 <= exposure <= 0.70, f"{cat} exposure {exposure} out of range"

    event_loop.run_until_complete(_test())


def test_category_correlation_penalties(event_loop):
    """High-correlation categories should have higher penalties."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        # Politics and Economics should have high correlation penalties
        politics_penalty = mgr.get_correlation_penalty("Politics")
        sports_penalty = mgr.get_correlation_penalty("Sports")
        assert politics_penalty > sports_penalty  # Politics more correlated

    event_loop.run_until_complete(_test())


def test_category_data_priorities(event_loop):
    """Each category should have ordered data source priorities."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        # Sports should prioritize whale detection
        sports_prio = mgr.get_data_priorities("Sports")
        assert sports_prio[0] == "whale_detection"

        # Mentions should prioritize X sentiment
        mentions_prio = mgr.get_data_priorities("Mentions")
        assert mentions_prio[0] == "x_sentiment"

        # Climate should prioritize perplexity research
        climate_prio = mgr.get_data_priorities("Climate")
        assert climate_prio[0] == "perplexity_research"

    event_loop.run_until_complete(_test())


def test_adjusted_signal_confidence_caps_at_099(event_loop):
    """Adjusted confidence should never exceed 0.99."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        # Sports with WHALE (high modifiers) on very high base confidence
        adjusted = mgr.adjust_signal(
            category="Sports",
            signal_type="WHALE",
            raw_confidence=0.98,
            raw_ev=0.15,
        )
        assert adjusted.confidence <= 0.99

    event_loop.run_until_complete(_test())


def test_category_strategy_all_engines_valid(event_loop):
    """All preferred engines should be either SGE or ACE."""
    from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager

    async def _test():
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        for cat in mgr.categories:
            engine = mgr.get_routing_preference(cat)
            assert engine in ("SGE", "ACE"), f"{cat} has invalid engine {engine}"

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# SignalRouter Category-Aware Integration Tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def db(event_loop):
    from sibyl.core.database import DatabaseManager

    async def _setup():
        db = DatabaseManager(":memory:")
        await db.initialize()
        return db

    return event_loop.run_until_complete(_setup())


def test_signal_router_route_with_category_preference(event_loop, db):
    """Router should use category preference as tiebreaker."""
    from sibyl.agents.intelligence.signal_router import SignalRouter

    config = {
        "polling": {"price_snapshot_interval_seconds": 5},
        "platforms": {
            "polymarket": {"rate_limit_per_second": 80},
            "kalshi": {"rate_limit_per_second": 8},
        },
    }

    async def _test():
        router = SignalRouter(db=db, config=config)
        await router.start()

        # Test: a signal that meets both thresholds and isn't on either
        # whitelist should use category preference as tiebreaker
        result = router._route_signal(
            signal_type="COMPOSITE",
            confidence=0.85,
            ev=0.10,
            category_engine_pref="ACE",
        )
        # Meets both SGE (0.60/0.03) and ACE (0.68/0.06) thresholds
        # Not on either whitelist → uses category pref = ACE
        assert result == "ACE"

        # Same signal but with SGE preference
        result2 = router._route_signal(
            signal_type="COMPOSITE",
            confidence=0.85,
            ev=0.10,
            category_engine_pref="SGE",
        )
        assert result2 == "SGE"

        await router.stop()

    event_loop.run_until_complete(_test())


def test_signal_router_route_without_category(event_loop, db):
    """Router should work correctly without category preference."""
    from sibyl.agents.intelligence.signal_router import SignalRouter

    config = {
        "polling": {"price_snapshot_interval_seconds": 5},
        "platforms": {
            "polymarket": {"rate_limit_per_second": 80},
            "kalshi": {"rate_limit_per_second": 8},
        },
    }

    async def _test():
        router = SignalRouter(db=db, config=config)
        await router.start()

        # Without category preference, should fall back to threshold-based routing
        result = router._route_signal(
            signal_type="WHALE",
            confidence=0.50,
            ev=0.02,
            category_engine_pref=None,
        )
        # Below both thresholds → DEFERRED
        assert result == "DEFERRED"

    event_loop.run_until_complete(_test())


@pytest.mark.skip(reason="Sprint 20: Sports category locked — signals deferred by design")
def test_signal_router_category_adjusts_routing(event_loop, db):
    """Category adjustments should affect routing outcomes for borderline signals."""
    from sibyl.agents.intelligence.signal_router import SignalRouter

    config = {
        "polling": {"price_snapshot_interval_seconds": 5},
        "platforms": {
            "polymarket": {"rate_limit_per_second": 80},
            "kalshi": {"rate_limit_per_second": 8},
        },
    }

    async def _test():
        router = SignalRouter(db=db, config=config)
        await router.start()

        # Seed a market with a category
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-SPORT', 'kalshi', 'Super Bowl Winner', 'Sports')"
        )
        # Create a borderline signal
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, status)
               VALUES ('MKT-SPORT', 'WHALE', 0.65, 0.05, 'PENDING')"""
        )
        await db.commit()

        # Run routing cycle
        await router.run_cycle()

        # Check result
        result = await db.fetchone("SELECT routed_to, confidence_adjusted FROM signals WHERE id = 1")
        # Sports WHALE: confidence = 0.65 * 1.05 * (1 + 0.4*0.3) ≈ 0.758
        # With adjusted confidence 0.758, should meet SGE threshold (0.60)
        assert result is not None
        assert result["routed_to"] in ("SGE", "ACE", "BOTH")  # Should be routed, not deferred
        assert result["confidence_adjusted"] is not None
        assert result["confidence_adjusted"] != 0.65  # Should be adjusted

        await router.stop()

    event_loop.run_until_complete(_test())
