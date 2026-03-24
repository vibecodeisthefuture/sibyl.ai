"""
Open-Meteo Client — Free weather forecast API.

Provides hourly/daily weather forecasts, historical weather, and air quality.
No API key required for free tier.
Docs: https://open-meteo.com/en/docs
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.open_meteo")

# Archive API lives on a separate subdomain
_ARCHIVE_BASE_URL = "https://archive-api.open-meteo.com"


class OpenMeteoClient(BaseDataClient):
    """Async Open-Meteo client for weather forecasts and historical data."""

    def __init__(self) -> None:
        super().__init__(
            name="OpenMeteo",
            base_url="https://api.open-meteo.com",
            requests_per_second=5.0,  # Generous free tier
        )

    def initialize(self) -> bool:
        # No API key needed
        return super().initialize()

    async def get_forecast(
        self,
        latitude: float,
        longitude: float,
        hourly: list[str] | None = None,
        daily: list[str] | None = None,
        forecast_days: int = 7,
    ) -> dict[str, Any] | None:
        """Get weather forecast for a location.

        Args:
            latitude:      Location latitude.
            longitude:     Location longitude.
            hourly:        Hourly variables (e.g., ["temperature_2m", "precipitation"]).
            daily:         Daily variables (e.g., ["temperature_2m_max", "precipitation_sum"]).
            forecast_days: Number of forecast days (1-16).

        Returns:
            Forecast data with hourly/daily arrays.
        """
        params: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
            "forecast_days": forecast_days,
            "timezone": "auto",
        }
        if hourly:
            params["hourly"] = ",".join(hourly)
        if daily:
            params["daily"] = ",".join(daily)

        return await self.get("/v1/forecast", params=params)

    async def get_historical_weather(
        self,
        latitude: float,
        longitude: float,
        start_date: str,
        end_date: str,
        daily: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Get historical weather data from Open-Meteo Archive API.

        Uses archive-api.open-meteo.com (separate subdomain from forecasts).

        Args:
            latitude:   Location latitude.
            longitude:  Location longitude.
            start_date: YYYY-MM-DD format.
            end_date:   YYYY-MM-DD format.
            daily:      Variables (e.g., ["temperature_2m_max", "precipitation_sum"]).
        """
        params: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_date,
            "end_date": end_date,
            "timezone": "auto",
        }
        if daily:
            params["daily"] = ",".join(daily)

        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                resp = await http.get(
                    f"{_ARCHIVE_BASE_URL}/v1/archive", params=params
                )
                if resp.status_code == 200:
                    return resp.json()
                logger.error(
                    "OpenMeteo Archive: HTTP %d: %s",
                    resp.status_code, resp.text[:200],
                )
                return None
        except Exception as e:
            logger.error("OpenMeteo Archive error: %s", e)
            return None

    async def health_check(self) -> dict[str, Any]:
        """Verify Open-Meteo connectivity with a NYC forecast."""
        try:
            data = await self.get_forecast(
                latitude=40.71, longitude=-74.01,
                daily=["temperature_2m_max"], forecast_days=1,
            )
            if data and "daily" in data:
                temp = data["daily"].get("temperature_2m_max", [None])[0]
                return {
                    "ok": True,
                    "service": "OpenMeteo",
                    "detail": f"NYC forecast max temp: {temp}°C",
                }
            return {"ok": False, "service": "OpenMeteo", "detail": "No data returned"}
        except Exception as e:
            return {"ok": False, "service": "OpenMeteo", "detail": str(e)}
