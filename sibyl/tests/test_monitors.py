"""Tests for Monitor Agents and Cross-Platform Sync Agent."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from sibyl.core.database import DatabaseManager


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_sibyl.db")
        manager = DatabaseManager(db_path)
        await manager.initialize()
        yield manager
        await manager.close()


@pytest.fixture
def system_config():
    """Minimal system config for agent tests."""
    return {
        "system": {"mode": "paper", "log_level": "DEBUG"},
        "platforms": {
            "polymarket": {"base_url": "https://clob.polymarket.com", "rate_limit_per_second": 100},
            "kalshi": {"base_url": "https://api.elections.kalshi.com/trade-api/v2", "rate_limit_per_second": 100},
        },
        "polling": {
            "price_snapshot_interval_seconds": 30,
            "sync_interval_seconds": 300,
        },
        "cross_platform": {
            "similarity_threshold": 0.55,
            "price_divergence_alert_pct": 0.05,
        },
    }


# ── Polymarket Monitor Tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_polymarket_monitor_start(db, system_config):
    """PolymarketMonitorAgent should initialize without errors."""
    from sibyl.agents.monitors.polymarket_monitor import PolymarketMonitorAgent

    agent = PolymarketMonitorAgent(db=db, config=system_config)
    await agent.start()
    assert agent._client is not None
    await agent.stop()


@pytest.mark.asyncio
async def test_polymarket_monitor_categorize():
    """Market categorization should correctly identify categories."""
    from sibyl.agents.monitors.polymarket_monitor import PolymarketMonitorAgent

    assert PolymarketMonitorAgent._categorize({"question": "Will Biden win the election?", "tags": []}) == "politics"
    assert PolymarketMonitorAgent._categorize({"question": "Will Bitcoin exceed 100k?", "tags": ["crypto"]}) == "crypto"
    assert PolymarketMonitorAgent._categorize({"question": "NFL Super Bowl winner?", "tags": []}) == "sports"
    assert PolymarketMonitorAgent._categorize({"question": "Fed interest rate cut?", "tags": []}) == "economics"
    assert PolymarketMonitorAgent._categorize({"question": "Something random", "tags": []}) == "other"


@pytest.mark.asyncio
async def test_polymarket_monitor_refresh_markets(db, system_config):
    """Refresh should upsert markets into the database."""
    from sibyl.agents.monitors.polymarket_monitor import PolymarketMonitorAgent

    agent = PolymarketMonitorAgent(db=db, config=system_config)
    await agent.start()

    # Mock the client's get_markets method
    agent._client.get_markets = AsyncMock(return_value=[
        {
            "condition_id": "pm-test-1",
            "question": "Will it rain tomorrow?",
            "tags": ["weather"],
            "end_date_iso": "2026-04-01T00:00:00Z",
            "tokens": [{"token_id": "tok1"}],
        }
    ])

    await agent._refresh_markets()

    row = await db.fetchone("SELECT * FROM markets WHERE id = 'pm-test-1'")
    assert row is not None
    assert row["platform"] == "polymarket"
    assert row["title"] == "Will it rain tomorrow?"

    await agent.stop()


# ── Kalshi Monitor Tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kalshi_monitor_start(db, system_config):
    """KalshiMonitorAgent should initialize (public mode when no keys)."""
    from sibyl.agents.monitors.kalshi_monitor import KalshiMonitorAgent

    with patch.dict(os.environ, {"KALSHI_KEY_ID": "", "KALSHI_PRIVATE_KEY_PATH": ""}):
        agent = KalshiMonitorAgent(db=db, config=system_config)
        await agent.start()
        assert agent._client is not None
        assert agent._client.is_authenticated is False
        await agent.stop()


@pytest.mark.asyncio
async def test_kalshi_monitor_categorize():
    """Event categorization should work correctly."""
    from sibyl.agents.monitors.kalshi_monitor import KalshiMonitorAgent

    assert KalshiMonitorAgent._categorize_event({"category": "politics", "title": ""}) == "politics"
    assert KalshiMonitorAgent._categorize_event({"category": "", "title": "Bitcoin price prediction"}) == "crypto"
    assert KalshiMonitorAgent._categorize_event({"category": "", "title": "Fed interest rate decision"}) == "economics"
    assert KalshiMonitorAgent._categorize_event({"category": "", "title": "Random event"}) == "other"


@pytest.mark.asyncio
async def test_kalshi_monitor_refresh_markets(db, system_config):
    """Refresh should upsert Kalshi events/markets into database."""
    from sibyl.agents.monitors.kalshi_monitor import KalshiMonitorAgent

    with patch.dict(os.environ, {"KALSHI_KEY_ID": "", "KALSHI_PRIVATE_KEY_PATH": ""}):
        agent = KalshiMonitorAgent(db=db, config=system_config)
        await agent.start()

        # Mock the client's get_events method
        agent._client.get_events = AsyncMock(return_value={
            "events": [{
                "event_ticker": "FED-RATE-MAR",
                "title": "Fed March Rate Decision",
                "category": "economics",
                "markets": [{
                    "ticker": "FED-RATE-MAR-25BP",
                    "title": "Will the Fed cut rates by 25bps?",
                    "status": "open",
                    "close_time": "2026-03-20T00:00:00Z",
                    "yes_ask": 65,
                    "volume": 5000,
                    "open_interest": 12000,
                }],
            }],
            "cursor": None,
        })

        await agent._refresh_markets()

        row = await db.fetchone("SELECT * FROM markets WHERE id = 'FED-RATE-MAR-25BP'")
        assert row is not None
        assert row["platform"] == "kalshi"
        assert row["event_id"] == "FED-RATE-MAR"
        assert row["category"] == "economics"

        await agent.stop()


# ── Cross-Platform Sync Tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_agent_start(db, system_config):
    """CrossPlatformSyncAgent should initialize without errors."""
    from sibyl.agents.monitors.sync_agent import CrossPlatformSyncAgent

    agent = CrossPlatformSyncAgent(db=db, config=system_config)
    await agent.start()
    await agent.stop()


@pytest.mark.asyncio
async def test_sync_agent_title_similarity():
    """Similarity matching should score identical titles high."""
    from sibyl.agents.monitors.sync_agent import CrossPlatformSyncAgent
    import types

    agent = CrossPlatformSyncAgent.__new__(CrossPlatformSyncAgent)

    pm = {"title": "Will the Fed cut rates in March 2026?", "category": "economics", "close_date": "2026-03-20"}
    km = {"title": "Fed March 2026 rate cut?", "category": "economics", "close_date": "2026-03-20"}

    score = agent._compute_similarity(pm, km)
    assert score > 0.5, f"Expected > 0.5 for similar titles, got {score}"


@pytest.mark.asyncio
async def test_sync_agent_divergence_detection(db, system_config):
    """Sync agent should detect when prices diverge between platforms."""
    from sibyl.agents.monitors.sync_agent import CrossPlatformSyncAgent

    agent = CrossPlatformSyncAgent(db=db, config=system_config)
    await agent.start()

    # Insert two matched markets with divergent prices
    await db.execute(
        "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
        ("pm-fed-1", "polymarket", "Fed rate cut March 2026?", "economics", "active"),
    )
    await db.execute(
        "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
        ("kal-fed-1", "kalshi", "Fed rate cut March 2026?", "economics", "active"),
    )
    await db.execute(
        "INSERT INTO prices (market_id, yes_price) VALUES (?, ?)", ("pm-fed-1", 0.70)
    )
    await db.execute(
        "INSERT INTO prices (market_id, yes_price) VALUES (?, ?)", ("kal-fed-1", 0.58)
    )
    await db.commit()

    await agent.run_cycle()

    # Should have found a divergence (0.12 > 0.05 threshold)
    row = await db.fetchone(
        "SELECT * FROM system_state WHERE key LIKE 'arb_divergence_%'"
    )
    assert row is not None, "Divergence alert should have been written"
    assert "spread=" in row["value"]

    await agent.stop()


@pytest.mark.asyncio
async def test_sync_agent_event_id_tagging(db, system_config):
    """Sync agent should auto-tag matched markets with event_id."""
    from sibyl.agents.monitors.sync_agent import CrossPlatformSyncAgent

    agent = CrossPlatformSyncAgent(db=db, config=system_config)
    await agent.start()

    await db.execute(
        "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
        ("pm-btc-1", "polymarket", "Bitcoin above 100K by June?", "crypto", "active"),
    )
    await db.execute(
        "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
        ("kal-btc-1", "kalshi", "Bitcoin above 100K by June?", "crypto", "active"),
    )
    await db.execute("INSERT INTO prices (market_id, yes_price) VALUES (?, ?)", ("pm-btc-1", 0.45))
    await db.execute("INSERT INTO prices (market_id, yes_price) VALUES (?, ?)", ("kal-btc-1", 0.45))
    await db.commit()

    await agent.run_cycle()

    row = await db.fetchone("SELECT event_id FROM markets WHERE id = 'pm-btc-1'")
    assert row["event_id"] is not None, "event_id should have been auto-tagged"

    await agent.stop()
