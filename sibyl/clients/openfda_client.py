"""
OpenFDA Client — FDA drug, device, and food data.

Provides drug approvals, adverse events, recalls, and enforcement actions.
Auth: API key as query parameter (optional but raises rate limit).
Rate limit: 240 requests/minute with key, 40 without.
Docs: https://open.fda.gov/apis/
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.openfda")


class OpenFdaClient(BaseDataClient):
    """Async OpenFDA client for drug and medical device data."""

    def __init__(self) -> None:
        super().__init__(
            name="OpenFDA",
            base_url="https://api.fda.gov",
            requests_per_second=4.0,  # 240/min with key
        )
        self._api_key = ""

    def initialize(self) -> bool:
        self._api_key = self._get_env("OPENFDA_API_KEY")
        # Works without key but with lower rate limits
        return super().initialize()

    def _build_params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def search_drug_events(
        self, search: str, limit: int = 10
    ) -> dict[str, Any]:
        """Search drug adverse event reports.

        Args:
            search: OpenFDA search query (e.g., 'patient.drug.openfda.brand_name:"OZEMPIC"').
            limit:  Max results.
        """
        data = await self.get("/drug/event.json", params={"search": search, "limit": limit})
        return data if isinstance(data, dict) else {}

    async def search_drug_labels(self, search: str, limit: int = 10) -> dict[str, Any]:
        """Search drug labeling (package inserts)."""
        data = await self.get("/drug/label.json", params={"search": search, "limit": limit})
        return data if isinstance(data, dict) else {}

    async def search_drug_enforcement(self, search: str = "", limit: int = 10) -> dict[str, Any]:
        """Search drug recalls and enforcement actions."""
        params: dict[str, Any] = {"limit": limit}
        if search:
            params["search"] = search
        data = await self.get("/drug/enforcement.json", params=params)
        return data if isinstance(data, dict) else {}

    async def search_device_recalls(self, search: str = "", limit: int = 10) -> dict[str, Any]:
        """Search medical device recalls."""
        params: dict[str, Any] = {"limit": limit}
        if search:
            params["search"] = search
        data = await self.get("/device/recall.json", params=params)
        return data if isinstance(data, dict) else {}

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await self.search_drug_enforcement(limit=1)
            if data and "results" in data:
                return {
                    "ok": True,
                    "service": "OpenFDA",
                    "detail": f"Total enforcement records: {data.get('meta', {}).get('results', {}).get('total', 'N/A')}",
                }
            return {"ok": False, "service": "OpenFDA", "detail": "No data returned"}
        except Exception as e:
            return {"ok": False, "service": "OpenFDA", "detail": str(e)}
