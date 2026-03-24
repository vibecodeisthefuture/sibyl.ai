"""
TheSportsDB Client — Free sports metadata API.

Provides team info, player details, league data, and recent/upcoming events.
Auth: API key as path parameter.
Free tier: key="123" for testing, limited endpoints. Patreon for premium.
Docs: https://www.thesportsdb.com/api.php
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.thesportsdb")


class TheSportsDbClient(BaseDataClient):
    """Async TheSportsDB client for sports metadata."""

    def __init__(self) -> None:
        super().__init__(
            name="TheSportsDB",
            base_url="https://www.thesportsdb.com/api/v1/json",
            requests_per_second=1.0,
        )
        self._api_key = ""

    def initialize(self) -> bool:
        self._api_key = self._get_env("THESPORTSDB_API_KEY", "123")
        return super().initialize()

    async def search_teams(self, team_name: str) -> list[dict[str, Any]]:
        """Search for teams by name."""
        data = await self.get(f"/{self._api_key}/searchteams.php", params={"t": team_name})
        if data and data.get("teams"):
            return data["teams"]
        return []

    async def get_events_by_date(self, date: str, sport: str = "") -> list[dict[str, Any]]:
        """Get events/games on a specific date (YYYY-MM-DD)."""
        params: dict[str, Any] = {"d": date}
        if sport:
            params["s"] = sport
        data = await self.get(f"/{self._api_key}/eventsday.php", params=params)
        if data and data.get("events"):
            return data["events"]
        return []

    async def get_last_events_by_league(self, league_id: int) -> list[dict[str, Any]]:
        """Get last 15 events for a league."""
        data = await self.get(f"/{self._api_key}/eventspastleague.php", params={"id": league_id})
        if data and data.get("events"):
            return data["events"]
        return []

    async def get_next_events_by_league(self, league_id: int) -> list[dict[str, Any]]:
        """Get next 15 upcoming events for a league."""
        data = await self.get(f"/{self._api_key}/eventsnextleague.php", params={"id": league_id})
        if data and data.get("events"):
            return data["events"]
        return []

    async def get_leagues(self) -> list[dict[str, Any]]:
        """List all available leagues."""
        data = await self.get(f"/{self._api_key}/all_leagues.php")
        if data and data.get("leagues"):
            return data["leagues"]
        return []

    async def health_check(self) -> dict[str, Any]:
        """Verify TheSportsDB connectivity by listing leagues."""
        try:
            leagues = await self.get_leagues()
            if leagues:
                return {
                    "ok": True,
                    "service": "TheSportsDB",
                    "detail": f"Found {len(leagues)} leagues",
                }
            return {"ok": False, "service": "TheSportsDB", "detail": "No leagues returned"}
        except Exception as e:
            return {"ok": False, "service": "TheSportsDB", "detail": str(e)}
