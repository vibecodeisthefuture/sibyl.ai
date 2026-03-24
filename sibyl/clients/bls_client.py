"""
BLS API Client — Bureau of Labor Statistics v2.

Provides employment, inflation, and labor market data.
Key series: CPI (CUSR0000SA0), Unemployment (LNS14000000),
Nonfarm Payrolls (CES0000000001).

Auth: Registration key in JSON POST body.
Rate limit: 500 queries/day (v2 registered), 50 series/query, 20 years/request.
Docs: https://www.bls.gov/developers/api_signature_v2.htm
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.bls")

BLS_SERIES = {
    "cpi_all": "CUSR0000SA0",          # CPI All Items, Seasonally Adjusted
    "cpi_core": "CUSR0000SA0L1E",      # CPI All Items Less Food and Energy
    "unemployment": "LNS14000000",      # Unemployment Rate
    "nonfarm_payroll": "CES0000000001", # Total Nonfarm Employment
    "avg_hourly_earnings": "CES0500000003",  # Average Hourly Earnings
    "labor_force_participation": "LNS11300000",  # LFPR
    "ppi_final_demand": "WPUFD49104",   # PPI Final Demand
}


class BlsClient(BaseDataClient):
    """Async BLS API v2 client for labor statistics."""

    def __init__(self) -> None:
        super().__init__(
            name="BLS",
            base_url="https://api.bls.gov/publicAPI/v2",
            requests_per_second=1.0,  # Conservative — 500/day
        )
        self._api_key = ""

    def initialize(self) -> bool:
        self._api_key = self._get_env("BLS_API_KEY")
        if not self._api_key:
            logger.warning("BLS_API_KEY not set — BLS client disabled")
            return False
        return super().initialize()

    async def get_series_data(
        self,
        series_ids: list[str],
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch data for one or more BLS series.

        Args:
            series_ids: List of BLS series IDs (max 50).
            start_year: Start year (e.g., 2020).
            end_year:   End year (e.g., 2026).

        Returns:
            Dict mapping series_id to list of data points:
            {"CUSR0000SA0": [{"year": "2026", "period": "M01", "value": "315.2"}, ...]}
        """
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        if not end_year:
            end_year = now.year
        if not start_year:
            start_year = end_year - 5

        body = {
            "seriesid": series_ids[:50],  # Max 50 per request
            "startyear": str(start_year),
            "endyear": str(end_year),
            "registrationkey": self._api_key,
        }

        data = await self.post("/timeseries/data/", json_body=body)
        if not data or data.get("status") != "REQUEST_SUCCEEDED":
            logger.error("BLS request failed: %s", data.get("message") if data else "no response")
            return {}

        result: dict[str, list[dict[str, Any]]] = {}
        for series in data.get("Results", {}).get("series", []):
            sid = series.get("seriesID", "")
            result[sid] = series.get("data", [])
        return result

    async def get_latest_value(self, series_id: str) -> dict[str, Any] | None:
        """Get the most recent data point for a series."""
        data = await self.get_series_data([series_id])
        points = data.get(series_id, [])
        return points[0] if points else None  # BLS returns most recent first

    async def health_check(self) -> dict[str, Any]:
        """Verify BLS API connectivity by fetching latest unemployment rate."""
        try:
            result = await self.get_latest_value("LNS14000000")
            if result:
                return {
                    "ok": True,
                    "service": "BLS",
                    "detail": f"Unemployment: {result['value']}% ({result.get('periodName', '')} {result['year']})",
                }
            return {"ok": False, "service": "BLS", "detail": "No data returned"}
        except Exception as e:
            return {"ok": False, "service": "BLS", "detail": str(e)}
