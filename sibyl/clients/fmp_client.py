"""
FMP Client — Financial Modeling Prep API.

Provides earnings calendars, economic calendars, market indices, and stock data.
Auth: API key as query parameter.
Rate limit: 250 requests/day (free tier).
Docs: https://site.financialmodelingprep.com/developer/docs
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.fmp")


class FmpClient(BaseDataClient):
    """Async FMP client for financial data and economic calendars."""

    def __init__(self) -> None:
        super().__init__(
            name="FMP",
            base_url="https://financialmodelingprep.com",
            requests_per_second=0.5,  # Conservative for 250/day
        )
        self._api_key = ""

    def initialize(self) -> bool:
        self._api_key = self._get_env("FMP_API_KEY")
        if not self._api_key:
            logger.warning("FMP_API_KEY not set — FMP client disabled")
            return False
        return super().initialize()

    def _build_params(self) -> dict[str, str]:
        return {"apikey": self._api_key}

    async def get_quote(self, symbol: str = "AAPL") -> dict[str, Any] | None:
        """Get real-time quote for a stock or index.

        Note: Uses /stable/ endpoint (new format as of 2025+).
        """
        data = await self.get(f"/stable/quote", params={"symbol": symbol})
        if isinstance(data, list) and data:
            return data[0]
        return None

    async def get_earnings_calendar(
        self, from_date: str | None = None, to_date: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch earnings announcements with estimated/actual EPS.

        Note: May require paid plan on new FMP endpoints.
        """
        params: dict[str, Any] = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = await self.get("/stable/earning-calendar", params=params)
        return data if isinstance(data, list) else []

    async def get_market_gainers(self) -> list[dict[str, Any]]:
        """Get today's top stock gainers."""
        data = await self.get("/stable/stock-market-gainers")
        return data if isinstance(data, list) else []

    async def get_market_losers(self) -> list[dict[str, Any]]:
        """Get today's top stock losers."""
        data = await self.get("/stable/stock-market-losers")
        return data if isinstance(data, list) else []

    async def search_company(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search for companies by name or ticker."""
        data = await self.get("/stable/search", params={"query": query, "limit": limit})
        return data if isinstance(data, list) else []

    async def health_check(self) -> dict[str, Any]:
        """Verify FMP API connectivity by fetching a stock quote."""
        try:
            quote = await self.get_quote("AAPL")
            if quote:
                return {
                    "ok": True,
                    "service": "FMP",
                    "detail": f"AAPL: ${quote.get('price', 'N/A')} ({quote.get('changePercentage', 0):+.2f}%)",
                }
            return {"ok": False, "service": "FMP", "detail": "No quote data"}
        except Exception as e:
            return {"ok": False, "service": "FMP", "detail": str(e)}
