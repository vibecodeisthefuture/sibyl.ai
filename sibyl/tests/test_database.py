"""Tests for database schema creation and core operations."""

import os
import tempfile

import pytest

from sibyl.core.database import DatabaseManager

EXPECTED_TABLES = [
    "markets",
    "prices",
    "orderbook",
    "trades_log",
    "signals",
    "positions",
    "executions",
    "performance",
    "engine_state",
    "system_state",
    "whale_events",
    "market_research",
]


@pytest.fixture
async def db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_sibyl.db")
        manager = DatabaseManager(db_path)
        await manager.initialize()
        yield manager
        await manager.close()


@pytest.mark.asyncio
async def test_schema_creates_all_tables(db: DatabaseManager):
    """Verify all 12 tables are created."""
    for table in EXPECTED_TABLES:
        exists = await db.table_exists(table)
        assert exists, f"Table '{table}' was not created"


@pytest.mark.asyncio
async def test_wal_mode_enabled(db: DatabaseManager):
    """Verify WAL journal mode is active."""
    mode = await db.get_wal_mode()
    assert mode == "wal", f"Expected WAL mode, got '{mode}'"


@pytest.mark.asyncio
async def test_engine_state_seeded(db: DatabaseManager):
    """Verify SGE and ACE engine state rows are seeded."""
    rows = await db.fetchall("SELECT engine FROM engine_state ORDER BY engine")
    engines = [row[0] for row in rows]
    assert engines == ["ACE", "SGE"]


@pytest.mark.asyncio
async def test_insert_market(db: DatabaseManager):
    """Test inserting and retrieving a market."""
    await db.execute(
        "INSERT INTO markets (id, platform, title, category) VALUES (?, ?, ?, ?)",
        ("test-market-1", "polymarket", "Will it rain tomorrow?", "other"),
    )
    await db.commit()

    row = await db.fetchone("SELECT * FROM markets WHERE id = ?", ("test-market-1",))
    assert row is not None
    assert row["title"] == "Will it rain tomorrow?"
    assert row["platform"] == "polymarket"


@pytest.mark.asyncio
async def test_insert_signal(db: DatabaseManager):
    """Test inserting a signal with engine routing."""
    # Insert a market first (FK constraint)
    await db.execute(
        "INSERT INTO markets (id, platform, title) VALUES (?, ?, ?)",
        ("mkt-1", "kalshi", "Test market"),
    )
    await db.execute(
        "INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, routed_to) "
        "VALUES (?, ?, ?, ?, ?)",
        ("mkt-1", "MOMENTUM", 0.75, 0.08, "ACE"),
    )
    await db.commit()

    row = await db.fetchone("SELECT * FROM signals WHERE market_id = ?", ("mkt-1",))
    assert row is not None
    assert row["signal_type"] == "MOMENTUM"
    assert row["routed_to"] == "ACE"
    assert row["confidence"] == 0.75


@pytest.mark.asyncio
async def test_insert_position(db: DatabaseManager):
    """Test inserting a position with engine tag."""
    await db.execute(
        "INSERT INTO markets (id, platform, title) VALUES (?, ?, ?)",
        ("mkt-2", "polymarket", "Test position market"),
    )
    await db.execute(
        "INSERT INTO positions (market_id, platform, engine, side, size, entry_price) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("mkt-2", "polymarket", "SGE", "YES", 50.0, 0.45),
    )
    await db.commit()

    row = await db.fetchone("SELECT * FROM positions WHERE market_id = ?", ("mkt-2",))
    assert row is not None
    assert row["engine"] == "SGE"
    assert row["side"] == "YES"
    assert row["entry_price"] == 0.45


@pytest.mark.asyncio
async def test_foreign_key_enforcement(db: DatabaseManager):
    """Verify that FK constraints are enforced."""
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES (?, ?)",
            ("nonexistent-market", 0.50),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_signals_columns_from_agent_spec(db: DatabaseManager):
    """Verify the signals table has all columns added by the agent specs."""
    await db.execute(
        "INSERT INTO markets (id, platform, title) VALUES (?, ?, ?)",
        ("mkt-spec", "polymarket", "Spec test"),
    )
    await db.execute(
        "INSERT INTO signals ("
        "  market_id, signal_type, confidence, routing_override, "
        "  confidence_adjusted, counter_thesis, reasoning, "
        "  scout_consensus_alignment, detection_modes_triggered, "
        "  pre_entry_correlation"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "mkt-spec", "WHALE", 0.80, "ACE",
            0.85, "Market may be priced correctly", "High conviction whale...",
            "ALIGNS WITH Scout BULLISH", "WHALE,VOLUME_SURGE",
            "CLEAR",
        ),
    )
    await db.commit()

    row = await db.fetchone(
        "SELECT * FROM signals WHERE market_id = ?", ("mkt-spec",)
    )
    assert row is not None
    assert row["routing_override"] == "ACE"
    assert row["confidence_adjusted"] == 0.85
    assert row["counter_thesis"] == "Market may be priced correctly"
    assert row["pre_entry_correlation"] == "CLEAR"
