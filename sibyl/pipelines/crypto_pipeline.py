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
    DEDUP_WINDOW_MINUTES = 15  # Sprint 16: crypto prices move fast

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

    # Threshold constants for signal generation
    PRICE_PROXIMITY_THRESHOLD = 0.05  # 5% distance to threshold
    MOMENTUM_MIN_24H_CHANGE = 0.02  # 2% minimum momentum
    EXTREME_FEAR_THRESHOLD = 20  # FGI score for extreme fear
    EXTREME_GREED_THRESHOLD = 80  # FGI score for extreme greed
    DOMINANCE_SHIFT_THRESHOLD = 0.01  # 1% dominance change
    TRENDING_MOMENTUM_THRESHOLD = 0.10  # 10% 24h change
    VOLATILITY_THRESHOLD = 0.10  # 10% 24h change for volatility signal

    def _create_clients(self) -> List:
        """
        Initialize data clients for crypto pipeline.

        Returns:
            List[BasePipeline]: Initialized client instances
                - CoinGeckoClient: Real-time crypto market data
                - FearGreedClient: Market sentiment index
        """
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

            # ── Run all analyses from cached data (0 extra API calls) ───
            signals.extend(self._analyze_price_thresholds_cached(markets))
            signals.extend(self._analyze_daily_brackets_cached(markets))
            signals.extend(self._analyze_monthly_extremes_cached(markets))
            signals.extend(self._analyze_sentiment_cached(markets, fgi_data_list))
            signals.extend(self._analyze_dominance_cached(markets, global_data))
            signals.extend(self._analyze_trending_cached(markets, trending_data))
            signals.extend(self._analyze_volatility_cached(markets))

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
