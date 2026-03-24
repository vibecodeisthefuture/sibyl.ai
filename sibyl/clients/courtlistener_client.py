"""
CourtListener Client — Free Law Project court data API v3.

Provides court opinions, dockets, and oral arguments from federal/state courts.
Useful for legal/regulatory prediction markets (Supreme Court decisions, etc.).
Auth: Token in Authorization header.
Docs: https://www.courtlistener.com/api/rest-info/
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.courtlistener")


class CourtListenerClient(BaseDataClient):
    """Async CourtListener client for court opinion and docket data."""

    def __init__(self) -> None:
        super().__init__(
            name="CourtListener",
            base_url="https://www.courtlistener.com/api/rest/v3",
            requests_per_second=0.25,  # 15/min free tier
        )
        self._token = ""

    def initialize(self) -> bool:
        self._token = self._get_env("COURTLISTENER_API_KEY")
        if not self._token:
            logger.warning("COURTLISTENER_API_KEY not set — CourtListener disabled")
            return False
        return super().initialize()

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self._token}",
            "Accept": "application/json",
        }

    async def search_opinions(self, query: str, court: str | None = None, page_size: int = 10) -> dict[str, Any]:
        """Search court opinions."""
        params: dict[str, Any] = {"q": query, "page_size": page_size}
        if court:
            params["court"] = court
        data = await self.get("/search/", params=params)
        return data if isinstance(data, dict) else {}

    async def get_opinion(self, opinion_id: int) -> dict[str, Any]:
        """Get a specific opinion by ID."""
        data = await self.get(f"/opinions/{opinion_id}/")
        return data if isinstance(data, dict) else {}

    async def get_dockets(self, query: str, court: str | None = None, page_size: int = 10) -> dict[str, Any]:
        """Search dockets (case filings)."""
        params: dict[str, Any] = {"q": query, "page_size": page_size}
        if court:
            params["court"] = court
        data = await self.get("/dockets/", params=params)
        return data if isinstance(data, dict) else {}

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await self.search_opinions("supreme court", page_size=1)
            if data and "results" in data:
                count = data.get("count", 0)
                return {
                    "ok": True,
                    "service": "CourtListener",
                    "detail": f"Supreme Court opinions: {count:,} results",
                }
            return {"ok": False, "service": "CourtListener", "detail": "No results"}
        except Exception as e:
            return {"ok": False, "service": "CourtListener", "detail": str(e)}
