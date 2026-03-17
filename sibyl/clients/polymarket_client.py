"""Async HTTP client for the Polymarket CLOB + Gamma APIs (read-only).

Polymarket is geo-restricted in the US.  Sibyl uses this client exclusively
for market data ingestion and cross-platform arbitrage detection — no order
placement or position management.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("sibyl.clients.polymarket")

# ── Public (unauthenticated) base URLs ────────────────────────────────────
CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"


class RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, max_per_second: float = 10.0) -> None:
        self._interval = 1.0 / max_per_second
        self._last: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class PolymarketClient:
    """Async client for Polymarket public data endpoints.

    All methods are read-only — no authentication required for the
    endpoints used here.
    """

    def __init__(
        self,
        rate_limit: float = 10.0,
        timeout: float = 15.0,
    ) -> None:
        self._rate = RateLimiter(rate_limit)
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── Low-level request ─────────────────────────────────────────────

    async def _get(self, base: str, path: str, params: dict | None = None) -> Any:
        """Rate-limited GET request with retry on transient errors."""
        await self._rate.acquire()
        client = await self._ensure_client()
        for attempt in range(3):
            try:
                resp = await client.get(f"{base}{path}", params=params)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2))
                    logger.warning("Polymarket 429 — backing off %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempt == 2:
                    raise
                logger.warning("Polymarket request failed (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(1.5 * (attempt + 1))
        return None  # unreachable but satisfies type checker

    # ── Gamma API — Market Discovery ──────────────────────────────────

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
    ) -> list[dict]:
        """Fetch paginated market listing from the Gamma API."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if active:
            params["active"] = "true"
        if closed:
            params["closed"] = "true"
        data = await self._get(GAMMA_BASE, "/markets", params)
        return data if isinstance(data, list) else []

    async def get_market(self, condition_id: str) -> dict | None:
        """Fetch a single market by condition ID."""
        data = await self._get(GAMMA_BASE, f"/markets/{condition_id}")
        return data if isinstance(data, dict) else None

    # ── CLOB API — Pricing ────────────────────────────────────────────

    async def get_midpoint(self, token_id: str) -> float | None:
        """Get the midpoint price for a token."""
        data = await self._get(CLOB_BASE, "/midpoint", {"token_id": token_id})
        if isinstance(data, dict) and "mid" in data:
            return float(data["mid"])
        return None

    async def get_price(self, token_id: str, side: str = "buy") -> float | None:
        """Get the current price for a token."""
        data = await self._get(
            CLOB_BASE, "/price", {"token_id": token_id, "side": side}
        )
        if isinstance(data, dict) and "price" in data:
            return float(data["price"])
        return None

    async def get_last_trade_price(self, token_id: str) -> float | None:
        """Get the last trade price for a token."""
        data = await self._get(
            CLOB_BASE, "/last-trade-price", {"token_id": token_id}
        )
        if isinstance(data, dict) and "price" in data:
            return float(data["price"])
        return None

    # ── CLOB API — Order Book ─────────────────────────────────────────

    async def get_orderbook(self, token_id: str) -> dict | None:
        """Get the L2 order book for a token.

        Returns dict with 'bids' and 'asks' arrays of {price, size} dicts.
        """
        data = await self._get(CLOB_BASE, "/book", {"token_id": token_id})
        if not isinstance(data, dict):
            return None

        def _parse_levels(levels: list) -> list[dict]:
            result = []
            for level in levels or []:
                try:
                    result.append({
                        "price": float(level.get("price", 0)),
                        "size": float(level.get("size", 0)),
                    })
                except (TypeError, ValueError):
                    continue
            return result

        return {
            "bids": _parse_levels(data.get("bids", [])),
            "asks": _parse_levels(data.get("asks", [])),
        }

    # ── CLOB API — Price History ──────────────────────────────────────

    async def get_price_history(
        self,
        token_id: str,
        interval: str = "1d",
        fidelity: int = 60,
    ) -> list[dict]:
        """Fetch historical price data for a token."""
        data = await self._get(
            CLOB_BASE,
            "/prices-history",
            {"market": token_id, "interval": interval, "fidelity": fidelity},
        )
        if isinstance(data, dict) and "history" in data:
            return data["history"]
        return data if isinstance(data, list) else []

    # ── CLOB API — Trades ─────────────────────────────────────────────

    async def get_trades(
        self,
        token_id: str | None = None,
        limit: int = 100,
        next_cursor: str | None = None,
    ) -> dict:
        """Fetch recent trades.  Returns {data: [...], next_cursor: ...}."""
        params: dict[str, Any] = {"limit": limit}
        if token_id:
            params["asset_id"] = token_id
        if next_cursor:
            params["next_cursor"] = next_cursor
        data = await self._get(CLOB_BASE, "/trades", params)
        if isinstance(data, dict):
            return data
        return {"data": [], "next_cursor": None}
