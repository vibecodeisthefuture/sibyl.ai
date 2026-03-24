"""
ClinicalTrials.gov Client — Clinical trial data API v2.

Provides clinical trial status, phases, sponsors, and outcomes.
Useful for FDA approval prediction markets (PDUFA dates, Phase III results).
No auth required.
Docs: https://clinicaltrials.gov/data-api/api
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.clinicaltrials")


class ClinicalTrialsClient(BaseDataClient):
    """Async ClinicalTrials.gov v2 client for trial data."""

    def __init__(self) -> None:
        super().__init__(
            name="ClinicalTrials",
            base_url="https://clinicaltrials.gov/api/v2",
            requests_per_second=2.0,
        )

    def initialize(self) -> bool:
        return super().initialize()

    def _build_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": "Sibyl.ai/0.1 (sybilpredictions.ai@gmail.com)",
        }

    async def search_studies(
        self,
        query: str,
        status: str | None = None,
        phase: str | None = None,
        page_size: int = 10,
    ) -> dict[str, Any]:
        """Search clinical trials.

        Args:
            query:  Free-text search (drug name, condition, sponsor).
            status: Filter by status (e.g., "RECRUITING", "COMPLETED").
            phase:  Filter by phase (e.g., "PHASE3", "PHASE2").
            page_size: Results per page.
        """
        params: dict[str, Any] = {
            "query.term": query,
            "pageSize": page_size,
            "format": "json",
        }
        if status:
            params["filter.overallStatus"] = status
        if phase:
            params["filter.phase"] = phase

        return await self.get("/studies", params=params) or {}

    async def get_study(self, nct_id: str) -> dict[str, Any]:
        """Get a specific study by NCT ID (e.g., NCT12345678)."""
        data = await self.get(f"/studies/{nct_id}", params={"format": "json"})
        return data if isinstance(data, dict) else {}

    async def get_study_count(self, query: str) -> int:
        """Get the total count of studies matching a query."""
        data = await self.search_studies(query, page_size=1)
        return data.get("totalCount", 0)

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await self.search_studies("cancer", page_size=1)
            total = data.get("totalCount", 0)
            if total > 0:
                return {
                    "ok": True,
                    "service": "ClinicalTrials",
                    "detail": f"'cancer' trials: {total:,} total",
                }
            # ClinicalTrials.gov blocks some cloud/VM IPs with 403
            # This is expected to work from residential/homelab IPs
            return {
                "ok": False,
                "service": "ClinicalTrials",
                "detail": "No data (may be IP-blocked in cloud environments; works from residential IPs)",
            }
        except Exception as e:
            return {"ok": False, "service": "ClinicalTrials", "detail": str(e)}
