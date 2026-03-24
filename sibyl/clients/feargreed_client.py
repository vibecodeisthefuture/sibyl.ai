"""
Crypto Fear & Greed Index Client — Alternative.me API.

Provides the daily Crypto Fear & Greed Index (0-100).
No API key required.
Docs: https://alternative.me/crypto/fear-and-greed-index/
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.feargreed")


class FearGreedClient(BaseDataClient):
    """Async Fear & Greed Index client."""

    def __init__(self) -> None:
        super().__init__(
            name="FearGreed",
            base_url="https://api.alternative.me",
            requests_per_second=1.0,
        )

    def initialize(self) -> bool:
        return super().initialize()

    async def get_index(self, limit: int = 1) -> list[dict[str, Any]]:
        """Get Fear & Greed Index data.

        Args:
            limit: Number of data points (1 = latest, 30 = last 30 days).

        Returns:
            List of {"value": "73", "value_classification": "Greed",
                      "timestamp": "1711065600"} dicts.
        """
        data = await self.get("/fng/", params={"limit": limit, "format": "json"})
        if data and "data" in data:
            return data["data"]
        return []

    async def get_latest(self) -> dict[str, Any] | None:
        """Get the latest Fear & Greed value."""
        points = await self.get_index(limit=1)
        return points[0] if points else None

    async def health_check(self) -> dict[str, Any]:
        try:
            latest = await self.get_latest()
            if latest:
                return {
                    "ok": True,
                    "service": "FearGreed",
                    "detail": f"Index: {latest['value']} ({latest['value_classification']})",
                }
            return {"ok": False, "service": "FearGreed", "detail": "No data"}
        except Exception as e:
            return {"ok": False, "service": "FearGreed", "detail": str(e)}
