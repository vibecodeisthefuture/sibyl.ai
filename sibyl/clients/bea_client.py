"""
BEA API Client — Bureau of Economic Analysis.

Provides GDP, personal income, PCE, and other national accounts data.
Auth: UserID as query parameter.
Docs: https://apps.bea.gov/API/docs/index.htm
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.bea")

# Key NIPA table IDs
BEA_TABLES = {
    "gdp": "T10101",               # GDP and Components
    "personal_income": "T20100",    # Personal Income
    "pce": "T20600",                # PCE by Major Type
    "corporate_profits": "T60900",  # Corporate Profits
    "govt_spending": "T30100",      # Government Current Receipts & Expenditures
}


class BeaClient(BaseDataClient):
    """Async BEA API client for national economic accounts."""

    def __init__(self) -> None:
        super().__init__(
            name="BEA",
            base_url="https://apps.bea.gov/api",
            requests_per_second=1.0,
        )
        self._user_id = ""

    def initialize(self) -> bool:
        self._user_id = self._get_env("BEA_API_KEY")
        if not self._user_id:
            logger.warning("BEA_API_KEY not set — BEA client disabled")
            return False
        return super().initialize()

    def _build_params(self) -> dict[str, str]:
        return {"UserID": self._user_id, "ResultFormat": "JSON"}

    async def get_nipa_data(
        self,
        table_name: str = "T10101",
        frequency: str = "Q",
        year: str = "LAST5",
    ) -> list[dict[str, Any]]:
        """Fetch NIPA (National Income and Product Accounts) table data.

        Args:
            table_name: NIPA table ID (e.g., "T10101" for GDP).
            frequency:  Q=quarterly, A=annual, M=monthly.
            year:       Specific years ("2024,2025") or "LAST5", "ALL".

        Returns:
            List of data rows from the table.
        """
        params = {
            "method": "GetData",
            "DataSetName": "NIPA",
            "TableName": table_name,
            "Frequency": frequency,
            "Year": year,
        }
        data = await self.get("/data", params=params)
        if data and "BEAAPI" in data:
            results = data["BEAAPI"].get("Results", {})
            return results.get("Data", [])
        return []

    async def get_gdp_data(self, year: str = "LAST5") -> list[dict[str, Any]]:
        """Get GDP and major components (quarterly)."""
        return await self.get_nipa_data("T10101", "Q", year)

    async def get_pce_data(self, year: str = "LAST5") -> list[dict[str, Any]]:
        """Get Personal Consumption Expenditures (monthly)."""
        return await self.get_nipa_data("T20600", "M", year)

    async def get_dataset_list(self) -> list[dict[str, Any]]:
        """List all available BEA datasets."""
        data = await self.get("/data", params={"method": "GetDataSetList"})
        if data and "BEAAPI" in data:
            return data["BEAAPI"].get("Results", {}).get("Dataset", [])
        return []

    async def health_check(self) -> dict[str, Any]:
        """Verify BEA API connectivity by listing datasets."""
        try:
            datasets = await self.get_dataset_list()
            if datasets:
                names = [d.get("DatasetName", "") for d in datasets[:5]]
                return {
                    "ok": True,
                    "service": "BEA",
                    "detail": f"Available datasets: {', '.join(names)}",
                }
            return {"ok": False, "service": "BEA", "detail": "No datasets returned"}
        except Exception as e:
            return {"ok": False, "service": "BEA", "detail": str(e)}
