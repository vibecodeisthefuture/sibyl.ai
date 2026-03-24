"""
Congress.gov Client — Congressional data API v3.

Provides bills, votes, members, hearings, and amendments data.
Useful for political/legislative prediction markets.
Auth: API key as query parameter.
Rate limit: 10 requests/second.
Docs: https://api.congress.gov/
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.congress")


class CongressClient(BaseDataClient):
    """Async Congress.gov client for legislative data."""

    def __init__(self) -> None:
        super().__init__(
            name="Congress",
            base_url="https://api.congress.gov/v3",
            requests_per_second=5.0,  # 10/sec limit
        )
        self._api_key = ""

    def initialize(self) -> bool:
        self._api_key = self._get_env("CONGRESS_API_KEY")
        if not self._api_key:
            logger.warning("CONGRESS_API_KEY not set — Congress client disabled")
            return False
        return super().initialize()

    def _build_params(self) -> dict[str, str]:
        return {"api_key": self._api_key, "format": "json"}

    async def get_bills(
        self, congress: int | None = None, bill_type: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """List recent bills.

        Args:
            congress:  Congress number (e.g., 119 for 2025-2027).
            bill_type: "hr", "s", "hjres", "sjres", etc.
            limit:     Max results.
        """
        path = "/bill"
        if congress:
            path += f"/{congress}"
            if bill_type:
                path += f"/{bill_type}"

        data = await self.get(path, params={"limit": limit})
        if data and "bills" in data:
            return data["bills"]
        return []

    async def get_bill(self, congress: int, bill_type: str, bill_number: int) -> dict[str, Any]:
        """Get details for a specific bill."""
        data = await self.get(f"/bill/{congress}/{bill_type}/{bill_number}")
        if data and "bill" in data:
            return data["bill"]
        return {}

    async def get_members(self, congress: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """List current members of Congress."""
        path = "/member"
        if congress:
            path += f"/{congress}"
        data = await self.get(path, params={"limit": limit})
        if data and "members" in data:
            return data["members"]
        return []

    async def get_nominations(self, congress: int, limit: int = 20) -> list[dict[str, Any]]:
        """Get presidential nominations pending Senate confirmation."""
        data = await self.get(f"/nomination/{congress}", params={"limit": limit})
        if data and "nominations" in data:
            return data["nominations"]
        return []

    async def health_check(self) -> dict[str, Any]:
        try:
            bills = await self.get_bills(limit=1)
            if bills:
                latest = bills[0]
                title = latest.get("title", "N/A")[:60]
                return {
                    "ok": True,
                    "service": "Congress",
                    "detail": f"Latest bill: {title}",
                }
            return {"ok": False, "service": "Congress", "detail": "No bills returned"}
        except Exception as e:
            return {"ok": False, "service": "Congress", "detail": str(e)}
