"""Weather / Climate Pipeline for Kalshi Prediction Markets.

Generates trading signals from Open-Meteo forecast data for three
Kalshi daily-weather market families:

  1. **Daily high/low temperature** — bracket markets
     e.g. "Will the high temp in Chicago be >65° on Mar 22?"
  2. **Monthly rain count** — "Rain in Chicago in Mar 2026?"
     with bracket thresholds on number of rainy days remaining
  3. **Monthly snow accumulation** — "Snow in Chicago in Mar 2026?"
     with bracket thresholds on total snowfall inches

Plus long-range climate markets (hottest year, warming targets) that
use historical-temperature trend data from Open-Meteo.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, date
from typing import List, Optional, Tuple, Dict, Any
import logging
import calendar

from sibyl.pipelines.base_pipeline import BasePipeline, PipelineSignal
from sibyl.clients.open_meteo_client import OpenMeteoClient

logger = logging.getLogger(__name__)


class WeatherPipeline(BasePipeline):
    """Signal pipeline for Kalshi Climate & Weather markets."""

    CATEGORY = "Weather"
    PIPELINE_NAME = "weather"
    DEDUP_WINDOW_MINUTES = 60

    # ── City coordinate registry ──────────────────────────────────────
    # Covers every city that appears in Kalshi daily weather markets.
    CITY_COORDS: dict[str, tuple[float, float]] = {
        # Full names
        "Chicago": (41.88, -87.63),
        "New York City": (40.71, -74.01),
        "NYC": (40.71, -74.01),
        "New York": (40.71, -74.01),
        "Los Angeles": (34.05, -118.24),
        "LA": (34.05, -118.24),
        "Miami": (25.76, -80.19),
        "Denver": (39.74, -104.99),
        "Austin": (30.27, -97.74),
        "Philadelphia": (39.95, -75.17),
        "San Francisco": (37.77, -122.42),
        "Seattle": (47.61, -122.33),
        "San Antonio": (29.42, -98.49),
        "Phoenix": (33.45, -112.07),
        "Oklahoma City": (35.47, -97.52),
        "New Orleans": (29.95, -90.07),
        "Minneapolis": (44.98, -93.27),
        "Las Vegas": (36.17, -115.14),
        "Houston": (29.76, -95.37),
        "Washington DC": (38.91, -77.04),
        "Washington D.C.": (38.91, -77.04),
        "Dallas": (32.78, -96.80),
        "Boston": (42.36, -71.06),
        "Atlanta": (33.75, -84.39),
        "Detroit": (42.33, -83.05),
        "Salt Lake City": (40.76, -111.89),
        "Jackson": (43.48, -110.76),  # Jackson, WY
        "Jackson, WY": (43.48, -110.76),
        "Denver Area": (39.74, -104.99),
    }

    def _create_clients(self) -> List:
        return [OpenMeteoClient()]

    def _category_variants(self) -> List[str]:
        return ["Weather", "weather", "Climate", "climate"]

    # ── Main analysis entry point ─────────────────────────────────────

    async def _analyze(self, markets: List[Dict[str, Any]]) -> List[PipelineSignal]:
        signals: list[PipelineSignal] = []

        # Bucket markets by type based on ticker / title patterns
        temp_high_markets: list[dict] = []
        temp_low_markets: list[dict] = []
        rain_markets: list[dict] = []
        snow_markets: list[dict] = []
        hottest_year_markets: list[dict] = []
        other_markets: list[dict] = []

        for m in markets:
            ticker = m.get("id", "")
            title = m.get("title", "").lower()

            if "KXHIGH" in ticker or "highest temp" in title or "high temp" in title:
                temp_high_markets.append(m)
            elif "KXLOWT" in ticker or "lowest temp" in title or "low temp" in title:
                temp_low_markets.append(m)
            elif "RAIN" in ticker.upper() or "rain" in title:
                rain_markets.append(m)
            elif "SNOW" in ticker.upper() or "snow" in title:
                snow_markets.append(m)
            elif "KXGTEMP" in ticker or "hottest year" in title:
                hottest_year_markets.append(m)
            else:
                other_markets.append(m)

        logger.info(
            "Weather markets: %d high-temp, %d low-temp, %d rain, %d snow, %d hottest-year, %d other",
            len(temp_high_markets), len(temp_low_markets),
            len(rain_markets), len(snow_markets),
            len(hottest_year_markets), len(other_markets),
        )

        client = self._get_client("OpenMeteoClient")
        if not client:
            logger.warning("OpenMeteoClient not available")
            return signals

        # ── Daily temperature bracket analysis ──────────────────
        temp_signals = await self._analyze_daily_temp_brackets(
            temp_high_markets + temp_low_markets, client
        )
        signals.extend(temp_signals)

        # ── Monthly rain-day analysis ───────────────────────────
        rain_signals = await self._analyze_rain_markets(rain_markets, client)
        signals.extend(rain_signals)

        # ── Monthly snow accumulation analysis ──────────────────
        snow_signals = await self._analyze_snow_markets(snow_markets, client)
        signals.extend(snow_signals)

        # ── Hottest year on record ──────────────────────────────
        hy_signals = await self._analyze_hottest_year(hottest_year_markets, client)
        signals.extend(hy_signals)

        return signals

    # ── 1) Daily Temperature Bracket Markets ──────────────────────────

    async def _analyze_daily_temp_brackets(
        self, markets: list[dict], client: OpenMeteoClient
    ) -> list[PipelineSignal]:
        """Analyse bracket temperature markets using Open-Meteo forecast.

        Market titles like:
          "Will the high temp in Chicago be >65° on Mar 22, 2026?"
          "Will the high temp in Chicago be 60-61° on Mar 22, 2026?"
          "Will the high temp in Chicago be <58° on Mar 22, 2026?"
        """
        signals: list[PipelineSignal] = []
        # Cache forecasts per (city, date) to avoid duplicate API calls
        forecast_cache: dict[tuple[str, str], dict | None] = {}

        for market in markets:
            try:
                title = market.get("title", "")
                ticker = market.get("id", "")

                # Extract city
                city_name, coords = self._extract_city(title)
                if not coords:
                    continue

                # Extract date from ticker (e.g., 26MAR22 → 2026-03-22)
                forecast_date = self._extract_date_from_ticker(ticker)
                if not forecast_date:
                    # Try extracting from title
                    forecast_date = self._extract_date_from_title(title)
                if not forecast_date:
                    continue

                # Determine if high or low temp market
                is_high = "high" in title.lower() or "highest" in title.lower()
                temp_var = "temperature_2m_max" if is_high else "temperature_2m_min"

                # Fetch forecast (cached)
                cache_key = (city_name, forecast_date.isoformat())
                if cache_key not in forecast_cache:
                    days_ahead = (forecast_date - date.today()).days
                    if days_ahead < 0 or days_ahead > 16:
                        forecast_cache[cache_key] = None
                        continue
                    lat, lon = coords
                    data = await client.get_forecast(
                        lat, lon,
                        daily=[temp_var],
                        forecast_days=min(days_ahead + 1, 16),
                    )
                    forecast_cache[cache_key] = data

                fdata = forecast_cache.get(cache_key)
                if not fdata or "daily" not in fdata:
                    continue

                # Extract the forecast temp for the target date
                dates_list = fdata["daily"].get("time", [])
                temps_list = fdata["daily"].get(temp_var, [])
                target_str = forecast_date.isoformat()
                forecast_temp_c = None
                for i, d in enumerate(dates_list):
                    if d == target_str and i < len(temps_list):
                        forecast_temp_c = temps_list[i]
                        break

                if forecast_temp_c is None:
                    continue

                # Convert to Fahrenheit (Kalshi uses °F)
                forecast_temp_f = (forecast_temp_c * 9.0 / 5.0) + 32.0

                # Parse the bracket from the title
                bracket = self._parse_temp_bracket(title)
                if not bracket:
                    continue

                bracket_type, low_f, high_f = bracket

                # Estimate probability using a normal distribution approach
                # Forecast uncertainty ~±3°F for same-day, ~±5°F multi-day
                days_ahead = (forecast_date - date.today()).days
                uncertainty = 3.0 + (days_ahead * 0.5)  # °F std dev

                prob = self._bracket_probability(
                    forecast_temp_f, uncertainty, bracket_type, low_f, high_f
                )

                # Compare to market price
                market_price = self._get_market_price_from_dict(market)
                if market_price is None or market_price <= 0 or market_price >= 1:
                    # Just emit the forecast signal
                    ev = prob - 0.5
                else:
                    ev = prob - market_price

                confidence = self._temp_confidence(prob, market_price, days_ahead)
                if confidence < 0.50:
                    continue

                direction = "YES" if ev > 0 else "NO"

                signal = PipelineSignal(
                    market_id=ticker,
                    signal_type="DATA_FUNDAMENTAL",
                    ev_estimate=ev,
                    confidence=confidence,
                    direction=direction,
                    reasoning=(
                        f"Forecast {forecast_temp_f:.0f}°F (±{uncertainty:.0f}), "
                        f"bracket {bracket_type}{low_f}-{high_f}°F, "
                        f"prob={prob:.1%}, mkt={market_price or 'N/A'}"
                    ),
                    source_pipeline=self.PIPELINE_NAME,
                    category="weather",
                )
                signals.append(signal)

            except Exception as e:
                logger.warning("Error in temp bracket analysis for %s: %s", market.get("id", ""), e)
                continue

        return signals

    # ── 2) Monthly Rain Markets ───────────────────────────────────────

    async def _analyze_rain_markets(
        self, markets: list[dict], client: OpenMeteoClient
    ) -> list[PipelineSignal]:
        """Analyse monthly rain-day count markets.

        Market titles: "Rain in Chicago in Mar 2026?"
        Brackets on number of rainy days in the month.
        """
        signals: list[PipelineSignal] = []
        rain_cache: dict[str, dict | None] = {}

        for market in markets:
            try:
                title = market.get("title", "")
                ticker = market.get("id", "")

                city_name, coords = self._extract_city(title)
                if not coords:
                    continue

                # Parse month/year from ticker or title (e.g., 26MAR)
                month_date = self._extract_month_from_ticker(ticker)
                if not month_date:
                    continue

                year, month = month_date
                today = date.today()

                # Only analyze current or next month
                target_start = date(year, month, 1)
                _, days_in_month = calendar.monthrange(year, month)
                target_end = date(year, month, days_in_month)

                if target_end < today:
                    continue  # Month already over

                cache_key = f"{city_name}-{year}-{month}"
                if cache_key not in rain_cache:
                    lat, lon = coords
                    # Fetch forecast for remaining days of the month
                    forecast_start = max(today, target_start)
                    days_remaining = (target_end - forecast_start).days + 1
                    if days_remaining <= 0 or days_remaining > 16:
                        rain_cache[cache_key] = None
                        continue

                    data = await client.get_forecast(
                        lat, lon,
                        daily=["precipitation_sum"],
                        forecast_days=min(days_remaining, 16),
                    )
                    rain_cache[cache_key] = data

                fdata = rain_cache.get(cache_key)
                if not fdata or "daily" not in fdata:
                    continue

                precip_list = fdata["daily"].get("precipitation_sum", [])
                # Count forecast rainy days (>0.1mm = 0.004 inches)
                forecast_rainy_days = sum(1 for p in precip_list if p and p > 0.25)

                # Count days already past in the month (we'd need historical data
                # for precise count, but for now estimate from forecast)
                days_elapsed = (today - target_start).days if today > target_start else 0

                # Parse the rain threshold from the ticker
                # Tickers like KXRAINCHIM-26MAR-7 (7+ rainy days), -6, -5, etc.
                threshold = self._parse_numeric_threshold(ticker)
                if threshold is None:
                    continue

                # Rough probability estimation
                # Total rainy days = already_observed + forecast_remaining
                # We approximate already-observed as proportional
                estimated_total = forecast_rainy_days  # conservative: forecast only
                prob = 1.0 if estimated_total >= threshold else estimated_total / max(threshold, 1)
                prob = max(0.05, min(0.95, prob))

                market_price = self._get_market_price_from_dict(market)
                ev = prob - (market_price if market_price else 0.5)

                confidence = min(0.85, 0.55 + abs(ev) * 0.8)
                if confidence < 0.50:
                    continue

                signal = PipelineSignal(
                    market_id=ticker,
                    signal_type="DATA_FUNDAMENTAL",
                    ev_estimate=ev,
                    confidence=confidence,
                    direction="YES" if ev > 0 else "NO",
                    reasoning=(
                        f"Forecast {forecast_rainy_days} rainy days remaining, "
                        f"threshold={threshold}, prob={prob:.1%}, "
                        f"mkt={market_price or 'N/A'}"
                    ),
                    source_pipeline=self.PIPELINE_NAME,
                    category="weather",
                )
                signals.append(signal)

            except Exception as e:
                logger.warning("Error in rain analysis for %s: %s", market.get("id", ""), e)
                continue

        return signals

    # ── 3) Monthly Snow Markets ───────────────────────────────────────

    async def _analyze_snow_markets(
        self, markets: list[dict], client: OpenMeteoClient
    ) -> list[PipelineSignal]:
        """Analyse monthly snow accumulation markets.

        Market titles: "Snow in Chicago in Mar 2026?"
        Brackets on total snowfall inches for the month.
        """
        signals: list[PipelineSignal] = []
        snow_cache: dict[str, dict | None] = {}

        for market in markets:
            try:
                title = market.get("title", "")
                ticker = market.get("id", "")

                city_name, coords = self._extract_city(title)
                if not coords:
                    continue

                month_date = self._extract_month_from_ticker(ticker)
                if not month_date:
                    continue

                year, month = month_date
                today = date.today()
                _, days_in_month = calendar.monthrange(year, month)
                target_end = date(year, month, days_in_month)

                if target_end < today:
                    continue

                cache_key = f"{city_name}-snow-{year}-{month}"
                if cache_key not in snow_cache:
                    lat, lon = coords
                    target_start = date(year, month, 1)
                    forecast_start = max(today, target_start)
                    days_remaining = (target_end - forecast_start).days + 1
                    if days_remaining <= 0 or days_remaining > 16:
                        snow_cache[cache_key] = None
                        continue

                    data = await client.get_forecast(
                        lat, lon,
                        daily=["snowfall_sum"],
                        forecast_days=min(days_remaining, 16),
                    )
                    snow_cache[cache_key] = data

                fdata = snow_cache.get(cache_key)
                if not fdata or "daily" not in fdata:
                    continue

                snowfall_list = fdata["daily"].get("snowfall_sum", [])
                # Open-Meteo returns snowfall in cm; convert to inches
                total_snow_cm = sum(s for s in snowfall_list if s and s > 0)
                total_snow_inches = total_snow_cm / 2.54

                # Parse threshold from ticker (e.g., KXCHISNOWM-26MAR-8.0)
                threshold = self._parse_numeric_threshold(ticker)
                if threshold is None:
                    continue

                # Probability estimation
                if total_snow_inches >= threshold:
                    prob = min(0.95, 0.7 + (total_snow_inches - threshold) * 0.05)
                else:
                    ratio = total_snow_inches / max(threshold, 0.1)
                    prob = max(0.05, ratio * 0.6)

                market_price = self._get_market_price_from_dict(market)
                ev = prob - (market_price if market_price else 0.5)

                confidence = min(0.85, 0.55 + abs(ev) * 0.8)
                if confidence < 0.50:
                    continue

                signal = PipelineSignal(
                    market_id=ticker,
                    signal_type="DATA_FUNDAMENTAL",
                    ev_estimate=ev,
                    confidence=confidence,
                    direction="YES" if ev > 0 else "NO",
                    reasoning=(
                        f"Forecast snow: {total_snow_inches:.1f}in remaining, "
                        f"threshold={threshold}in, prob={prob:.1%}, "
                        f"mkt={market_price or 'N/A'}"
                    ),
                    source_pipeline=self.PIPELINE_NAME,
                    category="weather",
                )
                signals.append(signal)

            except Exception as e:
                logger.warning("Error in snow analysis for %s: %s", market.get("id", ""), e)
                continue

        return signals

    # ── 4) Hottest Year on Record ─────────────────────────────────────

    async def _analyze_hottest_year(
        self, markets: list[dict], client: OpenMeteoClient
    ) -> list[PipelineSignal]:
        """Analyse 'hottest year on record' markets using temperature trends."""
        signals: list[PipelineSignal] = []

        if not markets:
            return signals

        # Use a selection of global reference stations to estimate trend
        # Compare year-to-date average vs last year
        reference_cities = [
            (40.71, -74.01),   # NYC
            (51.51, -0.13),    # London
            (35.68, 139.69),   # Tokyo
            (-33.87, 151.21),  # Sydney
        ]

        try:
            today = date.today()
            ytd_start = date(today.year, 1, 1)
            prev_start = date(today.year - 1, 1, 1)
            prev_end = date(today.year - 1, today.month, today.day)

            current_temps: list[float] = []
            prev_temps: list[float] = []

            for lat, lon in reference_cities:
                # Current YTD
                cur_data = await client.get_historical_weather(
                    lat, lon,
                    start_date=ytd_start.isoformat(),
                    end_date=today.isoformat(),
                    daily=["temperature_2m_mean"],
                )
                if cur_data and "daily" in cur_data:
                    temps = cur_data["daily"].get("temperature_2m_mean", [])
                    current_temps.extend(t for t in temps if t is not None)

                # Previous year same period
                prev_data = await client.get_historical_weather(
                    lat, lon,
                    start_date=prev_start.isoformat(),
                    end_date=prev_end.isoformat(),
                    daily=["temperature_2m_mean"],
                )
                if prev_data and "daily" in prev_data:
                    temps = prev_data["daily"].get("temperature_2m_mean", [])
                    prev_temps.extend(t for t in temps if t is not None)

            if current_temps and prev_temps:
                avg_current = sum(current_temps) / len(current_temps)
                avg_prev = sum(prev_temps) / len(prev_temps)
                delta = avg_current - avg_prev

                # If current year is warmer, higher probability of hottest year
                prob = 0.5 + (delta * 0.1)  # Each 1°C warmer → +10% prob
                prob = max(0.10, min(0.90, prob))

                for market in markets:
                    ticker = market.get("id", "")
                    market_price = self._get_market_price_from_dict(market)
                    ev = prob - (market_price if market_price else 0.5)

                    confidence = min(0.80, 0.55 + abs(delta) * 0.1)
                    if confidence < 0.50:
                        continue

                    signal = PipelineSignal(
                        market_id=ticker,
                        signal_type="DATA_FUNDAMENTAL",
                        ev_estimate=ev,
                        confidence=confidence,
                        direction="YES" if ev > 0 else "NO",
                        reasoning=(
                            f"YTD avg temp {avg_current:.1f}°C vs prev year {avg_prev:.1f}°C "
                            f"(Δ{delta:+.2f}°C), prob={prob:.1%}, mkt={market_price or 'N/A'}"
                        ),
                        source_pipeline=self.PIPELINE_NAME,
                        category="weather",
                    )
                    signals.append(signal)

        except Exception as e:
            logger.warning("Error in hottest-year analysis: %s", e)

        return signals

    # ── Helper methods ────────────────────────────────────────────────

    def _get_client(self, client_name: str) -> Optional[Any]:
        for c in self.clients:
            if c.__class__.__name__ == client_name:
                return c
        return None

    def _extract_city(self, title: str) -> tuple[str, tuple[float, float] | None]:
        """Extract city name and coordinates from a market title.
        Returns (city_name, (lat, lon)) or ("", None).
        Matches longest city name first to avoid partial matches.
        """
        title_lower = title.lower()
        # Sort by name length descending so "New York City" matches before "New York"
        for city_name in sorted(self.CITY_COORDS, key=len, reverse=True):
            if city_name.lower() in title_lower:
                return city_name, self.CITY_COORDS[city_name]
        return "", None

    def _extract_date_from_ticker(self, ticker: str) -> date | None:
        """Extract date from ticker like KXHIGHCHI-26MAR22 → 2026-03-22."""
        # Pattern: 2-digit year + 3-letter month + 2-digit day
        match = re.search(r"(\d{2})([A-Z]{3})(\d{2})(?:\b|-)", ticker.upper())
        if match:
            year_2d, month_str, day_str = match.groups()
            try:
                year = 2000 + int(year_2d)
                month_map = {
                    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
                    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
                    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
                }
                month = month_map.get(month_str)
                if month:
                    return date(year, month, int(day_str))
            except (ValueError, KeyError):
                pass
        return None

    def _extract_date_from_title(self, title: str) -> date | None:
        """Extract date from title like 'on Mar 22, 2026'."""
        match = re.search(
            r"on\s+(\w+)\s+(\d{1,2}),?\s*(\d{4})", title, re.IGNORECASE
        )
        if match:
            month_str, day_str, year_str = match.groups()
            try:
                dt = datetime.strptime(f"{month_str} {day_str} {year_str}", "%b %d %Y")
                return dt.date()
            except ValueError:
                pass
        return None

    def _extract_month_from_ticker(self, ticker: str) -> tuple[int, int] | None:
        """Extract (year, month) from ticker like KXRAINCHIM-26MAR-7."""
        match = re.search(r"(\d{2})([A-Z]{3})(?:\b|-)", ticker.upper())
        if match:
            year_2d, month_str = match.groups()
            month_map = {
                "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
                "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
                "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
            }
            month = month_map.get(month_str)
            if month:
                return (2000 + int(year_2d), month)
        return None

    def _parse_temp_bracket(self, title: str) -> tuple[str, float, float] | None:
        """Parse temperature bracket from market title.

        Returns: (type, low, high)
          type "gt":  >low  (high=999)
          type "lt":  <high (low=-999)
          type "range": low-high (inclusive)
        """
        title_lower = title.lower()

        # Pattern: >X° or above X°
        m = re.search(r"[>](\d+)[°]", title)
        if m:
            return ("gt", float(m.group(1)), 999.0)

        # Pattern: <X°
        m = re.search(r"[<](\d+)[°]", title)
        if m:
            return ("lt", -999.0, float(m.group(1)))

        # Pattern: X-Y° (range bracket)
        m = re.search(r"(\d+)\s*[-–]\s*(\d+)[°]", title)
        if m:
            return ("range", float(m.group(1)), float(m.group(2)))

        # Pattern: "above X" / "over X" / "exceed X"
        m = re.search(r"(?:above|over|exceed|greater than)\s+(\d+)", title_lower)
        if m:
            return ("gt", float(m.group(1)), 999.0)

        # Pattern: "below X" / "under X"
        m = re.search(r"(?:below|under|less than)\s+(\d+)", title_lower)
        if m:
            return ("lt", -999.0, float(m.group(1)))

        return None

    def _parse_numeric_threshold(self, ticker: str) -> float | None:
        """Extract the last numeric component from a ticker as a threshold.

        e.g. KXRAINCHIM-26MAR-7 → 7.0
             KXCHISNOWM-26MAR-8.0 → 8.0
        """
        parts = ticker.split("-")
        for part in reversed(parts):
            try:
                return float(part)
            except ValueError:
                # Also try removing leading letters (e.g., T65, B64.5)
                m = re.match(r"[A-Za-z]*(\d+\.?\d*)", part)
                if m:
                    try:
                        return float(m.group(1))
                    except ValueError:
                        continue
        return None

    def _bracket_probability(
        self,
        forecast: float,
        std_dev: float,
        bracket_type: str,
        low: float,
        high: float,
    ) -> float:
        """Estimate probability of forecast falling in bracket using normal CDF approx."""
        import math

        def norm_cdf(x: float) -> float:
            """Approximate standard normal CDF."""
            return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

        if std_dev <= 0:
            std_dev = 1.0

        if bracket_type == "gt":
            # P(temp > low)
            z = (low - forecast) / std_dev
            return 1.0 - norm_cdf(z)
        elif bracket_type == "lt":
            # P(temp < high)
            z = (high - forecast) / std_dev
            return norm_cdf(z)
        elif bracket_type == "range":
            # P(low <= temp <= high)
            z_low = (low - forecast) / std_dev
            z_high = (high - forecast) / std_dev
            return norm_cdf(z_high) - norm_cdf(z_low)

        return 0.5

    def _temp_confidence(
        self, prob: float, market_price: float | None, days_ahead: int
    ) -> float:
        """Calculate confidence score for temperature signal."""
        # Base confidence from probability distance from 0.5
        base = 0.50 + abs(prob - 0.5) * 0.4

        # Bonus if divergence from market price is large
        if market_price and 0 < market_price < 1:
            divergence = abs(prob - market_price)
            base += divergence * 0.3

        # Penalty for multi-day forecasts (less certain)
        if days_ahead > 3:
            base -= (days_ahead - 3) * 0.03

        return max(0.50, min(0.95, base))

    @staticmethod
    def _get_market_price_from_dict(market: dict) -> float | None:
        """Extract yes price from market dict."""
        # Try various field names
        for key in ("yes_price", "last_price_dollars", "yes_ask_dollars", "yes_bid_dollars"):
            val = market.get(key)
            if val is not None:
                try:
                    f = float(val)
                    if 0 < f < 1:
                        return f
                except (ValueError, TypeError):
                    pass
        return None
