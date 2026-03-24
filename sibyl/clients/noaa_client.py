"""
NOAA CDO Client — Climate Data Online API v2.

Provides historical weather observations from NOAA weather stations.
Auth: Token as query parameter.
Rate limit: ~5 requests/second.
Docs: https://www.ncei.noaa.gov/cdo-web/api/v2/
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.noaa")

# Common dataset IDs
NOAA_DATASETS = {
    "daily_summaries": "GHCND",       # Global Historical Climatology Network - Daily
    "normals_daily": "NORMAL_DLY",     # Climate normals (30-year averages)
    "global_summary_month": "GSOM",    # Monthly summaries
}

# Common data type IDs
NOAA_DATATYPES = {
    "max_temp": "TMAX",
    "min_temp": "TMIN",
    "precipitation": "PRCP",
    "snowfall": "SNOW",
    "avg_wind": "AWND",
}


class NoaaClient(BaseDataClient):
    """Async NOAA Climate Data Online client for historical weather."""

    def __init__(self) -> None:
        super().__init__(
            name="NOAA",
            base_url="https://www.ncei.noaa.gov/cdo-web/api/v2",
            requests_per_second=5.0,
        )
        self._token = ""

    def initialize(self) -> bool:
        self._token = self._get_env("NOAA_API_KEY")
        if not self._token:
            logger.warning("NOAA_API_KEY not set — NOAA client disabled")
            return False
        return super().initialize()

    def _build_headers(self) -> dict[str, str]:
        return {"token": self._token, "Accept": "application/json"}

    async def get_data(
        self,
        dataset_id: str = "GHCND",
        datatype_id: str | None = None,
        location_id: str | None = None,
        station_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch climate observation data.

        Args:
            dataset_id:  Dataset (e.g., "GHCND" for daily summaries).
            datatype_id: Data type (e.g., "TMAX" for max temp).
            location_id: Location (e.g., "FIPS:36" for New York State).
            station_id:  Specific station (e.g., "GHCND:USW00094728").
            start_date:  YYYY-MM-DD.
            end_date:    YYYY-MM-DD.
            limit:       Max results (max 1000).
        """
        params: dict[str, Any] = {
            "datasetid": dataset_id,
            "limit": min(limit, 1000),
        }
        if datatype_id:
            params["datatypeid"] = datatype_id
        if location_id:
            params["locationid"] = location_id
        if station_id:
            params["stationid"] = station_id
        if start_date:
            params["startdate"] = start_date
        if end_date:
            params["enddate"] = end_date

        data = await self.get("/data", params=params)
        if data and "results" in data:
            return data["results"]
        return []

    async def get_stations(
        self, location_id: str | None = None, limit: int = 25
    ) -> list[dict[str, Any]]:
        """List weather stations, optionally filtered by location."""
        params: dict[str, Any] = {"limit": limit}
        if location_id:
            params["locationid"] = location_id
        data = await self.get("/stations", params=params)
        if data and "results" in data:
            return data["results"]
        return []

    async def get_datasets(self) -> list[dict[str, Any]]:
        """List available datasets."""
        data = await self.get("/datasets")
        if data and "results" in data:
            return data["results"]
        return []

    async def health_check(self) -> dict[str, Any]:
        """Verify NOAA API connectivity by listing datasets."""
        try:
            datasets = await self.get_datasets()
            if datasets:
                names = [d.get("id", "") for d in datasets[:5]]
                return {
                    "ok": True,
                    "service": "NOAA",
                    "detail": f"Available datasets: {', '.join(names)}",
                }
            return {"ok": False, "service": "NOAA", "detail": "No datasets returned"}
        except Exception as e:
            return {"ok": False, "service": "NOAA", "detail": str(e)}
