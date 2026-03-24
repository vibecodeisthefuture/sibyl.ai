"""
TMDb Client — The Movie Database API v3.

Provides movie/TV data: trending, upcoming, box office, ratings.
Auth: Bearer token in Authorization header.
Rate limit: 40 requests per 10 seconds.
Docs: https://developer.themoviedb.org/v3
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.tmdb")


class TmdbClient(BaseDataClient):
    """Async TMDb client for movie/TV entertainment data."""

    def __init__(self) -> None:
        super().__init__(
            name="TMDb",
            base_url="https://api.themoviedb.org/3",
            requests_per_second=4.0,  # 40 per 10s
        )
        self._api_key = ""

    def initialize(self) -> bool:
        self._api_key = self._get_env("TMDB_API_KEY")
        if not self._api_key:
            logger.warning("TMDB_API_KEY not set — TMDb client disabled")
            return False
        return super().initialize()

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

    async def get_trending(self, media_type: str = "all", time_window: str = "week") -> list[dict[str, Any]]:
        """Get trending movies/TV/people."""
        data = await self.get(f"/trending/{media_type}/{time_window}")
        if data and "results" in data:
            return data["results"]
        return []

    async def get_upcoming_movies(self, region: str = "US") -> list[dict[str, Any]]:
        """Get upcoming movie releases."""
        data = await self.get("/movie/upcoming", params={"region": region})
        if data and "results" in data:
            return data["results"]
        return []

    async def get_now_playing(self, region: str = "US") -> list[dict[str, Any]]:
        """Get movies currently in theaters."""
        data = await self.get("/movie/now_playing", params={"region": region})
        if data and "results" in data:
            return data["results"]
        return []

    async def search_movie(self, query: str) -> list[dict[str, Any]]:
        """Search for movies by title."""
        data = await self.get("/search/movie", params={"query": query})
        if data and "results" in data:
            return data["results"]
        return []

    async def health_check(self) -> dict[str, Any]:
        try:
            trending = await self.get_trending("movie", "week")
            if trending:
                top = trending[0].get("title", "Unknown")
                return {"ok": True, "service": "TMDb", "detail": f"Top trending: {top}"}
            return {"ok": False, "service": "TMDb", "detail": "No trending data"}
        except Exception as e:
            return {"ok": False, "service": "TMDb", "detail": str(e)}
