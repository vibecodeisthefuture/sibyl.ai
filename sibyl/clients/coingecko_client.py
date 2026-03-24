"""
CoinGecko Client — Cryptocurrency market data API.

Provides crypto prices, market cap, volume, Fear & Greed index, and trending.

TIER CONFIGURATION (auto-detected from env):
    - COINGECKO_TIER=basic → Pro API URL, x-cg-pro-api-key header, 250 calls/min
    - COINGECKO_TIER=demo  → Public API URL, x-cg-demo-api-key header, 30 calls/min
    - Default: basic (upgraded Sprint 16)

BUDGET MANAGEMENT:
    Basic tier = 100,000 calls/month ≈ 3,333/day ≈ 139/hour.
    At 5-min pipeline intervals (12 cycles/hour), budget = ~11 calls/cycle.
    Actual usage per cycle: ~5 calls (price + global + trending + markets + FGI).
    Safety margin: 2x headroom.

Docs: https://docs.coingecko.com/reference/introduction
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sibyl.clients.base_data_client import BaseDataClient

logger = logging.getLogger("sibyl.clients.coingecko")

# ── Tier presets ─────────────────────────────────────────────────────────
_TIERS = {
    "demo": {
        "base_url": "https://api.coingecko.com/api/v3",
        "header_key": "x-cg-demo-api-key",
        "rps": 0.5,  # 30/min
    },
    "basic": {
        "base_url": "https://pro-api.coingecko.com/api/v3",
        "header_key": "x-cg-pro-api-key",
        "rps": 4.0,  # 250/min — leave ~10 calls/min headroom
    },
}


class CoinGeckoClient(BaseDataClient):
    """Async CoinGecko client for cryptocurrency data.

    Auto-detects tier from COINGECKO_TIER env var (default: basic).
    Basic tier: Pro API endpoint, 250 calls/min, 100K calls/month.
    """

    def __init__(self) -> None:
        tier_name = os.environ.get("COINGECKO_TIER", "basic").lower()
        tier = _TIERS.get(tier_name, _TIERS["basic"])
        self._tier_name = tier_name
        self._header_key = tier["header_key"]

        super().__init__(
            name="CoinGecko",
            base_url=tier["base_url"],
            requests_per_second=tier["rps"],
        )
        self._api_key = ""

    def initialize(self) -> bool:
        self._api_key = self._get_env("COINGECKO_API_KEY")
        logger.info(
            "CoinGecko tier=%s, url=%s, rps=%.1f",
            self._tier_name, self._base_url, self._rps,
        )
        return super().initialize()

    def _build_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers[self._header_key] = self._api_key
        return headers

    async def get_price(
        self, ids: list[str], vs_currencies: str = "usd", include_24hr_change: bool = True,
    ) -> dict[str, Any]:
        """Get current price for one or more coins.

        Args:
            ids: CoinGecko coin IDs (e.g., ["bitcoin", "ethereum"]).
            vs_currencies: Quote currency.
        """
        params: dict[str, Any] = {
            "ids": ",".join(ids),
            "vs_currencies": vs_currencies,
            "include_24hr_change": str(include_24hr_change).lower(),
        }
        data = await self.get("/simple/price", params=params)
        return data if isinstance(data, dict) else {}

    async def get_coin_markets(
        self, vs_currency: str = "usd", per_page: int = 100, page: int = 1,
        order: str = "market_cap_desc",
        price_change_percentage: str = "24h,7d",
    ) -> list[dict[str, Any]]:
        """Get top coins by market cap with full market data.

        Basic tier: fetches top 100 in a single call (includes 24h + 7d % change).
        This is the most data-dense endpoint — 1 call replaces N individual lookups.
        """
        params = {
            "vs_currency": vs_currency,
            "per_page": per_page,
            "page": page,
            "order": order,
            "price_change_percentage": price_change_percentage,
        }
        data = await self.get("/coins/markets", params=params)
        return data if isinstance(data, list) else []

    async def get_trending(self) -> dict[str, Any]:
        """Get trending coins (top-7 by search volume)."""
        data = await self.get("/search/trending")
        return data if isinstance(data, dict) else {}

    async def get_global(self) -> dict[str, Any]:
        """Get global crypto market data (total market cap, BTC dominance, etc.)."""
        data = await self.get("/global")
        if data and "data" in data:
            return data["data"]
        return {}

    async def health_check(self) -> dict[str, Any]:
        try:
            price = await self.get_price(["bitcoin"])
            if price and "bitcoin" in price:
                btc_price = price["bitcoin"].get("usd", 0)
                return {
                    "ok": True,
                    "service": "CoinGecko",
                    "detail": f"BTC: ${btc_price:,.0f}",
                }
            return {"ok": False, "service": "CoinGecko", "detail": "No price data"}
        except Exception as e:
            return {"ok": False, "service": "CoinGecko", "detail": str(e)}
