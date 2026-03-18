"""
Async HTTP client for the Polymarket CLOB + Gamma APIs (read-only).

Polymarket is geo-restricted in the US.  Sibyl uses this client exclusively
for market data ingestion and cross-platform arbitrage detection — NO order
placement or position management happens through this client.

POLYMARKET HAS TWO APIs:
    1. GAMMA API (https://gamma-api.polymarket.com)
       - Used for MARKET DISCOVERY: listing active markets, searching, metadata.
       - Returns high-level market info like title, category, close date, status.

    2. CLOB API (https://clob.polymarket.com)
       - Used for MARKET DATA: prices, order books, trades, price history.
       - Works with "token IDs" — each market has YES and NO tokens.
       - Token IDs are found in the Gamma API's market response.

AUTHENTICATION:
    None required.  All endpoints used here are public/unauthenticated.
    Polymarket does have authenticated endpoints for placing orders, but
    we don't use those (US geo-restriction).

RATE LIMITING:
    Polymarket limits to ~10 requests/second.  The RateLimiter class below
    enforces this on the client side to avoid 429 errors.

RETRY LOGIC:
    All requests automatically retry up to 3 times on network errors
    (timeouts, connection failures, 429 rate limits).

USAGE:
    client = PolymarketClient(rate_limit=10.0)
    markets = await client.get_markets(limit=50, active=True)
    price = await client.get_midpoint("token123")
    book = await client.get_orderbook("token123")
    await client.close()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("sibyl.clients.polymarket")

# ── Public (unauthenticated) base URLs ────────────────────────────────────
# These are the root URLs for Polymarket's two APIs.
# All endpoints are appended to these bases (e.g., GAMMA_BASE + "/markets").
CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"


class RateLimiter:
    """Simple token-bucket rate limiter to avoid hitting API rate limits.

    How it works:
        - Tracks the timestamp of the last request.
        - Before each request, calculates how long to wait to maintain
          the desired requests-per-second rate.
        - Uses an asyncio lock to ensure thread-safe access.

    Example:
        limiter = RateLimiter(max_per_second=10.0)  # Max 10 req/s
        await limiter.acquire()  # Waits if needed, then allows request
    """

    def __init__(self, max_per_second: float = 10.0) -> None:
        # Minimum time between requests: e.g., 10 req/s → 0.1s interval
        self._interval = 1.0 / max_per_second
        self._last: float = 0.0  # Timestamp of last request
        self._lock = asyncio.Lock()  # Prevents concurrent access

    async def acquire(self) -> None:
        """Wait until it's safe to make the next request."""
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class PolymarketClient:
    """Async client for Polymarket public data endpoints.

    All methods are READ-ONLY — no authentication required.

    Key concepts:
        - "condition_id": Unique ID for a market (also called market ID).
        - "token_id": Unique ID for a YES or NO token within a market.
          Each market has exactly two tokens (YES and NO).
          Most CLOB API endpoints require a token_id, not a condition_id.
    """

    def __init__(
        self,
        rate_limit: float = 10.0,
        timeout: float = 15.0,
    ) -> None:
        """Initialize the Polymarket client.

        Args:
            rate_limit: Maximum requests per second (default: 10).
            timeout:    HTTP request timeout in seconds (default: 15).
        """
        self._rate = RateLimiter(rate_limit)
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None  # Lazily created

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create the HTTP client on first use.

        This pattern avoids creating the client in __init__ (which runs
        outside the event loop) and instead creates it when actually needed.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client.  Call this when shutting down."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── Low-level request ─────────────────────────────────────────────

    async def _get(self, base: str, path: str, params: dict | None = None) -> Any:
        """Rate-limited GET request with automatic retry on transient errors.

        Retries up to 3 times for:
          - Network timeouts / connection errors
          - 429 (Too Many Requests) — uses Retry-After header for backoff

        Args:
            base:   API base URL (CLOB_BASE or GAMMA_BASE).
            path:   Endpoint path (e.g., "/markets", "/midpoint").
            params: Optional query parameters.

        Returns:
            Parsed JSON response (dict or list).

        Raises:
            httpx.HTTPStatusError: If the request fails after all retries.
        """
        await self._rate.acquire()
        client = await self._ensure_client()

        for attempt in range(3):
            try:
                resp = await client.get(f"{base}{path}", params=params)

                # Handle rate limiting: wait and retry
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2))
                    logger.warning("Polymarket 429 — backing off %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                resp.raise_for_status()  # Raise for 4xx/5xx errors
                return resp.json()

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempt == 2:  # Last attempt — re-raise the error
                    raise
                logger.warning("Polymarket request failed (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(1.5 * (attempt + 1))  # 1.5s, 3.0s backoff

        return None  # Unreachable, but satisfies type checker

    # ── Gamma API — Market Discovery ──────────────────────────────────
    # The Gamma API is used to find and list prediction markets.

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
    ) -> list[dict]:
        """Fetch paginated market listing from the Gamma API.

        Args:
            limit:   Max number of markets to return (default: 100).
            offset:  Pagination offset for fetching subsequent pages.
            active:  If True, only return active (open) markets.
            closed:  If True, include closed/resolved markets.

        Returns:
            List of market dicts, each containing:
              - condition_id: Unique market identifier
              - question: Market question text ("Will X happen?")
              - tokens: List of token objects with token_id
              - tags: Category tags
              - end_date_iso: Market close date
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if active:
            params["active"] = "true"
        if closed:
            params["closed"] = "true"
        data = await self._get(GAMMA_BASE, "/markets", params)
        return data if isinstance(data, list) else []

    async def get_market(self, condition_id: str) -> dict | None:
        """Fetch a single market by its condition ID.

        Args:
            condition_id: The market's unique identifier.

        Returns:
            Market dict with full metadata, or None if not found.
        """
        data = await self._get(GAMMA_BASE, f"/markets/{condition_id}")
        return data if isinstance(data, dict) else None

    # ── CLOB API — Pricing ────────────────────────────────────────────
    # The CLOB API provides real-time pricing data for individual tokens.

    async def get_midpoint(self, token_id: str) -> float | None:
        """Get the midpoint price for a token (average of best bid and ask).

        Args:
            token_id: The YES or NO token's unique identifier.

        Returns:
            Float between 0.0 and 1.0 representing probability, or None.
        """
        data = await self._get(CLOB_BASE, "/midpoint", {"token_id": token_id})
        if isinstance(data, dict) and "mid" in data:
            return float(data["mid"])
        return None

    async def get_price(self, token_id: str, side: str = "buy") -> float | None:
        """Get the current executable price for a token.

        Args:
            token_id: The token's unique identifier.
            side:     "buy" or "sell" (default: "buy").

        Returns:
            Float price (0.0–1.0), or None if unavailable.
        """
        data = await self._get(
            CLOB_BASE, "/price", {"token_id": token_id, "side": side}
        )
        if isinstance(data, dict) and "price" in data:
            return float(data["price"])
        return None

    async def get_last_trade_price(self, token_id: str) -> float | None:
        """Get the last trade price for a token.

        Returns the price at which the most recent trade executed.
        """
        data = await self._get(
            CLOB_BASE, "/last-trade-price", {"token_id": token_id}
        )
        if isinstance(data, dict) and "price" in data:
            return float(data["price"])
        return None

    # ── CLOB API — Order Book ─────────────────────────────────────────

    async def get_orderbook(self, token_id: str) -> dict | None:
        """Get the L2 order book for a token.

        The order book shows all resting limit orders at each price level.

        Returns:
            Dict with 'bids' and 'asks' arrays of {price, size} dicts.
            Example:
                {
                    "bids": [{"price": 0.60, "size": 100.0}, ...],
                    "asks": [{"price": 0.65, "size": 150.0}, ...]
                }
            Or None if the request failed.
        """
        data = await self._get(CLOB_BASE, "/book", {"token_id": token_id})
        if not isinstance(data, dict):
            return None

        def _parse_levels(levels: list) -> list[dict]:
            """Convert raw API levels into clean {price, size} dicts."""
            result = []
            for level in levels or []:
                try:
                    result.append({
                        "price": float(level.get("price", 0)),
                        "size": float(level.get("size", 0)),
                    })
                except (TypeError, ValueError):
                    continue  # Skip malformed levels
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
        """Fetch historical price data for a token.

        Args:
            token_id: The token's unique identifier.
            interval: Time window (e.g., "1d", "1w", "1m").
            fidelity: Candlestick resolution in minutes (default: 60 = hourly).

        Returns:
            List of price history entries (OHLCV-like data).
        """
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
        """Fetch recent trades.

        Args:
            token_id:    Optional filter by token ID.
            limit:       Max trades to return (default: 100).
            next_cursor: Pagination cursor for subsequent pages.

        Returns:
            Dict with:
              - data: List of trade objects (side, size, price, timestamp)
              - next_cursor: Cursor string for fetching the next page
        """
        params: dict[str, Any] = {"limit": limit}
        if token_id:
            params["asset_id"] = token_id
        if next_cursor:
            params["next_cursor"] = next_cursor
        data = await self._get(CLOB_BASE, "/trades", params)
        if isinstance(data, dict):
            return data
        return {"data": [], "next_cursor": None}
