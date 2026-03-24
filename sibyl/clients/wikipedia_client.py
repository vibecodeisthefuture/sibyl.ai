"""
Wikipedia Pageviews Client — Wikimedia REST API.

Provides page view statistics for Wikipedia articles — useful for tracking
public interest in topics related to prediction markets (celebrities,
events, companies, etc.).

No auth required. Free and open.
Docs: https://wikimedia.org/api/rest_v1/
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.wikipedia")


class WikipediaClient(BaseDataClient):
    """Async Wikipedia Pageviews client for tracking public interest."""

    def __init__(self) -> None:
        super().__init__(
            name="Wikipedia",
            base_url="https://wikimedia.org/api/rest_v1",
            requests_per_second=5.0,
        )

    def initialize(self) -> bool:
        return super().initialize()

    def _build_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": "Sibyl.ai/0.1 (sybilpredictions.ai@gmail.com)",
        }

    async def get_pageviews(
        self,
        article: str,
        start: str,
        end: str,
        project: str = "en.wikipedia",
        granularity: str = "daily",
    ) -> list[dict[str, Any]]:
        """Get daily/monthly pageviews for a Wikipedia article.

        Args:
            article:     Article title (URL-encoded spaces as underscores).
            start:       Start date (YYYYMMDD or YYYYMMDDHH).
            end:         End date.
            project:     Wiki project (default: en.wikipedia).
            granularity: "daily" or "monthly".
        """
        article_encoded = article.replace(" ", "_")
        path = (
            f"/metrics/pageviews/per-article/{project}/all-access/all-agents"
            f"/{article_encoded}/{granularity}/{start}/{end}"
        )
        data = await self.get(path)
        if data and "items" in data:
            return data["items"]
        return []

    async def get_most_viewed(
        self, date: str, project: str = "en.wikipedia"
    ) -> list[dict[str, Any]]:
        """Get most viewed articles for a date (YYYY/MM/DD format)."""
        path = f"/metrics/pageviews/top/{project}/all-access/{date}"
        data = await self.get(path)
        if data and "items" in data:
            for item in data["items"]:
                return item.get("articles", [])
        return []

    async def health_check(self) -> dict[str, Any]:
        try:
            views = await self.get_pageviews("United_States", "20260301", "20260320")
            if views:
                total = sum(v.get("views", 0) for v in views)
                return {
                    "ok": True,
                    "service": "Wikipedia",
                    "detail": f"'United States' article: {total:,} views in period",
                }
            return {"ok": False, "service": "Wikipedia", "detail": "No pageview data"}
        except Exception as e:
            return {"ok": False, "service": "Wikipedia", "detail": str(e)}
