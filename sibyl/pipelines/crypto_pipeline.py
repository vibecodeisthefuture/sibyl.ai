"""
Crypto Category Signal Pipeline for Sibyl.

Transforms crypto market data from CoinGecko and Fear & Greed Index
into trading signals for Kalshi crypto prediction markets.

Architecture:
    - Monitors BTC/ETH price thresholds and momentum
    - Analyzes market sentiment via Fear & Greed Index
    - Tracks BTC market dominance shifts
    - Identifies trending coins with Kalshi markets
    - Detects unusual 24h price movements
"""

import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional

from sibyl.pipelines.base_pipeline import BasePipeline, PipelineSignal
from sibyl.clients.coingecko_client import CoinGeckoClient
from sibyl.clients.feargreed_client import FearGreedClient

logger = logging.getLogger(__name__)


class CryptoPipeline(BasePipeline):
    """
    Crypto market analysis pipeline for Kalshi prediction markets.

    This pipeline integrates real-time crypto data from CoinGecko and
    the Fear & Greed Index to generate trading signals based on:
        - Price proximity to market thresholds
        - Market sentiment extremes
        - BTC market dominance trends
        - Trending coin momentum
        - Unusual intraday volatility

    Attributes:
        CATEGORY: Signal category identifier ("Crypto")
        PIPELINE_NAME: Unique pipeline name ("crypto")
    """

    CATEGORY = "Crypto"
    PIPELINE_NAME = "crypto"
    DEDUP_WINDOW_MINUTES = 5   # Sprint 20: 5-min dedup — crypto needs rapid signal refresh
    MARKET_HORIZON_DAYS = 30   # Sprint 22: 30 days — captures monthly min/max brackets

    # Crypto keyword mappings for market matching
    # Expanded for gap-fill discovered markets (daily brackets, monthly min/max)
    BITCOIN_KEYWORDS = {
        "bitcoin", "btc", "$100k", "100,000",
        "kxbtc", "kxbtcmin", "kxbtcmax", "kxbtcd",
    }
    ETHEREUM_KEYWORDS = {
        "ethereum", "eth", "ether",
        "kxeth", "kxethmin", "kxethmax", "kxethd",
    }
    SOLANA_KEYWORDS = {
        "solana", "sol",
        "kxsol", "kxsolmin", "kxsolmax", "kxsold",
    }
    ALTCOIN_KEYWORDS = {
        "xrp", "doge", "dogecoin", "bnb", "hype", "cardano", "ada",
        "kxxrp", "kxdoge", "kxbnb", "kxada",
    }
    GENERAL_KEYWORDS = {"crypto", "cryptocurrency", "coin", "token", "fdv", "megaeth"}
    ALL_CRYPTO_KEYWORDS = (
        BITCOIN_KEYWORDS | ETHEREUM_KEYWORDS | SOLANA_KEYWORDS
        | ALTCOIN_KEYWORDS | GENERAL_KEYWORDS
    )

    # CoinGecko ID mapping for Kalshi-relevant coins
    COIN_ID_MAP = {
        "bitcoin": "bitcoin", "btc": "bitcoin",
        "ethereum": "ethereum", "eth": "ethereum",
        "solana": "solana", "sol": "solana",
        "xrp": "ripple",
        "doge": "dogecoin", "dogecoin": "dogecoin",
        "bnb": "binancecoin",
        "cardano": "cardano", "ada": "cardano",
    }

    # ── Sprint 20.5: Targeted Series Tracker (Option C) ─────────────────
    # Deterministic enumeration of the 4 core crypto assets across all
    # Kalshi series. market_id (= Kalshi ticker) prefixes used for DB queries.
    # Each entry maps a CoinGecko ID to a set of Kalshi ticker prefixes that
    # cover 15-min, hourly, 4-hour, daily, monthly-min, and monthly-max series.
    TARGET_SERIES = {
        "bitcoin": {
            "cg_id": "bitcoin",
            "ticker_prefixes": [
                "KXBTC",       # 15-min / hourly / 4h brackets
                "KXBTCD",      # daily brackets
                "KXBTCMIN",    # monthly minimum
                "KXBTCMAX",    # monthly maximum
            ],
        },
        "ethereum": {
            "cg_id": "ethereum",
            "ticker_prefixes": [
                "KXETH",
                "KXETHD",
                "KXETHMIN",
                "KXETHMAX",
            ],
        },
        "solana": {
            "cg_id": "solana",
            "ticker_prefixes": [
                "KXSOL",
                "KXSOLD",
                "KXSOLMIN",
                "KXSOLMAX",
            ],
        },
        "xrp": {
            "cg_id": "ripple",
            "ticker_prefixes": [
                "KXXRP",
                "KXXRPD",
                "KXXRPMIN",
                "KXXRPMAX",
            ],
        },
    }

    # Sprint 22: Minimum edge (in probability points) for BRACKET_MODEL signals.
    # Aligned with investment_policy_config.yaml bracket_min_edge: 0.015.
    BRACKET_MIN_EDGE = 0.015

    # Threshold constants for signal generation
    # Sprint 20: Widened thresholds to capture more crypto signals
    PRICE_PROXIMITY_THRESHOLD = 0.08  # 8% distance to threshold (was 5%)
    MOMENTUM_MIN_24H_CHANGE = 0.015   # 1.5% minimum momentum (was 2%)
    EXTREME_FEAR_THRESHOLD = 25       # FGI score for extreme fear (was 20)
    EXTREME_GREED_THRESHOLD = 75      # FGI score for extreme greed (was 80)
    DOMINANCE_SHIFT_THRESHOLD = 0.008 # 0.8% dominance change (was 1%)
    TRENDING_MOMENTUM_THRESHOLD = 0.08 # 8% 24h change (was 10%)
    VOLATILITY_THRESHOLD = 0.08       # 8% 24h change for volatility signal (was 10%)

    def _create_clients(self) -> List:
        """
        Initialize data clients for crypto pipeline.

        Returns:
            List[BasePipeline]: Initialized client instances
                - CoinGeckoClient: Real-time crypto market data
                - FearGreedClient: Market sentiment index

        Sprint 20.5: HyperliquidClient is initialized separately (not a
        BaseDataClient subclass) and stored on self._hyperliquid for
        enriching the coin cache with sub-second price data.
        """
        # Initialize Hyperliquid client (separate from BaseDataClient lifecycle)
        self._hyperliquid = None
        try:
            from sibyl.clients.hyperliquid_client import HyperliquidClient
            hl = HyperliquidClient()
            if hl.initialize():
                self._hyperliquid = hl
                logger.info("HyperliquidClient initialized for real-time price enrichment")
        except Exception as e:
            logger.warning("HyperliquidClient init failed (non-fatal): %s", e)

        try:
            return [CoinGeckoClient(), FearGreedClient()]
        except Exception as e:
            logger.error(f"Failed to initialize crypto clients: {e}", exc_info=True)
            raise

    async def _analyze(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze crypto markets and generate trading signals.

        OPTIMIZED FOR BASIC TIER (Sprint 16):
        Pre-fetches all data in 4 batched API calls, then runs 5 analyses
        entirely from the cached data. No per-coin lookups.

        API call budget per cycle: ~4 calls
          1. get_coin_markets(top 100) — prices, volumes, 24h change
          2. get_global() — BTC dominance, total market cap
          3. get_trending() — trending search coins
          4. FearGreed get_index() — sentiment

        Args:
            markets: List of Kalshi market definitions with titles, descriptions

        Returns:
            List[PipelineSignal]: Generated trading signals with confidence scores
        """
        signals = []

        try:
            # ── Batch-fetch all data upfront (4 API calls total) ────────
            coingecko = self.clients[0]  # CoinGeckoClient
            feargreed = self.clients[1]  # FearGreedClient

            # Fire independent requests concurrently
            import asyncio
            coin_markets_task = asyncio.create_task(
                coingecko.get_coin_markets(per_page=100)
            )
            global_task = asyncio.create_task(coingecko.get_global())
            trending_task = asyncio.create_task(coingecko.get_trending())
            fgi_task = asyncio.create_task(feargreed.get_index(limit=1))

            coin_markets_data = await coin_markets_task
            global_data = await global_task
            trending_data = await trending_task
            fgi_data_list = await fgi_task

            # Build price lookup from the batch coin_markets response
            # This replaces ALL individual get_price() calls
            self._coin_cache: Dict[str, Dict] = {}
            for coin in (coin_markets_data or []):
                cid = coin.get("id", "").lower()
                name = coin.get("name", "").lower()
                symbol = coin.get("symbol", "").lower()
                entry = {
                    "id": cid,
                    "name": name,
                    "symbol": symbol,
                    "price_usd": coin.get("current_price", 0),
                    "change_24h_pct": (coin.get("price_change_percentage_24h") or 0) / 100,
                    "change_7d_pct": (coin.get("price_change_percentage_7d_in_currency") or 0) / 100,
                    "market_cap": coin.get("market_cap", 0),
                    "volume_24h": coin.get("total_volume", 0),
                }
                self._coin_cache[cid] = entry
                self._coin_cache[name] = entry
                self._coin_cache[symbol] = entry

            logger.info(
                "Crypto batch fetch: %d coins cached, global=%s, trending=%s, FGI=%s",
                len(coin_markets_data or []),
                "OK" if global_data else "FAIL",
                "OK" if trending_data else "FAIL",
                "OK" if fgi_data_list else "FAIL",
            )

            # ── Sprint 22: Hyperliquid-first data sourcing ────────────────
            # Priority #1: Read real-time prices from crypto_spot_prices table
            # (HyperliquidPriceAgent writes 1-second spot prices to the DB).
            # Priority #2: Direct Hyperliquid API call as fallback.
            # Priority #3: CoinGecko batch data (already loaded above).
            hl_enriched = False
            try:
                hl_db_prices = await self._read_spot_prices_from_db()
                if hl_db_prices:
                    for cg_id, hl_entry in hl_db_prices.items():
                        if cg_id in self._coin_cache and hl_entry.get("price_usd", 0) > 0:
                            existing = self._coin_cache[cg_id]
                            existing["price_usd"] = hl_entry["price_usd"]
                            if hl_entry.get("change_24h_pct", 0) != 0:
                                existing["change_24h_pct"] = hl_entry["change_24h_pct"]
                            if hl_entry.get("volume_24h", 0) > 0:
                                existing["volume_24h"] = hl_entry["volume_24h"]
                        elif hl_entry.get("price_usd", 0) > 0:
                            self._coin_cache[cg_id] = hl_entry
                    logger.info(
                        "[P1] DB spot price enrichment: %d assets (BTC=$%s, ETH=$%s)",
                        len(hl_db_prices),
                        f"{hl_db_prices.get('bitcoin', {}).get('price_usd', 0):,.0f}",
                        f"{hl_db_prices.get('ethereum', {}).get('price_usd', 0):,.0f}",
                    )
                    hl_enriched = True
            except Exception as e:
                logger.debug("[P1] DB spot price read failed (will try P2 API): %s", e)

            # Priority #2: Direct Hyperliquid API call if DB read failed
            if not hl_enriched and self._hyperliquid:
                try:
                    hl_data = await self._hyperliquid.get_asset_contexts()
                    if hl_data:
                        hl_cache = self._hyperliquid.to_coingecko_cache_format()
                        for cg_id, hl_entry in hl_cache.items():
                            if cg_id in self._coin_cache and hl_entry.get("price_usd", 0) > 0:
                                existing = self._coin_cache[cg_id]
                                existing["price_usd"] = hl_entry["price_usd"]
                                if hl_entry.get("change_24h_pct", 0) != 0:
                                    existing["change_24h_pct"] = hl_entry["change_24h_pct"]
                                if hl_entry.get("volume_24h", 0) > 0:
                                    existing["volume_24h"] = hl_entry["volume_24h"]
                            elif hl_entry.get("price_usd", 0) > 0:
                                self._coin_cache[cg_id] = hl_entry
                        logger.info(
                            "[P2] Hyperliquid API fallback: %d assets (BTC=$%s, ETH=$%s)",
                            len(hl_data),
                            f"{hl_data.get('BTC', {}).get('mid_price', 0):,.0f}",
                            f"{hl_data.get('ETH', {}).get('mid_price', 0):,.0f}",
                        )
                        hl_enriched = True
                except Exception as e:
                    logger.warning("[P2] Hyperliquid API fallback failed (non-fatal): %s", e)

            if not hl_enriched:
                logger.info("[P3] Using CoinGecko data only (Hyperliquid unavailable)")

            # ── Run all analyses from cached data (0 extra API calls) ───
            signals.extend(self._analyze_price_thresholds_cached(markets))
            signals.extend(self._analyze_daily_brackets_cached(markets))
            signals.extend(self._analyze_monthly_extremes_cached(markets))
            signals.extend(self._analyze_sentiment_cached(markets, fgi_data_list))
            signals.extend(self._analyze_dominance_cached(markets, global_data))
            signals.extend(self._analyze_trending_cached(markets, trending_data))
            signals.extend(self._analyze_volatility_cached(markets))

            # ── Sprint 21: Read realized volatility from crypto_volatility table ─
            # HyperliquidPriceAgent computes vol every 5 min and writes to DB.
            # Pipeline reads the latest value per coin. Falls back to direct calc.
            self._realized_vol: Dict[str, float] = {}
            try:
                vol_from_db = await self._read_volatility_from_db()
                if vol_from_db:
                    self._realized_vol = vol_from_db
                    logger.info(
                        "Realized vol (from DB): %s",
                        {k: f"{v*100:.1f}%" for k, v in self._realized_vol.items()},
                    )
            except Exception as e:
                logger.debug("DB vol read failed (will try direct calc): %s", e)

            # Fallback: direct candle fetch if DB had no recent vol
            if not self._realized_vol and self._hyperliquid:
                from sibyl.clients.hyperliquid_client import HL_TO_CG_MAP
                for hl_sym, cg_id in HL_TO_CG_MAP.items():
                    try:
                        candles = await self._hyperliquid.get_candles(
                            coin=hl_sym, interval="1h",
                        )
                        if candles and len(candles) >= 5:
                            vol = self._hyperliquid.compute_realized_volatility(candles, "1h")
                            self._realized_vol[cg_id] = vol
                    except Exception as e:
                        logger.debug("Vol calc failed for %s: %s", hl_sym, e)
                if self._realized_vol:
                    logger.info(
                        "Realized vol (Hyperliquid API fallback): %s",
                        {k: f"{v*100:.1f}%" for k, v in self._realized_vol.items()},
                    )

            # ── Sprint 21 Phase 2: Load order book, funding, micro-vol ─────
            self._order_book_data: Dict[str, Dict] = {}
            self._funding_data: Dict[str, Dict] = {}
            self._micro_vol_data: Dict[str, Dict] = {}
            try:
                self._order_book_data = await self._read_order_book_from_db()
                self._funding_data = await self._read_funding_from_db()
                self._micro_vol_data = await self._read_micro_vol_from_db()
                enrichment_count = sum(1 for d in [self._order_book_data, self._funding_data, self._micro_vol_data] if d)
                if enrichment_count > 0:
                    logger.info(
                        "HL enrichment: book=%d funding=%d micro_vol=%d coins",
                        len(self._order_book_data), len(self._funding_data), len(self._micro_vol_data),
                    )
            except Exception as e:
                logger.debug("HL enrichment read failed (non-fatal): %s", e)

            # ── Sprint 20.5: Always-On Bracket Trader ─────────────────────
            # Enumerates ALL active BTC/ETH/SOL/XRP markets via ticker-prefix
            # matching and generates BRACKET_MODEL signals for every bracket
            # where the model sees edge ≥ BRACKET_MIN_EDGE.  This guarantees
            # persistent participation regardless of conditional triggers.
            target_markets = await self._enumerate_target_markets()

            # Sprint 22: Pre-fetch Kalshi spreads for spread-adjusted EV.
            # Loads bid-ask spread from the Kalshi orderbook table for each
            # target market so the bracket model deducts execution costs.
            self._kalshi_spreads: Dict[str, float] = {}
            try:
                all_market_ids = []
                for mkt_list in target_markets.values():
                    for m in mkt_list:
                        all_market_ids.append(m.get("id", ""))
                if all_market_ids:
                    placeholders = ",".join("?" for _ in all_market_ids)
                    spread_rows = await self._db.fetchall(
                        f"""SELECT market_id, bids, asks FROM orderbook
                            WHERE market_id IN ({placeholders})
                            AND timestamp > datetime('now', '-5 minutes')
                            ORDER BY timestamp DESC""",
                        tuple(all_market_ids),
                    )
                    import json as _json
                    seen = set()
                    for row in spread_rows:
                        mid = row["market_id"]
                        if mid in seen:
                            continue
                        seen.add(mid)
                        try:
                            _b = _json.loads(row["bids"]) if row["bids"] else []
                            _a = _json.loads(row["asks"]) if row["asks"] else []
                            if _b and _a:
                                bb = float(_b[0].get("price", 0))
                                ba = float(_a[0].get("price", 1))
                                if bb > 0 and ba > bb:
                                    self._kalshi_spreads[mid] = ba - bb
                        except Exception:
                            pass
                    if self._kalshi_spreads:
                        avg_spread = sum(self._kalshi_spreads.values()) / len(self._kalshi_spreads)
                        logger.info(
                            "Kalshi spreads loaded: %d markets, avg=%.3f",
                            len(self._kalshi_spreads), avg_spread,
                        )
            except Exception as e:
                logger.debug("Kalshi spread pre-fetch failed (non-fatal): %s", e)

            signals.extend(self._bracket_model_signals(target_markets))

            logger.info(
                f"Crypto pipeline analysis complete: {len(signals)} signals generated"
            )
            return signals

        except Exception as e:
            logger.error(f"Error during crypto analysis: {e}", exc_info=True)
            return signals

    # ── Cached analysis methods (no API calls) ──────────────────────────

    def _analyze_price_thresholds_cached(self, markets: List[Dict]) -> List[PipelineSignal]:
        """Price threshold analysis using cached coin data."""
        signals = []
        crypto_markets = self._find_matching_markets(
            markets, self.BITCOIN_KEYWORDS | self.ETHEREUM_KEYWORDS | self.SOLANA_KEYWORDS | self.ALTCOIN_KEYWORDS
        )

        for market in crypto_markets:
            title = market.get("title", "")
            threshold = self._extract_price_threshold(title)
            coin = self._extract_coin_from_title(title)
            if not threshold or not coin:
                continue

            # Look up price from cache
            coin_lower = coin.lower()
            cg_id = self.COIN_ID_MAP.get(coin_lower, coin_lower)
            cached = self._coin_cache.get(cg_id)
            if not cached:
                continue

            current_price = cached["price_usd"]
            momentum = cached["change_24h_pct"]
            if current_price <= 0:
                continue

            distance_to_threshold = abs(current_price - threshold) / threshold
            positive_momentum = momentum >= self.MOMENTUM_MIN_24H_CHANGE

            if distance_to_threshold <= self.PRICE_PROXIMITY_THRESHOLD and positive_momentum:
                confidence = self._calculate_threshold_probability(
                    current_price, threshold,
                    self._extract_days_remaining(market),
                    abs(momentum),
                )
                signals.append(PipelineSignal(
                    market_id=market.get("id"),
                    signal_type="DATA_MOMENTUM",
                    confidence=confidence,
                    direction="YES",
                    reasoning=f"{coin} at ${current_price:,.0f} within {distance_to_threshold:.1%} of ${threshold:,.0f} threshold with {momentum*100:+.1f}% momentum",
                ))
        return signals

    def _analyze_daily_brackets_cached(self, markets: List[Dict]) -> List[PipelineSignal]:
        """Analyze daily crypto price bracket markets using cached data.

        Handles Kalshi daily bracket markets like:
          - "Bitcoin above $85,000?" (over/under)
          - "Bitcoin between $84,000 and $85,000?" (range bracket)
          - "Highest Bitcoin price today?" (daily high)
          - "Lowest Ethereum price today?" (daily low)

        Uses current price + 24h volatility to estimate bracket probability
        via a simple normal distribution model.
        """
        import math
        signals = []

        for market in markets:
            title = market.get("title", "")
            ticker = market.get("ticker", "")
            title_lower = title.lower()

            # Identify which coin this market is about
            coin_id = None
            if any(kw in title_lower or kw in ticker.lower()
                   for kw in ("bitcoin", "btc", "kxbtc")):
                coin_id = "bitcoin"
            elif any(kw in title_lower or kw in ticker.lower()
                     for kw in ("ethereum", "eth", "kxeth")):
                coin_id = "ethereum"
            elif any(kw in title_lower or kw in ticker.lower()
                     for kw in ("solana", "sol", "kxsol")):
                coin_id = "solana"
            elif any(kw in title_lower or kw in ticker.lower()
                     for kw in ("xrp", "kxxrp")):
                coin_id = "ripple"
            elif any(kw in title_lower or kw in ticker.lower()
                     for kw in ("doge", "dogecoin", "kxdoge")):
                coin_id = "dogecoin"
            else:
                continue

            cached = self._coin_cache.get(coin_id)
            if not cached:
                continue

            price = cached["price_usd"]
            vol_24h = abs(cached["change_24h_pct"])
            if price <= 0:
                continue

            # Estimate daily volatility (annualize 24h change as proxy)
            daily_vol = max(vol_24h, 0.015)  # Floor at 1.5% daily vol

            # Parse bracket from title
            bracket = self._parse_crypto_bracket(title, price)
            if not bracket:
                continue

            bracket_type, lower, upper = bracket

            # Estimate probability using normal CDF approximation
            if bracket_type == "above":
                # P(price > threshold) ≈ 1 - Φ((threshold - price) / (price * vol))
                z = (lower - price) / (price * daily_vol)
                prob = 1.0 - self._normal_cdf(z)
            elif bracket_type == "below":
                z = (upper - price) / (price * daily_vol)
                prob = self._normal_cdf(z)
            elif bracket_type == "between":
                z_low = (lower - price) / (price * daily_vol)
                z_high = (upper - price) / (price * daily_vol)
                prob = self._normal_cdf(z_high) - self._normal_cdf(z_low)
            else:
                continue

            prob = max(0.02, min(0.98, prob))

            # Only generate signal if we have meaningful edge
            if prob > 0.05:
                confidence = min(0.95, 0.5 + abs(prob - 0.5))
                direction = "YES" if prob > 0.5 else "NO"
                signals.append(PipelineSignal(
                    market_id=market.get("id"),
                    signal_type="DATA_FUNDAMENTAL",
                    confidence=confidence,
                    ev_estimate=abs(prob - 0.5) * 0.1,
                    direction=direction,
                    reasoning=(
                        f"{coin_id.capitalize()} at ${price:,.0f} "
                        f"(24h vol {vol_24h*100:.1f}%): "
                        f"bracket {bracket_type} "
                        f"{'$'+f'{lower:,.0f}' if lower else ''}"
                        f"{'-$'+f'{upper:,.0f}' if upper and bracket_type == 'between' else ''} "
                        f"→ est. prob {prob:.1%}"
                    ),
                ))
        return signals

    def _analyze_monthly_extremes_cached(self, markets: List[Dict]) -> List[PipelineSignal]:
        """Analyze monthly min/max crypto markets using cached data.

        Handles markets like:
          - "Bitcoin monthly minimum above $80,000?"
          - "Ethereum monthly maximum above $4,000?"
          - "Solana highest price this month?"
        """
        signals = []

        for market in markets:
            title = market.get("title", "")
            ticker = market.get("ticker", "")
            title_lower = title.lower()

            # Check for monthly min/max keywords
            is_monthly = any(kw in title_lower for kw in (
                "month", "monthly", "minmon", "maxmon",
            )) or any(kw in ticker.lower() for kw in (
                "minmon", "maxmon", "min", "max",
            ))
            if not is_monthly:
                continue

            # Identify coin
            coin_id = None
            for keyword, cid in [
                ("bitcoin", "bitcoin"), ("btc", "bitcoin"),
                ("ethereum", "ethereum"), ("eth", "ethereum"),
                ("solana", "solana"), ("sol", "solana"),
                ("xrp", "ripple"), ("doge", "dogecoin"),
            ]:
                if keyword in title_lower or keyword in ticker.lower():
                    coin_id = cid
                    break
            if not coin_id:
                continue

            cached = self._coin_cache.get(coin_id)
            if not cached:
                continue

            price = cached["price_usd"]
            vol_24h = abs(cached["change_24h_pct"])
            if price <= 0:
                continue

            # Parse threshold from title
            threshold = self._extract_price_threshold(title)
            if not threshold:
                continue

            is_min = any(kw in title_lower or kw in ticker.lower()
                         for kw in ("min", "low", "minimum", "lowest"))
            is_max = any(kw in title_lower or kw in ticker.lower()
                         for kw in ("max", "high", "maximum", "highest"))

            # Monthly vol ≈ daily vol * sqrt(~20 trading days remaining in month)
            import math
            days_left = max(1, 30 - datetime.now().day)
            monthly_vol = max(vol_24h, 0.015) * math.sqrt(days_left)

            if is_max:
                # P(monthly_max > threshold) using extreme value approximation
                z = (threshold - price) / (price * monthly_vol)
                # Monthly max is higher than spot — boost probability
                prob = 1.0 - self._normal_cdf(z - 0.5)
            elif is_min:
                # P(monthly_min > threshold) — min staying above threshold
                z = (threshold - price) / (price * monthly_vol)
                prob = self._normal_cdf(-z - 0.5)
            else:
                continue

            prob = max(0.02, min(0.98, prob))

            if prob > 0.05:
                confidence = min(0.95, 0.5 + abs(prob - 0.5))
                direction = "YES" if prob > 0.5 else "NO"
                signals.append(PipelineSignal(
                    market_id=market.get("id"),
                    signal_type="DATA_FUNDAMENTAL",
                    confidence=confidence,
                    ev_estimate=abs(prob - 0.5) * 0.1,
                    direction=direction,
                    reasoning=(
                        f"{coin_id.capitalize()} at ${price:,.0f}: "
                        f"monthly {'max' if is_max else 'min'} "
                        f"{'above' if is_max else 'above'} ${threshold:,.0f} "
                        f"→ est. prob {prob:.1%} "
                        f"(vol {monthly_vol*100:.1f}%, {days_left}d left)"
                    ),
                ))
        return signals

    @staticmethod
    def _parse_crypto_bracket(title: str, current_price: float):
        """Parse a bracket from a Kalshi crypto market title.

        Returns (bracket_type, lower, upper) or None:
          - ("above", threshold, None) for "above $X" / ">$X"
          - ("below", None, threshold) for "below $X" / "<$X"
          - ("between", lower, upper) for "between $X and $Y" / "$X-$Y"
        """
        import re
        title_lower = title.lower()

        # Pattern: "between $X and $Y" or "$X to $Y" or "$X-$Y"
        between_match = re.search(
            r'(?:between\s+)?\$?([\d,]+(?:\.\d+)?)\s*(?:and|to|-)\s*\$?([\d,]+(?:\.\d+)?)',
            title, re.IGNORECASE,
        )
        if between_match:
            lower = float(between_match.group(1).replace(",", ""))
            upper = float(between_match.group(2).replace(",", ""))
            if lower > 0 and upper > 0:
                return ("between", min(lower, upper), max(lower, upper))

        # Pattern: "above $X" / "over $X" / ">$X" / "≥$X"
        above_match = re.search(
            r'(?:above|over|exceed|≥|>|higher than|at least)\s*\$?([\d,]+(?:\.\d+)?)',
            title, re.IGNORECASE,
        )
        if above_match:
            threshold = float(above_match.group(1).replace(",", ""))
            if threshold > 0:
                return ("above", threshold, None)

        # Pattern: "below $X" / "under $X" / "<$X"
        below_match = re.search(
            r'(?:below|under|less than|≤|<|lower than)\s*\$?([\d,]+(?:\.\d+)?)',
            title, re.IGNORECASE,
        )
        if below_match:
            threshold = float(below_match.group(1).replace(",", ""))
            if threshold > 0:
                return ("below", None, threshold)

        # Fallback: just a dollar amount with context clues
        price_match = re.findall(r'\$([\d,]+(?:\.\d+)?)', title)
        if price_match:
            threshold = float(price_match[0].replace(",", ""))
            if threshold > 0:
                # If title has "highest" or "high", treat as "above"
                if any(kw in title_lower for kw in ("high", "top", "peak", "max")):
                    return ("above", threshold, None)
                elif any(kw in title_lower for kw in ("low", "bottom", "min")):
                    return ("below", None, threshold)
                # Default: above
                return ("above", threshold, None)

        return None

    @staticmethod
    def _parse_bracket_from_ticker(ticker: str, current_price: float):
        """Parse bracket from Kalshi's new ticker format (Sprint 21 fix).

        New Kalshi crypto tickers encode the bracket in the suffix:
            KXBTC-26MAR2717-B82650   → between bracket at $82,650
            KXBTC-26MAR2717-T82899.99 → above/top at $82,899.99

        The last segment after the final '-' starts with:
            'B' + value → "between" bracket (value is the lower bound, upper = lower + $500)
            'T' + value → "above" threshold (price must be above this)
        """
        import re
        if not ticker:
            return None

        # Extract last segment after final '-'
        parts = ticker.rsplit("-", 1)
        if len(parts) < 2:
            return None

        suffix = parts[-1]

        # Match B<number> (bracket) or T<number> (top/threshold)
        match = re.match(r'^([BT])([\d.]+)$', suffix)
        if not match:
            return None

        bracket_code = match.group(1)
        value = float(match.group(2))

        if value <= 0:
            return None

        if bracket_code == "B":
            # "B" = between bracket.  Kalshi brackets are typically $500 wide for BTC,
            # $50 for ETH, $5 for SOL.  Use the ticker prefix to determine asset.
            ticker_upper = ticker.upper()
            if "KXBTC" in ticker_upper:
                width = 500
            elif "KXETH" in ticker_upper:
                width = 50
            elif "KXSOL" in ticker_upper:
                width = 5
            elif "KXXRP" in ticker_upper or "KXRP" in ticker_upper:
                width = 0.5
            else:
                width = value * 0.006  # ~0.6% width as fallback
            return ("between", value, value + width)
        elif bracket_code == "T":
            # "T" = top/above threshold
            return ("above", value, None)

        return None

    @staticmethod
    def _normal_cdf(z: float) -> float:
        """Approximate the standard normal CDF using the error function."""
        import math
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    def _analyze_sentiment_cached(self, markets: List[Dict], fgi_data_list) -> List[PipelineSignal]:
        """Sentiment analysis using pre-fetched FGI data."""
        signals = []
        if not fgi_data_list:
            return signals

        fgi_data = fgi_data_list[0] if isinstance(fgi_data_list, list) else fgi_data_list
        fgi_value = int(fgi_data.get("value", 50))
        crypto_markets = self._find_matching_markets(markets, self.ALL_CRYPTO_KEYWORDS)

        for market in crypto_markets:
            if fgi_value < self.EXTREME_FEAR_THRESHOLD:
                confidence = 1.0 - (fgi_value / self.EXTREME_FEAR_THRESHOLD)
                signals.append(PipelineSignal(
                    market_id=market.get("id"),
                    signal_type="DATA_SENTIMENT",
                    confidence=confidence,
                    direction="YES",
                    reasoning=f"Extreme Fear (FGI={fgi_value}): Historical contrarian buy signal",
                ))
            elif fgi_value > self.EXTREME_GREED_THRESHOLD:
                confidence = (fgi_value - self.EXTREME_GREED_THRESHOLD) / (100 - self.EXTREME_GREED_THRESHOLD)
                signals.append(PipelineSignal(
                    market_id=market.get("id"),
                    signal_type="DATA_SENTIMENT",
                    confidence=confidence,
                    direction="NO",
                    reasoning=f"Extreme Greed (FGI={fgi_value}): Potential market saturation",
                ))
        return signals

    def _analyze_dominance_cached(self, markets: List[Dict], global_data) -> List[PipelineSignal]:
        """BTC dominance analysis using pre-fetched global data."""
        signals = []
        if not global_data:
            return signals

        btc_dom_change = global_data.get("btc_dominance_24h_change", 0)
        if btc_dom_change <= self.DOMINANCE_SHIFT_THRESHOLD:
            return signals

        crypto_markets = self._find_matching_markets(
            markets, self.BITCOIN_KEYWORDS | self.GENERAL_KEYWORDS
        )
        for market in crypto_markets:
            title_lower = market.get("title", "").lower()
            if "bitcoin" in title_lower or "btc" in title_lower or "dominance" in title_lower:
                confidence = min(btc_dom_change / 0.05, 1.0)
                signals.append(PipelineSignal(
                    market_id=market.get("id"),
                    signal_type="DATA_MOMENTUM",
                    confidence=confidence,
                    direction="YES",
                    reasoning=f"BTC dominance rising ({btc_dom_change:+.2f}%): Flight to quality indicator",
                ))
        return signals

    def _analyze_trending_cached(self, markets: List[Dict], trending_data) -> List[PipelineSignal]:
        """Trending coin analysis using pre-fetched data + coin cache."""
        signals = []
        if not trending_data or "coins" not in trending_data:
            return signals

        trending_names = []
        for coin in trending_data.get("coins", [])[:10]:
            item = coin.get("item", {})
            trending_names.append(item.get("id", "").lower())
            trending_names.append(item.get("name", "").lower())
            trending_names.append(item.get("symbol", "").lower())

        for market in markets:
            title_lower = market.get("title", "").lower()
            for name in trending_names:
                if not name or len(name) < 3:
                    continue
                if name in title_lower:
                    cached = self._coin_cache.get(name)
                    if cached and cached["change_24h_pct"] > 0:
                        momentum = cached["change_24h_pct"]
                        confidence = min(momentum / self.TRENDING_MOMENTUM_THRESHOLD, 1.0)
                        signals.append(PipelineSignal(
                            market_id=market.get("id"),
                            signal_type="DATA_MOMENTUM",
                            confidence=confidence,
                            direction="YES",
                            reasoning=f"{name.capitalize()} is trending with {momentum*100:+.1f}% momentum",
                        ))
                    break
        return signals

    def _analyze_volatility_cached(self, markets: List[Dict]) -> List[PipelineSignal]:
        """Volatility analysis using pre-fetched coin_markets data."""
        signals = []

        # Find volatile coins from cache
        volatile = {
            k: v for k, v in self._coin_cache.items()
            if isinstance(v, dict) and abs(v.get("change_24h_pct", 0)) > self.VOLATILITY_THRESHOLD
            and v.get("id") == k  # deduplicate — only match on CG id key
        }

        crypto_markets = self._find_matching_markets(markets, self.ALL_CRYPTO_KEYWORDS)
        for market in crypto_markets:
            title_lower = market.get("title", "").lower()
            for cg_id, data in volatile.items():
                coin_name = data.get("name", "")
                symbol = data.get("symbol", "")
                if coin_name in title_lower or symbol in title_lower:
                    change = data["change_24h_pct"]
                    confidence = min(abs(change) / (self.VOLATILITY_THRESHOLD * 2), 1.0)
                    direction_str = "up" if change > 0 else "down"
                    signals.append(PipelineSignal(
                        market_id=market.get("id"),
                        signal_type="DATA_MOMENTUM",
                        confidence=confidence,
                        direction="YES" if change > 0 else "NO",
                        reasoning=f"Unusual 24h volatility: {coin_name.capitalize()} moved {change*100:+.1f}% ({direction_str})",
                    ))
                    break
        return signals

    # ── Sprint 20.5: Always-On Bracket Trader (Option A) ──────────────
    # This method runs every pipeline cycle and generates a BRACKET_MODEL
    # signal for EVERY active bracket market where the model sees edge ≥
    # BRACKET_MIN_EDGE, regardless of momentum, sentiment, or other
    # conditional triggers.  This guarantees persistent participation in
    # every BTC/ETH/SOL/XRP market iteration across all timeframes.

    # ── Sprint 21: DB readers for HyperliquidPriceAgent data ──────────

    async def _read_spot_prices_from_db(self) -> Dict[str, Dict]:
        """Read the latest crypto spot prices from crypto_spot_prices table.

        Returns a dict keyed by CoinGecko ID (e.g., 'bitcoin') with price_usd,
        change_24h_pct, volume_24h — same format as CoinGecko cache entries.
        Returns empty dict if table doesn't exist or has no recent data.
        """
        HL_TO_CG_LOCAL = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
        }
        result: Dict[str, Dict] = {}

        for hl_sym, cg_id in HL_TO_CG_LOCAL.items():
            row = await self._db.fetchone(
                """SELECT mid_price, mark_price, oracle_price, funding_rate,
                          open_interest, day_volume, prev_day_price, timestamp
                   FROM crypto_spot_prices
                   WHERE coin = ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (hl_sym,),
            )
            if not row or not row["mid_price"]:
                continue

            price = float(row["mid_price"])
            prev_day = float(row["prev_day_price"]) if row["prev_day_price"] else 0
            change_24h = 0.0
            if prev_day > 0 and price > 0:
                change_24h = (price - prev_day) / prev_day

            result[cg_id] = {
                "id": cg_id,
                "name": cg_id,
                "symbol": hl_sym.lower(),
                "price_usd": price,
                "change_24h_pct": change_24h,
                "change_7d_pct": 0,
                "market_cap": 0,
                "volume_24h": float(row["day_volume"]) if row["day_volume"] else 0,
            }

        return result

    async def _read_volatility_from_db(self) -> Dict[str, float]:
        """Read the latest realized volatility per coin from crypto_volatility table.

        Returns a dict keyed by CoinGecko ID → annualized daily vol (decimal).
        Only returns values written in the last 10 minutes.
        """
        HL_TO_CG_LOCAL = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
        }
        result: Dict[str, float] = {}

        for hl_sym, cg_id in HL_TO_CG_LOCAL.items():
            row = await self._db.fetchone(
                """SELECT daily_vol, timestamp FROM crypto_volatility
                   WHERE coin = ?
                     AND timestamp > datetime('now', '-10 minutes')
                   ORDER BY timestamp DESC LIMIT 1""",
                (hl_sym,),
            )
            if row and row["daily_vol"]:
                result[cg_id] = float(row["daily_vol"])

        return result

    # ── Sprint 21 Phase 2: Order Book, Funding, Micro-Vol readers ─────

    async def _read_order_book_from_db(self) -> Dict[str, Dict]:
        """Read latest order book snapshot per coin from crypto_order_book.

        Returns dict keyed by CoinGecko ID → {imbalance, spread_bps,
        bid_depth_usd, ask_depth_usd, bid_wall_count, ask_wall_count}.
        Only returns data from last 30 seconds.
        """
        HL_TO_CG_LOCAL = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
        }
        result: Dict[str, Dict] = {}

        for hl_sym, cg_id in HL_TO_CG_LOCAL.items():
            row = await self._db.fetchone(
                """SELECT imbalance, spread_bps, bid_depth_usd, ask_depth_usd,
                          bid_wall_count, ask_wall_count, bid_wall_prices, ask_wall_prices
                   FROM crypto_order_book
                   WHERE coin = ?
                     AND timestamp > datetime('now', '-30 seconds')
                   ORDER BY timestamp DESC LIMIT 1""",
                (hl_sym,),
            )
            if row:
                result[cg_id] = {
                    "imbalance": float(row["imbalance"]) if row["imbalance"] else 0.0,
                    "spread_bps": float(row["spread_bps"]) if row["spread_bps"] else 0.0,
                    "bid_depth_usd": float(row["bid_depth_usd"]) if row["bid_depth_usd"] else 0.0,
                    "ask_depth_usd": float(row["ask_depth_usd"]) if row["ask_depth_usd"] else 0.0,
                    "bid_wall_count": int(row["bid_wall_count"]) if row["bid_wall_count"] else 0,
                    "ask_wall_count": int(row["ask_wall_count"]) if row["ask_wall_count"] else 0,
                    "bid_wall_prices": row["bid_wall_prices"],
                    "ask_wall_prices": row["ask_wall_prices"],
                }

        return result

    async def _read_funding_from_db(self) -> Dict[str, Dict]:
        """Read latest predicted funding rates per coin from crypto_funding.

        Returns dict keyed by CoinGecko ID → {hl_rate, binance_rate, bybit_rate,
        avg_rate, sentiment}. Sentiment: 'bullish' if positive, 'bearish' if negative.
        Only returns data from last 5 minutes.
        """
        HL_TO_CG_LOCAL = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
        }
        result: Dict[str, Dict] = {}

        for hl_sym, cg_id in HL_TO_CG_LOCAL.items():
            row = await self._db.fetchone(
                """SELECT hl_rate, binance_rate, bybit_rate
                   FROM crypto_funding
                   WHERE coin = ? AND source_type = 'predicted'
                     AND timestamp > datetime('now', '-5 minutes')
                   ORDER BY timestamp DESC LIMIT 1""",
                (hl_sym,),
            )
            if row:
                hl = float(row["hl_rate"]) if row["hl_rate"] else 0.0
                bn = float(row["binance_rate"]) if row["binance_rate"] else 0.0
                by = float(row["bybit_rate"]) if row["bybit_rate"] else 0.0
                rates = [r for r in [hl, bn, by] if r != 0]
                avg = sum(rates) / len(rates) if rates else 0.0

                result[cg_id] = {
                    "hl_rate": hl,
                    "binance_rate": bn,
                    "bybit_rate": by,
                    "avg_rate": avg,
                    "sentiment": "bullish" if avg > 0 else ("bearish" if avg < 0 else "neutral"),
                }

        return result

    async def _read_micro_vol_from_db(self) -> Dict[str, Dict]:
        """Read latest 1-minute candle stats per coin from crypto_micro_candles.

        Returns dict keyed by CoinGecko ID → {micro_vol, avg_buy_pressure,
        price_velocity, candle_count}. micro_vol is stdev of 1m returns
        (useful for 15-min bracket vol scaling). Only reads last 15 minutes.
        """
        import math
        HL_TO_CG_LOCAL = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
        }
        result: Dict[str, Dict] = {}

        for hl_sym, cg_id in HL_TO_CG_LOCAL.items():
            rows = await self._db.fetchall(
                """SELECT close, buy_pressure
                   FROM crypto_micro_candles
                   WHERE coin = ?
                     AND timestamp > datetime('now', '-15 minutes')
                   ORDER BY open_time ASC""",
                (hl_sym,),
            )
            if not rows or len(rows) < 3:
                continue

            closes = [float(r["close"]) for r in rows if r["close"]]
            pressures = [float(r["buy_pressure"]) for r in rows if r["buy_pressure"] is not None]

            # Log returns for micro-vol
            returns = []
            for i in range(1, len(closes)):
                if closes[i - 1] > 0:
                    returns.append(math.log(closes[i] / closes[i - 1]))

            if len(returns) < 2:
                continue

            mean_ret = sum(returns) / len(returns)
            variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
            micro_vol = math.sqrt(variance)

            # Price velocity: average return direction (positive = rising)
            velocity = mean_ret * len(returns)  # net move over period

            avg_pressure = sum(pressures) / len(pressures) if pressures else 0.5

            result[cg_id] = {
                "micro_vol": micro_vol,
                "micro_vol_daily": micro_vol * math.sqrt(1440),  # annualize 1m to daily
                "avg_buy_pressure": avg_pressure,
                "price_velocity": velocity,
                "candle_count": len(closes),
            }

        return result

    async def _enumerate_target_markets(self) -> Dict[str, List[Dict]]:
        """Enumerate all active markets for the 4 target crypto assets.

        Uses deterministic ticker-prefix matching (Option C) instead of
        keyword search on titles.  Groups results by CoinGecko ID.

        Returns:
            Dict mapping cg_id → list of market dicts with id, title,
            close_date, and latest yes_price from the prices table.
        """
        result: Dict[str, List[Dict]] = {}

        for asset_name, spec in self.TARGET_SERIES.items():
            cg_id = spec["cg_id"]
            asset_markets = []

            for prefix in spec["ticker_prefixes"]:
                rows = await self._db.fetchall(
                    """SELECT m.id, m.title, m.close_date, m.category,
                              (SELECT p.yes_price FROM prices p
                               WHERE p.market_id = m.id
                               ORDER BY p.timestamp DESC LIMIT 1) AS yes_price
                       FROM markets m
                       WHERE m.status = 'active'
                         AND m.platform = 'kalshi'
                         AND m.id LIKE ? || '%'
                         AND m.close_date <= datetime('now', '+{days} days')
                       ORDER BY m.close_date ASC""".format(
                        days=self.MARKET_HORIZON_DAYS
                    ),
                    (prefix,),
                )
                for row in rows:
                    asset_markets.append(dict(row))

            # Deduplicate by market id (some tickers match multiple prefixes)
            seen = set()
            unique = []
            for m in asset_markets:
                if m["id"] not in seen:
                    seen.add(m["id"])
                    unique.append(m)

            result[cg_id] = unique

        total = sum(len(v) for v in result.values())
        logger.info(
            "Series tracker: %d target markets (BTC=%d, ETH=%d, SOL=%d, XRP=%d)",
            total,
            len(result.get("bitcoin", [])),
            len(result.get("ethereum", [])),
            len(result.get("solana", [])),
            len(result.get("ripple", [])),
        )
        return result

    def _estimate_minutes_remaining(self, market: Dict) -> float:
        """Estimate minutes until market closes from its close_date field.

        Returns a floor of 1.0 to avoid division by zero.
        """
        close_str = market.get("close_date", "")
        if not close_str:
            # Default to 1 day if close_date missing
            return 1440.0

        try:
            from datetime import timezone
            close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta = (close_dt - now).total_seconds() / 60.0
            return max(delta, 1.0)
        except Exception:
            return 1440.0

    def _classify_timeframe(self, minutes_remaining: float) -> str:
        """Classify a market into a human-readable timeframe bucket."""
        if minutes_remaining <= 20:
            return "15min"
        elif minutes_remaining <= 75:
            return "hourly"
        elif minutes_remaining <= 300:
            return "4hour"
        elif minutes_remaining <= 1500:
            return "daily"
        else:
            return "monthly"

    def _bracket_model_signals(
        self, target_markets: Dict[str, List[Dict]]
    ) -> List[PipelineSignal]:
        """Generate BRACKET_MODEL signals for every target market with edge.

        For each market:
        1. Look up current price + 24h vol from the coin cache.
        2. Compute time-scaled volatility: σ_t = daily_vol × √(mins_left / 1440).
        3. Parse the bracket from the title.
        4. Compute model probability using normal CDF with σ_t.
        5. Compare model probability vs. market YES price → compute real EV.
        6. If |edge| ≥ BRACKET_MIN_EDGE, emit a BRACKET_MODEL signal.

        This runs unconditionally — no momentum, sentiment, or other gates.
        """
        import math
        signals = []
        stats = {"scanned": 0, "no_price": 0, "no_bracket": 0, "low_edge": 0, "emitted": 0}

        for cg_id, markets in target_markets.items():
            cached = self._coin_cache.get(cg_id)
            if not cached:
                logger.debug("Bracket model: no cache for %s", cg_id)
                continue

            spot_price = cached["price_usd"]
            if spot_price <= 0:
                continue

            # Sprint 21: Multi-source volatility — prefer micro-vol for short
            # timeframes, realized vol for longer, CoinGecko as last resort.
            realized = getattr(self, "_realized_vol", {})
            micro = getattr(self, "_micro_vol_data", {})
            if cg_id in realized:
                daily_vol = realized[cg_id]
            else:
                daily_vol = abs(cached["change_24h_pct"])

            # Floor daily vol at 1.5% to avoid degenerate σ near zero
            daily_vol = max(daily_vol, 0.015)

            # Sprint 21 Phase 2: Enrichment data for this coin
            book_data = getattr(self, "_order_book_data", {}).get(cg_id, {})
            funding_data = getattr(self, "_funding_data", {}).get(cg_id, {})
            micro_data = micro.get(cg_id, {})

            for market in markets:
                stats["scanned"] += 1
                title = market.get("title", "")
                market_yes_price = market.get("yes_price")

                # Need a market price to compute edge
                if market_yes_price is None or market_yes_price <= 0 or market_yes_price >= 1.0:
                    # No Kalshi price data yet — assume fair value (0.50) as baseline.
                    # This lets the model generate signals for newly discovered markets
                    # before the price polling cycle catches up.
                    market_yes_price = 0.50
                    stats["no_price"] += 1  # Still track for diagnostics

                # Parse bracket — try title first, then fall back to ticker
                bracket = self._parse_crypto_bracket(title, spot_price)
                if not bracket:
                    bracket = self._parse_bracket_from_ticker(market.get("id", ""), spot_price)
                if not bracket:
                    stats["no_bracket"] += 1
                    continue

                bracket_type, lower, upper = bracket

                # Time-scaled volatility
                mins_left = self._estimate_minutes_remaining(market)
                timeframe = self._classify_timeframe(mins_left)

                # Sprint 21: For short timeframes (≤20min), prefer micro-vol
                # from 1m candles — captures recent regime better than 1h vol.
                effective_vol = daily_vol
                if timeframe == "15min" and micro_data.get("micro_vol_daily"):
                    micro_daily = micro_data["micro_vol_daily"]
                    # Blend: 70% micro-vol + 30% daily (micro dominates short term)
                    effective_vol = micro_daily * 0.7 + daily_vol * 0.3
                    effective_vol = max(effective_vol, 0.015)

                sigma_t = effective_vol * math.sqrt(mins_left / 1440.0)
                # Floor sigma_t to avoid near-zero for very short durations
                sigma_t = max(sigma_t, 0.003)

                # Compute model probability
                if bracket_type == "above" and lower is not None:
                    z = (lower - spot_price) / (spot_price * sigma_t)
                    model_prob = 1.0 - self._normal_cdf(z)
                elif bracket_type == "below" and upper is not None:
                    z = (upper - spot_price) / (spot_price * sigma_t)
                    model_prob = self._normal_cdf(z)
                elif bracket_type == "between" and lower is not None and upper is not None:
                    z_low = (lower - spot_price) / (spot_price * sigma_t)
                    z_high = (upper - spot_price) / (spot_price * sigma_t)
                    model_prob = self._normal_cdf(z_high) - self._normal_cdf(z_low)
                else:
                    stats["no_bracket"] += 1
                    continue

                model_prob = max(0.01, min(0.99, model_prob))

                # Sprint 22: Compute edge with spread deduction.
                # Kalshi crypto brackets often have wide spreads or zero
                # liquidity.  Deducting half-spread from edge prevents
                # phantom edge signals where the spread eats the profit.
                # Use pre-fetched Kalshi spread data (loaded in _kalshi_spreads).
                market_id = market.get("id", "")
                kalshi_spread = self._kalshi_spreads.get(market_id, 0.01)
                half_spread = kalshi_spread / 2.0

                edge_yes = model_prob - market_yes_price - half_spread
                edge_no = (1.0 - model_prob) - (1.0 - market_yes_price) - half_spread

                if edge_yes >= edge_no:
                    edge = edge_yes
                    direction = "YES"
                else:
                    edge = edge_no
                    direction = "NO"

                if edge < self.BRACKET_MIN_EDGE:
                    stats["low_edge"] += 1
                    continue

                # ── Sprint 21 Phase 2: Enriched confidence ────────────────
                # Base confidence scales with edge magnitude
                confidence = min(0.55 + edge * 2.5, 0.95)

                # Adjustment 1: Order book imbalance confirms direction
                # If we're betting YES (price goes up) and book is bid-heavy → boost
                # If we're betting NO (price goes down) and book is ask-heavy → boost
                book_adj = 0.0
                imbalance = book_data.get("imbalance", 0)
                if imbalance != 0:
                    if direction == "YES" and bracket_type == "above" and imbalance > 0:
                        book_adj = min(imbalance * 0.05, 0.03)  # up to +3%
                    elif direction == "NO" and bracket_type == "below" and imbalance < 0:
                        book_adj = min(abs(imbalance) * 0.05, 0.03)
                    elif direction == "YES" and bracket_type == "above" and imbalance < -0.3:
                        book_adj = -0.02  # Strong sell wall contradicts our bet
                    elif direction == "NO" and bracket_type == "below" and imbalance > 0.3:
                        book_adj = -0.02

                # Adjustment 2: Funding rate sentiment confirms direction
                # Positive funding = longs paying shorts = bullish consensus
                funding_adj = 0.0
                avg_funding = funding_data.get("avg_rate", 0)
                if avg_funding != 0:
                    if direction == "YES" and bracket_type == "above" and avg_funding > 0:
                        funding_adj = min(abs(avg_funding) * 100, 0.02)  # up to +2%
                    elif direction == "NO" and bracket_type == "below" and avg_funding < 0:
                        funding_adj = min(abs(avg_funding) * 100, 0.02)
                    elif direction == "YES" and bracket_type == "above" and avg_funding < -0.0001:
                        funding_adj = -0.01  # Bearish funding contradicts bullish bet
                    elif direction == "NO" and bracket_type == "below" and avg_funding > 0.0001:
                        funding_adj = -0.01

                # Adjustment 3: Buy pressure from micro-candles confirms momentum
                pressure_adj = 0.0
                buy_pressure = micro_data.get("avg_buy_pressure", 0.5)
                if buy_pressure != 0.5:
                    if direction == "YES" and bracket_type == "above" and buy_pressure > 0.6:
                        pressure_adj = min((buy_pressure - 0.5) * 0.06, 0.02)
                    elif direction == "NO" and bracket_type == "below" and buy_pressure < 0.4:
                        pressure_adj = min((0.5 - buy_pressure) * 0.06, 0.02)

                # Apply adjustments (capped total ±5% swing)
                total_adj = max(-0.05, min(0.05, book_adj + funding_adj + pressure_adj))
                confidence = max(0.50, min(0.95, confidence + total_adj))

                # Real EV: edge itself is the EV (probability points of mispricing)
                ev_estimate = edge

                coin_label = cg_id.capitalize()
                if cg_id == "ripple":
                    coin_label = "XRP"

                # Build enrichment tag for reasoning
                enrich_parts = []
                if book_adj != 0:
                    enrich_parts.append(f"book={imbalance:+.2f}/{book_adj:+.1%}")
                if funding_adj != 0:
                    enrich_parts.append(f"fund={avg_funding:+.4%}/{funding_adj:+.1%}")
                if pressure_adj != 0:
                    enrich_parts.append(f"pres={buy_pressure:.0%}/{pressure_adj:+.1%}")
                enrich_str = f" | {' '.join(enrich_parts)}" if enrich_parts else ""

                signals.append(PipelineSignal(
                    market_id=market["id"],
                    signal_type="BRACKET_MODEL",
                    confidence=round(confidence, 4),
                    ev_estimate=round(ev_estimate, 4),
                    direction=direction,
                    reasoning=(
                        f"[{timeframe}] {coin_label} ${spot_price:,.0f} | "
                        f"bracket {bracket_type} "
                        f"{'$'+f'{lower:,.0f}' if lower else ''}"
                        f"{'-$'+f'{upper:,.0f}' if upper and bracket_type == 'between' else ''} | "
                        f"model={model_prob:.1%} mkt={market_yes_price:.1%} "
                        f"edge={edge:+.1%} σ_t={sigma_t:.3f} "
                        f"({mins_left:.0f}min left)"
                        f"{enrich_str}"
                    ),
                ))
                stats["emitted"] += 1

        logger.info(
            "Bracket model: scanned=%d emitted=%d (no_price=%d no_bracket=%d low_edge=%d)",
            stats["scanned"], stats["emitted"],
            stats["no_price"], stats["no_bracket"], stats["low_edge"],
        )
        return signals

    async def _analyze_price_thresholds(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Generate signals based on price proximity to Kalshi market thresholds.

        Strategy:
            - Extract price targets from market titles (e.g., "Will BTC exceed $100k?")
            - Fetch current BTC/ETH prices from CoinGecko
            - Calculate distance to threshold
            - If within 5% with positive momentum → generate momentum signal

        Args:
            markets: List of market definitions

        Returns:
            List[PipelineSignal]: Price threshold signals
        """
        signals = []

        try:
            # Get price data from CoinGecko
            coingecko = self.clients[0]  # CoinGeckoClient
            price_data = await coingecko.get_price(["bitcoin", "ethereum"], include_24hr_change=True)

            if not price_data or "bitcoin" not in price_data or "ethereum" not in price_data:
                logger.warning("Failed to fetch BTC/ETH price data")
                return signals

            btc_price = price_data["bitcoin"].get("usd", 0)
            eth_price = price_data["ethereum"].get("usd", 0)
            btc_24h_change = price_data["bitcoin"].get("usd_24h_change", 0) / 100
            eth_24h_change = price_data["ethereum"].get("usd_24h_change", 0) / 100

            logger.debug(
                f"Current prices - BTC: ${btc_price:,.0f}, ETH: ${eth_price:,.0f}"
            )

            # Find crypto-related markets
            crypto_markets = self._find_matching_markets(
                markets, self.BITCOIN_KEYWORDS | self.ETHEREUM_KEYWORDS
            )

            for market in crypto_markets:
                threshold = self._extract_price_threshold(market.get("title", ""))
                coin = self._extract_coin_from_title(market.get("title", ""))

                if not threshold or not coin:
                    continue

                # Determine current price and momentum
                if coin.lower() == "bitcoin":
                    current_price = btc_price
                    momentum = btc_24h_change
                elif coin.lower() == "ethereum":
                    current_price = eth_price
                    momentum = eth_24h_change
                else:
                    continue

                # Check proximity to threshold
                distance_to_threshold = abs(current_price - threshold) / threshold
                positive_momentum = momentum >= self.MOMENTUM_MIN_24H_CHANGE

                if (
                    distance_to_threshold <= self.PRICE_PROXIMITY_THRESHOLD
                    and positive_momentum
                ):
                    confidence = self._calculate_threshold_probability(
                        current_price,
                        threshold,
                        self._extract_days_remaining(market),
                        abs(momentum),
                    )

                    signal = PipelineSignal(
                        market_id=market.get("id"),
                        signal_type="DATA_MOMENTUM",
                        confidence=confidence,
                        direction="YES",
                        reasoning=f"{coin} at ${current_price:,.0f} within 5% of ${threshold:,.0f} threshold with +{momentum*100:.1f}% momentum",
                    )
                    signals.append(signal)
                    logger.info(f"Price threshold signal: {market.get('title')}")

        except Exception as e:
            logger.error(f"Error in price threshold analysis: {e}", exc_info=True)

        return signals

    async def _analyze_sentiment_extremes(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Generate signals based on Fear & Greed Index extremes.

        Strategy:
            - Fetch current Fear & Greed Index (0-100 scale)
            - Extreme Fear (<20) → contrarian buy signal (historically precedes rallies)
            - Extreme Greed (>80) → potential sell signal
            - Apply to general crypto markets

        Args:
            markets: List of market definitions

        Returns:
            List[PipelineSignal]: Sentiment-based signals
        """
        signals = []

        try:
            # Get Fear & Greed Index
            feargreed = self.clients[1]  # FearGreedClient
            fgi_data_list = await feargreed.get_index(limit=1)

            if not fgi_data_list or len(fgi_data_list) == 0:
                logger.warning("Failed to fetch Fear & Greed Index")
                return signals

            fgi_data = fgi_data_list[0]
            fgi_value = int(fgi_data.get("value", 0))
            fgi_classification = fgi_data.get("value_classification", "")

            logger.debug(f"Fear & Greed Index: {fgi_value} ({fgi_classification})")

            # Find general crypto markets
            crypto_markets = self._find_matching_markets(
                markets, self.GENERAL_KEYWORDS
            )

            for market in crypto_markets:
                signal_type = None
                reasoning = None

                # Extreme Fear → contrarian buy signal
                if fgi_value < self.EXTREME_FEAR_THRESHOLD:
                    signal_type = "DATA_SENTIMENT"
                    confidence = 1.0 - (fgi_value / self.EXTREME_FEAR_THRESHOLD)
                    reasoning = f"Extreme Fear (FGI={fgi_value}): Historical contrarian buy signal"

                # Extreme Greed → potential sell signal
                elif fgi_value > self.EXTREME_GREED_THRESHOLD:
                    signal_type = "DATA_SENTIMENT"
                    confidence = (fgi_value - self.EXTREME_GREED_THRESHOLD) / (
                        100 - self.EXTREME_GREED_THRESHOLD
                    )
                    reasoning = f"Extreme Greed (FGI={fgi_value}): Potential market saturation"

                if signal_type:
                    signal = PipelineSignal(
                        market_id=market.get("id"),
                        signal_type=signal_type,
                        confidence=confidence,
                        direction="YES" if fgi_value < self.EXTREME_FEAR_THRESHOLD else "NO",
                        reasoning=reasoning,
                    )
                    signals.append(signal)
                    logger.info(f"Sentiment signal: {market.get('title')} (FGI={fgi_value})")

        except Exception as e:
            logger.error(f"Error in sentiment analysis: {e}", exc_info=True)

        return signals

    async def _analyze_dominance_shifts(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Generate signals based on BTC market dominance trends.

        Strategy:
            - Fetch global market data (BTC dominance %)
            - Rising BTC dominance + falling altcoin markets → flight to quality
            - Generate signal for BTC-specific or macro crypto markets

        Args:
            markets: List of market definitions

        Returns:
            List[PipelineSignal]: Dominance shift signals
        """
        signals = []

        try:
            # Get global market data
            coingecko = self.clients[0]  # CoinGeckoClient
            global_data = await coingecko.get_global()

            if not global_data:
                logger.warning("Failed to fetch global market data")
                return signals

            btc_dominance = global_data.get("btc_market_cap_percentage", 0)
            btc_dominance_24h_change = global_data.get(
                "btc_dominance_24h_change", 0
            )

            logger.debug(
                f"BTC Dominance: {btc_dominance:.2f}% (24h change: {btc_dominance_24h_change:+.2f}%)"
            )

            # Analyze dominance shift
            if btc_dominance_24h_change > self.DOMINANCE_SHIFT_THRESHOLD:
                # Rising dominance indicates flight to quality
                crypto_markets = self._find_matching_markets(
                    markets,
                    self.BITCOIN_KEYWORDS | self.GENERAL_KEYWORDS,
                )

                for market in crypto_markets:
                    if "dominance" in market.get("title", "").lower() or "bitcoin" in market.get(
                        "title", ""
                    ).lower():
                        confidence = min(btc_dominance_24h_change / 0.05, 1.0)
                        signal = PipelineSignal(
                            market_id=market.get("id"),
                            signal_type="DATA_MOMENTUM",
                            confidence=confidence,
                            direction="YES",
                            reasoning=f"BTC dominance rising ({btc_dominance_24h_change:+.2f}%): Flight to quality indicator",
                        )
                        signals.append(signal)
                        logger.info(
                            f"Dominance signal: {market.get('title')} "
                            f"(dominance change: {btc_dominance_24h_change:+.2f}%)"
                        )

        except Exception as e:
            logger.error(f"Error in dominance analysis: {e}", exc_info=True)

        return signals

    async def _analyze_trending_coins(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Generate signals based on trending coins with active Kalshi markets.

        Strategy:
            - Fetch trending coins from CoinGecko
            - Check if trending coins have corresponding Kalshi markets
            - Upward momentum in trending coins signals market interest

        Args:
            markets: List of market definitions

        Returns:
            List[PipelineSignal]: Trending momentum signals
        """
        signals = []

        try:
            # Get trending coins
            coingecko = self.clients[0]  # CoinGeckoClient
            trending_data = await coingecko.get_trending()

            if not trending_data or "coins" not in trending_data:
                logger.warning("Failed to fetch trending coins")
                return signals

            trending_coins = [
                coin["item"]["name"].lower()
                for coin in trending_data.get("coins", [])[:10]
            ]

            logger.debug(f"Trending coins: {trending_coins}")

            # Find markets matching trending coins
            for market in markets:
                title = market.get("title", "").lower()
                for coin in trending_coins:
                    if coin in title:
                        # Get price data for the trending coin
                        coin_price_data = await coingecko.get_price([coin], include_24hr_change=True)
                        if coin_price_data and coin in coin_price_data:
                            momentum = coin_price_data[coin].get(
                                "usd_24h_change", 0
                            ) / 100

                            if momentum > 0:
                                confidence = min(
                                    momentum / self.TRENDING_MOMENTUM_THRESHOLD, 1.0
                                )
                                signal = PipelineSignal(
                                    market_id=market.get("id"),
                                    signal_type="DATA_MOMENTUM",
                                    confidence=confidence,
                                    direction="YES",
                                    reasoning=f"{coin.capitalize()} is trending with +{momentum*100:.1f}% momentum",
                                )
                                signals.append(signal)
                                logger.info(
                                    f"Trending signal: {market.get('title')} ({coin} +{momentum*100:.1f}%)"
                                )
                        break

        except Exception as e:
            logger.error(f"Error in trending coins analysis: {e}", exc_info=True)

        return signals

    async def _analyze_volatility_momentum(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Generate signals based on unusual 24h price movements.

        Strategy:
            - Fetch market data for major coins (BTC, ETH, etc.)
            - Identify coins with >10% 24h change
            - Cross-reference with Kalshi markets
            - Strong moves may not be fully priced into prediction markets

        Args:
            markets: List of market definitions

        Returns:
            List[PipelineSignal]: Volatility momentum signals
        """
        signals = []

        try:
            # Get market data for major coins
            coingecko = self.clients[0]  # CoinGeckoClient
            coin_markets = await coingecko.get_coin_markets()

            if not coin_markets:
                logger.warning("Failed to fetch coin market data")
                return signals

            # Identify high-volatility coins
            volatile_coins = [
                (coin["name"].lower(), coin.get("price_change_percentage_24h", 0) / 100)
                for coin in coin_markets
                if abs(coin.get("price_change_percentage_24h", 0) / 100)
                > self.VOLATILITY_THRESHOLD
            ]

            logger.debug(f"Volatile coins (24h >10%): {len(volatile_coins)}")

            # Match volatile coins to markets
            for market in markets:
                title = market.get("title", "").lower()
                for coin_name, change in volatile_coins:
                    if coin_name in title or any(
                        keyword in title
                        for keyword in self.BITCOIN_KEYWORDS
                        | self.ETHEREUM_KEYWORDS
                    ):
                        confidence = min(abs(change) / (self.VOLATILITY_THRESHOLD * 2), 1.0)
                        direction = "up" if change > 0 else "down"

                        signal = PipelineSignal(
                            market_id=market.get("id"),
                            signal_type="DATA_MOMENTUM",
                            confidence=confidence,
                            direction="YES" if change > 0 else "NO",
                            reasoning=f"Unusual 24h volatility: {coin_name.capitalize()} moved {change*100:+.1f}% ({direction})",
                        )
                        signals.append(signal)
                        logger.info(
                            f"Volatility signal: {market.get('title')} ({coin_name} {change*100:+.1f}%)"
                        )
                        break

        except Exception as e:
            logger.error(f"Error in volatility analysis: {e}", exc_info=True)

        return signals

    @staticmethod
    def _find_matching_markets(
        markets: List[Dict], keywords: set
    ) -> List[Dict]:
        """
        Filter markets matching crypto keywords.

        Args:
            markets: List of market definitions
            keywords: Set of keywords to match (case-insensitive)

        Returns:
            List[Dict]: Filtered markets containing any keyword
        """
        matching = []
        for market in markets:
            title = market.get("title", "").lower()
            description = market.get("description", "").lower()
            full_text = f"{title} {description}"

            if any(keyword.lower() in full_text for keyword in keywords):
                matching.append(market)

        return matching

    @staticmethod
    def _extract_price_threshold(title: str) -> Optional[float]:
        """
        Extract price threshold from market title using regex.

        Matches patterns like:
            - "$100,000"
            - "$100k"
            - "100000"

        Args:
            title: Market title string

        Returns:
            Optional[float]: Extracted price threshold or None
        """
        try:
            # Match dollar amounts with commas ($100,000) or shorthand ($100k)
            matches = re.findall(r"\$?([\d,]+)(?:,000)?k?\b", title, re.IGNORECASE)

            if not matches:
                return None

            # Get the first match and clean it
            price_str = matches[0].replace(",", "")

            # Handle 'k' suffix
            if "k" in title.lower():
                price = float(price_str) * 1000
            else:
                price = float(price_str)

            return price if price > 0 else None

        except (ValueError, IndexError):
            return None

    @staticmethod
    def _extract_coin_from_title(title: str) -> Optional[str]:
        """
        Detect which crypto coin a market refers to.

        Checks against known coin keyword sets. Expanded for gap-fill markets.

        Args:
            title: Market title string

        Returns:
            Optional[str]: Coin name or None
        """
        title_lower = title.lower()

        if any(kw in title_lower for kw in CryptoPipeline.BITCOIN_KEYWORDS):
            return "Bitcoin"
        elif any(kw in title_lower for kw in CryptoPipeline.ETHEREUM_KEYWORDS):
            return "Ethereum"
        elif any(kw in title_lower for kw in CryptoPipeline.SOLANA_KEYWORDS):
            return "Solana"
        elif any(kw in title_lower for kw in ("xrp", "ripple", "kxxrp")):
            return "XRP"
        elif any(kw in title_lower for kw in ("doge", "dogecoin", "kxdoge")):
            return "Dogecoin"
        elif any(kw in title_lower for kw in ("bnb", "binance", "kxbnb")):
            return "BNB"
        elif any(kw in title_lower for kw in ("cardano", "ada", "kxada")):
            return "Cardano"

        return None

    def _get_market_price(self, market_id: str) -> Optional[float]:
        """
        Look up current price for a market from database.

        Args:
            market_id: Kalshi market ID

        Returns:
            Optional[float]: Current market price (0-1) or None if not found
        """
        try:
            # This would integrate with Sibyl's market database
            # Placeholder for actual implementation
            logger.debug(f"Market price lookup for {market_id}")
            return None
        except Exception as e:
            logger.error(f"Error fetching market price for {market_id}: {e}")
            return None

    @staticmethod
    def _calculate_threshold_probability(
        current_price: float, threshold: float, days_remaining: int, volatility: float
    ) -> float:
        """
        Estimate probability of reaching price threshold using simple model.

        Simple model based on:
            - Distance to threshold
            - Time remaining
            - Historical volatility
            - Random walk approximation

        Args:
            current_price: Current price of asset
            threshold: Target price threshold
            days_remaining: Days until market expiration
            volatility: Historical volatility (as decimal, e.g., 0.10 for 10%)

        Returns:
            float: Probability estimate (0.0 to 1.0)
        """
        if days_remaining <= 0 or current_price <= 0:
            return 0.0

        try:
            # Distance to threshold as percentage
            distance_pct = abs(threshold - current_price) / current_price

            # Expected move based on volatility and time
            # Using approximation: expected_move ~ volatility * sqrt(time_in_years)
            time_in_years = days_remaining / 365.0
            expected_move = volatility * (time_in_years ** 0.5)

            # Probability based on how many standard deviations away threshold is
            # Using simplified normal distribution approximation
            if expected_move > 0:
                std_devs = distance_pct / expected_move
                # Rough approximation: probability decreases with distance
                probability = max(0.0, 1.0 - (std_devs / 3.0))
            else:
                probability = 0.0

            return min(probability, 1.0)

        except Exception as e:
            logger.error(
                f"Error calculating threshold probability: {e}", exc_info=True
            )
            return 0.0

    @staticmethod
    def _extract_days_remaining(market: Dict) -> int:
        """
        Extract days remaining until market expiration.

        Args:
            market: Market definition dict

        Returns:
            int: Days remaining (default 30 if not found)
        """
        try:
            expiry = market.get("expiration_date")
            if not expiry:
                return 30  # Default assumption

            if isinstance(expiry, str):
                expiry_date = datetime.fromisoformat(expiry)
            else:
                expiry_date = expiry

            days = (expiry_date - datetime.now()).days
            return max(days, 0)

        except Exception:
            return 30  # Default to 30 days on error
