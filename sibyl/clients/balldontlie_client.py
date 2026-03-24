"""
BallDontLie Client — NBA statistics API v2.

Provides NBA player stats, game scores, team data, and season averages.
Auth: API key as Authorization header.
Rate limit: 60 requests/minute (free tier).
Docs: https://docs.balldontlie.io
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.balldontlie")


class BallDontLieClient(BaseDataClient):
    """Async BallDontLie client for NBA stats."""

    def __init__(self) -> None:
        super().__init__(
            name="BallDontLie",
            base_url="https://api.balldontlie.io/v1",
            requests_per_second=1.0,
        )
        self._api_key = ""

    def initialize(self) -> bool:
        self._api_key = self._get_env("BALLDONTLIE_API_KEY")
        if not self._api_key:
            logger.warning("BALLDONTLIE_API_KEY not set — BallDontLie client disabled")
            return False
        return super().initialize()

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": self._api_key,
            "Accept": "application/json",
        }

    async def get_games(
        self, dates: list[str] | None = None, seasons: list[int] | None = None,
        team_ids: list[int] | None = None, per_page: int = 25,
    ) -> list[dict[str, Any]]:
        """Fetch NBA games with optional filters."""
        params: dict[str, Any] = {"per_page": per_page}
        if dates:
            for d in dates:
                params.setdefault("dates[]", [])
                # httpx handles list params
            params["dates[]"] = dates
        if seasons:
            params["seasons[]"] = seasons
        if team_ids:
            params["team_ids[]"] = team_ids

        data = await self.get("/games", params=params)
        if data and "data" in data:
            return data["data"]
        return []

    async def get_players(self, search: str | None = None, per_page: int = 25) -> list[dict[str, Any]]:
        """Search for NBA players by name."""
        params: dict[str, Any] = {"per_page": per_page}
        if search:
            params["search"] = search
        data = await self.get("/players", params=params)
        if data and "data" in data:
            return data["data"]
        return []

    async def get_teams(self) -> list[dict[str, Any]]:
        """Get all NBA teams."""
        data = await self.get("/teams")
        if data and "data" in data:
            return data["data"]
        return []

    async def get_stats(
        self, game_ids: list[int] | None = None, player_ids: list[int] | None = None,
        per_page: int = 25,
    ) -> list[dict[str, Any]]:
        """Get player stats for specific games or players."""
        params: dict[str, Any] = {"per_page": per_page}
        if game_ids:
            params["game_ids[]"] = game_ids
        if player_ids:
            params["player_ids[]"] = player_ids
        data = await self.get("/stats", params=params)
        if data and "data" in data:
            return data["data"]
        return []

    async def health_check(self) -> dict[str, Any]:
        """Verify BallDontLie connectivity by fetching teams."""
        try:
            teams = await self.get_teams()
            if teams:
                return {
                    "ok": True,
                    "service": "BallDontLie",
                    "detail": f"Found {len(teams)} NBA teams",
                }
            return {"ok": False, "service": "BallDontLie", "detail": "No teams returned"}
        except Exception as e:
            return {"ok": False, "service": "BallDontLie", "detail": str(e)}
