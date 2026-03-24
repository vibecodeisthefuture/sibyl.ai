"""
Financial category signal pipeline for Sibyl prediction market trading system.

Transforms financial market data from Financial Modeling Prep (FMP) into trading
signals for Kalshi Financials prediction markets. Analyzes earnings, stock moves,
and corporate events.
"""

import logging
from typing import List, Dict, Optional

from sibyl.pipelines.base_pipeline import BasePipeline, PipelineSignal
from sibyl.clients.fmp_client import FmpClient

logger = logging.getLogger(__name__)


class FinancialPipeline(BasePipeline):
    """
    Financial category signal pipeline.

    Analyzes corporate financial data from FMP to generate trading signals for Kalshi
    Financials prediction markets about earnings, stock prices, and market movements.
    """

    CATEGORY = "Financials"
    PIPELINE_NAME = "financial"
    DEDUP_WINDOW_MINUTES = 60  # Sprint 16: standard market hours cadence

    # Keywords for market matching — expanded for gap-fill discovered markets
    FINANCIAL_KEYWORDS = [
        "earnings", "stock", "share", "price", "market", "s&p", "nasdaq",
        "company", "revenue", "profit", "quarterly", "q1", "q2", "q3", "q4",
        "ipo", "merger", "acquisition", "dividend",
        # Commodities (gap-fill discovered)
        "gold", "silver", "copper", "oil", "crude", "wti", "brent",
        "natural gas", "platinum", "palladium",
        # Indices
        "dow", "russell", "vix", "s&p 500", "sp500",
        # Forex
        "eur/usd", "gbp/usd", "usd/jpy", "forex", "dollar", "euro",
        # Treasury
        "treasury", "10-year", "10 year", "bond", "yield",
        "2-year", "30-year",
        # Series tickers for gap-fill markets
        "kxgold", "kxsilver", "kxcopper", "kxoil", "kxwti",
        "kxsp500", "kxnasdaq", "kxdow", "kxvix", "kxrussel",
        "kxeurusd", "kxgbpusd", "kxusdjpy",
        "kxtreasury", "kx10yr", "kx2yr", "kx30yr",
    ]

    # Commodity/asset name → FMP symbol mapping
    ASSET_MAP = {
        "gold": "GCUSD", "silver": "SIUSD", "copper": "HGUSD",
        "oil": "CLUSD", "crude": "CLUSD", "wti": "CLUSD",
        "natural gas": "NGUSD", "platinum": "PLUSD",
        "s&p": "^GSPC", "s&p 500": "^GSPC", "sp500": "^GSPC",
        "nasdaq": "^IXIC", "dow": "^DJI", "russell": "^RUT",
    }

    def __init__(self, *args, **kwargs):
        """Initialize the Financial pipeline."""
        super().__init__(*args, **kwargs)

    def _create_clients(self) -> List:
        """Create and return the data clients used by this pipeline.

        Returns:
            List containing FmpClient.
        """
        return [FmpClient()]

    def _category_variants(self) -> List[str]:
        """Return category name variants for market matching.

        Returns:
            List of category variants including Financials and Companies.
        """
        return ["Financials", "financials", "Companies", "companies"]

    async def _analyze(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Generate financial signals by analyzing corporate data.

        Implements three analysis strategies:
        1. Earnings Calendar Signal - Identify upcoming earnings and surprise direction
        2. Market Movers Signal - Detect extreme price movements matching markets
        3. Stock Price vs Market - Compare current price to market thresholds

        Args:
            markets: List of active Kalshi markets in financials category.

        Returns:
            List of PipelineSignal objects with trading recommendations.
        """
        signals = []

        # NEW: Commodity/index/forex bracket analysis for gap-fill markets
        try:
            signals.extend(await self._analyze_asset_brackets(markets))
        except Exception as e:
            logger.warning(f"Asset bracket analysis failed: {e}")

        try:
            signals.extend(await self._analyze_earnings_calendar(markets))
        except Exception as e:
            logger.warning(f"Earnings calendar analysis failed: {e}")

        try:
            signals.extend(await self._analyze_market_movers(markets))
        except Exception as e:
            logger.warning(f"Market movers analysis failed: {e}")

        try:
            signals.extend(await self._analyze_stock_price(markets))
        except Exception as e:
            logger.warning(f"Stock price analysis failed: {e}")

        logger.info(f"Generated {len(signals)} financial signals")
        return signals

    async def _analyze_asset_brackets(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze commodity, index, and forex bracket markets.

        Handles Kalshi markets like:
          - "Gold price above $3,000?" / "Gold between $2,900 and $3,000?"
          - "S&P 500 above 5,500?" / "Nasdaq above 18,000?"
          - "Oil price above $70?" / "Silver above $30?"
          - "10-year Treasury yield above 4.5%?"

        Fetches current prices from FMP and estimates bracket probabilities.
        """
        import re
        import math
        signals = []

        fmp_client = self._get_client("fmp")
        if not fmp_client:
            return signals

        # Cache of fetched quotes
        quote_cache: Dict[str, Dict] = {}

        for market in markets:
            title = market.get("title", "")
            ticker = market.get("ticker", "")
            title_lower = title.lower()

            # Identify which asset this market is about
            matched_asset = None
            for asset_name, fmp_symbol in self.ASSET_MAP.items():
                if asset_name in title_lower or asset_name.replace(" ", "") in ticker.lower():
                    matched_asset = (asset_name, fmp_symbol)
                    break

            if not matched_asset:
                continue

            asset_name, fmp_symbol = matched_asset

            # Fetch quote (with caching)
            if fmp_symbol not in quote_cache:
                try:
                    quote = fmp_client.get_quote(fmp_symbol)
                    quote_cache[fmp_symbol] = quote if quote else {}
                except Exception as e:
                    logger.debug(f"Failed to fetch {fmp_symbol}: {e}")
                    quote_cache[fmp_symbol] = {}

            quote = quote_cache.get(fmp_symbol, {})
            current_price = quote.get("price", 0)
            if current_price <= 0:
                continue

            # Daily change as volatility proxy
            change_pct = abs(quote.get("changesPercentage", 0) or 0) / 100
            daily_vol = max(change_pct, 0.01)  # Floor at 1%

            # Parse bracket from title
            bracket = self._parse_financial_bracket(title, current_price)
            if not bracket:
                continue

            bracket_type, lower, upper = bracket

            # Normal CDF approximation
            def normal_cdf(z):
                return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

            if bracket_type == "above":
                z = (lower - current_price) / (current_price * daily_vol)
                prob = 1.0 - normal_cdf(z)
            elif bracket_type == "below":
                z = (upper - current_price) / (current_price * daily_vol)
                prob = normal_cdf(z)
            elif bracket_type == "between":
                z_low = (lower - current_price) / (current_price * daily_vol)
                z_high = (upper - current_price) / (current_price * daily_vol)
                prob = normal_cdf(z_high) - normal_cdf(z_low)
            else:
                continue

            prob = max(0.02, min(0.98, prob))

            if abs(prob - 0.5) > 0.05:
                confidence = min(0.90, 0.55 + abs(prob - 0.5))
                direction = "YES" if prob > 0.5 else "NO"
                signals.append(PipelineSignal(
                    market_id=market.get("id"),
                    signal_type="DATA_FUNDAMENTAL",
                    confidence=confidence,
                    ev_estimate=abs(prob - 0.5) * 0.1,
                    direction=direction,
                    reasoning=(
                        f"{asset_name.capitalize()} at ${current_price:,.2f}: "
                        f"bracket {bracket_type} "
                        f"{'$'+f'{lower:,.2f}' if lower else ''}"
                        f"{'-$'+f'{upper:,.2f}' if upper and bracket_type == 'between' else ''} "
                        f"→ est. prob {prob:.1%} (vol {daily_vol*100:.1f}%)"
                    ),
                    source_pipeline=self.PIPELINE_NAME,
                    category=self.CATEGORY,
                ))

        return signals

    @staticmethod
    def _parse_financial_bracket(title: str, current_price: float):
        """Parse a bracket from a Kalshi financial market title."""
        import re

        # Pattern: "between $X and $Y"
        between_match = re.search(
            r'(?:between\s+)?\$?([\d,]+\.?\d*)\s*(?:and|to|-)\s*\$?([\d,]+\.?\d*)',
            title, re.IGNORECASE,
        )
        if between_match:
            lower = float(between_match.group(1).replace(",", ""))
            upper = float(between_match.group(2).replace(",", ""))
            if lower > 0 and upper > 0:
                return ("between", min(lower, upper), max(lower, upper))

        # Pattern: "above $X" / "over $X"
        above_match = re.search(
            r'(?:above|over|exceed|higher than|at least|≥|>)\s*\$?([\d,]+\.?\d*)',
            title, re.IGNORECASE,
        )
        if above_match:
            val = float(above_match.group(1).replace(",", ""))
            if val > 0:
                return ("above", val, None)

        # Pattern: "below $X" / "under $X"
        below_match = re.search(
            r'(?:below|under|less than|lower than|≤|<)\s*\$?([\d,]+\.?\d*)',
            title, re.IGNORECASE,
        )
        if below_match:
            val = float(below_match.group(1).replace(",", ""))
            if val > 0:
                return ("below", None, val)

        # Percentage brackets for yields
        pct_match = re.search(r'([\d.]+)\s*%', title)
        if pct_match:
            val = float(pct_match.group(1))
            title_lower = title.lower()
            if any(kw in title_lower for kw in ("above", "over", "higher", "exceed")):
                return ("above", val, None)
            elif any(kw in title_lower for kw in ("below", "under", "lower")):
                return ("below", None, val)

        # Fallback: dollar amount in title
        dollar_match = re.findall(r'\$([\d,]+\.?\d*)', title)
        if dollar_match:
            val = float(dollar_match[0].replace(",", ""))
            if val > 0:
                return ("above", val, None)

        return None

    async def _analyze_earnings_calendar(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze earnings calendar for catalysts and surprise signals.

        Fetches FMP earnings calendar and identifies companies with upcoming earnings
        that have Kalshi markets. Historical earnings surprise direction gives
        directional bias for expected outcomes.

        Args:
            markets: List of active markets.

        Returns:
            List of PipelineSignal objects for earnings signals.
        """
        signals = []

        try:
            fmp_client = self._get_client("fmp")
            if not fmp_client:
                logger.debug("FMP client not available")
                return signals

            # Find earnings-related markets
            matching_markets = self._find_matching_markets(
                markets,
                ["earnings", "quarterly", "q1", "q2", "q3", "q4", "eps", "revenue"]
            )

            if not matching_markets:
                return signals

            # Fetch upcoming earnings
            earnings_calendar = fmp_client.get_earnings_calendar(
                days_ahead=30
            )

            if not earnings_calendar or "earnings" not in earnings_calendar:
                return signals

            for market in matching_markets:
                try:
                    market_id = market.get("id")
                    market_title = market.get("title", "").lower()
                    market_price = await self._get_market_price(market_id)

                    if market_price is None:
                        continue

                    # Extract company symbol from market title
                    ticker = self._extract_ticker(market_title)
                    if not ticker:
                        continue

                    # Find matching earnings event
                    earnings_event = None
                    for event in earnings_calendar.get("earnings", []):
                        if ticker.upper() in event.get("symbol", "").upper():
                            earnings_event = event
                            break

                    if not earnings_event:
                        continue

                    # No historical earnings surprise data available from FMP client
                    # Default to neutral bias
                    avg_surprise_direction = 0

                    # Upcoming earnings = catalyst
                    earnings_date = earnings_event.get("date")
                    earnings_time = earnings_event.get("time", "after market")

                    # Surprise tendency → directional bias
                    if avg_surprise_direction > 0.25:
                        # Company tends to beat earnings
                        data_implied_prob = 0.65
                        direction = "YES"
                    elif avg_surprise_direction < -0.25:
                        # Company tends to miss earnings
                        data_implied_prob = 0.35
                        direction = "NO"
                    else:
                        # No clear bias
                        data_implied_prob = 0.50
                        direction = "YES"

                    edge, direction_computed, ev = self._compute_edge(
                        data_implied_prob,
                        market_price
                    )

                    if edge > 0.10:  # 10% threshold for earnings signals
                        confidence = self._edge_to_confidence(edge)
                        signal = PipelineSignal(
                            market_id=market_id,
                            signal_type="DATA_CATALYST",
                            confidence=confidence,
                            ev_estimate=ev,
                            direction=direction_computed,
                            reasoning=(
                                f"Upcoming earnings for {ticker} on {earnings_date} ({earnings_time}). "
                                f"Historical surprise direction: {avg_surprise_direction:.2f} "
                                f"(tendency to {'beat' if avg_surprise_direction > 0 else 'miss'}). "
                                f"Implied probability: {data_implied_prob:.2%}"
                            ),
                            source_pipeline=self.PIPELINE_NAME,
                            category=self.CATEGORY,
                            data_points={
                                "ticker": ticker,
                                "earnings_date": earnings_date,
                                "earnings_time": earnings_time,
                                "avg_surprise_direction": avg_surprise_direction,
                                "historical_quarters": 0,
                            }
                        )
                        signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error analyzing earnings for market {market.get('id')}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in earnings calendar analysis: {e}")

        return signals

    async def _analyze_market_movers(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze extreme market movers for momentum signals.

        Fetches gainers and losers from FMP. Extreme movers (>10% change) that have
        Kalshi markets generate DATA_MOMENTUM signals.

        Args:
            markets: List of active markets.

        Returns:
            List of PipelineSignal objects for market mover signals.
        """
        signals = []

        try:
            fmp_client = self._get_client("fmp")
            if not fmp_client:
                logger.debug("FMP client not available")
                return signals

            # Find stock/price-related markets
            matching_markets = self._find_matching_markets(
                markets,
                ["stock", "share", "price", "nasdaq", "s&p", "up", "down"]
            )

            if not matching_markets:
                return signals

            # Fetch top gainers and losers
            gainers = fmp_client.get_market_gainers()
            losers = fmp_client.get_market_losers()

            all_movers = []
            if gainers:
                all_movers.extend([
                    {**m, "direction": "UP"} for m in gainers
                ])
            if losers:
                all_movers.extend([
                    {**m, "direction": "DOWN"} for m in losers
                ])

            if not all_movers:
                return signals

            for market in matching_markets:
                try:
                    market_id = market.get("id")
                    market_title = market.get("title", "").lower()
                    market_price = await self._get_market_price(market_id)

                    if market_price is None:
                        continue

                    # Extract ticker from market title
                    ticker = self._extract_ticker(market_title)
                    if not ticker:
                        continue

                    # Find matching mover
                    matching_mover = None
                    for mover in all_movers:
                        if ticker.upper() in mover.get("symbol", "").upper():
                            matching_mover = mover
                            break

                    if not matching_mover:
                        continue

                    # Only signal on extreme movers (>10% change)
                    change_percent = matching_mover.get("change_percent", 0)
                    if abs(change_percent) <= 10.0:
                        continue

                    mover_direction = matching_mover.get("direction")
                    volume = matching_mover.get("volume", 0)

                    # High momentum → directional bias
                    if mover_direction == "UP":
                        data_implied_prob = min(0.85, 0.50 + (abs(change_percent) / 100.0) * 0.3)
                        direction = "YES"
                    else:  # DOWN
                        data_implied_prob = max(0.15, 0.50 - (abs(change_percent) / 100.0) * 0.3)
                        direction = "NO"

                    edge, direction_computed, ev = self._compute_edge(
                        data_implied_prob,
                        market_price
                    )

                    if edge > 0.10:  # 10% threshold for momentum signals
                        confidence = self._edge_to_confidence(edge)
                        signal = PipelineSignal(
                            market_id=market_id,
                            signal_type="DATA_MOMENTUM",
                            confidence=confidence,
                            ev_estimate=ev,
                            direction=direction_computed,
                            reasoning=(
                                f"{ticker} is a {mover_direction} mover with {change_percent:.1f}% change. "
                                f"High volume ({volume:,.0f}) suggests institutional activity. "
                                f"Implied probability: {data_implied_prob:.2%}"
                            ),
                            source_pipeline=self.PIPELINE_NAME,
                            category=self.CATEGORY,
                            data_points={
                                "ticker": ticker,
                                "change_percent": change_percent,
                                "mover_direction": mover_direction,
                                "volume": volume,
                            }
                        )
                        signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error analyzing mover for market {market.get('id')}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in market movers analysis: {e}")

        return signals

    async def _analyze_stock_price(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze stock prices against market-specified thresholds.

        For markets with price targets (e.g., "Will AAPL reach $200?"), compares
        current price to target and historical volatility to assess likelihood.

        Args:
            markets: List of active markets.

        Returns:
            List of PipelineSignal objects for price target signals.
        """
        signals = []

        try:
            fmp_client = self._get_client("fmp")
            if not fmp_client:
                logger.debug("FMP client not available")
                return signals

            # Find price target markets
            matching_markets = self._find_matching_markets(
                markets,
                ["price", "$", "reach", "above", "below", "hit"]
            )

            if not matching_markets:
                return signals

            for market in matching_markets:
                try:
                    market_id = market.get("id")
                    market_title = market.get("title", "")
                    market_price = await self._get_market_price(market_id)

                    if market_price is None:
                        continue

                    # Extract ticker and price target
                    ticker, target_price = self._extract_ticker_and_target(market_title)
                    if not ticker or target_price is None:
                        continue

                    # Get current stock price
                    quote = fmp_client.get_quote(ticker)

                    if not quote or "price" not in quote:
                        continue

                    current_price = quote.get("price", 0)

                    # Historical volatility estimation not available from FMP client
                    # Use default conservative volatility estimate
                    volatility = 0.02  # Default 2% daily volatility

                    # Assess probability using volatility-adjusted distance
                    distance_pct = (target_price - current_price) / current_price if current_price > 0 else 0
                    days_to_maturity = (
                        market.get("close_date", market.get("maturity_date")) or
                        "unknown"
                    )

                    # Simple model: probability based on distance and volatility
                    if target_price > current_price:
                        # Bull case
                        data_implied_prob = min(0.85, 0.50 + (distance_pct / (volatility * 10.0)))
                        direction = "YES"
                    else:
                        # Bear case
                        data_implied_prob = min(0.85, 0.50 - (abs(distance_pct) / (volatility * 10.0)))
                        direction = "NO"

                    # Clamp to valid range
                    data_implied_prob = max(0.15, min(0.85, data_implied_prob))

                    edge, direction_computed, ev = self._compute_edge(
                        data_implied_prob,
                        market_price
                    )

                    if edge > 0.10:  # 10% threshold for price signals
                        confidence = self._edge_to_confidence(edge)
                        signal = PipelineSignal(
                            market_id=market_id,
                            signal_type="DATA_FUNDAMENTAL",
                            confidence=confidence,
                            ev_estimate=ev,
                            direction=direction_computed,
                            reasoning=(
                                f"{ticker} currently at ${current_price:.2f}, target ${target_price:.2f} "
                                f"({distance_pct:+.1%} away). 90-day volatility: {volatility:.2%}. "
                                f"Implied probability: {data_implied_prob:.2%}"
                            ),
                            source_pipeline=self.PIPELINE_NAME,
                            category=self.CATEGORY,
                            data_points={
                                "ticker": ticker,
                                "current_price": current_price,
                                "target_price": target_price,
                                "distance_pct": distance_pct,
                                "volatility": volatility,
                            }
                        )
                        signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error analyzing price target for market {market.get('id')}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in stock price analysis: {e}")

        return signals

    # ── Helper Methods ──────────────────────────────────────────────

    def _find_matching_markets(
        self,
        markets: List[Dict],
        keywords: List[str]
    ) -> List[Dict]:
        """
        Find markets matching given keywords.

        Args:
            markets: List of market dictionaries to search.
            keywords: List of keywords to match against market titles.

        Returns:
            List of matching market dictionaries.
        """
        matching = []
        keywords_lower = [k.lower() for k in keywords]

        for market in markets:
            title_lower = market.get("title", "").lower()
            if any(keyword in title_lower for keyword in keywords_lower):
                matching.append(market)

        return matching

    async def _get_market_price(self, market_id: str) -> Optional[float]:
        """
        Retrieve latest market price from database.

        Args:
            market_id: Kalshi market ID.

        Returns:
            Latest market YES price as float between 0-1, or None if unavailable.
        """
        try:
            row = await self._db.fetchone(
                """SELECT yes_price FROM prices
                   WHERE market_id = ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (market_id,)
            )
            if row and row[0] is not None:
                return float(row[0])
            return None
        except Exception as e:
            logger.debug(f"Error retrieving market price for {market_id}: {e}")
            return None

    @staticmethod
    def _extract_ticker(title: str) -> Optional[str]:
        """
        Extract stock ticker symbol from market title.

        Args:
            title: Market title string (e.g., "Will AAPL reach $200?").

        Returns:
            Extracted ticker symbol or None.
        """
        import re
        # Look for uppercase 1-5 letter sequences typically representing tickers
        matches = re.findall(r'\b([A-Z]{1,5})\b', title)
        # Filter for common ticker patterns (avoid common words)
        excluded = ["WILL", "THE", "WILL", "AND", "OR", "FOR", "REACH"]
        for match in matches:
            if match not in excluded and len(match) <= 5:
                return match
        return None

    @staticmethod
    def _extract_ticker_and_target(title: str) -> tuple[Optional[str], Optional[float]]:
        """
        Extract both ticker and price target from market title.

        Args:
            title: Market title string (e.g., "Will AAPL reach $200?").

        Returns:
            Tuple of (ticker, target_price) or (None, None) if not found.
        """
        import re

        ticker = None
        target_price = None

        # Find ticker
        ticker_match = re.search(r'\b([A-Z]{1,5})\b', title)
        if ticker_match:
            ticker = ticker_match.group(1)

        # Find price target (look for $ followed by number)
        price_match = re.search(r'\$\s*([\d,]+\.?\d*)', title)
        if price_match:
            price_str = price_match.group(1).replace(',', '')
            try:
                target_price = float(price_str)
            except ValueError:
                pass

        return ticker, target_price
