"""Kalshi Monitor Agent — ingests market data from Kalshi's Trading API v2.

Kalshi is the PRIMARY execution platform for Sibyl.  This agent streams
market metadata, prices, orderbook snapshots, and trades into the shared
SQLite database.  It uses authenticated requests when credentials are
available.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from sibyl.clients.kalshi_client import KalshiClient
from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.kalshi_monitor")


class KalshiMonitorAgent(BaseAgent):
    """Streams Kalshi market data into SQLite.

    Polling cadences:
      - Market/event list refresh: every 10 cycles (≈5 min at 30s polling)
      - Price snapshots: every cycle (30s)
      - Orderbook snapshots: every cycle (30s)
      - Recent trades: every 2nd cycle (60s)
    """

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="kalshi_monitor", db=db, config=config)
        self._kalshi_config = config.get("platforms", {}).get("kalshi", {})
        self._polling = config.get("polling", {})
        self._client: KalshiClient | None = None
        self._tracked_markets: dict[str, dict] = {}  # ticker → market metadata

    @property
    def poll_interval(self) -> float:
        return float(self._polling.get("price_snapshot_interval_seconds", 30))

    async def start(self) -> None:
        key_id = os.environ.get("KALSHI_KEY_ID", "")
        pk_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
        rate_limit = float(self._kalshi_config.get("rate_limit_per_second", 10))
        base_url = self._kalshi_config.get(
            "base_url", "https://api.elections.kalshi.com/trade-api/v2"
        )

        self._client = KalshiClient(
            key_id=key_id or None,
            private_key_path=pk_path or None,
            base_url=base_url,
            rate_limit=rate_limit,
        )

        auth_status = "authenticated" if self._client.is_authenticated else "public-only"
        self.logger.info(
            "Kalshi client initialized (rate_limit=%.0f, auth=%s)", rate_limit, auth_status
        )

        # Load existing tracked markets from DB
        rows = await self.db.fetchall(
            "SELECT id, title FROM markets WHERE platform = 'kalshi' AND status = 'active'"
        )
        for row in rows:
            self._tracked_markets[row["id"]] = {"title": row["title"]}
        self.logger.info("Loaded %d tracked Kalshi markets from DB", len(self._tracked_markets))

    async def run_cycle(self) -> None:
        if not self._client:
            return

        # ── Market list refresh (every 10 cycles ≈ 5 min) ────────────
        if self._cycle_count % 10 == 0:
            await self._refresh_markets()

        if not self._tracked_markets:
            return

        # ── Price snapshots (every cycle) ─────────────────────────────
        await self._snapshot_prices()

        # ── Orderbook snapshots (every cycle) ─────────────────────────
        await self._snapshot_orderbooks()

        # ── Trade feed (every 2nd cycle ≈ 60s) ───────────────────────
        if self._cycle_count % 2 == 0:
            await self._fetch_trades()

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
        self.logger.info("Kalshi monitor stopped")

    # ── Internal methods ──────────────────────────────────────────────

    async def _refresh_markets(self) -> None:
        """Fetch active events + nested markets from Kalshi API."""
        try:
            result = await self._client.get_events(  # type: ignore[union-attr]
                limit=100, status="open", with_nested_markets=True,
            )
        except Exception:
            self.logger.exception("Failed to fetch Kalshi events")
            return

        events = result.get("events", [])
        upserted = 0

        for event in events:
            event_ticker = event.get("event_ticker", "")
            event_title = event.get("title", "")
            category = self._categorize_event(event)
            markets = event.get("markets", [])

            for m in markets:
                ticker = m.get("ticker", "")
                if not ticker:
                    continue

                title = m.get("title") or m.get("subtitle") or event_title
                close_date = m.get("close_time") or m.get("expiration_time")
                status = "active" if m.get("status", "").lower() in ("open", "active") else "closed"

                # Extract current price from market data
                yes_price = None
                if "yes_ask" in m and m["yes_ask"] is not None:
                    yes_price = float(m["yes_ask"]) / 100.0
                elif "last_price" in m and m["last_price"] is not None:
                    yes_price = float(m["last_price"]) / 100.0

                await self.db.execute(
                    """INSERT INTO markets (id, platform, title, category, close_date, status,
                                          event_id, updated_at)
                       VALUES (?, 'kalshi', ?, ?, ?, ?, ?, datetime('now'))
                       ON CONFLICT(id) DO UPDATE SET
                         title = excluded.title,
                         category = excluded.category,
                         close_date = excluded.close_date,
                         status = excluded.status,
                         event_id = excluded.event_id,
                         updated_at = datetime('now')
                    """,
                    (ticker, title, category, close_date, status, event_ticker),
                )

                self._tracked_markets[ticker] = {
                    "title": title,
                    "event_ticker": event_ticker,
                    "yes_price": yes_price,
                }

                # If we got a price inline, record it
                if yes_price is not None:
                    await self.db.execute(
                        "INSERT INTO prices (market_id, yes_price, no_price) VALUES (?, ?, ?)",
                        (ticker, yes_price, 1.0 - yes_price),
                    )

                upserted += 1

        await self.db.commit()
        self.logger.info("Refreshed %d Kalshi markets from %d events", upserted, len(events))

        # Pagination — fetch more if cursor is present
        cursor = result.get("cursor")
        if cursor and upserted >= 100:
            self.logger.info("More events available (cursor=%s) — will fetch next cycle", cursor)

    async def _snapshot_prices(self) -> None:
        """Fetch current prices for tracked markets via individual market endpoint."""
        for ticker in list(self._tracked_markets.keys()):
            try:
                m = await self._client.get_market(ticker)  # type: ignore[union-attr]
                if not m:
                    continue

                yes_price = None
                if "yes_ask" in m and m["yes_ask"] is not None:
                    yes_price = float(m["yes_ask"]) / 100.0
                elif "last_price" in m and m["last_price"] is not None:
                    yes_price = float(m["last_price"]) / 100.0

                if yes_price is not None:
                    volume = float(m.get("volume", 0))
                    oi = float(m.get("open_interest", 0))
                    await self.db.execute(
                        """INSERT INTO prices (market_id, yes_price, no_price, volume_24h, open_interest)
                           VALUES (?, ?, ?, ?, ?)""",
                        (ticker, yes_price, 1.0 - yes_price, volume, oi),
                    )
            except Exception:
                self.logger.debug("Price fetch failed for %s", ticker)
                continue
        await self.db.commit()

    async def _snapshot_orderbooks(self) -> None:
        """Fetch orderbook snapshots for tracked markets."""
        for ticker in list(self._tracked_markets.keys()):
            try:
                book = await self._client.get_orderbook(ticker)  # type: ignore[union-attr]
                if not book:
                    continue

                await self.db.execute(
                    "INSERT INTO orderbook (market_id, bids, asks) VALUES (?, ?, ?)",
                    (ticker, json.dumps(book["bids"]), json.dumps(book["asks"])),
                )
            except Exception:
                self.logger.debug("Orderbook fetch failed for %s", ticker)
                continue
        await self.db.commit()

    async def _fetch_trades(self) -> None:
        """Fetch recent trades for tracked markets."""
        for ticker in list(self._tracked_markets.keys()):
            try:
                result = await self._client.get_trades(ticker=ticker, limit=50)  # type: ignore[union-attr]
                trades = result.get("trades", [])
                for t in trades:
                    # Kalshi: taker_side is "yes" or "no"
                    taker_side = t.get("taker_side", "").upper()
                    side = "YES" if taker_side in ("YES", "BUY") else "NO"
                    count = float(t.get("count", 0))
                    price = float(t.get("yes_price", t.get("no_price", 0))) / 100.0

                    if count > 0:
                        await self.db.execute(
                            "INSERT INTO trades_log (market_id, side, size, price) VALUES (?, ?, ?, ?)",
                            (ticker, side, count, price),
                        )
            except Exception:
                self.logger.debug("Trade fetch failed for %s", ticker)
                continue
        await self.db.commit()

    @staticmethod
    def _categorize_event(event: dict) -> str:
        """Categorize based on Kalshi's event category + title keywords."""
        import re

        category = (event.get("category", "") or "").lower()
        title = (event.get("title", "") or "").lower()
        text = f"{category} {title}"

        def _has(words: tuple[str, ...]) -> bool:
            return any(re.search(rf"\b{w}", text) for w in words)

        if _has(("politic", "elect", "president", "congress", "senate", "vote")):
            return "politics"
        if _has(("sport", "nfl", "nba", "mlb", "game", "match")):
            return "sports"
        if _has(("crypto", "bitcoin", "btc", "ethereum", "blockchain")):
            return "crypto"
        if _has(("fed ", "gdp", "inflation", "interest rate", "econ", "cpi", "jobs")):
            return "economics"
        if _has(("artificial intelligence", "tech", "science", "space", "climate")):
            return "science_tech"
        return "other"
