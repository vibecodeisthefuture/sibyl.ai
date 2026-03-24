"""
FRED API Client — Federal Reserve Economic Data.

Provides access to 800,000+ economic time series from the St. Louis Fed.
Key series for Sibyl: GDP (GDPC1), CPI (CPIAUCSL), Unemployment (UNRATE),
Fed Funds Rate (FEDFUNDS), 10Y Treasury (DGS10).

Auth: API key as query parameter.
Rate limit: 120 requests/minute.
Docs: https://fred.stlouisfed.org/docs/api/fred/
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.fred")

# Key economic series IDs for prediction market analysis
FRED_SERIES = {
    "gdp": "GDPC1",              # Real GDP (quarterly)
    "cpi": "CPIAUCSL",           # CPI All Items (monthly)
    "unemployment": "UNRATE",     # Unemployment Rate (monthly)
    "fed_funds": "FEDFUNDS",      # Effective Federal Funds Rate (monthly)
    "treasury_10y": "DGS10",      # 10-Year Treasury Constant Maturity (daily)
    "treasury_2y": "DGS2",        # 2-Year Treasury (daily)
    "initial_claims": "ICSA",     # Initial Jobless Claims (weekly)
    "pce": "PCEPI",               # PCE Price Index (monthly)
    "core_pce": "PCEPILFE",       # Core PCE (monthly)
    "nonfarm_payroll": "PAYEMS",  # Total Nonfarm Payrolls (monthly)
    "retail_sales": "RSAFS",      # Advance Retail Sales (monthly)
    "housing_starts": "HOUST",    # Housing Starts (monthly)
    "ism_manufacturing": "MANEMP",# Manufacturing Employment (monthly)
    "consumer_sentiment": "UMCSENT",  # U of Michigan Consumer Sentiment
}


class FredClient(BaseDataClient):
    """Async FRED API client for economic data retrieval."""

    def __init__(self) -> None:
        super().__init__(
            name="FRED",
            base_url="https://api.stlouisfed.org/fred",
            requests_per_second=2.0,  # 120/min
        )
        self._api_key = ""

    def initialize(self) -> bool:
        self._api_key = self._get_env("FRED_API_KEY")
        if not self._api_key:
            logger.warning("FRED_API_KEY not set — FRED client disabled")
            return False
        return super().initialize()

    def _build_params(self) -> dict[str, str]:
        return {"api_key": self._api_key, "file_type": "json"}

    async def get_series_observations(
        self,
        series_id: str,
        observation_start: str | None = None,
        observation_end: str | None = None,
        limit: int = 100,
        sort_order: str = "desc",
    ) -> list[dict[str, Any]]:
        """Fetch time series observations.

        Args:
            series_id:         FRED series ID (e.g., "GDPC1").
            observation_start: Start date (YYYY-MM-DD).
            observation_end:   End date (YYYY-MM-DD).
            limit:             Max observations to return.
            sort_order:        "asc" or "desc".

        Returns:
            List of {"date": "YYYY-MM-DD", "value": "123.45"} dicts.
        """
        params: dict[str, Any] = {
            "series_id": series_id,
            "limit": limit,
            "sort_order": sort_order,
        }
        if observation_start:
            params["observation_start"] = observation_start
        if observation_end:
            params["observation_end"] = observation_end

        data = await self.get("/series/observations", params=params)
        if data and "observations" in data:
            return data["observations"]
        return []

    async def get_latest_value(self, series_id: str) -> dict[str, Any] | None:
        """Get the most recent observation for a series."""
        obs = await self.get_series_observations(series_id, limit=1, sort_order="desc")
        return obs[0] if obs else None

    async def search_series(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search for FRED series by keyword."""
        data = await self.get("/series/search", params={
            "search_text": query, "limit": limit,
        })
        if data and "seriess" in data:
            return data["seriess"]
        return []

    async def get_series_info(self, series_id: str) -> dict[str, Any] | None:
        """Get metadata for a series (title, frequency, units, etc.)."""
        data = await self.get("/series", params={"series_id": series_id})
        if data and "seriess" in data and data["seriess"]:
            return data["seriess"][0]
        return None

    async def health_check(self) -> dict[str, Any]:
        """Verify FRED API connectivity by fetching latest fed funds rate."""
        try:
            result = await self.get_latest_value("FEDFUNDS")
            if result:
                return {
                    "ok": True,
                    "service": "FRED",
                    "detail": f"Fed Funds Rate: {result['value']}% (as of {result['date']})",
                }
            return {"ok": False, "service": "FRED", "detail": "No data returned"}
        except Exception as e:
            return {"ok": False, "service": "FRED", "detail": str(e)}
