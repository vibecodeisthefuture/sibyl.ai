"""
Async SQLite database manager with schema initialization.

This module is the single source of truth for Sibyl's database schema and
provides a thin async wrapper around aiosqlite.  Every table, index, and
seed row is defined in the SCHEMA_SQL constant below.

HOW IT WORKS:
    1. DatabaseManager is instantiated with a file path (default: data/sibyl.db).
    2. Calling `await manager.initialize()` creates the DB file, enables WAL
       mode for concurrent reads, and runs the full schema (CREATE IF NOT EXISTS
       ensures it's safe to call repeatedly).
    3. All agents share a single DatabaseManager instance.  They call
       `await db.execute(sql, params)` to write data, and
       `await db.fetchone / fetchall` to read data.
    4. Always call `await db.commit()` after a batch of writes.

TABLES (12 total):
    markets         — Every prediction market we track (Polymarket + Kalshi).
    prices          — Point-in-time price snapshots for each market.
    orderbook       — L2 order book snapshots (bids/asks stored as JSON).
    trades_log      — Observed trades from the market feed.
    signals         — Generated trading signals with routing info.
    positions       — Open/closed trading positions with P&L tracking.
    executions      — Individual order fills tied to positions.
    performance     — Post-resolution outcome records for backtesting.
    engine_state    — Capital allocation state for SGE and ACE engines.
    system_state    — Key-value store for system-wide flags and alerts.
    whale_events    — Large trade detection events from Market Intelligence.
    market_research — Research packets from Breakout Scout agent.

WHY WAL MODE?
    SQLite's default journal mode blocks readers while writing.  WAL (Write-
    Ahead Logging) allows multiple agents to read the database simultaneously
    while one agent writes — critical for our multi-agent architecture.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger("sibyl.database")

# ─── Full Schema ─────────────────────────────────────────────────────────────
# This SQL runs once at startup.  "CREATE TABLE IF NOT EXISTS" means it's
# safe to call repeatedly — existing tables won't be dropped or altered.
# If you need to modify a table structure, you must write a migration.

SCHEMA_SQL = """
-- ─── Markets ─────────────────────────────────────────────────────────
-- Central registry of all prediction markets we monitor.
-- `id` is the platform's native identifier (condition_id for Polymarket,
-- ticker for Kalshi).
-- `event_id` groups related markets (e.g., same real-world event on
-- both platforms).  Auto-populated by the CrossPlatformSyncAgent.
CREATE TABLE IF NOT EXISTS markets (
    id              TEXT PRIMARY KEY,
    platform        TEXT NOT NULL CHECK (platform IN ('polymarket', 'kalshi')),
    title           TEXT NOT NULL,
    category        TEXT,
    close_date      TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    event_id        TEXT,
    event_id_confidence REAL,
    discovery_source TEXT,
    breakout_score  REAL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ─── Prices ──────────────────────────────────────────────────────────
-- Time-series of price snapshots.  Monitor agents insert a new row
-- every polling cycle (default: every 30 seconds).
-- yes_price is the probability (0.0–1.0) that the market resolves YES.
-- no_price = 1.0 - yes_price (stored for convenience).
CREATE TABLE IF NOT EXISTS prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   TEXT NOT NULL REFERENCES markets(id),
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    yes_price   REAL NOT NULL,
    no_price    REAL,
    volume_24h  REAL,
    open_interest REAL
);
-- Composite index for fast "get latest price for market X" queries.
CREATE INDEX IF NOT EXISTS idx_prices_market_ts ON prices(market_id, timestamp);

-- ─── Order Book Snapshots ────────────────────────────────────────────
-- L2 order book snapshots.  `bids` and `asks` are JSON arrays of
-- [price, quantity] pairs, e.g.: [{"price": 0.65, "size": 100}, ...]
CREATE TABLE IF NOT EXISTS orderbook (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   TEXT NOT NULL REFERENCES markets(id),
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    bids        TEXT NOT NULL,  -- JSON array of {price, size} objects
    asks        TEXT NOT NULL   -- JSON array of {price, size} objects
);
CREATE INDEX IF NOT EXISTS idx_orderbook_market_ts ON orderbook(market_id, timestamp);

-- ─── Observed Trades ─────────────────────────────────────────────────
-- Raw trades pulled from the market feed.  Used by the Market
-- Intelligence Agent for whale detection and volume anomaly analysis.
CREATE TABLE IF NOT EXISTS trades_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   TEXT NOT NULL REFERENCES markets(id),
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    side        TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    size        REAL NOT NULL,
    price       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_market_ts ON trades_log(market_id, timestamp);

-- ─── Signals ─────────────────────────────────────────────────────────
-- Trading signals generated by the Intelligence layer.
-- Each signal is scored (confidence, EV) and routed to SGE, ACE, or BOTH.
-- `detection_modes_triggered` is a comma-separated list like "WHALE,VOLUME_SURGE".
-- `pre_entry_correlation` is set by the Position Lifecycle Manager before execution.
CREATE TABLE IF NOT EXISTS signals (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id               TEXT NOT NULL REFERENCES markets(id),
    timestamp               TEXT NOT NULL DEFAULT (datetime('now')),
    signal_type             TEXT NOT NULL,
    confidence              REAL NOT NULL,
    ev_estimate             REAL,
    routed_to               TEXT CHECK (routed_to IN ('SGE', 'ACE', 'BOTH', 'DEFERRED')),
    status                  TEXT NOT NULL DEFAULT 'PENDING',
    routing_override        TEXT,
    confidence_adjusted     REAL,
    counter_thesis          TEXT,
    reasoning               TEXT,
    scout_consensus_alignment TEXT,
    detection_modes_triggered TEXT,
    pre_entry_correlation   TEXT CHECK (pre_entry_correlation IN ('CLEAR', 'FLAGGED', 'BLOCKED'))
);
CREATE INDEX IF NOT EXISTS idx_signals_market ON signals(market_id);
CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);

-- ─── Positions ───────────────────────────────────────────────────────
-- Active and historical trading positions.  Each position belongs to
-- exactly one engine (SGE or ACE) and tracks real-time P&L.
CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT NOT NULL REFERENCES markets(id),
    platform        TEXT NOT NULL CHECK (platform IN ('polymarket', 'kalshi')),
    engine          TEXT NOT NULL CHECK (engine IN ('SGE', 'ACE')),
    side            TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    size            REAL NOT NULL,
    entry_price     REAL NOT NULL,
    current_price   REAL,
    target_price    REAL,
    stop_loss       REAL,
    pnl             REAL DEFAULT 0.0,
    ev_current      REAL,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    thesis          TEXT,
    signal_id       INTEGER REFERENCES signals(id),
    opened_at       TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions_engine ON positions(engine);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_market ON positions(market_id);

-- ─── Executions ──────────────────────────────────────────────────────
-- Individual order fills.  A single position may have multiple executions
-- (e.g., partial fills, DCA entries).
CREATE TABLE IF NOT EXISTS executions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   INTEGER REFERENCES signals(id),
    position_id INTEGER REFERENCES positions(id),
    engine      TEXT NOT NULL CHECK (engine IN ('SGE', 'ACE')),
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    platform    TEXT NOT NULL CHECK (platform IN ('polymarket', 'kalshi')),
    order_id    TEXT,
    side        TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    fill_price  REAL NOT NULL,
    size        REAL NOT NULL,
    order_type  TEXT DEFAULT 'LIMIT'
);
CREATE INDEX IF NOT EXISTS idx_executions_engine ON executions(engine);

-- ─── Performance ─────────────────────────────────────────────────────
-- Post-resolution records for tracking prediction accuracy and calibration.
-- `correct` indicates whether Sibyl's prediction matched the actual outcome.
CREATE TABLE IF NOT EXISTS performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER REFERENCES signals(id),
    position_id     INTEGER REFERENCES positions(id),
    engine          TEXT NOT NULL CHECK (engine IN ('SGE', 'ACE')),
    resolved        INTEGER NOT NULL DEFAULT 0,
    correct         INTEGER,
    pnl             REAL,
    ev_estimated    REAL,
    ev_realized     REAL,
    post_mortem     TEXT,
    resolved_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_performance_engine ON performance(engine);

-- ─── Engine State ────────────────────────────────────────────────────
-- Capital allocation snapshot for each trading engine.
-- Updated by the Portfolio Allocator agent.
-- `circuit_breaker` can be 'CLEAR', 'WARNING', or 'TRIGGERED'.
CREATE TABLE IF NOT EXISTS engine_state (
    engine              TEXT PRIMARY KEY CHECK (engine IN ('SGE', 'ACE')),
    total_capital       REAL NOT NULL DEFAULT 0.0,
    deployed_capital    REAL NOT NULL DEFAULT 0.0,
    available_capital   REAL NOT NULL DEFAULT 0.0,
    exposure_pct        REAL NOT NULL DEFAULT 0.0,
    drawdown_pct        REAL NOT NULL DEFAULT 0.0,
    daily_pnl           REAL NOT NULL DEFAULT 0.0,
    circuit_breaker     TEXT NOT NULL DEFAULT 'CLEAR',
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ─── System State ────────────────────────────────────────────────────
-- Generic key-value store for system-wide flags, alerts, and metadata.
-- Example keys: "arb_divergence_MKT1_MKT2", "last_market_refresh".
CREATE TABLE IF NOT EXISTS system_state (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ─── Whale Events (Market Intelligence Mode A) ──────────────────────
-- Records of unusually large trades detected by the whale-watching
-- sub-module of the Market Intelligence Agent.
CREATE TABLE IF NOT EXISTS whale_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   TEXT NOT NULL REFERENCES markets(id),
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    platform    TEXT NOT NULL CHECK (platform IN ('polymarket', 'kalshi')),
    side        TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    size        REAL NOT NULL,
    price       REAL NOT NULL,
    threshold   REAL NOT NULL,
    wallet_id   TEXT,
    severity    TEXT DEFAULT 'NORMAL'
);
CREATE INDEX IF NOT EXISTS idx_whale_market ON whale_events(market_id);

-- ─── Market Research (Breakout Scout) ────────────────────────────────
-- Sentiment analysis and research synthesis generated by the Breakout
-- Scout Agent.  Used to enrich signals with external context.
CREATE TABLE IF NOT EXISTS market_research (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id           TEXT NOT NULL REFERENCES markets(id),
    timestamp           TEXT NOT NULL DEFAULT (datetime('now')),
    sentiment_score     REAL,
    sentiment_label     TEXT CHECK (sentiment_label IN (
        'BULLISH', 'BEARISH', 'CONTESTED', 'NEUTRAL'
    )),
    confidence          REAL,
    source_breakdown    TEXT,  -- JSON: {"newsapi": 0.7, "reddit": 0.5, ...}
    key_yes_args        TEXT,  -- JSON array: ["Strong polling data", ...]
    key_no_args         TEXT,  -- JSON array: ["Historical precedent against", ...]
    notable_dissent     TEXT,
    synthesis           TEXT,
    freshness_score     REAL NOT NULL DEFAULT 1.0,
    routing_priority    TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_market ON market_research(market_id);
CREATE INDEX IF NOT EXISTS idx_research_freshness ON market_research(freshness_score);

-- ─── Seed engine state rows ──────────────────────────────────────────
-- Pre-populate the two engine rows so agents can UPDATE without INSERT checks.
INSERT OR IGNORE INTO engine_state (engine) VALUES ('SGE');
INSERT OR IGNORE INTO engine_state (engine) VALUES ('ACE');
"""


class DatabaseManager:
    """Async SQLite database manager with WAL mode and schema management.

    This is the main database interface used by all Sibyl agents.

    Usage:
        db = DatabaseManager("data/sibyl.db")
        await db.initialize()            # Creates DB + runs schema
        await db.execute("INSERT ...", (params,))
        await db.commit()                # IMPORTANT: must commit after writes
        row = await db.fetchone("SELECT ...", (params,))
        rows = await db.fetchall("SELECT ...", (params,))
        await db.close()                 # Call on shutdown

    Thread Safety:
        This class is NOT thread-safe.  All calls must happen within the
        same asyncio event loop.  The agents achieve this naturally because
        they all run as asyncio tasks on the same loop (see __main__.py).
    """

    def __init__(self, db_path: str = "data/sibyl.db") -> None:
        """Initialize the manager with a database file path.

        Args:
            db_path: Path to the SQLite database file.  Parent directories
                     will be created automatically if they don't exist.
        """
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create database file, enable WAL mode, and run the full schema.

        This method is safe to call multiple times — CREATE IF NOT EXISTS
        means existing tables won't be dropped.

        Steps:
            1. Create parent directories for the DB file.
            2. Open an aiosqlite connection.
            3. Set row_factory to aiosqlite.Row so results are dict-like.
            4. Enable WAL mode for concurrent reads across agents.
            5. Set busy_timeout to 5 seconds (wait instead of erroring on lock).
            6. Enable foreign key enforcement (SQLite disables this by default!).
            7. Run the full schema SQL.
        """
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.db_path)

        # aiosqlite.Row lets us access columns by name: row["column_name"]
        self._connection.row_factory = aiosqlite.Row

        # Enable WAL mode for concurrent reads (critical for multi-agent system)
        await self._connection.execute("PRAGMA journal_mode=WAL")
        # Wait up to 5s if another writer holds the lock, instead of erroring
        await self._connection.execute("PRAGMA busy_timeout=5000")
        # SQLite doesn't enforce foreign keys by default — we must opt in!
        await self._connection.execute("PRAGMA foreign_keys=ON")

        # Run full schema (CREATE IF NOT EXISTS = safe to repeat)
        await self._connection.executescript(SCHEMA_SQL)
        await self._connection.commit()
        logger.info("Schema initialized (%d tables)", 12)

    @property
    def connection(self) -> aiosqlite.Connection:
        """Get the active database connection (raises if not initialized)."""
        if self._connection is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._connection

    async def execute(
        self, sql: str, params: tuple | dict | None = None
    ) -> aiosqlite.Cursor:
        """Execute a single SQL statement.  Don't forget to commit() after writes!"""
        return await self.connection.execute(sql, params or ())

    async def executemany(
        self, sql: str, params_seq: list[tuple | dict]
    ) -> aiosqlite.Cursor:
        """Execute the same SQL statement with multiple parameter sets (batch insert)."""
        return await self.connection.executemany(sql, params_seq)

    async def fetchone(
        self, sql: str, params: tuple | dict | None = None
    ) -> aiosqlite.Row | None:
        """Execute a query and return the first row, or None if no results."""
        cursor = await self.execute(sql, params)
        return await cursor.fetchone()

    async def fetchall(
        self, sql: str, params: tuple | dict | None = None
    ) -> list[aiosqlite.Row]:
        """Execute a query and return all matching rows as a list."""
        cursor = await self.execute(sql, params)
        return await cursor.fetchall()

    async def commit(self) -> None:
        """Commit the current transaction.  MUST be called after write operations."""
        await self.connection.commit()

    async def close(self) -> None:
        """Close the database connection.  Call this during shutdown."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed.")

    async def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database (useful for testing)."""
        row = await self.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return row is not None

    async def get_wal_mode(self) -> str:
        """Return the current journal mode (should be 'wal' after init)."""
        row = await self.fetchone("PRAGMA journal_mode")
        return row[0] if row else "unknown"
