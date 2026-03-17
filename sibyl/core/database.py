"""Async SQLite database manager with schema initialization."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger("sibyl.database")

# ─── Full Schema ─────────────────────────────────────────────────────────────
# 12 tables covering markets, prices, orderbook, trades, signals, positions,
# executions, performance, engine state, system state, whale events, and
# market research.  All position/execution/performance records are tagged
# with `engine` (SGE | ACE) for per-engine accounting.

SCHEMA_SQL = """
-- ─── Markets ─────────────────────────────────────────────────────────
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
CREATE TABLE IF NOT EXISTS prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   TEXT NOT NULL REFERENCES markets(id),
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    yes_price   REAL NOT NULL,
    no_price    REAL,
    volume_24h  REAL,
    open_interest REAL
);
CREATE INDEX IF NOT EXISTS idx_prices_market_ts ON prices(market_id, timestamp);

-- ─── Order Book Snapshots ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orderbook (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   TEXT NOT NULL REFERENCES markets(id),
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    bids        TEXT NOT NULL,  -- JSON array of [price, qty] pairs
    asks        TEXT NOT NULL   -- JSON array of [price, qty] pairs
);
CREATE INDEX IF NOT EXISTS idx_orderbook_market_ts ON orderbook(market_id, timestamp);

-- ─── Observed Trades ─────────────────────────────────────────────────
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
CREATE TABLE IF NOT EXISTS system_state (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ─── Whale Events (Market Intelligence Mode A) ──────────────────────
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
CREATE TABLE IF NOT EXISTS market_research (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id           TEXT NOT NULL REFERENCES markets(id),
    timestamp           TEXT NOT NULL DEFAULT (datetime('now')),
    sentiment_score     REAL,
    sentiment_label     TEXT CHECK (sentiment_label IN (
        'BULLISH', 'BEARISH', 'CONTESTED', 'NEUTRAL'
    )),
    confidence          REAL,
    source_breakdown    TEXT,  -- JSON
    key_yes_args        TEXT,  -- JSON array
    key_no_args         TEXT,  -- JSON array
    notable_dissent     TEXT,
    synthesis           TEXT,
    freshness_score     REAL NOT NULL DEFAULT 1.0,
    routing_priority    TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_market ON market_research(market_id);
CREATE INDEX IF NOT EXISTS idx_research_freshness ON market_research(freshness_score);

-- ─── Seed engine state rows ──────────────────────────────────────────
INSERT OR IGNORE INTO engine_state (engine) VALUES ('SGE');
INSERT OR IGNORE INTO engine_state (engine) VALUES ('ACE');
"""


class DatabaseManager:
    """Async SQLite database manager with WAL mode and schema management."""

    def __init__(self, db_path: str = "data/sibyl.db") -> None:
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create database file, enable WAL mode, and run schema."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row

        # Enable WAL mode for concurrent reads
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA busy_timeout=5000")
        await self._connection.execute("PRAGMA foreign_keys=ON")

        # Run full schema
        await self._connection.executescript(SCHEMA_SQL)
        await self._connection.commit()
        logger.info("Schema initialized (%d tables)", 12)

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._connection

    async def execute(
        self, sql: str, params: tuple | dict | None = None
    ) -> aiosqlite.Cursor:
        return await self.connection.execute(sql, params or ())

    async def executemany(
        self, sql: str, params_seq: list[tuple | dict]
    ) -> aiosqlite.Cursor:
        return await self.connection.executemany(sql, params_seq)

    async def fetchone(
        self, sql: str, params: tuple | dict | None = None
    ) -> aiosqlite.Row | None:
        cursor = await self.execute(sql, params)
        return await cursor.fetchone()

    async def fetchall(
        self, sql: str, params: tuple | dict | None = None
    ) -> list[aiosqlite.Row]:
        cursor = await self.execute(sql, params)
        return await cursor.fetchall()

    async def commit(self) -> None:
        await self.connection.commit()

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed.")

    async def table_exists(self, table_name: str) -> bool:
        row = await self.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return row is not None

    async def get_wal_mode(self) -> str:
        row = await self.fetchone("PRAGMA journal_mode")
        return row[0] if row else "unknown"
