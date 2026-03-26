"""
Kalshi Monitor Agent — ingests market data from Kalshi's Trading API v2.

PURPOSE:
    Kalshi is the PRIMARY execution platform for Sibyl (the only platform
    where we actually place bets).  This agent polls Kalshi's APIs for
    market data and writes it to SQLite for analysis and trading decisions.

DATA FLOW:
    Kalshi API v2  →  KalshiClient  →  KalshiMonitorAgent  →  SQLite
      (REST API)      (HTTP layer)    (polling + DB writes)  (database.py)

KALSHI'S DATA HIERARCHY:
    Event → contains multiple Markets
    Example:
        Event "Fed March Rate Decision" (event_ticker: "FED-RATE-MAR")
          ├── Market "25bps cut?" (ticker: "FED-RATE-MAR-25BP")
          └── Market "50bps cut?" (ticker: "FED-RATE-MAR-50BP")

AUTHENTICATION MODES:
    - If KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH are set in .env,
      the agent runs in AUTHENTICATED mode (can read portfolio, positions).
    - Otherwise, it runs in PUBLIC-ONLY mode (market data only).

MARKET DISCOVERY (Sprint 17 — Gap-Fill Integration):
    On the FIRST market refresh cycle, runs the full gap-fill discovery
    system that scans ALL open events (not just the first 100) to discover
    up to 27,000+ markets.  This is critical for Sports (14K markets),
    Financial (1K markets), and Weather (300+ markets) pipelines.

    Subsequent refreshes use standard pagination (fast, ~7s) to pick up
    newly listed markets without the full gap-fill overhead.

    A periodic FULL refresh with gap-fill runs every 30 minutes to catch
    any markets that were listed after the initial discovery.

WHAT THIS AGENT WRITES TO THE DATABASE:
    - markets table:    Market metadata with Kalshi-specific event_id grouping
    - prices table:     YES/NO prices + volume + open interest (every 5s)
    - orderbook table:  Normalized order book snapshots (every 5s)
    - trades_log table: Recent trades with taker side (every 10s)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from sibyl.clients.kalshi_client import KalshiClient
from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager
from sibyl.core.market_discovery import classify_category, discover_markets, seed_markets

logger = logging.getLogger("sibyl.agents.kalshi_monitor")


class KalshiMonitorAgent(BaseAgent):
    """Streams Kalshi market data into SQLite.

    AGGRESSIVE POLLING (tuned to Kalshi's Advanced-tier limits):
      Kalshi Advanced tier allows 30 read/s + 30 write/s.
      TieredRateLimiter enforces separate read/write budgets.

      At a 3-second poll interval:
        - Market/event list refresh: every 20th cycle ≈ 1 minute
        - Full gap-fill discovery: first cycle + every 360th cycle ≈ 18 minutes
        - Price snapshots: EVERY cycle (3s)
        - Orderbook snapshots: EVERY cycle (3s)
        - Recent trades: every 2nd cycle (6s)
    """

    # Maximum number of markets to poll for live prices per cycle.
    # With 3s cycles and 30 read/s Advanced tier, we can poll ~80+ markets per cycle.
    # The rest get prices seeded during discovery and updated less frequently.
    MAX_LIVE_POLL_MARKETS = 120

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="kalshi_monitor", db=db, config=config)
        self._kalshi_config = config.get("platforms", {}).get("kalshi", {})
        self._polling = config.get("polling", {})
        self._client: KalshiClient | None = None
        self._tracked_markets: dict[str, dict] = {}  # ticker → market metadata
        self._live_poll_markets: list[str] = []  # Priority subset for live polling
        self._gap_fill_done = False  # True after first full gap-fill discovery
        self._gap_fill_task: asyncio.Task | None = None  # Background gap-fill task

    @property
    def poll_interval(self) -> float:
        """Poll every 5 seconds — Kalshi's ~10 req/s limit is the bottleneck."""
        return float(self._polling.get("price_snapshot_interval_seconds", 5))

    async def start(self) -> None:
        key_id = os.environ.get("KALSHI_KEY_ID", "")
        pk_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
        tier = self._kalshi_config.get("tier", "basic")
        base_url = self._kalshi_config.get(
            "base_url", "https://api.elections.kalshi.com/trade-api/v2"
        )

        self._client = KalshiClient(
            key_id=key_id or None,
            private_key_path=pk_path or None,
            base_url=base_url,
            tier=tier,
        )

        auth_status = "authenticated" if self._client.is_authenticated else "public-only"
        self.logger.info(
            "Kalshi client initialized (tier=%s, auth=%s)", tier, auth_status
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

        # ── Market list refresh (every 24 cycles ≈ 2 min at 5s polling) ─
        if self._cycle_count % 24 == 0:
            # First refresh or every 360 cycles (~30 min): full gap-fill discovery
            # This runs as a background task so it doesn't block price polling.
            if not self._gap_fill_done or self._cycle_count % 360 == 0:
                if self._gap_fill_task is None or self._gap_fill_task.done():
                    use_gap_fill = not self._gap_fill_done
                    self._gap_fill_task = asyncio.create_task(
                        self._refresh_markets_with_discovery(gap_fill=use_gap_fill)
                    )
            else:
                # Standard refresh: just paginate for new/updated markets
                await self._refresh_markets_standard()

        if not self._tracked_markets:
            return

        # ── Auto-close expired markets (every 120 cycles ≈ 10 min) ────
        # Sprint 21.5: DB-level guard — marks markets as closed once their
        # close_date has passed.  Prevents stale markets from accumulating
        # even if the API filter misses some edge cases.
        if self._cycle_count % 120 == 0:
            await self._auto_close_expired_markets()

        # ── Refresh live poll priority list (every 12 cycles ≈ 1 min) ──
        if self._cycle_count % 12 == 0 or not self._live_poll_markets:
            await self._refresh_live_poll_list()

        # ── Price snapshots (EVERY cycle — 5s) for priority markets ───
        await self._snapshot_prices()

        # ── Orderbook snapshots (EVERY cycle — 5s) for priority markets
        await self._snapshot_orderbooks()

        # ── Trade feed (every 2nd cycle ≈ 10s) for priority markets ───
        if self._cycle_count % 2 == 0:
            await self._fetch_trades()

    async def stop(self) -> None:
        if self._gap_fill_task and not self._gap_fill_task.done():
            self._gap_fill_task.cancel()
            try:
                await self._gap_fill_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.close()
        self.logger.info("Kalshi monitor stopped")

    # ── Market Discovery Methods ───────────────────────────────────────

    async def _refresh_markets_with_discovery(self, gap_fill: bool = True) -> None:
        """Full market discovery using the shared gap-fill system.

        This uses the market_discovery module which scans ALL open events,
        classifies them into Sibyl's 8 pipeline categories, and seeds
        them into the markets table with correct category assignments.

        Args:
            gap_fill: If True, run the full gap-fill scan (~600s on first run).
                      If False, use standard pagination only (~7s).
        """
        try:
            self.logger.info(
                "Starting market discovery (gap_fill=%s)...", gap_fill
            )
            events, markets = await discover_markets(
                self._client, max_pages=15, gap_fill=gap_fill,
            )

            # Seed all discovered markets to DB with proper categories
            written = await seed_markets(self.db, markets)

            # Update tracked markets dict for price/orderbook polling
            for m in markets:
                ticker = m.get("ticker", "")
                if ticker:
                    self._tracked_markets[ticker] = {
                        "title": m.get("title", ""),
                        "event_ticker": m.get("_event_ticker", ""),
                    }

            self.logger.info(
                "Market discovery complete: %d events, %d markets discovered, "
                "%d written to DB, %d total tracked",
                len(events), len(markets), written, len(self._tracked_markets),
            )

            if gap_fill:
                self._gap_fill_done = True

                # Write discovery stats to system_state for dashboard
                await self.db.execute(
                    """INSERT OR REPLACE INTO system_state (key, value, updated_at)
                       VALUES ('market_discovery_total', ?, datetime('now'))""",
                    (str(len(self._tracked_markets)),),
                )
                await self.db.commit()

        except Exception:
            self.logger.exception("Market discovery failed")

    async def _refresh_markets_standard(self) -> None:
        """Standard market refresh using simple pagination (fast, no gap-fill).

        This is the quick refresh that runs every 2 minutes to pick up
        newly listed markets without the overhead of full gap-fill.

        Sprint 21.5: Uses min_close_ts=now to filter out expired events at the
        API level, reducing payload size and preventing stale market accumulation.
        """
        import time
        now_ts = int(time.time())
        try:
            result = await self._client.get_events(  # type: ignore[union-attr]
                limit=100, status="open", with_nested_markets=True,
                min_close_ts=now_ts,
            )
        except Exception:
            self.logger.exception("Failed to fetch Kalshi events")
            return

        events = result.get("events", [])
        upserted = 0

        for event in events:
            event_ticker = event.get("event_ticker", "")
            event_title = event.get("title", "")
            event_category = event.get("category", "")
            markets = event.get("markets", [])

            for m in markets:
                ticker = m.get("ticker", "")
                if not ticker:
                    continue

                title = m.get("title") or m.get("subtitle") or event_title
                close_date = m.get("close_time") or m.get("expiration_time")
                status = "active" if m.get("status", "").lower() in ("open", "active") else "closed"

                # Use the improved classifier from market_discovery module
                sibyl_category = classify_category(
                    event_category, title
                )

                # Extract current price
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
                    (ticker, title, sibyl_category, close_date, status, event_ticker),
                )

                self._tracked_markets[ticker] = {
                    "title": title,
                    "event_ticker": event_ticker,
                    "yes_price": yes_price,
                }

                if yes_price is not None:
                    await self.db.execute(
                        "INSERT INTO prices (market_id, yes_price, no_price) VALUES (?, ?, ?)",
                        (ticker, yes_price, 1.0 - yes_price),
                    )

                upserted += 1

        await self.db.commit()
        self.logger.info("Refreshed %d Kalshi markets from %d events", upserted, len(events))

    # ── DB Auto-Close Guard ────────────────────────────────────────────

    async def _auto_close_expired_markets(self) -> None:
        """Mark markets as closed once their close_date has passed.

        Sprint 21.5: DB-level safety net that runs every ~10 minutes.
        This catches markets that were active in the DB but whose close_date
        has now passed — prevents stale markets from being polled, traded,
        or clogging the signal pipeline.

        Also expires any ROUTED/PENDING signals that reference newly-closed
        markets, preventing 409 Conflict errors on order placement.
        """
        try:
            # Close expired markets
            result = await self.db.execute(
                """UPDATE markets SET status = 'closed', updated_at = datetime('now')
                   WHERE status = 'active'
                     AND close_date IS NOT NULL
                     AND close_date < datetime('now')"""
            )
            closed_count = result.rowcount if hasattr(result, 'rowcount') else 0

            # Expire signals referencing closed markets
            signal_result = await self.db.execute(
                """UPDATE signals SET status = 'EXPIRED'
                   WHERE status IN ('ROUTED', 'PENDING')
                     AND market_id IN (
                         SELECT id FROM markets WHERE status = 'closed'
                     )"""
            )
            expired_signals = signal_result.rowcount if hasattr(signal_result, 'rowcount') else 0

            if closed_count > 0 or expired_signals > 0:
                await self.db.commit()
                # Remove closed markets from tracked set
                closed_ids = []
                for mid in list(self._tracked_markets.keys()):
                    row = await self.db.fetchone(
                        "SELECT status FROM markets WHERE id = ?", (mid,)
                    )
                    if row and row["status"] == "closed":
                        closed_ids.append(mid)
                for mid in closed_ids:
                    self._tracked_markets.pop(mid, None)

                self.logger.info(
                    "Auto-close guard: %d markets closed, %d signals expired, "
                    "%d removed from tracking",
                    closed_count, expired_signals, len(closed_ids),
                )
        except Exception:
            self.logger.exception("Auto-close guard failed")

    # ── Live Poll Priority Selection ──────────────────────────────────

    async def _refresh_live_poll_list(self) -> None:
        """Select the highest-priority markets for live price/orderbook polling.

        Priority criteria (in order):
        1. Markets with OPEN positions (we need real-time prices for P&L)
        2. Markets with ROUTED signals (about to be executed)
        3. Markets with recent PENDING signals (being evaluated)
        4. Markets closing soonest (time-sensitive opportunities)

        Caps at MAX_LIVE_POLL_MARKETS to stay within Kalshi rate limits.
        """
        priority_tickers: list[str] = []

        # Priority 1: Markets with open positions (critical — need live P&L)
        position_rows = await self.db.fetchall(
            "SELECT DISTINCT market_id FROM positions WHERE status = 'OPEN'"
        )
        for row in position_rows:
            if row["market_id"] not in priority_tickers:
                priority_tickers.append(row["market_id"])

        # Priority 2: Markets with ROUTED signals (about to execute)
        signal_rows = await self.db.fetchall(
            """SELECT DISTINCT market_id FROM signals
               WHERE status IN ('ROUTED', 'PENDING')
               ORDER BY timestamp DESC LIMIT 50"""
        )
        for row in signal_rows:
            if row["market_id"] not in priority_tickers:
                priority_tickers.append(row["market_id"])

        # Priority 3: Fill remaining slots with markets closing soonest
        if len(priority_tickers) < self.MAX_LIVE_POLL_MARKETS:
            remaining = self.MAX_LIVE_POLL_MARKETS - len(priority_tickers)
            if priority_tickers:
                exclude_placeholders = ",".join("?" for _ in priority_tickers)
                close_rows = await self.db.fetchall(
                    f"""SELECT id FROM markets
                        WHERE platform = 'kalshi' AND status = 'active'
                          AND close_date IS NOT NULL AND close_date > datetime('now')
                          AND id NOT IN ({exclude_placeholders})
                        ORDER BY close_date ASC
                        LIMIT ?""",
                    (*priority_tickers, remaining),
                )
            else:
                close_rows = await self.db.fetchall(
                    """SELECT id FROM markets
                       WHERE platform = 'kalshi' AND status = 'active'
                         AND close_date IS NOT NULL AND close_date > datetime('now')
                       ORDER BY close_date ASC
                       LIMIT ?""",
                    (remaining,),
                )
            for row in close_rows:
                priority_tickers.append(row["id"])

        self._live_poll_markets = priority_tickers[:self.MAX_LIVE_POLL_MARKETS]
        self.logger.debug(
            "Live poll list: %d markets (positions: %d, signals: %d)",
            len(self._live_poll_markets), len(position_rows), len(signal_rows),
        )

    # ── Price/Orderbook/Trade Polling ──────────────────────────────────

    async def _snapshot_prices(self) -> None:
        """Fetch current prices for priority markets via individual market endpoint."""
        for ticker in self._live_poll_markets:
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
        """Fetch orderbook snapshots for priority markets."""
        for ticker in self._live_poll_markets:
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
        """Fetch recent trades for priority markets."""
        for ticker in self._live_poll_markets:
            try:
                result = await self._client.get_trades(ticker=ticker, limit=50)  # type: ignore[union-attr]
                trades = result.get("trades", [])
                for t in trades:
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
