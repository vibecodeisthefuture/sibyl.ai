"""
Economic signal pipeline for Sibyl prediction market trading system.

Transforms raw economic data from FRED, BLS, and BEA API clients into
actionable trading signals for Kalshi economics prediction markets.
"""

import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta

from sibyl.pipelines.base_pipeline import BasePipeline, PipelineSignal
from sibyl.clients.fred_client import FredClient, FRED_SERIES
from sibyl.clients.bls_client import BlsClient
from sibyl.clients.bea_client import BeaClient


logger = logging.getLogger(__name__)


class EconomicsPipeline(BasePipeline):
    """
    Economic signals pipeline for prediction market trading.

    Analyzes macroeconomic indicators from FRED, BLS, and BEA to generate
    trading signals for Kalshi economics prediction markets.
    """

    CATEGORY = "Economics"
    PIPELINE_NAME = "economics"
    DEDUP_WINDOW_MINUTES = 240  # Sprint 16: economic data releases are sparse

    def _create_clients(self) -> List:
        """Initialize API clients for economic data sources."""
        return [FredClient(), BlsClient(), BeaClient()]

    def _category_variants(self) -> List[str]:
        """Return category name variants for market matching."""
        return ["Economics", "economics", "Financials", "financials"]

    # Bracket parsing for Kalshi economics markets
    # Maps indicator keywords to FRED series IDs and latest values
    INDICATOR_KEYWORDS = {
        "cpi": {"series": "CPIAUCSL", "unit": "%", "type": "yoy"},
        "pce": {"series": "PCEPILFE", "unit": "%", "type": "yoy"},
        "core pce": {"series": "PCEPILFE", "unit": "%", "type": "yoy"},
        "inflation": {"series": "CPIAUCSL", "unit": "%", "type": "yoy"},
        "unemployment": {"series": "UNRATE", "unit": "%", "type": "level"},
        "jobless": {"series": "UNRATE", "unit": "%", "type": "level"},
        "nonfarm": {"series": "PAYEMS", "unit": "K", "type": "mom_change"},
        "payroll": {"series": "PAYEMS", "unit": "K", "type": "mom_change"},
        "gdp": {"series": "GDPC1", "unit": "%", "type": "qoq"},
        "fed fund": {"series": "FEDFUNDS", "unit": "%", "type": "level"},
        "interest rate": {"series": "FEDFUNDS", "unit": "%", "type": "level"},
    }

    async def _analyze(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Generate economic signals by analyzing multiple indicators.

        Args:
            markets: List of active Kalshi markets

        Returns:
            List of PipelineSignal objects with trading recommendations
        """
        signals = []

        # NEW: Bracket analysis for gap-fill discovered markets
        try:
            signals.extend(await self._analyze_econ_brackets(markets))
        except Exception as e:
            logger.warning(f"Economic bracket analysis failed: {e}")

        try:
            signals.extend(await self._analyze_fed_funds(markets))
        except Exception as e:
            logger.warning(f"Fed funds analysis failed: {e}")

        try:
            signals.extend(await self._analyze_inflation(markets))
        except Exception as e:
            logger.warning(f"Inflation analysis failed: {e}")

        try:
            signals.extend(await self._analyze_unemployment(markets))
        except Exception as e:
            logger.warning(f"Unemployment analysis failed: {e}")

        try:
            signals.extend(await self._analyze_gdp(markets))
        except Exception as e:
            logger.warning(f"GDP analysis failed: {e}")

        try:
            signals.extend(await self._analyze_yield_curve(markets))
        except Exception as e:
            logger.warning(f"Yield curve analysis failed: {e}")

        try:
            signals.extend(await self._analyze_jobs(markets))
        except Exception as e:
            logger.warning(f"Jobs data analysis failed: {e}")

        logger.info(f"Generated {len(signals)} economic signals")
        return signals

    async def _analyze_econ_brackets(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze bracket-style economics markets using FRED data.

        Handles Kalshi markets like:
          - "CPI YoY above 3.0%?" / "CPI between 2.5% and 3.0%?"
          - "Unemployment rate below 4.0%?"
          - "Nonfarm payrolls above 200K?"
          - "GDP growth above 2.0%?"
          - "Fed funds rate above 4.5%?"

        Fetches latest indicator values from FRED, estimates probability of
        bracket outcome using recent trend + volatility.
        """
        import re
        import math
        signals = []

        fred_client = next((c for c in self.clients if isinstance(c, FredClient)), None)
        if not fred_client:
            return signals

        # Pre-fetch key FRED series we'll need
        series_cache: Dict[str, List[Dict]] = {}

        for market in markets:
            title = market.get("title", "")
            title_lower = title.lower()

            # Identify which indicator this market is about
            matched_indicator = None
            for keyword, info in self.INDICATOR_KEYWORDS.items():
                if keyword in title_lower:
                    matched_indicator = (keyword, info)
                    break

            if not matched_indicator:
                continue

            kw, indicator_info = matched_indicator
            series_id = indicator_info["series"]
            indicator_type = indicator_info["type"]

            # Fetch series data (with caching)
            if series_id not in series_cache:
                try:
                    obs = await fred_client.get_series_observations(series_id, limit=15)
                    if obs:
                        # Filter out missing values
                        obs = [o for o in obs if o.get("value") not in (None, "", ".")]
                        series_cache[series_id] = obs
                    else:
                        series_cache[series_id] = []
                except Exception as e:
                    logger.debug(f"Failed to fetch {series_id}: {e}")
                    series_cache[series_id] = []

            obs = series_cache.get(series_id, [])
            if len(obs) < 2:
                continue

            # Compute the current indicator value
            try:
                if indicator_type == "yoy" and len(obs) >= 13:
                    current = float(obs[-1]["value"])
                    year_ago = float(obs[-13]["value"])
                    if year_ago > 0:
                        indicator_value = ((current - year_ago) / year_ago) * 100
                    else:
                        continue
                elif indicator_type == "mom_change" and len(obs) >= 2:
                    indicator_value = float(obs[-1]["value"]) - float(obs[-2]["value"])
                elif indicator_type == "level":
                    indicator_value = float(obs[-1]["value"])
                elif indicator_type == "qoq" and len(obs) >= 2:
                    current = float(obs[-1]["value"])
                    prev = float(obs[-2]["value"])
                    if prev > 0:
                        indicator_value = ((current - prev) / prev) * 100
                    else:
                        continue
                else:
                    indicator_value = float(obs[-1]["value"])
            except (ValueError, TypeError):
                continue

            # Compute recent volatility (std dev of last few changes)
            try:
                recent_vals = [float(o["value"]) for o in obs[-5:]]
                changes = [recent_vals[i] - recent_vals[i-1] for i in range(1, len(recent_vals))]
                if changes:
                    vol = max(0.05, (sum(c**2 for c in changes) / len(changes)) ** 0.5)
                else:
                    vol = 0.1
            except (ValueError, TypeError):
                vol = 0.1

            # Parse bracket from title
            bracket = self._parse_econ_bracket(title, indicator_value)
            if not bracket:
                continue

            bracket_type, lower, upper = bracket

            # Estimate probability
            def normal_cdf(z):
                return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

            if bracket_type == "above":
                z = (lower - indicator_value) / max(vol, 0.01)
                prob = 1.0 - normal_cdf(z)
            elif bracket_type == "below":
                z = (upper - indicator_value) / max(vol, 0.01)
                prob = normal_cdf(z)
            elif bracket_type == "between":
                z_low = (lower - indicator_value) / max(vol, 0.01)
                z_high = (upper - indicator_value) / max(vol, 0.01)
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
                        f"{kw.upper()} at {indicator_value:.2f}: "
                        f"bracket {bracket_type} "
                        f"{lower if lower else ''}"
                        f"{'-' + str(upper) if upper and bracket_type == 'between' else ''} "
                        f"→ est. prob {prob:.1%} (vol {vol:.3f})"
                    ),
                    source_pipeline="economics",
                ))

        return signals

    @staticmethod
    def _parse_econ_bracket(title: str, current_value: float):
        """Parse a bracket from a Kalshi economics market title.

        Returns (bracket_type, lower, upper) or None.
        """
        import re

        # Pattern: "between X% and Y%" or "X% to Y%" or "X-Y%"
        between_match = re.search(
            r'(?:between\s+)?([\d.]+)\s*%?\s*(?:and|to|-)\s*([\d.]+)\s*%?',
            title, re.IGNORECASE,
        )
        if between_match:
            lower = float(between_match.group(1))
            upper = float(between_match.group(2))
            return ("between", min(lower, upper), max(lower, upper))

        # Pattern: "above X%" / "over X%" / "at least X%"
        above_match = re.search(
            r'(?:above|over|exceed|higher than|at least|more than|≥|>)\s*([\d,.]+)\s*[%K]?',
            title, re.IGNORECASE,
        )
        if above_match:
            val = float(above_match.group(1).replace(",", ""))
            return ("above", val, None)

        # Pattern: "below X%" / "under X%"
        below_match = re.search(
            r'(?:below|under|less than|lower than|≤|<)\s*([\d,.]+)\s*[%K]?',
            title, re.IGNORECASE,
        )
        if below_match:
            val = float(below_match.group(1).replace(",", ""))
            return ("below", None, val)

        # Fallback: percentage in title with context
        pct_match = re.findall(r'([\d.]+)\s*%', title)
        if pct_match:
            val = float(pct_match[0])
            title_lower = title.lower()
            if any(kw in title_lower for kw in ("high", "rise", "increase")):
                return ("above", val, None)
            elif any(kw in title_lower for kw in ("low", "fall", "decrease", "drop")):
                return ("below", None, val)
            return ("above", val, None)

        # Payrolls: "above 200K" or "200,000"
        k_match = re.search(r'([\d,]+)\s*[Kk]', title)
        if k_match:
            val = float(k_match.group(1).replace(",", "")) * 1000
            return ("above", val, None)

        return None

    async def _analyze_fed_funds(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze Fed Funds Rate and generate signals.

        Compares current rate against trend to identify monetary policy direction.
        Signals on Fed rate decision markets.
        """
        signals = []

        try:
            fred_client = next((c for c in self.clients if isinstance(c, FredClient)), None)
            if not fred_client:
                logger.debug("FredClient not available")
                return signals

            # Fetch latest Fed Funds Rate data
            observations = await fred_client.get_series_observations("FEDFUNDS", limit=10)
            if not observations or len(observations) < 2:
                logger.debug("Insufficient Fed Funds data")
                return signals

            # Extract recent observations
            if len(observations) < 3:
                logger.debug("Less than 3 Fed Funds observations available")
                return signals

            # Get latest and recent values
            latest_value = float(observations[-1]["value"])
            # FRED returns "." for missing values — filter them out
            observations = [o for o in observations if o.get("value") not in (None, "", ".")]
            recent_values = [float(obs["value"]) for obs in observations[-3:]]

            # Detect trend from last 3 observations
            trend = self._detect_trend(recent_values, n=3)

            # Find matching markets
            matching_markets = self._find_matching_markets(
                markets,
                ["fed", "rate", "federal funds", "monetary policy"]
            )

            if not matching_markets:
                logger.debug("No Fed Funds markets found")
                return signals

            # Generate signal based on trend
            if trend == "rising":
                signal_type = "RATE_RISING"
                confidence = 0.65 if recent_values[-1] > recent_values[-2] else 0.55
                reasoning = (
                    f"Fed Funds Rate trending upward at {latest_value:.2f}%. "
                    f"Last 3 observations: {recent_values[-3:]}"
                )
            elif trend == "falling":
                signal_type = "RATE_FALLING"
                confidence = 0.65 if recent_values[-1] < recent_values[-2] else 0.55
                reasoning = (
                    f"Fed Funds Rate trending downward at {latest_value:.2f}%. "
                    f"Last 3 observations: {recent_values[-3:]}"
                )
            else:
                signal_type = "RATE_STABLE"
                confidence = 0.50
                reasoning = f"Fed Funds Rate stable at {latest_value:.2f}%"

            for market in matching_markets:
                signal = PipelineSignal(
                    market_id=market["id"],
                    signal_type=signal_type,
                    confidence=confidence,
                    reasoning=reasoning,
                    ev_estimate=0.0,
                    source_pipeline="economics",
                )
                signals.append(signal)
                logger.debug(f"Fed Funds signal: {signal_type} confidence={confidence}")

        except Exception as e:
            logger.error(f"Error in Fed Funds analysis: {e}")

        return signals

    async def _analyze_inflation(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze CPI and Core PCE inflation indicators.

        Calculates year-over-year changes and compares against thresholds.
        Signals on inflation-related markets.
        """
        signals = []

        try:
            fred_client = next((c for c in self.clients if isinstance(c, FredClient)), None)
            if not fred_client:
                logger.debug("FredClient not available")
                return signals

            # Fetch CPI data
            cpi_observations = await fred_client.get_series_observations("CPIAUCSL", limit=15)
            if not cpi_observations or len(cpi_observations) < 13:
                logger.debug("Insufficient CPI data")
                return signals

            cpi_yoy = self._calculate_yoy_change(cpi_observations)

            # Fetch Core PCE data
            pce_observations = await fred_client.get_series_observations("PCEPILFE", limit=15)
            pce_yoy = self._calculate_yoy_change(pce_observations) if len(pce_observations) >= 13 else None

            # Find matching markets
            matching_markets = self._find_matching_markets(
                markets,
                ["inflation", "cpi", "price", "pce", "deflation"]
            )

            if not matching_markets:
                logger.debug("No inflation markets found")
                return signals

            # Determine inflation signal
            latest_cpi = float(cpi_observations[-1]["value"])
            latest_date = cpi_observations[-1].get("date", datetime.now().isoformat())

            if cpi_yoy is not None:
                if cpi_yoy > 3.5:
                    signal_type = "INFLATION_HIGH"
                    confidence = min(0.75, 0.55 + (cpi_yoy - 3.5) * 0.05)
                    reasoning = (
                        f"CPI YoY change: {cpi_yoy:.2f}% (threshold: 3.5%). "
                        f"Current CPI: {latest_cpi:.2f}"
                    )
                elif cpi_yoy < 2.0:
                    signal_type = "INFLATION_LOW"
                    confidence = min(0.75, 0.55 + (2.0 - cpi_yoy) * 0.05)
                    reasoning = (
                        f"CPI YoY change: {cpi_yoy:.2f}% (threshold: 2.0%). "
                        f"Current CPI: {latest_cpi:.2f}"
                    )
                else:
                    signal_type = "INFLATION_MODERATE"
                    confidence = 0.50
                    reasoning = (
                        f"CPI YoY change: {cpi_yoy:.2f}% within target range. "
                        f"Current CPI: {latest_cpi:.2f}"
                    )

                # Add PCE information if available
                if pce_yoy is not None:
                    reasoning += f" | Core PCE YoY: {pce_yoy:.2f}%"

                for market in matching_markets:
                    signal = PipelineSignal(
                        market_id=market["id"],
                        signal_type=signal_type,
                        confidence=confidence,
                        reasoning=reasoning,
                    )
                    signals.append(signal)
                    logger.debug(f"Inflation signal: {signal_type} confidence={confidence}")

        except Exception as e:
            logger.error(f"Error in inflation analysis: {e}")

        return signals

    async def _analyze_unemployment(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze unemployment rate trends.

        Detects 3-month trend to identify labor market strength.
        Signals on recession, jobs, and employment-related markets.
        """
        signals = []

        try:
            fred_client = next((c for c in self.clients if isinstance(c, FredClient)), None)
            if not fred_client:
                logger.debug("FredClient not available")
                return signals

            # Fetch unemployment rate data
            observations = await fred_client.get_series_observations("UNRATE", limit=5)
            if not observations or len(observations) < 3:
                logger.debug("Insufficient unemployment data")
                return signals

            # FRED returns "." for missing values — filter them out
            observations = [o for o in observations if o.get("value") not in (None, "", ".")]
            recent_values = [float(obs["value"]) for obs in observations[-3:]]

            # Detect trend
            trend = self._detect_trend(recent_values, n=3)

            # Find matching markets
            matching_markets = self._find_matching_markets(
                markets,
                ["unemployment", "jobs", "employment", "recession", "labor"]
            )

            if not matching_markets:
                logger.debug("No unemployment markets found")
                return signals

            latest_unemployment = recent_values[-1]
            latest_date = observations[-1].get("date", datetime.now().isoformat())

            # Generate signal based on trend
            if trend == "falling":
                signal_type = "ECONOMY_STRONG"
                confidence = 0.65
                reasoning = (
                    f"Unemployment trending down at {latest_unemployment:.2f}%. "
                    f"Last 3 months: {recent_values}. Strong labor market conditions."
                )
            elif trend == "rising":
                signal_type = "ECONOMY_WEAK"
                confidence = 0.65
                reasoning = (
                    f"Unemployment trending up at {latest_unemployment:.2f}%. "
                    f"Last 3 months: {recent_values}. Weakening labor market."
                )
            else:
                signal_type = "ECONOMY_FLAT"
                confidence = 0.50
                reasoning = (
                    f"Unemployment stable at {latest_unemployment:.2f}%. "
                    f"Last 3 months: {recent_values}. Steady labor market."
                )

            for market in matching_markets:
                signal = PipelineSignal(
                    market_id=market["id"],
                    signal_type=signal_type,
                    confidence=confidence,
                    reasoning=reasoning,
                )
                signals.append(signal)
                logger.debug(f"Unemployment signal: {signal_type} confidence={confidence}")

        except Exception as e:
            logger.error(f"Error in unemployment analysis: {e}")

        return signals

    async def _analyze_gdp(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze real GDP growth rates.

        Detects negative growth for recession signals.
        Signals on GDP and recession-related markets.
        """
        signals = []

        try:
            fred_client = next((c for c in self.clients if isinstance(c, FredClient)), None)
            if not fred_client:
                logger.debug("FredClient not available")
                return signals

            # Fetch real GDP data
            observations = await fred_client.get_series_observations("GDPC1", limit=5)
            if not observations or len(observations) < 2:
                logger.debug("Insufficient GDP data")
                return signals

            recent_values = [float(obs["value"]) for obs in observations[-2:]]

            # Calculate growth rate
            if recent_values[0] > 0:
                growth_rate = ((recent_values[-1] - recent_values[0]) / recent_values[0]) * 100
            else:
                logger.warning("Invalid GDP values for growth calculation")
                return signals

            # Find matching markets
            matching_markets = self._find_matching_markets(
                markets,
                ["gdp", "growth", "recession", "economic", "contraction"]
            )

            if not matching_markets:
                logger.debug("No GDP markets found")
                return signals

            latest_gdp = recent_values[-1]
            latest_date = observations[-1].get("date", datetime.now().isoformat())

            # Generate signal based on growth rate
            if growth_rate < 0:
                signal_type = "RECESSION_RISK"
                confidence = 0.70
                reasoning = (
                    f"Real GDP contraction detected: {growth_rate:.2f}% growth. "
                    f"Current GDP: ${latest_gdp:.2f}B. Negative growth signals recession risk."
                )
            elif growth_rate < 1.0:
                signal_type = "GROWTH_SLOW"
                confidence = 0.60
                reasoning = (
                    f"Real GDP growth: {growth_rate:.2f}% (below 1.0%). "
                    f"Current GDP: ${latest_gdp:.2f}B. Weak growth conditions."
                )
            else:
                signal_type = "GROWTH_POSITIVE"
                confidence = 0.60
                reasoning = (
                    f"Real GDP growth: {growth_rate:.2f}%. "
                    f"Current GDP: ${latest_gdp:.2f}B. Positive economic growth."
                )

            for market in matching_markets:
                signal = PipelineSignal(
                    market_id=market["id"],
                    signal_type=signal_type,
                    confidence=confidence,
                    reasoning=reasoning,
                )
                signals.append(signal)
                logger.debug(f"GDP signal: {signal_type} confidence={confidence}")

        except Exception as e:
            logger.error(f"Error in GDP analysis: {e}")

        return signals

    async def _analyze_yield_curve(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze Treasury yield curve inversion.

        Detects inverted yield curve (2Y > 10Y) as recession indicator.
        Signals on recession-related markets.
        """
        signals = []

        try:
            fred_client = next((c for c in self.clients if isinstance(c, FredClient)), None)
            if not fred_client:
                logger.debug("FredClient not available")
                return signals

            # Fetch 2-year and 10-year Treasury yields
            dgs2_obs = await fred_client.get_series_observations("DGS2", limit=2)
            dgs10_obs = await fred_client.get_series_observations("DGS10", limit=2)

            if not dgs2_obs or not dgs10_obs:
                logger.debug("Insufficient Treasury yield data")
                return signals

            if not dgs2_obs or not dgs10_obs:
                logger.debug("No Treasury yield observations")
                return signals

            dgs2 = float(dgs2_obs[-1]["value"])
            dgs10 = float(dgs10_obs[-1]["value"])
            latest_date = dgs2_obs[-1].get("date", datetime.now().isoformat())

            # Find matching markets
            matching_markets = self._find_matching_markets(
                markets,
                ["yield", "curve", "recession", "inversion", "treasury"]
            )

            if not matching_markets:
                logger.debug("No yield curve markets found")
                return signals

            # Analyze curve slope
            curve_slope = dgs10 - dgs2

            if curve_slope < 0:
                signal_type = "YIELD_INVERTED"
                confidence = 0.70
                reasoning = (
                    f"Yield curve inverted: 2Y ({dgs2:.2f}%) > 10Y ({dgs10:.2f}%). "
                    f"Curve slope: {curve_slope:.2f}%. Recession indicator."
                )
            elif curve_slope < 0.5:
                signal_type = "YIELD_FLAT"
                confidence = 0.60
                reasoning = (
                    f"Yield curve flattening: 2Y ({dgs2:.2f}%) vs 10Y ({dgs10:.2f}%). "
                    f"Curve slope: {curve_slope:.2f}%. Risk of inversion."
                )
            else:
                signal_type = "YIELD_NORMAL"
                confidence = 0.50
                reasoning = (
                    f"Normal yield curve: 2Y ({dgs2:.2f}%) < 10Y ({dgs10:.2f}%). "
                    f"Curve slope: {curve_slope:.2f}%. Low recession risk."
                )

            for market in matching_markets:
                signal = PipelineSignal(
                    market_id=market["id"],
                    signal_type=signal_type,
                    confidence=confidence,
                    reasoning=reasoning,
                )
                signals.append(signal)
                logger.debug(f"Yield curve signal: {signal_type} confidence={confidence}")

        except Exception as e:
            logger.error(f"Error in yield curve analysis: {e}")

        return signals

    async def _analyze_jobs(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze BLS nonfarm payrolls data.

        Evaluates monthly job creation/loss trends.
        Signals on employment and jobs-related markets.
        """
        signals = []

        try:
            bls_client = next((c for c in self.clients if isinstance(c, BlsClient)), None)
            if not bls_client:
                logger.debug("BlsClient not available")
                return signals

            # Fetch nonfarm payrolls data (CES0000000001)
            series_data = await bls_client.get_series_data(["CES0000000001"])
            data = series_data.get("CES0000000001", [])
            if not data or len(data) < 2:
                logger.debug("Insufficient nonfarm payrolls data")
                return signals

            # Calculate month-over-month change
            if len(data) >= 2:
                latest_value = float(data[-1]["value"]) if isinstance(data[-1]["value"], (int, float)) else 0
                previous_value = float(data[-2]["value"]) if isinstance(data[-2]["value"], (int, float)) else 0
                mom_change = latest_value - previous_value
            else:
                logger.debug("Cannot calculate month-over-month change")
                return signals

            # Find matching markets
            matching_markets = self._find_matching_markets(
                markets,
                ["jobs", "payroll", "nonfarm", "employment", "hiring"]
            )

            if not matching_markets:
                logger.debug("No jobs markets found")
                return signals

            latest_date = data[-1].get("date", datetime.now().isoformat()) if isinstance(data[-1], dict) else datetime.now().isoformat()

            # Generate signal based on job change
            if mom_change > 150000:
                signal_type = "JOBS_STRONG"
                confidence = 0.70
                reasoning = (
                    f"Strong job creation: +{mom_change:,.0f} nonfarm payrolls. "
                    f"Latest value: {latest_value:,.0f}. Robust labor market."
                )
            elif mom_change > 0:
                signal_type = "JOBS_POSITIVE"
                confidence = 0.60
                reasoning = (
                    f"Positive job growth: +{mom_change:,.0f} nonfarm payrolls. "
                    f"Latest value: {latest_value:,.0f}. Moderate growth."
                )
            elif mom_change > -50000:
                signal_type = "JOBS_FLAT"
                confidence = 0.55
                reasoning = (
                    f"Modest job loss: {mom_change:,.0f} nonfarm payrolls. "
                    f"Latest value: {latest_value:,.0f}. Slowing growth."
                )
            else:
                signal_type = "JOBS_WEAK"
                confidence = 0.70
                reasoning = (
                    f"Significant job loss: {mom_change:,.0f} nonfarm payrolls. "
                    f"Latest value: {latest_value:,.0f}. Weakening labor market."
                )

            for market in matching_markets:
                signal = PipelineSignal(
                    market_id=market["id"],
                    signal_type=signal_type,
                    confidence=confidence,
                    reasoning=reasoning,
                )
                signals.append(signal)
                logger.debug(f"Jobs signal: {signal_type} confidence={confidence}")

        except Exception as e:
            logger.error(f"Error in jobs analysis: {e}")

        return signals

    def _find_matching_markets(
        self,
        markets: List[Dict],
        keywords: List[str]
    ) -> List[Dict]:
        """
        Filter markets by title keywords.

        Args:
            markets: List of market dictionaries with 'title' key
            keywords: List of keywords to match (case-insensitive)

        Returns:
            List of matching market dictionaries
        """
        matching = []
        keywords_lower = [kw.lower() for kw in keywords]

        for market in markets:
            title = market.get("title", "").lower()
            if any(kw in title for kw in keywords_lower):
                matching.append(market)

        return matching

    def _calculate_yoy_change(self, observations: List[Dict]) -> Optional[float]:
        """
        Calculate year-over-year percentage change from observations.

        Args:
            observations: List of observation dicts with 'value' field

        Returns:
            YoY percentage change, or None if calculation not possible
        """
        if len(observations) < 13:
            return None

        try:
            current_value = float(observations[-1]["value"])
            previous_year_value = float(observations[-13]["value"])

            if previous_year_value <= 0:
                return None

            yoy_change = ((current_value - previous_year_value) / previous_year_value) * 100
            return yoy_change
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Error calculating YoY change: {e}")
            return None

    def _detect_trend(
        self,
        values: List[float],
        n: int = 3
    ) -> str:
        """
        Detect trend direction from last n values.

        Args:
            values: List of numerical values
            n: Number of recent values to consider (default: 3)

        Returns:
            "rising", "falling", or "flat"
        """
        if len(values) < n:
            return "flat"

        recent = values[-n:]

        # Count increases and decreases
        increases = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i-1])
        decreases = sum(1 for i in range(1, len(recent)) if recent[i] < recent[i-1])

        if increases > decreases:
            return "rising"
        elif decreases > increases:
            return "falling"
        else:
            return "flat"
