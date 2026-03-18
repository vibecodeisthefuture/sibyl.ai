"""
Polymarket Monitor Agent — ingests market data from Polymarket's public APIs.

PURPOSE:
    Polymarket is READ-ONLY for Sibyl due to US geo-restrictions.  This agent
    continuously polls Polymarket's APIs and writes market data into SQLite
    so that other agents (Intelligence, Sync) can analyze it.

DATA FLOW:
    Polymarket APIs  →  PolymarketClient  →  PolymarketMonitorAgent  →  SQLite
         (Gamma + CLOB)     (HTTP layer)     (polling + DB writes)    (database.py)

WHAT THIS AGENT WRITES TO THE DATABASE:
    - markets table:    Market metadata (title, category, close date, status)
    - prices table:     YES/NO price snapshots (every 30s)
    - orderbook table:  L2 order book snapshots as JSON (every 30s)
    - trades_log table: Recent trades (every 60s)

POLLING CADENCES (at default 30s poll_interval):
    - Market list refresh: every 10th cycle ≈ 5 minutes
    - Price snapshots:     every cycle ≈ 30 seconds
    - Orderbook snapshots: every cycle ≈ 30 seconds
    - Trade feed:          every 2nd cycle ≈ 60 seconds

TOKEN ID RESOLUTION:
    Polymarket uses "token IDs" for pricing — each market has YES and NO tokens.
    This agent extracts the YES token ID from market metadata to query prices.
    The token ID is found in the `tokens` or `clob_token_ids` field of the
    market response from the Gamma API.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sibyl.clients.polymarket_client import PolymarketClient
from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.polymarket_monitor")


class PolymarketMonitorAgent(BaseAgent):
    """Streams Polymarket market data into SQLite.

    AGGRESSIVE POLLING (tuned to Polymarket's free-tier limits):
      Polymarket allows 900 req/10s = 90 req/s.  We use 80 req/s (90% safety).

      At a 5-second poll interval:
        - Market list refresh: every 24th cycle ≈ 2 minutes
        - Price snapshots: EVERY cycle (5s)
        - Orderbook snapshots: EVERY cycle (5s)
        - Recent trades: every 2nd cycle (10s)
    """

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="polymarket_monitor", db=db, config=config)
        self._pm_config = config.get("platforms", {}).get("polymarket", {})
        self._polling = config.get("polling", {})
        self._client: PolymarketClient | None = None
        self._tracked_markets: dict[str, dict] = {}  # id → market metadata

    @property
    def poll_interval(self) -> float:
        """Poll every 5 seconds — Polymarket's 90 req/s limit gives massive headroom."""
        return float(self._polling.get("price_snapshot_interval_seconds", 5))

    async def start(self) -> None:
        rate_limit = float(self._pm_config.get("rate_limit_per_second", 10))
        self._client = PolymarketClient(rate_limit=rate_limit)
        self.logger.info("Polymarket client initialized (rate_limit=%.0f req/s)", rate_limit)

        # Load any existing tracked markets from DB
        rows = await self.db.fetchall(
            "SELECT id, title FROM markets WHERE platform = 'polymarket' AND status = 'active'"
        )
        for row in rows:
            self._tracked_markets[row["id"]] = {"title": row["title"]}
        self.logger.info("Loaded %d tracked Polymarket markets from DB", len(self._tracked_markets))

    async def run_cycle(self) -> None:
        if not self._client:
            return

        # ── Market list refresh (every 24 cycles ≈ 2 min at 5s polling) ─
        if self._cycle_count % 24 == 0:
            await self._refresh_markets()

        if not self._tracked_markets:
            return

        # ── Price snapshots (EVERY cycle — 5s) ────────────────────────
        await self._snapshot_prices()

        # ── Orderbook snapshots (EVERY cycle — 5s) ────────────────────
        await self._snapshot_orderbooks()

        # ── Trade feed (every 2nd cycle ≈ 10s) ────────────────────────
        if self._cycle_count % 2 == 0:
            await self._fetch_trades()

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
        self.logger.info("Polymarket monitor stopped")

    # ── Internal methods ──────────────────────────────────────────────

    async def _refresh_markets(self) -> None:
        """Fetch the full market list and upsert into the markets table."""
        try:
            markets = await self._client.get_markets(limit=100, active=True)  # type: ignore[union-attr]
        except Exception:
            self.logger.exception("Failed to fetch Polymarket market list")
            return

        upserted = 0
        for m in markets:
            market_id = m.get("condition_id") or m.get("id", "")
            if not market_id:
                continue
            title = m.get("question") or m.get("title", "")
            category = self._categorize(m)
            close_date = m.get("end_date_iso") or m.get("close_time")

            await self.db.execute(
                """INSERT INTO markets (id, platform, title, category, close_date, status, updated_at)
                   VALUES (?, 'polymarket', ?, ?, ?, 'active', datetime('now'))
                   ON CONFLICT(id) DO UPDATE SET
                     title = excluded.title,
                     category = excluded.category,
                     close_date = excluded.close_date,
                     updated_at = datetime('now')
                """,
                (market_id, title, category, close_date),
            )
            self._tracked_markets[market_id] = {
                "title": title,
                "tokens": m.get("tokens", []),
                "clob_token_ids": m.get("clob_token_ids", []),
            }
            upserted += 1

        await self.db.commit()
        self.logger.info("Refreshed %d Polymarket markets", upserted)

    async def _snapshot_prices(self) -> None:
        """Fetch current prices for all tracked markets."""
        for market_id, meta in list(self._tracked_markets.items()):
            try:
                # Try to get token IDs from market metadata
                token_ids = meta.get("clob_token_ids", [])
                tokens = meta.get("tokens", [])
                if token_ids:
                    yes_token = token_ids[0] if token_ids else None
                elif tokens:
                    yes_token = tokens[0].get("token_id") if isinstance(tokens[0], dict) else tokens[0]
                else:
                    yes_token = market_id

                if not yes_token:
                    continue

                mid = await self._client.get_midpoint(yes_token)  # type: ignore[union-attr]
                if mid is not None:
                    await self.db.execute(
                        "INSERT INTO prices (market_id, yes_price, no_price) VALUES (?, ?, ?)",
                        (market_id, mid, 1.0 - mid),
                    )
            except Exception:
                self.logger.debug("Price fetch failed for %s", market_id)
                continue
        await self.db.commit()

    async def _snapshot_orderbooks(self) -> None:
        """Fetch orderbook snapshots for all tracked markets."""
        for market_id, meta in list(self._tracked_markets.items()):
            try:
                token_ids = meta.get("clob_token_ids", [])
                tokens = meta.get("tokens", [])
                if token_ids:
                    yes_token = token_ids[0]
                elif tokens:
                    yes_token = tokens[0].get("token_id") if isinstance(tokens[0], dict) else tokens[0]
                else:
                    yes_token = market_id

                book = await self._client.get_orderbook(yes_token)  # type: ignore[union-attr]
                if book:
                    await self.db.execute(
                        "INSERT INTO orderbook (market_id, bids, asks) VALUES (?, ?, ?)",
                        (market_id, json.dumps(book["bids"]), json.dumps(book["asks"])),
                    )
            except Exception:
                self.logger.debug("Orderbook fetch failed for %s", market_id)
                continue
        await self.db.commit()

    async def _fetch_trades(self) -> None:
        """Fetch recent trades and insert new ones."""
        for market_id, meta in list(self._tracked_markets.items()):
            try:
                token_ids = meta.get("clob_token_ids", [])
                tokens = meta.get("tokens", [])
                if token_ids:
                    yes_token = token_ids[0]
                elif tokens:
                    yes_token = tokens[0].get("token_id") if isinstance(tokens[0], dict) else tokens[0]
                else:
                    yes_token = market_id

                result = await self._client.get_trades(token_id=yes_token, limit=50)  # type: ignore[union-attr]
                trades = result.get("data", []) if isinstance(result, dict) else []
                for t in trades:
                    side = "YES" if t.get("side", "").upper() in ("BUY", "YES") else "NO"
                    size = float(t.get("size", 0))
                    price = float(t.get("price", 0))
                    if size > 0:
                        await self.db.execute(
                            """INSERT INTO trades_log (market_id, side, size, price)
                               VALUES (?, ?, ?, ?)""",
                            (market_id, side, size, price),
                        )
            except Exception:
                self.logger.debug("Trade fetch failed for %s", market_id)
                continue
        await self.db.commit()

    @staticmethod
    def _categorize(market_data: dict) -> str:
        """Keyword-based categorization using word-boundary matching."""
        import re

        tags = " ".join(market_data.get("tags", [])).lower()
        title = (market_data.get("question") or market_data.get("title", "")).lower()
        text = f"{tags} {title}"

        def _has(words: tuple[str, ...]) -> bool:
            return any(re.search(rf"\b{w}", text) for w in words)

        if _has(("elect", "president", "congress", "senate", "vote", "politic")):
            return "politics"
        if _has(("nfl", "nba", "mlb", "sport", "game", "match", "team")):
            return "sports"
        if _has(("bitcoin", "btc", "ethereum", "crypto", "token", "defi", "blockchain")):
            return "crypto"
        if _has(("fed ", "gdp", "inflation", "interest rate", "econom", "cpi")):
            return "economics"
        if _has(("artificial intelligence", "tech", "science", "space", "climate")):
            return "science_tech"
        return "other"
