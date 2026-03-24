"""
API-SPORTS Client — Real-time sports data (scores, fixtures, odds).

Supports multiple sports: football (soccer), basketball, baseball, hockey, etc.
Auth: x-apisports-key header.
Rate limit: 100 requests/day (free tier, per sport).
Docs: https://api-sports.io/documentation/football/v3
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.api_sports")

# Base URLs per sport
SPORT_URLS = {
    "football": "https://v3.football.api-sports.io",
    "basketball": "https://v1.basketball.api-sports.io",
    "baseball": "https://v1.baseball.api-sports.io",
    "hockey": "https://v1.hockey.api-sports.io",
    "american_football": "https://v1.american-football.api-sports.io",
}


class ApiSportsClient(BaseDataClient):
    """Async API-SPORTS client for multi-sport data."""

    def __init__(self, sport: str = "football") -> None:
        base_url = SPORT_URLS.get(sport, SPORT_URLS["football"])
        super().__init__(
            name=f"API-SPORTS-{sport}",
            base_url=base_url,
            requests_per_second=0.5,  # Conservative for 100/day
        )
        self._api_key = ""
        self._sport = sport

    def initialize(self) -> bool:
        self._api_key = self._get_env("API_SPORTS_KEY")
        if not self._api_key:
            logger.warning("API_SPORTS_KEY not set — API-SPORTS client disabled")
            return False
        return super().initialize()

    def _build_headers(self) -> dict[str, str]:
        return {
            "x-apisports-key": self._api_key,
            "Accept": "application/json",
        }

    async def get_fixtures(
        self,
        league: int | None = None,
        season: int | None = None,
        date: str | None = None,
        live: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get fixtures/games.

        Args:
            league: League ID.
            season: Season year.
            date:   Date (YYYY-MM-DD).
            live:   "all" for live games.
        """
        params: dict[str, Any] = {}
        if league:
            params["league"] = league
        if season:
            params["season"] = season
        if date:
            params["date"] = date
        if live:
            params["live"] = live

        data = await self.get("/fixtures", params=params)
        if data and "response" in data:
            return data["response"]
        return []

    async def get_odds(
        self, fixture: int | None = None, league: int | None = None, season: int | None = None
    ) -> list[dict[str, Any]]:
        """Get betting odds for fixtures."""
        params: dict[str, Any] = {}
        if fixture:
            params["fixture"] = fixture
        if league:
            params["league"] = league
        if season:
            params["season"] = season
        data = await self.get("/odds", params=params)
        if data and "response" in data:
            return data["response"]
        return []

    async def get_standings(self, league: int, season: int) -> list[dict[str, Any]]:
        """Get league standings."""
        data = await self.get("/standings", params={"league": league, "season": season})
        if data and "response" in data:
            return data["response"]
        return []

    async def get_status(self) -> dict[str, Any] | None:
        """Get API account status (remaining requests, etc.)."""
        return await self.get("/status")

    async def health_check(self) -> dict[str, Any]:
        """Verify API-SPORTS connectivity by checking account status."""
        try:
            data = await self.get_status()
            if data and "response" in data:
                resp = data["response"]
                account = resp.get("account", {})
                requests_info = resp.get("requests", {})
                return {
                    "ok": True,
                    "service": f"API-SPORTS-{self._sport}",
                    "detail": f"Plan: {account.get('plan', 'N/A')}, "
                              f"Requests today: {requests_info.get('current', 0)}/{requests_info.get('limit_day', 100)}",
                }
            return {"ok": False, "service": f"API-SPORTS-{self._sport}", "detail": "No status data"}
        except Exception as e:
            return {"ok": False, "service": f"API-SPORTS-{self._sport}", "detail": str(e)}
