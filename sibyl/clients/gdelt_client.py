"""
GDELT Client — Global Database of Events, Language, and Tone.

Monitors global news and events in real-time. Useful for geopolitical
prediction markets (conflicts, diplomatic events, crises).
No auth required.
Docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-documentation/
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.gdelt")


class GdeltClient(BaseDataClient):
    """Async GDELT client for global event monitoring."""

    def __init__(self) -> None:
        super().__init__(
            name="GDELT",
            base_url="https://api.gdeltproject.org/api/v2",
            requests_per_second=1.0,
        )

    def initialize(self) -> bool:
        return super().initialize()

    async def search_articles(
        self,
        query: str,
        mode: str = "ArtList",
        max_records: int = 25,
        timespan: str = "7d",
        sourcelang: str = "english",
    ) -> dict[str, Any]:
        """Search GDELT's global news database.

        Args:
            query:       Search query.
            mode:        ArtList (articles), TimelineVol (volume timeline),
                         TimelineTone (tone timeline), ToneChart.
            max_records: Max articles to return.
            timespan:    Time window (e.g., "7d", "30d", "1y").
            sourcelang:  Language filter.
        """
        params = {
            "query": query,
            "mode": mode,
            "maxrecords": max_records,
            "timespan": timespan,
            "sourcelang": sourcelang,
            "format": "json",
        }
        data = await self.get("/doc/doc", params=params)
        return data if isinstance(data, dict) else {}

    async def get_geo_events(
        self, query: str, mode: str = "PointData", max_records: int = 25
    ) -> dict[str, Any]:
        """Get geo-located events from GDELT GEO API."""
        params = {
            "query": query,
            "mode": mode,
            "maxrecords": max_records,
            "format": "json",
        }
        data = await self.get("/geo/geo", params=params)
        return data if isinstance(data, dict) else {}

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await self.search_articles("United States", max_records=1, timespan="1d")
            if data and "articles" in data:
                count = len(data["articles"])
                return {
                    "ok": True,
                    "service": "GDELT",
                    "detail": f"US news articles found: {count}",
                }
            # GDELT may return different structure
            return {"ok": True, "service": "GDELT", "detail": "API responsive (checking format)"}
        except Exception as e:
            return {"ok": False, "service": "GDELT", "detail": str(e)}
