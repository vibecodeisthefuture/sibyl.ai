"""
Hyperliquid Info API Client — Real-time crypto price data.

Sprint 20.5: Provides high-frequency price data (sub-second updates via WebSocket)
for BTC, ETH, SOL, and XRP to feed Sibyl's bracket model with fresh volatility
and spot price data.

NO AUTHENTICATION REQUIRED — all info endpoints are public.

Architecture:
    - REST: POST https://api.hyperliquid.xyz/info for on-demand queries
    - WebSocket: wss://api.hyperliquid.xyz/ws for streaming data
    - Rate limit: 1200 weight points/minute (allMids = weight 2)

Data available:
    - allMids: Current mid prices for all perpetual contracts
    - metaAndAssetCtxs: Rich ticker data (mark, mid, oracle, funding, OI, volume)
    - l2Book: Full order book (bid/ask depth)
    - candleSnapshot: Historical OHLCV candles (1m to 1M intervals)

Coin symbols: "BTC", "ETH", "SOL", "XRP" (simple names, not USDT pairs)

Docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("sibyl.clients.hyperliquid")

# Hyperliquid perpetual symbols for our target assets
TARGET_COINS = ("BTC", "ETH", "SOL", "XRP")

# Map Hyperliquid symbols to CoinGecko IDs (for cache integration)
HL_TO_CG_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
}


class HyperliquidClient:
    """Async client for Hyperliquid Info API — real-time crypto price data.

    Provides two modes:
    1. REST polling: Call get_all_mids() or get_asset_contexts() on demand.
    2. WebSocket streaming: Call start_price_stream() for continuous updates
       that write to the Sibyl prices table via a callback.

    No authentication required. Free within rate limits.
    """

    BASE_URL = "https://api.hyperliquid.xyz"
    INFO_URL = f"{BASE_URL}/info"
    WS_URL = "wss://api.hyperliquid.xyz/ws"

    def __init__(self, target_coins: tuple[str, ...] = TARGET_COINS) -> None:
        self._http: httpx.AsyncClient | None = None
        self._target_coins = target_coins
        self._initialized = False

        # Rate limiting (token bucket, 1200 weight/min = 20 weight/sec)
        self._last_request_time: float = 0.0
        self._min_interval: float = 0.1  # 100ms between requests (conservative)

        # Cache for latest prices (updated by REST or WebSocket)
        self._price_cache: dict[str, dict[str, Any]] = {}
        self._cache_timestamp: float = 0.0

        # WebSocket state
        self._ws_task: asyncio.Task | None = None
        self._ws_running: bool = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def price_cache(self) -> dict[str, dict[str, Any]]:
        """Current price cache. Keys are Hyperliquid symbols (BTC, ETH, etc.)."""
        return self._price_cache

    @property
    def cache_age_seconds(self) -> float:
        """Seconds since last cache update."""
        if self._cache_timestamp == 0:
            return float("inf")
        return time.time() - self._cache_timestamp

    def initialize(self) -> bool:
        """Initialize HTTP client."""
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"Content-Type": "application/json"},
        )
        self._initialized = True
        logger.info("HyperliquidClient initialized (targets: %s)", ", ".join(self._target_coins))
        return True

    async def close(self) -> None:
        """Shutdown HTTP client and WebSocket."""
        self._ws_running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._http:
            await self._http.aclose()
            self._http = None
        self._initialized = False

    # ── Rate Limiting ─────────────────────────────────────────────────

    async def _rate_limit(self) -> None:
        """Simple token-bucket rate limiter."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    # ── REST API Methods ──────────────────────────────────────────────

    async def _post_info(self, payload: dict) -> Any:
        """Send a POST request to the Hyperliquid info endpoint."""
        if not self._http:
            logger.error("HyperliquidClient not initialized")
            return None

        await self._rate_limit()
        try:
            response = await self._http.post(self.INFO_URL, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("Hyperliquid API error %d: %s", e.response.status_code, e)
            return None
        except Exception as e:
            logger.error("Hyperliquid request failed: %s", e)
            return None

    async def get_all_mids(self) -> dict[str, float] | None:
        """Get current mid prices for all perpetual contracts.

        Returns:
            Dict mapping coin symbol → mid price (float).
            E.g., {"BTC": 87450.5, "ETH": 3150.25, "SOL": 142.80, "XRP": 2.35}
            Returns only target coins, filtered from the full response.
        """
        data = await self._post_info({"type": "allMids"})
        if not data:
            return None

        # Response format: {"mids": {"BTC": "87450.5", "ETH": "3150.25", ...}}
        # or sometimes just the dict directly
        mids_raw = data.get("mids", data) if isinstance(data, dict) else data

        result = {}
        for coin in self._target_coins:
            if coin in mids_raw:
                try:
                    result[coin] = float(mids_raw[coin])
                except (ValueError, TypeError):
                    continue

        if result:
            self._cache_timestamp = time.time()
            for coin, price in result.items():
                self._price_cache[coin] = {
                    "mid_price": price,
                    "updated_at": self._cache_timestamp,
                    "source": "rest_allMids",
                }

        logger.debug("allMids: %s", {k: f"${v:,.2f}" for k, v in result.items()})
        return result

    async def get_asset_contexts(self) -> dict[str, dict[str, Any]] | None:
        """Get rich ticker data for all assets (mark, mid, oracle, funding, volume, OI).

        Returns:
            Dict mapping coin symbol → context dict with fields:
            - mark_price: float
            - mid_price: float
            - oracle_price: float
            - funding_rate: float (hourly)
            - open_interest: float
            - day_volume: float (24h notional)
            - prev_day_price: float
        """
        data = await self._post_info({"type": "metaAndAssetCtxs"})
        if not data or not isinstance(data, list) or len(data) < 2:
            return None

        # Response: [meta, [assetCtx, assetCtx, ...]]
        meta = data[0]
        asset_ctxs = data[1]

        # meta.universe is an array of {name: "BTC", szDecimals: 5, ...}
        universe = meta.get("universe", [])

        result = {}
        for i, asset_info in enumerate(universe):
            coin = asset_info.get("name", "")
            if coin not in self._target_coins:
                continue
            if i >= len(asset_ctxs):
                continue

            ctx = asset_ctxs[i]
            try:
                entry = {
                    "mark_price": float(ctx.get("markPx", 0)),
                    "mid_price": float(ctx.get("midPx", 0)),
                    "oracle_price": float(ctx.get("oraclePx", 0)),
                    "funding_rate": float(ctx.get("funding", 0)),
                    "open_interest": float(ctx.get("openInterest", 0)),
                    "day_volume": float(ctx.get("dayNtlVlm", 0)),
                    "prev_day_price": float(ctx.get("prevDayPx", 0)),
                    "updated_at": time.time(),
                    "source": "rest_assetCtxs",
                }
                result[coin] = entry

                # Update cache with richer data
                self._price_cache[coin] = entry
                self._cache_timestamp = time.time()
            except (ValueError, TypeError) as e:
                logger.debug("Failed to parse asset context for %s: %s", coin, e)

        logger.debug(
            "assetCtxs: %s",
            {k: f"${v['mid_price']:,.2f}" for k, v in result.items()},
        )
        return result

    async def get_candles(
        self,
        coin: str,
        interval: str = "1h",
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]] | None:
        """Get historical OHLCV candle data for a coin.

        Args:
            coin: Hyperliquid symbol (e.g., "BTC").
            interval: Candle interval — "1m", "5m", "15m", "1h", "4h", "1d", etc.
            start_time: Start timestamp in milliseconds. Default: 24h ago.
            end_time: End timestamp in milliseconds. Default: now.

        Returns:
            List of candle dicts with keys: open, high, low, close, volume,
            open_time, close_time, num_trades.
        """
        now_ms = int(time.time() * 1000)
        if start_time is None:
            start_time = now_ms - (24 * 60 * 60 * 1000)  # 24h ago
        if end_time is None:
            end_time = now_ms

        data = await self._post_info({
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_time,
                "endTime": end_time,
            },
        })

        if not data or not isinstance(data, list):
            return None

        candles = []
        for c in data:
            try:
                candles.append({
                    "open": float(c.get("o", 0)),
                    "high": float(c.get("h", 0)),
                    "low": float(c.get("l", 0)),
                    "close": float(c.get("c", 0)),
                    "volume": float(c.get("v", 0)),
                    "open_time": int(c.get("t", 0)),
                    "close_time": int(c.get("T", 0)),
                    "num_trades": int(c.get("n", 0)),
                    "coin": c.get("s", coin),
                    "interval": c.get("i", interval),
                })
            except (ValueError, TypeError):
                continue

        logger.debug("Candles for %s (%s): %d candles", coin, interval, len(candles))
        return candles

    def compute_realized_volatility(self, candles: list[dict], period: str = "1h") -> float:
        """Compute annualized realized volatility from candle data.

        Uses close-to-close returns to estimate volatility, then annualizes
        based on the candle period.

        Args:
            candles: List of candle dicts from get_candles().
            period: Candle interval for annualization factor.

        Returns:
            Annualized volatility as a decimal (e.g., 0.65 = 65% annual vol).
            Returns 0.03 (3% daily) as floor if insufficient data.
        """
        import math

        if len(candles) < 5:
            return 0.03  # Default 3% daily vol

        closes = [c["close"] for c in candles if c["close"] > 0]
        if len(closes) < 5:
            return 0.03

        # Log returns
        returns = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                returns.append(math.log(closes[i] / closes[i - 1]))

        if len(returns) < 3:
            return 0.03

        # Standard deviation of returns
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        std_dev = math.sqrt(variance)

        # Annualization factor depends on candle period
        # (periods per day) × sqrt(365) for crypto (24/7)
        period_factors = {
            "1m": 1440,   # 1440 per day
            "5m": 288,
            "15m": 96,
            "1h": 24,
            "4h": 6,
            "1d": 1,
        }
        periods_per_day = period_factors.get(period, 24)

        # Daily vol = std_dev * sqrt(periods_per_day)
        daily_vol = std_dev * math.sqrt(periods_per_day)

        # Floor at 1.5% daily
        return max(daily_vol, 0.015)

    # ── Sprint 21 Phase 2: Additional Data Streams ──────────────────

    async def get_l2_book(self, coin: str, n_sig_figs: int = 5) -> dict[str, Any] | None:
        """Get L2 order book snapshot (20 levels of bid/ask depth).

        Returns:
            Dict with 'bids' and 'asks' lists, each entry: [price, size].
            Also includes derived metrics: spread, bid_depth, ask_depth,
            imbalance (positive = bid-heavy = bullish pressure).
        """
        data = await self._post_info({
            "type": "l2Book",
            "coin": coin,
            "nSigFigs": n_sig_figs,
        })
        if not data or "levels" not in data:
            return None

        levels = data["levels"]
        if not isinstance(levels, list) or len(levels) < 2:
            return None

        bids_raw = levels[0]  # [[{px, sz, n}, ...]]
        asks_raw = levels[1]

        bids = []
        for entry in bids_raw:
            try:
                bids.append({
                    "price": float(entry.get("px", 0)),
                    "size": float(entry.get("sz", 0)),
                    "n_orders": int(entry.get("n", 1)),
                })
            except (ValueError, TypeError):
                continue

        asks = []
        for entry in asks_raw:
            try:
                asks.append({
                    "price": float(entry.get("px", 0)),
                    "size": float(entry.get("sz", 0)),
                    "n_orders": int(entry.get("n", 1)),
                })
            except (ValueError, TypeError):
                continue

        # Derived metrics
        best_bid = bids[0]["price"] if bids else 0
        best_ask = asks[0]["price"] if asks else 0
        spread = (best_ask - best_bid) if (best_bid > 0 and best_ask > 0) else 0
        spread_bps = (spread / best_bid * 10000) if best_bid > 0 else 0

        # Total depth within 0.5% of mid
        mid = (best_bid + best_ask) / 2 if (best_bid > 0 and best_ask > 0) else 0
        depth_range = mid * 0.005  # 0.5%
        bid_depth = sum(b["size"] * b["price"] for b in bids if mid - b["price"] <= depth_range)
        ask_depth = sum(a["size"] * a["price"] for a in asks if a["price"] - mid <= depth_range)
        total_depth = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0

        # Detect walls (any single level > 3× average)
        avg_bid_sz = sum(b["size"] for b in bids) / len(bids) if bids else 0
        avg_ask_sz = sum(a["size"] for a in asks) / len(asks) if asks else 0
        bid_walls = [b for b in bids if b["size"] > avg_bid_sz * 3]
        ask_walls = [a for a in asks if a["size"] > avg_ask_sz * 3]

        result = {
            "coin": coin,
            "bids": bids,
            "asks": asks,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "spread_bps": spread_bps,
            "mid": mid,
            "bid_depth_usd": bid_depth,
            "ask_depth_usd": ask_depth,
            "imbalance": imbalance,  # -1 to +1, positive = bullish
            "bid_walls": bid_walls,
            "ask_walls": ask_walls,
            "updated_at": time.time(),
        }

        logger.debug(
            "L2 %s: mid=$%s spread=%.1fbps imb=%.2f walls=%d/%d",
            coin, f"{mid:,.1f}", spread_bps, imbalance,
            len(bid_walls), len(ask_walls),
        )
        return result

    async def get_funding_history(
        self,
        coin: str,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]] | None:
        """Get historical funding rates for a coin.

        Args:
            coin: e.g. "BTC"
            start_time: ms timestamp. Default: 24h ago.
            end_time: ms timestamp. Default: now.

        Returns:
            List of {funding_rate, premium, timestamp} dicts, chronological.
        """
        now_ms = int(time.time() * 1000)
        if start_time is None:
            start_time = now_ms - (24 * 60 * 60 * 1000)
        if end_time is None:
            end_time = now_ms

        data = await self._post_info({
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_time,
            "endTime": end_time,
        })
        if not data or not isinstance(data, list):
            return None

        results = []
        for entry in data:
            try:
                results.append({
                    "coin": entry.get("coin", coin),
                    "funding_rate": float(entry.get("fundingRate", 0)),
                    "premium": float(entry.get("premium", 0)),
                    "timestamp": int(entry.get("time", 0)),
                })
            except (ValueError, TypeError):
                continue

        return results

    async def get_predicted_fundings(self) -> dict[str, dict[str, Any]] | None:
        """Get predicted funding rates across exchanges (Hyperliquid, Binance, Bybit).

        Returns:
            Dict keyed by coin → {hl_rate, binance_rate, bybit_rate, next_funding_time}.
            Only includes target coins.
        """
        data = await self._post_info({"type": "predictedFundings"})
        if not data or not isinstance(data, list):
            return None

        result = {}
        for entry in data:
            # entry format: [venue_list] where each venue has coin, funding info
            if not isinstance(entry, list):
                continue
            for venue_data in entry:
                if not isinstance(venue_data, dict):
                    continue
                coin = venue_data.get("coin", "")
                if coin not in self._target_coins:
                    continue

                venue = venue_data.get("venue", "")
                rate_str = venue_data.get("fundingRate", "0")
                try:
                    rate = float(rate_str)
                except (ValueError, TypeError):
                    rate = 0.0

                if coin not in result:
                    result[coin] = {
                        "coin": coin,
                        "hl_rate": 0.0,
                        "binance_rate": 0.0,
                        "bybit_rate": 0.0,
                        "updated_at": time.time(),
                    }

                venue_lower = venue.lower() if venue else ""
                if "hyperliquid" in venue_lower or "hl" in venue_lower or venue == "":
                    result[coin]["hl_rate"] = rate
                elif "binance" in venue_lower:
                    result[coin]["binance_rate"] = rate
                elif "bybit" in venue_lower:
                    result[coin]["bybit_rate"] = rate

        if result:
            logger.debug(
                "Predicted fundings: %s",
                {c: f"HL={d['hl_rate']:.4%} BN={d['binance_rate']:.4%}"
                 for c, d in result.items()},
            )
        return result

    async def get_recent_trades(self, coin: str) -> list[dict[str, Any]] | None:
        """Get recent trades for a coin via candleSnapshot at 1m resolution.

        Since the trades endpoint requires a user address for REST, we
        approximate trade flow data from 1-minute candles: volume, close vs open
        (buy/sell pressure), and number of trades per candle.

        For real-time trade stream, use WebSocket subscription instead.

        Returns:
            List of 1-minute candle dicts from the last 15 minutes, with
            derived buy_pressure metric.
        """
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (15 * 60 * 1000)  # last 15 minutes

        candles = await self.get_candles(
            coin=coin, interval="1m", start_time=start_ms, end_time=now_ms,
        )
        if not candles:
            return None

        # Enrich with buy/sell pressure estimation
        for c in candles:
            # Positive close-open = net buying; volume-weighted
            price_move = c["close"] - c["open"]
            bar_range = c["high"] - c["low"]
            if bar_range > 0:
                # Buy pressure: how much of the bar was bullish (0-1)
                c["buy_pressure"] = max(0, min(1, (c["close"] - c["low"]) / bar_range))
            else:
                c["buy_pressure"] = 0.5
            c["net_direction"] = "buy" if price_move > 0 else "sell"

        return candles

    # ── WebSocket Streaming ───────────────────────────────────────────

    async def start_price_stream(
        self,
        on_price_update: Any = None,
        poll_interval: float = 1.0,
    ) -> None:
        """Start streaming price updates via REST polling.

        Uses REST allMids endpoint polled at the specified interval.
        WebSocket would be ideal but adds complexity (reconnection logic,
        heartbeats, etc.). REST at 1-second intervals uses ~120 weight/min
        out of the 1200/min budget — well within limits.

        Args:
            on_price_update: Async callback(coin: str, price: float, timestamp: float).
                             Called for each target coin on every update.
            poll_interval: Seconds between polls (default 1.0).
        """
        self._ws_running = True
        logger.info(
            "Starting price stream (REST polling at %.1fs intervals, coins=%s)",
            poll_interval, ", ".join(self._target_coins),
        )

        while self._ws_running:
            try:
                mids = await self.get_all_mids()
                if mids and on_price_update:
                    now = time.time()
                    for coin, price in mids.items():
                        try:
                            await on_price_update(coin, price, now)
                        except Exception as e:
                            logger.error("Price callback error for %s: %s", coin, e)
            except Exception as e:
                logger.error("Price stream error: %s", e)

            await asyncio.sleep(poll_interval)

        logger.info("Price stream stopped")

    def stop_price_stream(self) -> None:
        """Signal the price stream to stop."""
        self._ws_running = False

    # ── Integration Helpers ───────────────────────────────────────────

    def get_cached_price(self, coin: str) -> float | None:
        """Get the last cached price for a coin.

        Args:
            coin: Hyperliquid symbol (e.g., "BTC") or CoinGecko ID (e.g., "bitcoin").

        Returns:
            Latest price as float, or None if not cached.
        """
        # Try direct lookup
        entry = self._price_cache.get(coin.upper())
        if entry:
            return entry.get("mid_price") or entry.get("mark_price")

        # Try CoinGecko ID reverse lookup
        for hl_sym, cg_id in HL_TO_CG_MAP.items():
            if cg_id == coin.lower():
                entry = self._price_cache.get(hl_sym)
                if entry:
                    return entry.get("mid_price") or entry.get("mark_price")

        return None

    def to_coingecko_cache_format(self) -> dict[str, dict[str, Any]]:
        """Convert the Hyperliquid price cache to CoinGecko-compatible format.

        This allows the crypto pipeline to seamlessly use Hyperliquid data
        in place of (or alongside) CoinGecko data. The returned dict can be
        merged into CryptoPipeline._coin_cache.

        Returns:
            Dict keyed by CoinGecko ID with price_usd, change_24h_pct, etc.
        """
        result = {}
        for hl_sym, cg_id in HL_TO_CG_MAP.items():
            entry = self._price_cache.get(hl_sym)
            if not entry:
                continue

            price = entry.get("mid_price") or entry.get("mark_price", 0)
            prev_day = entry.get("prev_day_price", 0)

            # Compute 24h change from prev_day_price if available
            change_24h = 0.0
            if prev_day and prev_day > 0 and price > 0:
                change_24h = (price - prev_day) / prev_day

            cg_entry = {
                "id": cg_id,
                "name": cg_id,
                "symbol": hl_sym.lower(),
                "price_usd": price,
                "change_24h_pct": change_24h,
                "change_7d_pct": 0,  # Not available from Hyperliquid
                "market_cap": 0,     # Not available
                "volume_24h": entry.get("day_volume", 0),
            }
            result[cg_id] = cg_entry
            # Also key by symbol for lookup compatibility
            result[hl_sym.lower()] = cg_entry

        return result
