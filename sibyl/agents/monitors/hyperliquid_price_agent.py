"""
Hyperliquid Price Agent — Persistent multi-tier crypto data streaming.

Sprint 21: Continuously polls the Hyperliquid Info API and writes real-time
price, order book, funding, and volatility data for BTC, ETH, SOL, and XRP.

PURPOSE:
    Provides comprehensive real-time crypto market data that feeds:
      1. CryptoPipeline bracket model — spot price, vol, book imbalance, funding
      2. PositionLifecycleManager — sub-5s stop-loss price decisions
      3. EV Monitor — accurate mark-to-market for open positions

DATA FLOW:
    Hyperliquid Info API  →  HyperliquidClient  →  HyperliquidPriceAgent  →  SQLite
      (REST polling)         (HTTP layer)          (multi-tier loop)        (6 tables)

POLLING TIERS (Sprint 21 Phase 2):
    Tier 1 — Every 1s:   allMids              → crypto_spot_prices      (~120 wt/min)
    Tier 2 — Every 5s:   l2Book × 4 coins     → crypto_order_book      (~96 wt/min)
    Tier 3 — Every 30s:  metaAndAssetCtxs      → crypto_spot_prices     (~1 wt/min)
    Tier 4 — Every 60s:  predictedFundings     → crypto_funding         (~2 wt/min)
                          1m candles × 4 coins  → crypto_micro_candles   (~16 wt/min)
    Tier 5 — Every 300s: 1h candles × 4 coins  → crypto_volatility      (~1 wt/min)
                          fundingHistory × 4    → crypto_funding         (~1 wt/min)
    ─────────────────────────────────────────────────────────────────────
    TOTAL:                                                               ~237 wt/min
                                                                         (20% of 1200)

RELATIONSHIP TO KalshiMonitorAgent:
    KalshiMonitorAgent writes Kalshi market prices (YES/NO contract prices).
    This agent writes crypto SPOT data (actual BTC/ETH/SOL/XRP market data).
    Both feed into the bracket model: spot price determines probability,
    contract price determines edge, book depth and funding inform confidence.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.hyperliquid_price")

# Map Hyperliquid symbols to CoinGecko IDs (must match crypto_pipeline.py)
HL_TO_CG = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
}


class HyperliquidPriceAgent(BaseAgent):
    """Streams real-time crypto market data from Hyperliquid into SQLite.

    Six polling tiers covering spot prices, order books, funding rates,
    micro-candles, realized volatility, and funding history.

    All data is written to dedicated tables with indexed lookups so the
    crypto pipeline and position lifecycle manager can read the latest
    values with zero API calls of their own.
    """

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="hyperliquid_price", db=db, config=config)
        self._hl_client = None
        self._realized_vol: dict[str, float] = {}  # coin → annualized daily vol

        # Read config overrides
        hl_cfg = config.get("hyperliquid", {})
        self._poll_interval: float = hl_cfg.get("poll_interval_seconds", 1.0)
        self._rich_data_interval: int = hl_cfg.get("rich_data_interval_seconds", 30)
        self._vol_interval: int = hl_cfg.get("volatility_interval_seconds", 300)
        self._book_interval: int = hl_cfg.get("order_book_interval_seconds", 5)
        self._funding_interval: int = hl_cfg.get("funding_interval_seconds", 60)
        self._micro_candle_interval: int = hl_cfg.get("micro_candle_interval_seconds", 60)
        self._funding_history_interval: int = hl_cfg.get("funding_history_interval_seconds", 300)

        # Tier timestamps
        self._last_rich_fetch: float = 0.0
        self._last_vol_fetch: float = 0.0
        self._last_book_fetch: float = 0.0
        self._last_funding_fetch: float = 0.0
        self._last_micro_candle_fetch: float = 0.0
        self._last_funding_history_fetch: float = 0.0

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    async def start(self) -> None:
        """Initialize HyperliquidClient and ensure all DB tables exist."""
        from sibyl.clients.hyperliquid_client import HyperliquidClient

        self._hl_client = HyperliquidClient()
        if not self._hl_client.initialize():
            raise RuntimeError("Failed to initialize HyperliquidClient")

        await self._create_tables()
        await self.db.commit()

        self.logger.info(
            "HyperliquidPriceAgent initialized — 6 tiers "
            "(mids=%0.fs, book=%ds, rich=%ds, funding=%ds, micro=%ds, vol=%ds)",
            self._poll_interval, self._book_interval, self._rich_data_interval,
            self._funding_interval, self._micro_candle_interval, self._vol_interval,
        )

    async def _create_tables(self) -> None:
        """Create all 5 Hyperliquid data tables."""

        # ── Table 1: crypto_spot_prices (Tier 1 + Tier 3) ─────────────
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS crypto_spot_prices (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                coin         TEXT NOT NULL,
                cg_id        TEXT NOT NULL,
                mid_price    REAL NOT NULL,
                mark_price   REAL,
                oracle_price REAL,
                funding_rate REAL,
                open_interest REAL,
                day_volume   REAL,
                prev_day_price REAL,
                source       TEXT NOT NULL DEFAULT 'hyperliquid',
                timestamp    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_crypto_spot_coin_ts
            ON crypto_spot_prices (coin, timestamp DESC)
        """)

        # ── Table 2: crypto_volatility (Tier 5) ───────────────────────
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS crypto_volatility (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                coin         TEXT NOT NULL,
                cg_id        TEXT NOT NULL,
                daily_vol    REAL NOT NULL,
                candle_count INTEGER,
                interval     TEXT NOT NULL DEFAULT '1h',
                timestamp    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_crypto_vol_coin_ts
            ON crypto_volatility (coin, timestamp DESC)
        """)

        # ── Table 3: crypto_order_book (Tier 2) ───────────────────────
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS crypto_order_book (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                coin         TEXT NOT NULL,
                best_bid     REAL NOT NULL,
                best_ask     REAL NOT NULL,
                spread_bps   REAL,
                mid          REAL,
                bid_depth_usd REAL,
                ask_depth_usd REAL,
                imbalance    REAL,
                bid_wall_count INTEGER DEFAULT 0,
                ask_wall_count INTEGER DEFAULT 0,
                bid_wall_prices TEXT,
                ask_wall_prices TEXT,
                timestamp    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_crypto_book_coin_ts
            ON crypto_order_book (coin, timestamp DESC)
        """)

        # ── Table 4: crypto_funding (Tier 4 + Tier 5) ─────────────────
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS crypto_funding (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                coin         TEXT NOT NULL,
                source_type  TEXT NOT NULL,
                hl_rate      REAL,
                binance_rate REAL,
                bybit_rate   REAL,
                premium      REAL,
                funding_time INTEGER,
                timestamp    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_crypto_funding_coin_ts
            ON crypto_funding (coin, timestamp DESC)
        """)

        # ── Table 5: crypto_micro_candles (Tier 4) ────────────────────
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS crypto_micro_candles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                coin         TEXT NOT NULL,
                interval     TEXT NOT NULL DEFAULT '1m',
                open         REAL NOT NULL,
                high         REAL NOT NULL,
                low          REAL NOT NULL,
                close        REAL NOT NULL,
                volume       REAL,
                num_trades   INTEGER,
                buy_pressure REAL,
                open_time    INTEGER,
                close_time   INTEGER,
                timestamp    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_crypto_micro_coin_ts
            ON crypto_micro_candles (coin, open_time DESC)
        """)

    # ── Main run cycle ────────────────────────────────────────────────

    async def run_cycle(self) -> None:
        """One polling cycle: dispatch work based on elapsed timers."""
        if not self._hl_client:
            return

        now = time.time()

        # Tier 5: Volatility + Funding History (every 300s)
        if now - self._last_vol_fetch >= self._vol_interval:
            await self._compute_volatility()
            await self._fetch_funding_history()
            self._last_vol_fetch = now
            self._last_funding_history_fetch = now

        # Tier 4: Predicted Fundings + Micro-candles (every 60s)
        if now - self._last_funding_fetch >= self._funding_interval:
            await self._fetch_predicted_fundings()
            self._last_funding_fetch = now

        if now - self._last_micro_candle_fetch >= self._micro_candle_interval:
            await self._fetch_micro_candles()
            self._last_micro_candle_fetch = now

        # Tier 3: Rich data (every 30s)
        if now - self._last_rich_fetch >= self._rich_data_interval:
            await self._fetch_rich_data()
            self._last_rich_fetch = now
        else:
            # Tier 1: Basic mids (every 1s — default)
            await self._fetch_mids()

        # Tier 2: Order book (every 5s)
        if now - self._last_book_fetch >= self._book_interval:
            await self._fetch_order_books()
            self._last_book_fetch = now

    # ── Tier 1: allMids (1s) ─────────────────────────────────────────

    async def _fetch_mids(self) -> None:
        """Fetch allMids and write to crypto_spot_prices."""
        mids = await self._hl_client.get_all_mids()
        if not mids:
            return

        for coin, price in mids.items():
            cg_id = HL_TO_CG.get(coin, coin.lower())
            await self.db.execute(
                """INSERT INTO crypto_spot_prices
                   (coin, cg_id, mid_price, source)
                   VALUES (?, ?, ?, 'hl_allMids')""",
                (coin, cg_id, price),
            )
        await self.db.commit()

    # ── Tier 2: L2 Order Book (5s) ───────────────────────────────────

    async def _fetch_order_books(self) -> None:
        """Fetch L2 order book for each target coin."""
        import json as _json

        for coin in HL_TO_CG:
            book = await self._hl_client.get_l2_book(coin)
            if not book:
                continue

            bid_wall_prices = _json.dumps(
                [w["price"] for w in book.get("bid_walls", [])]
            ) if book.get("bid_walls") else None
            ask_wall_prices = _json.dumps(
                [w["price"] for w in book.get("ask_walls", [])]
            ) if book.get("ask_walls") else None

            await self.db.execute(
                """INSERT INTO crypto_order_book
                   (coin, best_bid, best_ask, spread_bps, mid,
                    bid_depth_usd, ask_depth_usd, imbalance,
                    bid_wall_count, ask_wall_count,
                    bid_wall_prices, ask_wall_prices)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    coin,
                    book["best_bid"], book["best_ask"],
                    book["spread_bps"], book["mid"],
                    book["bid_depth_usd"], book["ask_depth_usd"],
                    book["imbalance"],
                    len(book.get("bid_walls", [])),
                    len(book.get("ask_walls", [])),
                    bid_wall_prices, ask_wall_prices,
                ),
            )

        await self.db.commit()

    # ── Tier 3: Rich data / metaAndAssetCtxs (30s) ───────────────────

    async def _fetch_rich_data(self) -> None:
        """Fetch metaAndAssetCtxs and write full context to crypto_spot_prices."""
        data = await self._hl_client.get_asset_contexts()
        if not data:
            await self._fetch_mids()
            return

        for coin, ctx in data.items():
            cg_id = HL_TO_CG.get(coin, coin.lower())
            await self.db.execute(
                """INSERT INTO crypto_spot_prices
                   (coin, cg_id, mid_price, mark_price, oracle_price,
                    funding_rate, open_interest, day_volume, prev_day_price, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'hl_assetCtxs')""",
                (
                    coin, cg_id,
                    ctx.get("mid_price", 0),
                    ctx.get("mark_price"),
                    ctx.get("oracle_price"),
                    ctx.get("funding_rate"),
                    ctx.get("open_interest"),
                    ctx.get("day_volume"),
                    ctx.get("prev_day_price"),
                ),
            )
        await self.db.commit()

        self.logger.debug(
            "Rich data: %s",
            {c: f"${d.get('mid_price', 0):,.2f}" for c, d in data.items()},
        )

    # ── Tier 4: Predicted Fundings (60s) ─────────────────────────────

    async def _fetch_predicted_fundings(self) -> None:
        """Fetch cross-exchange predicted funding rates."""
        data = await self._hl_client.get_predicted_fundings()
        if not data:
            return

        for coin, rates in data.items():
            await self.db.execute(
                """INSERT INTO crypto_funding
                   (coin, source_type, hl_rate, binance_rate, bybit_rate)
                   VALUES (?, 'predicted', ?, ?, ?)""",
                (
                    coin,
                    rates.get("hl_rate", 0),
                    rates.get("binance_rate", 0),
                    rates.get("bybit_rate", 0),
                ),
            )
        await self.db.commit()

        self.logger.debug(
            "Predicted funding: %s",
            {c: f"HL={d.get('hl_rate', 0):.4%}" for c, d in data.items()},
        )

    # ── Tier 4: Micro-candles / 1m (60s) ────────────────────────────

    async def _fetch_micro_candles(self) -> None:
        """Fetch 1-minute candles for last 15 minutes per coin."""
        for coin in HL_TO_CG:
            candles = await self._hl_client.get_recent_trades(coin)
            if not candles:
                continue

            for c in candles:
                await self.db.execute(
                    """INSERT OR IGNORE INTO crypto_micro_candles
                       (coin, interval, open, high, low, close, volume,
                        num_trades, buy_pressure, open_time, close_time)
                       VALUES (?, '1m', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        coin,
                        c["open"], c["high"], c["low"], c["close"],
                        c["volume"], c["num_trades"],
                        c.get("buy_pressure", 0.5),
                        c["open_time"], c["close_time"],
                    ),
                )

        await self.db.commit()

    # ── Tier 5: Realized volatility / 1h candles (300s) ──────────────

    async def _compute_volatility(self) -> None:
        """Fetch 1h candles and compute realized volatility per coin."""
        if not self._hl_client:
            return

        for coin in HL_TO_CG:
            candles = await self._hl_client.get_candles(coin=coin, interval="1h")
            if not candles or len(candles) < 5:
                continue

            vol = self._hl_client.compute_realized_volatility(candles, "1h")
            cg_id = HL_TO_CG[coin]
            self._realized_vol[coin] = vol

            await self.db.execute(
                """INSERT INTO crypto_volatility
                   (coin, cg_id, daily_vol, candle_count, interval)
                   VALUES (?, ?, ?, ?, '1h')""",
                (coin, cg_id, vol, len(candles)),
            )

        await self.db.commit()
        if self._realized_vol:
            self.logger.info(
                "Realized vol: %s",
                {c: f"{v:.2%}" for c, v in self._realized_vol.items()},
            )

    # ── Tier 5: Funding history (300s) ───────────────────────────────

    async def _fetch_funding_history(self) -> None:
        """Fetch 24h funding history per coin for trend analysis."""
        if not self._hl_client:
            return

        for coin in HL_TO_CG:
            history = await self._hl_client.get_funding_history(coin)
            if not history:
                continue

            for entry in history:
                await self.db.execute(
                    """INSERT INTO crypto_funding
                       (coin, source_type, hl_rate, premium, funding_time)
                       VALUES (?, 'historical', ?, ?, ?)""",
                    (
                        coin,
                        entry.get("funding_rate", 0),
                        entry.get("premium", 0),
                        entry.get("timestamp", 0),
                    ),
                )

        await self.db.commit()

    # ── Lifecycle ────────────────────────────────────────────────────

    async def stop(self) -> None:
        """Shutdown HyperliquidClient."""
        if self._hl_client:
            await self._hl_client.close()
            self._hl_client = None
        self.logger.info("HyperliquidPriceAgent stopped")

    # ── Public API for other agents ──────────────────────────────────

    def get_realized_vol(self, coin: str) -> float | None:
        """Get the latest realized volatility for a coin."""
        if coin.upper() in self._realized_vol:
            return self._realized_vol[coin.upper()]
        for hl, cg in HL_TO_CG.items():
            if cg == coin.lower():
                return self._realized_vol.get(hl)
        return None

    def get_cached_price(self, coin: str) -> float | None:
        """Get the latest cached price from HyperliquidClient."""
        if self._hl_client:
            return self._hl_client.get_cached_price(coin)
        return None
