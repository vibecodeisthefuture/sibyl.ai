"""
Tests for Sprint 7 — Advanced Intelligence.

Tests:
    - Breakout Scout: discovery scoring, freshness decay, fallback synthesis
    - Narrator: snapshot gathering, fallback digest, alert counting, escalation logic
    - SignalGenerator: cross-platform arbitrage scanning, divergence parsing
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def db(event_loop):
    from sibyl.core.database import DatabaseManager

    async def _setup():
        db = DatabaseManager(":memory:")
        await db.initialize()
        return db

    return event_loop.run_until_complete(_setup())


@pytest.fixture
def config():
    return {
        "polling": {
            "price_snapshot_interval_seconds": 5,
            "position_sync_interval_seconds": 15,
        },
        "platforms": {
            "polymarket": {"rate_limit_per_second": 80},
            "kalshi": {"rate_limit_per_second": 8},
        },
        "cross_platform": {
            "similarity_threshold": 0.55,
            "price_divergence_alert_pct": 0.05,
        },
        "notifications": {
            "enabled": True,
            "channel": "ntfy",
            "ntfy_server": "https://ntfy.sh",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# Breakout Scout Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_scout_fallback_synthesis_bullish():
    """Fallback synthesis should classify high scores as BULLISH."""
    from sibyl.agents.scout.breakout_scout import BreakoutScout

    source_data = {
        "reddit": {"score": 0.80, "posts_found": 5},
        "newsapi": {"score": 0.70, "articles_found": 3},
    }
    result = BreakoutScout._fallback_synthesis(source_data)

    assert result["sentiment_label"] == "BULLISH"
    assert result["sentiment_score"] == 0.75
    assert result["confidence"] == 0.35  # Low confidence without LLM
    assert "reddit" in result["source_breakdown"]
    assert "newsapi" in result["source_breakdown"]


def test_scout_fallback_synthesis_bearish():
    """Fallback synthesis should classify low scores as BEARISH."""
    from sibyl.agents.scout.breakout_scout import BreakoutScout

    source_data = {
        "reddit": {"score": 0.20},
        "newsapi": {"score": 0.30},
    }
    result = BreakoutScout._fallback_synthesis(source_data)

    assert result["sentiment_label"] == "BEARISH"
    assert result["sentiment_score"] == 0.25


def test_scout_fallback_synthesis_neutral():
    """Fallback synthesis should classify midrange scores as NEUTRAL."""
    from sibyl.agents.scout.breakout_scout import BreakoutScout

    source_data = {"reddit": {"score": 0.50}}
    result = BreakoutScout._fallback_synthesis(source_data)

    assert result["sentiment_label"] == "NEUTRAL"


def test_scout_fallback_synthesis_contested():
    """Fallback synthesis should classify scores outside neutral but not extreme as CONTESTED."""
    from sibyl.agents.scout.breakout_scout import BreakoutScout

    source_data = {
        "reddit": {"score": 0.60},
        "newsapi": {"score": 0.58},
    }
    result = BreakoutScout._fallback_synthesis(source_data)

    assert result["sentiment_label"] == "CONTESTED"


def test_scout_freshness_decay(db, config, event_loop):
    """Freshness decay should reduce freshness_score on existing research."""
    from sibyl.agents.scout.breakout_scout import BreakoutScout

    async def _test():
        # Seed a market and research packet
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('MKT-1', 'kalshi', 'Test')"
        )
        await db.execute(
            """INSERT INTO market_research
               (market_id, sentiment_score, sentiment_label, freshness_score)
               VALUES ('MKT-1', 0.7, 'BULLISH', 1.0)"""
        )
        await db.commit()

        scout = BreakoutScout(db=db, config=config)
        scout._freshness_decay = 0.15
        await scout._decay_freshness()

        row = await db.fetchone(
            "SELECT freshness_score FROM market_research WHERE market_id = 'MKT-1'"
        )
        assert abs(float(row["freshness_score"]) - 0.85) < 0.01

    event_loop.run_until_complete(_test())


def test_scout_freshness_decay_floor(db, config, event_loop):
    """Freshness should not go below 0.0."""
    from sibyl.agents.scout.breakout_scout import BreakoutScout

    async def _test():
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('MKT-1', 'kalshi', 'Test')"
        )
        await db.execute(
            """INSERT INTO market_research
               (market_id, sentiment_score, sentiment_label, freshness_score)
               VALUES ('MKT-1', 0.5, 'NEUTRAL', 0.05)"""
        )
        await db.commit()

        scout = BreakoutScout(db=db, config=config)
        scout._freshness_decay = 0.15
        await scout._decay_freshness()

        row = await db.fetchone(
            "SELECT freshness_score FROM market_research WHERE market_id = 'MKT-1'"
        )
        assert float(row["freshness_score"]) == 0.0

    event_loop.run_until_complete(_test())


def test_scout_discovery_empty_markets(db, config, event_loop):
    """Discovery should return empty list when no active markets."""
    from sibyl.agents.scout.breakout_scout import BreakoutScout

    async def _test():
        scout = BreakoutScout(db=db, config=config)
        scout._weights = {
            "volume_growth_rate": 0.35,
            "odds_velocity": 0.30,
            "listing_recency": 0.20,
            "category_heat": 0.15,
        }
        scout._breakout_threshold = 52.0
        scout._category_multipliers = {"standard": 1.0}

        candidates = await scout._discover_breakout_markets()
        assert candidates == []

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Narrator Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_narrator_fallback_digest():
    """Fallback digest should produce readable output without LLM."""
    from sibyl.agents.narrator.narrator import Narrator

    snapshot = {
        "portfolio": {
            "portfolio_total_balance": 500.00,
            "portfolio_cash_reserve": 25.00,
            "portfolio_allocable": 475.00,
        },
        "positions": [
            {"title": "Fed Rate", "engine": "SGE", "side": "YES",
             "size": 10, "entry": 0.50, "current": 0.60, "pnl": 1.00},
        ],
        "risk": {
            "risk_drawdown_pct": "0.05",
            "risk_drawdown_level": "WARNING",
            "risk_win_rate_7d": "0.75",
        },
        "engines": {},
        "recent_signals": [],
        "alerts": [],
    }

    digest = Narrator._fallback_digest(snapshot, escalation=False)
    assert "$500.00" in digest
    assert "WARNING" in digest
    assert "75%" in digest
    assert "1" in digest  # open positions count


def test_narrator_fallback_digest_escalation():
    """Escalation digest should include alert details."""
    from sibyl.agents.narrator.narrator import Narrator

    snapshot = {
        "portfolio": {"portfolio_total_balance": 450.00},
        "positions": [],
        "risk": {
            "risk_drawdown_pct": "0.12",
            "risk_drawdown_level": "CAUTION",
            "risk_win_rate_7d": "0.50",
        },
        "engines": {},
        "recent_signals": [],
        "alerts": ["SGE circuit breaker: TRIGGERED", "Portfolio drawdown: CAUTION"],
    }

    digest = Narrator._fallback_digest(snapshot, escalation=True)
    assert "ALERT" in digest
    assert "2 active alert" in digest
    assert "circuit breaker" in digest


def test_narrator_count_alerts(db, config, event_loop):
    """Alert counter should detect circuit breaker + drawdown alerts."""
    from sibyl.agents.narrator.narrator import Narrator

    async def _test():
        # Set SGE circuit breaker to TRIGGERED
        await db.execute(
            "UPDATE engine_state SET circuit_breaker = 'TRIGGERED' WHERE engine = 'SGE'"
        )
        # Set drawdown to CRITICAL
        await db.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES ('risk_drawdown_level', 'CRITICAL', datetime('now'))"
        )
        await db.commit()

        narrator = Narrator(db=db, config=config)
        count = await narrator._count_active_alerts()
        assert count == 2

    event_loop.run_until_complete(_test())


def test_narrator_count_alerts_zero(db, config, event_loop):
    """Alert counter should return 0 when all systems are clear."""
    from sibyl.agents.narrator.narrator import Narrator

    async def _test():
        narrator = Narrator(db=db, config=config)
        count = await narrator._count_active_alerts()
        assert count == 0

    event_loop.run_until_complete(_test())


def test_narrator_gather_snapshot(db, config, event_loop):
    """Snapshot should include all expected keys."""
    from sibyl.agents.narrator.narrator import Narrator

    async def _test():
        # Seed some data
        await db.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES ('portfolio_total_balance', '500.00', datetime('now'))"
        )
        await db.commit()

        narrator = Narrator(db=db, config=config)
        snapshot = await narrator._gather_snapshot()

        assert "portfolio" in snapshot
        assert "positions" in snapshot
        assert "risk" in snapshot
        assert "engines" in snapshot
        assert "recent_signals" in snapshot
        assert "alerts" in snapshot
        assert snapshot["portfolio"]["portfolio_total_balance"] == 500.0

    event_loop.run_until_complete(_test())


def test_narrator_heavy_loss_alert(db, config, event_loop):
    """Positions with >15% unrealized loss should trigger alerts."""
    from sibyl.agents.narrator.narrator import Narrator

    async def _test():
        # Seed a market and a losing position
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('MKT-1', 'kalshi', 'Losing Bet')"
        )
        await db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price, current_price, status)
               VALUES ('MKT-1', 'kalshi', 'SGE', 'YES', 10, 0.50, 0.40, 'OPEN')"""
        )
        await db.commit()

        narrator = Narrator(db=db, config=config)
        alerts = await narrator._get_active_alerts()

        # entry=0.50, current=0.40 → -20% loss → should trigger
        assert len(alerts) >= 1
        assert any("Losing Bet" in a for a in alerts)

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# SignalGenerator Arbitrage Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_parse_divergence_alert():
    """Divergence alert parser should extract all fields."""
    from sibyl.agents.intelligence.signal_generator import SignalGenerator

    key = "arb_divergence_POLY-123_KALSHI-456"
    value = "Polymarket=0.700 Kalshi=0.580 spread=0.120 sim=0.75"

    result = SignalGenerator._parse_divergence_alert(key, value)

    assert result is not None
    assert result["poly_id"] == "POLY-123"
    assert result["kalshi_id"] == "KALSHI-456"
    assert result["poly_price"] == 0.700
    assert result["kalshi_price"] == 0.580
    assert result["spread"] == 0.120
    assert result["similarity"] == 0.75


def test_parse_divergence_alert_invalid():
    """Parser should return None for malformed alerts."""
    from sibyl.agents.intelligence.signal_generator import SignalGenerator

    assert SignalGenerator._parse_divergence_alert("bad_key", "bad_value") is None
    assert SignalGenerator._parse_divergence_alert("arb_divergence_", "bad") is None


def test_arbitrage_signal_creation(db, config, event_loop):
    """Arbitrage scanner should create ARBITRAGE signals from divergence alerts."""
    from sibyl.agents.intelligence.signal_generator import SignalGenerator

    async def _test():
        # Seed a Kalshi market
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('KALSHI-456', 'kalshi', 'Fed Decision')"
        )
        # Seed a divergence alert with >8% spread
        await db.execute(
            """INSERT INTO system_state (key, value, updated_at)
               VALUES ('arb_divergence_POLY-123_KALSHI-456',
                       'Polymarket=0.700 Kalshi=0.580 spread=0.120 sim=0.75',
                       datetime('now'))"""
        )
        await db.commit()

        sig_gen = SignalGenerator(db=db, config=config, intel_agent=None)
        sig_gen._composite_config = {"min_arb_spread": 0.08}

        count = await sig_gen._scan_arbitrage_opportunities()
        assert count == 1

        # Verify signal was written
        signal = await db.fetchone(
            "SELECT * FROM signals WHERE market_id = 'KALSHI-456' AND signal_type = 'ARBITRAGE'"
        )
        assert signal is not None
        assert float(signal["confidence"]) >= 0.65
        assert float(signal["ev_estimate"]) > 0
        assert "ARBITRAGE" in signal["reasoning"]

    event_loop.run_until_complete(_test())


def test_arbitrage_ignores_small_spread(db, config, event_loop):
    """Arbitrage scanner should skip divergences below the minimum spread."""
    from sibyl.agents.intelligence.signal_generator import SignalGenerator

    async def _test():
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('KALSHI-789', 'kalshi', 'Small Spread')"
        )
        # Seed a divergence alert with only 3% spread
        await db.execute(
            """INSERT INTO system_state (key, value, updated_at)
               VALUES ('arb_divergence_POLY-789_KALSHI-789',
                       'Polymarket=0.500 Kalshi=0.470 spread=0.030 sim=0.80',
                       datetime('now'))"""
        )
        await db.commit()

        sig_gen = SignalGenerator(db=db, config=config, intel_agent=None)
        sig_gen._composite_config = {"min_arb_spread": 0.08}

        count = await sig_gen._scan_arbitrage_opportunities()
        assert count == 0

    event_loop.run_until_complete(_test())


def test_arbitrage_no_duplicate_signals(db, config, event_loop):
    """Arbitrage scanner should not create duplicate signals within 30 minutes."""
    from sibyl.agents.intelligence.signal_generator import SignalGenerator

    async def _test():
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('KALSHI-DUP', 'kalshi', 'Dup Test')"
        )
        await db.execute(
            """INSERT INTO system_state (key, value, updated_at)
               VALUES ('arb_divergence_POLY-DUP_KALSHI-DUP',
                       'Polymarket=0.700 Kalshi=0.580 spread=0.120 sim=0.75',
                       datetime('now'))"""
        )
        await db.commit()

        sig_gen = SignalGenerator(db=db, config=config, intel_agent=None)
        sig_gen._composite_config = {"min_arb_spread": 0.08}

        # First scan: should create signal
        count1 = await sig_gen._scan_arbitrage_opportunities()
        assert count1 == 1

        # Second scan: should NOT create duplicate
        count2 = await sig_gen._scan_arbitrage_opportunities()
        assert count2 == 0

    event_loop.run_until_complete(_test())


def test_signal_generator_runs_arb_scan(db, config, event_loop):
    """run_cycle should include arbitrage scanning even without detections."""
    from sibyl.agents.intelligence.signal_generator import SignalGenerator

    async def _test():
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('KALSHI-CYCLE', 'kalshi', 'Cycle Test')"
        )
        await db.execute(
            """INSERT INTO system_state (key, value, updated_at)
               VALUES ('arb_divergence_POLY-CYCLE_KALSHI-CYCLE',
                       'Polymarket=0.800 Kalshi=0.650 spread=0.150 sim=0.70',
                       datetime('now'))"""
        )
        await db.commit()

        sig_gen = SignalGenerator(db=db, config=config, intel_agent=None)
        sig_gen._composite_config = {"min_arb_spread": 0.08}
        await sig_gen.start()
        await sig_gen.run_cycle()

        signal = await db.fetchone(
            "SELECT * FROM signals WHERE market_id = 'KALSHI-CYCLE' AND signal_type = 'ARBITRAGE'"
        )
        assert signal is not None

    event_loop.run_until_complete(_test())
