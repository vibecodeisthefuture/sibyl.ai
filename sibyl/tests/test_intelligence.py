"""
Tests for Sprint 2 Intelligence Layer.

Tests all three intelligence agents:
    - MarketIntelligenceAgent — whale detection, volume anomaly, orderbook depth
    - SignalGenerator — composite scoring, EV estimation, signal classification
    - SignalRouter — engine routing logic (SGE/ACE/BOTH/DEFERRED)
"""

import asyncio
import json
import pytest

# ─── Helper: Create an in-memory database with test data ──────────────────

@pytest.fixture
def event_loop():
    """Create a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def db(event_loop):
    """Create and initialize an in-memory database for testing."""
    from sibyl.core.database import DatabaseManager

    async def _setup():
        db = DatabaseManager(":memory:")
        await db.initialize()
        return db

    return event_loop.run_until_complete(_setup())


@pytest.fixture
def config():
    """Minimal system config for testing."""
    return {
        "polling": {
            "price_snapshot_interval_seconds": 30,
        },
        "platforms": {
            "polymarket": {"rate_limit_per_second": 10},
            "kalshi": {"rate_limit_per_second": 10},
        },
        "cross_platform": {
            "similarity_threshold": 0.55,
            "price_divergence_alert_pct": 0.05,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# Market Intelligence Agent Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_whale_detection_above_threshold(db, config, event_loop):
    """A trade 5× the average size should trigger a WHALE detection."""
    from sibyl.agents.intelligence.market_intelligence import MarketIntelligenceAgent

    async def _test():
        agent = MarketIntelligenceAgent(db=db, config=config)
        agent._surv = {"whale_threshold_multiplier_default": 4.0, "whale_threshold_by_category": {}}

        # Insert a test market
        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-1", "kalshi", "Test Market", "other", "active"),
        )

        # Insert 10 normal trades (size=10 each) to establish a baseline
        for _ in range(10):
            await db.execute(
                "INSERT INTO trades_log (market_id, side, size, price) VALUES (?, ?, ?, ?)",
                ("MKT-1", "YES", 10.0, 0.50),
            )

        # Insert one whale trade (size=100, which is 10× avg = well above 4× threshold)
        await db.execute(
            "INSERT INTO trades_log (market_id, side, size, price, timestamp) VALUES (?, ?, ?, ?, datetime('now'))",
            ("MKT-1", "YES", 100.0, 0.55),
        )
        await db.commit()

        # Run Mode A
        markets = await db.fetchall("SELECT id, platform, category FROM markets WHERE status = 'active'")
        await agent._mode_a_whale_watching(markets)

        # Check detection queue
        detections = agent.get_and_clear_detections()
        assert len(detections) >= 1
        assert detections[0]["mode"] == "WHALE"
        assert detections[0]["market_id"] == "MKT-1"

        # Check whale_events table
        whale = await db.fetchone("SELECT * FROM whale_events WHERE market_id = 'MKT-1'")
        assert whale is not None
        assert float(whale["size"]) == 100.0

    event_loop.run_until_complete(_test())


def test_whale_detection_below_threshold(db, config, event_loop):
    """A trade at average size should NOT trigger a WHALE detection."""
    from sibyl.agents.intelligence.market_intelligence import MarketIntelligenceAgent

    async def _test():
        agent = MarketIntelligenceAgent(db=db, config=config)
        agent._surv = {"whale_threshold_multiplier_default": 4.0, "whale_threshold_by_category": {}}

        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-2", "kalshi", "Test Market 2", "other", "active"),
        )

        # Insert normal trades
        for _ in range(10):
            await db.execute(
                "INSERT INTO trades_log (market_id, side, size, price) VALUES (?, ?, ?, ?)",
                ("MKT-2", "YES", 10.0, 0.50),
            )

        # Insert a normal-sized trade (just above average, NOT whale-sized)
        await db.execute(
            "INSERT INTO trades_log (market_id, side, size, price, timestamp) VALUES (?, ?, ?, ?, datetime('now'))",
            ("MKT-2", "YES", 15.0, 0.52),
        )
        await db.commit()

        markets = await db.fetchall("SELECT id, platform, category FROM markets WHERE status = 'active'")
        await agent._mode_a_whale_watching(markets)

        detections = agent.get_and_clear_detections()
        whale_detections = [d for d in detections if d["market_id"] == "MKT-2"]
        assert len(whale_detections) == 0

    event_loop.run_until_complete(_test())


def test_volume_anomaly_detection(db, config, event_loop):
    """A sudden volume spike should trigger a VOLUME_SURGE detection."""
    from sibyl.agents.intelligence.market_intelligence import MarketIntelligenceAgent

    async def _test():
        agent = MarketIntelligenceAgent(db=db, config=config)
        agent._surv = {"volume_zscore_threshold": 2.5}

        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-VOL", "polymarket", "Volume Test", "crypto", "active"),
        )

        # Insert 10 days of normal volume (100 each)
        for i in range(10):
            await db.execute(
                """INSERT INTO prices (market_id, yes_price, volume_24h, timestamp)
                   VALUES (?, ?, ?, datetime('now', ?))""",
                ("MKT-VOL", 0.50, 100.0, f"-{i+1} days"),
            )

        # Insert a spike (volume = 1000 = 10× normal → Z-score well above 2.5)
        await db.execute(
            "INSERT INTO prices (market_id, yes_price, volume_24h) VALUES (?, ?, ?)",
            ("MKT-VOL", 0.55, 1000.0),
        )
        await db.commit()

        markets = await db.fetchall("SELECT id, platform, category FROM markets WHERE status = 'active'")
        await agent._mode_b_volume_anomaly(markets)

        detections = agent.get_and_clear_detections()
        vol_detections = [d for d in detections if d["mode"] == "VOLUME_SURGE"]
        assert len(vol_detections) >= 1
        assert vol_detections[0]["details"]["zscore"] > 2.5

    event_loop.run_until_complete(_test())


def test_orderbook_spread_expansion(db, config, event_loop):
    """A wide bid-ask spread should trigger SPREAD_EXPANSION detection."""
    from sibyl.agents.intelligence.market_intelligence import MarketIntelligenceAgent

    async def _test():
        agent = MarketIntelligenceAgent(db=db, config=config)
        agent._surv = {
            "spread_expansion_threshold": 0.04,
            "thin_market_depth_threshold": 500,
            "wall_size_multiplier": 8.0,
        }

        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-OB", "kalshi", "Orderbook Test", "politics", "active"),
        )

        # Insert orderbook with wide spread: bid=0.40, ask=0.60 → spread=0.20 (40% normalized)
        bids = json.dumps([{"price": 0.40, "size": 100}])
        asks = json.dumps([{"price": 0.60, "size": 100}])
        await db.execute(
            "INSERT INTO orderbook (market_id, bids, asks) VALUES (?, ?, ?)",
            ("MKT-OB", bids, asks),
        )
        await db.commit()

        markets = await db.fetchall("SELECT id, platform, category FROM markets WHERE status = 'active'")
        await agent._mode_c_orderbook_depth(markets)

        detections = agent.get_and_clear_detections()
        spread_detections = [d for d in detections if d["mode"] == "SPREAD_EXPANSION"]
        assert len(spread_detections) >= 1

    event_loop.run_until_complete(_test())


def test_orderbook_liquidity_vacuum(db, config, event_loop):
    """Very thin depth should trigger LIQUIDITY_VACUUM detection."""
    from sibyl.agents.intelligence.market_intelligence import MarketIntelligenceAgent

    async def _test():
        agent = MarketIntelligenceAgent(db=db, config=config)
        agent._surv = {
            "spread_expansion_threshold": 0.04,
            "thin_market_depth_threshold": 500,
            "wall_size_multiplier": 8.0,
        }

        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-THIN", "kalshi", "Thin Market", "other", "active"),
        )

        # Insert orderbook with very thin depth (total = 20)
        bids = json.dumps([{"price": 0.49, "size": 10}])
        asks = json.dumps([{"price": 0.51, "size": 10}])
        await db.execute(
            "INSERT INTO orderbook (market_id, bids, asks) VALUES (?, ?, ?)",
            ("MKT-THIN", bids, asks),
        )
        await db.commit()

        markets = await db.fetchall("SELECT id, platform, category FROM markets WHERE status = 'active'")
        await agent._mode_c_orderbook_depth(markets)

        detections = agent.get_and_clear_detections()
        vacuum_detections = [d for d in detections if d["mode"] == "LIQUIDITY_VACUUM"]
        assert len(vacuum_detections) >= 1
        assert vacuum_detections[0]["details"]["total_depth"] == 20

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Signal Generator Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_signal_type_classification():
    """Verify correct mapping from detection modes to signal types."""
    from sibyl.agents.intelligence.signal_generator import SignalGenerator

    # Single modes
    assert SignalGenerator._classify_signal_type(["WHALE"], False) == "MOMENTUM"
    assert SignalGenerator._classify_signal_type(["VOLUME_SURGE"], False) == "VOLUME_SURGE"
    assert SignalGenerator._classify_signal_type(["LIQUIDITY_VACUUM"], False) == "LIQUIDITY_VACUUM"
    assert SignalGenerator._classify_signal_type(["WALL_APPEARED"], False) == "MEAN_REVERSION"

    # Composite (multi-mode)
    assert SignalGenerator._classify_signal_type(["WHALE", "VOLUME_SURGE"], True) == "COMPOSITE_HIGH_CONVICTION"


def test_signal_generator_creates_signal(db, config, event_loop):
    """Injecting a detection should result in a signal being written to the DB."""
    from sibyl.agents.intelligence.signal_generator import SignalGenerator

    async def _test():
        sig_gen = SignalGenerator(db=db, config=config, intel_agent=None)
        sig_gen._composite_config = {"high_conviction_modes_required": 2}

        # Insert a market and a price for EV calculation
        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-SIG", "kalshi", "Signal Test", "crypto", "active"),
        )
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES (?, ?)",
            ("MKT-SIG", 0.40),
        )
        await db.commit()

        # Inject a whale detection
        sig_gen.inject_detections([{
            "market_id": "MKT-SIG",
            "mode": "WHALE",
            "timestamp": "2026-01-01T00:00:00Z",
            "details": {"side": "YES", "size": 500, "multiplier": 5.0},
        }])

        await sig_gen.run_cycle()

        # Check that a signal was created
        signal = await db.fetchone(
            "SELECT * FROM signals WHERE market_id = 'MKT-SIG'"
        )
        assert signal is not None
        assert signal["signal_type"] == "MOMENTUM"
        assert signal["status"] == "PENDING"
        assert float(signal["confidence"]) == pytest.approx(0.55, abs=0.01)

    event_loop.run_until_complete(_test())


def test_composite_signal_boosted_confidence(db, config, event_loop):
    """Multiple modes on the same market should produce a composite with higher confidence."""
    from sibyl.agents.intelligence.signal_generator import SignalGenerator

    async def _test():
        sig_gen = SignalGenerator(db=db, config=config, intel_agent=None)
        sig_gen._composite_config = {"high_conviction_modes_required": 2}

        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-COMP", "kalshi", "Composite Test", "economics", "active"),
        )
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES (?, ?)",
            ("MKT-COMP", 0.35),
        )
        await db.commit()

        # Inject TWO detection modes on the same market
        sig_gen.inject_detections([
            {
                "market_id": "MKT-COMP",
                "mode": "WHALE",
                "timestamp": "2026-01-01T00:00:00Z",
                "details": {"side": "YES", "size": 500, "multiplier": 5.0},
            },
            {
                "market_id": "MKT-COMP",
                "mode": "VOLUME_SURGE",
                "timestamp": "2026-01-01T00:00:00Z",
                "details": {"zscore": 3.5},
            },
        ])

        await sig_gen.run_cycle()

        signal = await db.fetchone(
            "SELECT * FROM signals WHERE market_id = 'MKT-COMP'"
        )
        assert signal is not None
        assert signal["signal_type"] == "COMPOSITE_HIGH_CONVICTION"
        # Composite confidence should be higher than single-mode (0.55)
        assert float(signal["confidence"]) > 0.60

    event_loop.run_until_complete(_test())


def test_ev_estimation(db, config, event_loop):
    """EV should be positive for a confident signal on a low-priced market."""
    from sibyl.agents.intelligence.signal_generator import SignalGenerator

    async def _test():
        sig_gen = SignalGenerator(db=db, config=config, intel_agent=None)

        # Market priced at 0.30 (upside = 0.70 if correct)
        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-EV", "kalshi", "EV Test", "other", "active"),
        )
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES (?, ?)",
            ("MKT-EV", 0.30),
        )
        await db.commit()

        ev = await sig_gen._estimate_ev("MKT-EV", confidence=0.70)
        # EV = (0.70 × 0.70) - (0.30 × 0.30) = 0.49 - 0.09 = 0.40
        assert ev > 0
        assert ev == pytest.approx(0.40, abs=0.01)

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Signal Router Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_route_to_sge():
    """ARBITRAGE signal with moderate confidence should route to SGE."""
    from sibyl.agents.intelligence.signal_router import SignalRouter

    router = SignalRouter.__new__(SignalRouter)
    router._sge_whitelist = {"ARBITRAGE", "MEAN_REVERSION", "LIQUIDITY_VACUUM"}
    router._ace_whitelist = {"MOMENTUM", "VOLUME_SURGE", "STALE_MARKET", "HIGH_CONVICTION_ARB"}
    router._sge_min_confidence = 0.60
    router._sge_min_ev = 0.03
    router._ace_min_confidence = 0.68
    router._ace_min_ev = 0.06

    result = router._route_signal("ARBITRAGE", confidence=0.65, ev=0.05)
    assert result == "SGE"


def test_route_to_ace():
    """MOMENTUM signal with high confidence should route to ACE."""
    from sibyl.agents.intelligence.signal_router import SignalRouter

    router = SignalRouter.__new__(SignalRouter)
    router._sge_whitelist = {"ARBITRAGE", "MEAN_REVERSION", "LIQUIDITY_VACUUM"}
    router._ace_whitelist = {"MOMENTUM", "VOLUME_SURGE", "STALE_MARKET", "HIGH_CONVICTION_ARB"}
    router._sge_min_confidence = 0.60
    router._sge_min_ev = 0.03
    router._ace_min_confidence = 0.68
    router._ace_min_ev = 0.06

    result = router._route_signal("MOMENTUM", confidence=0.72, ev=0.08)
    assert result == "ACE"


def test_route_composite_to_both():
    """COMPOSITE_HIGH_CONVICTION meeting both thresholds should route to BOTH."""
    from sibyl.agents.intelligence.signal_router import SignalRouter

    router = SignalRouter.__new__(SignalRouter)
    router._sge_whitelist = {"ARBITRAGE", "MEAN_REVERSION", "LIQUIDITY_VACUUM"}
    router._ace_whitelist = {"MOMENTUM", "VOLUME_SURGE", "STALE_MARKET", "HIGH_CONVICTION_ARB"}
    router._sge_min_confidence = 0.60
    router._sge_min_ev = 0.03
    router._ace_min_confidence = 0.68
    router._ace_min_ev = 0.06

    result = router._route_signal("COMPOSITE_HIGH_CONVICTION", confidence=0.80, ev=0.10)
    assert result == "BOTH"


def test_route_deferred_below_threshold():
    """Signal below all thresholds should be DEFERRED."""
    from sibyl.agents.intelligence.signal_router import SignalRouter

    router = SignalRouter.__new__(SignalRouter)
    router._sge_whitelist = {"ARBITRAGE", "MEAN_REVERSION", "LIQUIDITY_VACUUM"}
    router._ace_whitelist = {"MOMENTUM", "VOLUME_SURGE", "STALE_MARKET", "HIGH_CONVICTION_ARB"}
    router._sge_min_confidence = 0.60
    router._sge_min_ev = 0.03
    router._ace_min_confidence = 0.68
    router._ace_min_ev = 0.06

    result = router._route_signal("ARBITRAGE", confidence=0.50, ev=0.01)
    assert result == "DEFERRED"


def test_router_updates_database(db, config, event_loop):
    """Router should update signal status from PENDING to ROUTED in the DB."""
    from sibyl.agents.intelligence.signal_router import SignalRouter

    async def _test():
        router = SignalRouter(db=db, config=config)
        # Manually set engine configs (skip yaml loading)
        router._sge_whitelist = {"ARBITRAGE", "MEAN_REVERSION", "LIQUIDITY_VACUUM"}
        router._ace_whitelist = {"MOMENTUM", "VOLUME_SURGE", "STALE_MARKET", "HIGH_CONVICTION_ARB"}
        router._sge_min_confidence = 0.60
        router._sge_min_ev = 0.03
        router._ace_min_confidence = 0.68
        router._ace_min_ev = 0.06

        # Insert a market
        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-RT", "kalshi", "Router Test", "politics", "active"),
        )

        # Insert a PENDING signal
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, status)
               VALUES (?, ?, ?, ?, ?)""",
            ("MKT-RT", "ARBITRAGE", 0.70, 0.05, "PENDING"),
        )
        await db.commit()

        # Run the router
        await router.run_cycle()

        # Check the signal was routed to SGE
        signal = await db.fetchone("SELECT * FROM signals WHERE market_id = 'MKT-RT'")
        assert signal["status"] == "ROUTED"
        assert signal["routed_to"] == "SGE"

    event_loop.run_until_complete(_test())
