"""
ESPN Public API Client — Community-discovered ESPN endpoints.

Provides scores, standings, and schedule data for major US sports.
No official API or auth required — these are publicly accessible endpoints.
Note: Unofficial; endpoints may change without notice.
Reference: https://github.com/pseudo-r/Public-ESPN-API
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.espn")

# Sport/league slugs for ESPN API
ESPN_SPORTS = {
    "nfl": ("football", "nfl"),
    "nba": ("basketball", "nba"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "ncaaf": ("football", "college-football"),
    "ncaab": ("basketball", "mens-college-basketball"),
    "mls": ("soccer", "usa.1"),
    "epl": ("soccer", "eng.1"),
}


class EspnClient(BaseDataClient):
    """Async ESPN client for US sports scores and schedules."""

    def __init__(self) -> None:
        super().__init__(
            name="ESPN",
            base_url="https://site.api.espn.com",
            requests_per_second=2.0,
        )

    def initialize(self) -> bool:
        # No auth needed
        return super().initialize()

    async def get_scoreboard(self, league: str = "nfl", dates: str | None = None) -> dict[str, Any] | None:
        """Get current/recent scoreboard for a league.

        Args:
            league: League key from ESPN_SPORTS (e.g., "nfl", "nba").
            dates:  Date filter (YYYYMMDD format).
        """
        sport, league_slug = ESPN_SPORTS.get(league, ("football", "nfl"))
        params: dict[str, Any] = {}
        if dates:
            params["dates"] = dates
        return await self.get(
            f"/apis/site/v2/sports/{sport}/{league_slug}/scoreboard",
            params=params,
        )

    async def get_standings(self, league: str = "nfl", season: int | None = None) -> dict[str, Any] | None:
        """Get league standings."""
        sport, league_slug = ESPN_SPORTS.get(league, ("football", "nfl"))
        params: dict[str, Any] = {}
        if season:
            params["season"] = season
        return await self.get(
            f"/apis/site/v2/sports/{sport}/{league_slug}/standings",
            params=params,
        )

    async def get_teams(self, league: str = "nfl") -> list[dict[str, Any]]:
        """Get all teams for a league."""
        sport, league_slug = ESPN_SPORTS.get(league, ("football", "nfl"))
        data = await self.get(
            f"/apis/site/v2/sports/{sport}/{league_slug}/teams",
        )
        if data and "sports" in data:
            for s in data["sports"]:
                for lg in s.get("leagues", []):
                    return lg.get("teams", [])
        return []

    async def health_check(self) -> dict[str, Any]:
        """Verify ESPN API connectivity with NBA scoreboard."""
        try:
            data = await self.get_scoreboard("nba")
            if data and "events" in data:
                count = len(data["events"])
                return {
                    "ok": True,
                    "service": "ESPN",
                    "detail": f"NBA scoreboard: {count} events",
                }
            return {"ok": False, "service": "ESPN", "detail": "No scoreboard data"}
        except Exception as e:
            return {"ok": False, "service": "ESPN", "detail": str(e)}
