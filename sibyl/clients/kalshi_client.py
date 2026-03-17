"""Async HTTP client for the Kalshi Trading API v2.

Kalshi is the PRIMARY execution platform for Sibyl.  This client handles
both public data endpoints and authenticated trading operations using
RSA-PSS request signing.

Authentication:
    Kalshi uses RSA-PSS (SHA-256) signatures.  Each request includes three
    custom headers:
      - KALSHI-ACCESS-KEY: your API Key ID
      - KALSHI-ACCESS-TIMESTAMP: Unix timestamp in milliseconds
      - KALSHI-ACCESS-SIGNATURE: base64(RSA-PSS-sign(timestamp + method + path))
    The path used for signing excludes query parameters.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger("sibyl.clients.kalshi")

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


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


def _load_private_key(path_or_pem: str) -> rsa.RSAPrivateKey:
    """Load RSA private key from a PEM file or from embedded PEM text."""
    path = Path(path_or_pem)
    if path.exists():
        # Read from file — extract PEM block
        text = path.read_text(encoding="utf-8")
        # Extract just the PEM portion
        start = text.find("-----BEGIN RSA PRIVATE KEY-----")
        end = text.find("-----END RSA PRIVATE KEY-----")
        if start == -1 or end == -1:
            raise ValueError(f"No RSA private key found in {path}")
        pem_text = text[start : end + len("-----END RSA PRIVATE KEY-----")]
        pem_bytes = pem_text.encode("utf-8")
    else:
        pem_bytes = path_or_pem.encode("utf-8")

    key = serialization.load_pem_private_key(pem_bytes, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("Key is not an RSA private key")
    return key


def _sign_request(
    private_key: rsa.RSAPrivateKey,
    timestamp_ms: int,
    method: str,
    path: str,
) -> str:
    """Create RSA-PSS signature for a Kalshi API request.

    The message is: str(timestamp_ms) + METHOD + path  (no query params).
    """
    message = f"{timestamp_ms}{method.upper()}{path}"
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


class KalshiClient:
    """Async client for the Kalshi Trading API v2.

    Supports both public data endpoints and authenticated operations.
    Authentication is optional — pass key_id + private_key_path for
    signed requests.
    """

    def __init__(
        self,
        key_id: str | None = None,
        private_key_path: str | None = None,
        base_url: str = BASE_URL,
        rate_limit: float = 10.0,
        timeout: float = 15.0,
    ) -> None:
        self._key_id = key_id
        self._private_key: rsa.RSAPrivateKey | None = None
        if private_key_path:
            self._private_key = _load_private_key(private_key_path)
            logger.info("Kalshi RSA key loaded from %s", private_key_path)
        self._base_url = base_url.rstrip("/")
        self._rate = RateLimiter(rate_limit)
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def is_authenticated(self) -> bool:
        return self._key_id is not None and self._private_key is not None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        """Generate authentication headers for a signed request."""
        if not self.is_authenticated:
            return {}
        ts_ms = int(time.time() * 1000)
        sig = _sign_request(self._private_key, ts_ms, method, path)  # type: ignore[arg-type]
        return {
            "KALSHI-ACCESS-KEY": self._key_id,  # type: ignore[dict-item]
            "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
            "KALSHI-ACCESS-SIGNATURE": sig,
        }

    # ── Low-level requests ────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
        auth: bool = False,
    ) -> Any:
        """Rate-limited HTTP request with retry on transient errors."""
        await self._rate.acquire()
        client = await self._ensure_client()
        url = f"{self._base_url}{path}"
        headers = self._auth_headers(method, path) if auth else {}

        for attempt in range(3):
            try:
                resp = await client.request(
                    method, url, params=params, json=json_body, headers=headers,
                )
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2))
                    logger.warning("Kalshi 429 — backing off %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    # Re-sign on retry (timestamp changes)
                    headers = self._auth_headers(method, path) if auth else {}
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempt == 2:
                    raise
                logger.warning("Kalshi request failed (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(1.5 * (attempt + 1))
        return None

    async def _get(self, path: str, params: dict | None = None, auth: bool = False) -> Any:
        return await self._request("GET", path, params=params, auth=auth)

    async def _post(self, path: str, json_body: dict | None = None, auth: bool = True) -> Any:
        return await self._request("POST", path, json_body=json_body, auth=auth)

    # ── Events ────────────────────────────────────────────────────────

    async def get_events(
        self,
        limit: int = 100,
        cursor: str | None = None,
        status: str | None = None,
        with_nested_markets: bool = True,
    ) -> dict:
        """Fetch paginated event listing.

        Returns {"events": [...], "cursor": "..."}.
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        if with_nested_markets:
            params["with_nested_markets"] = "true"
        data = await self._get("/events", params)
        if isinstance(data, dict):
            return data
        return {"events": [], "cursor": None}

    async def get_event(self, event_ticker: str) -> dict | None:
        """Fetch a single event by ticker."""
        data = await self._get(f"/events/{event_ticker}")
        if isinstance(data, dict):
            return data.get("event", data)
        return None

    # ── Markets ───────────────────────────────────────────────────────

    async def get_markets(
        self,
        limit: int = 100,
        cursor: str | None = None,
        event_ticker: str | None = None,
        status: str | None = None,
    ) -> dict:
        """Fetch paginated market listing.

        Returns {"markets": [...], "cursor": "..."}.
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if event_ticker:
            params["event_ticker"] = event_ticker
        if status:
            params["status"] = status
        data = await self._get("/markets", params)
        if isinstance(data, dict):
            return data
        return {"markets": [], "cursor": None}

    async def get_market(self, ticker: str) -> dict | None:
        """Fetch a single market by ticker."""
        data = await self._get(f"/markets/{ticker}")
        if isinstance(data, dict):
            return data.get("market", data)
        return None

    # ── Order Book ────────────────────────────────────────────────────

    async def get_orderbook(self, ticker: str, depth: int | None = None) -> dict | None:
        """Fetch the L2 order book for a market.

        Kalshi's orderbook returns only bids for both YES and NO sides:
          - yes: [{price, quantity}]  (bids to buy YES)
          - no:  [{price, quantity}]  (bids to buy NO = asks to sell YES)

        This method normalizes to standard bids/asks format:
          bids = YES bids
          asks = derived from NO bids (ask_price = 100 - no_bid_price)
        """
        params: dict[str, Any] = {}
        if depth:
            params["depth"] = depth
        data = await self._get(f"/markets/{ticker}/orderbook", params)
        if not isinstance(data, dict):
            return None

        orderbook = data.get("orderbook", data)

        # Normalize Kalshi format → standard bids/asks
        bids = []
        for level in orderbook.get("yes", []):
            try:
                bids.append({
                    "price": float(level[0]) / 100.0,  # Kalshi uses cents
                    "size": float(level[1]) / 100.0,
                })
            except (TypeError, ValueError, IndexError):
                continue

        asks = []
        for level in orderbook.get("no", []):
            try:
                no_price = float(level[0]) / 100.0
                asks.append({
                    "price": 1.0 - no_price,  # Convert NO bid → YES ask
                    "size": float(level[1]) / 100.0,
                })
            except (TypeError, ValueError, IndexError):
                continue
        # Sort asks ascending by price
        asks.sort(key=lambda x: x["price"])

        return {"bids": bids, "asks": asks, "raw": orderbook}

    # ── Trades ────────────────────────────────────────────────────────

    async def get_trades(
        self,
        ticker: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict:
        """Fetch recent trades.

        Returns {"trades": [...], "cursor": "..."}.
        """
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        data = await self._get("/markets/trades", params)
        if isinstance(data, dict):
            return data
        return {"trades": [], "cursor": None}

    # ── Authenticated: Portfolio ──────────────────────────────────────

    async def get_balance(self) -> float | None:
        """Get account balance (requires auth)."""
        if not self.is_authenticated:
            logger.warning("Cannot get balance — not authenticated")
            return None
        data = await self._get("/portfolio/balance", auth=True)
        if isinstance(data, dict):
            return float(data.get("balance", 0)) / 100.0  # Kalshi uses cents
        return None

    async def get_positions(
        self,
        limit: int = 100,
        cursor: str | None = None,
        settlement_status: str | None = None,
    ) -> dict:
        """Get current portfolio positions (requires auth)."""
        if not self.is_authenticated:
            logger.warning("Cannot get positions — not authenticated")
            return {"market_positions": [], "cursor": None}
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if settlement_status:
            params["settlement_status"] = settlement_status
        data = await self._get("/portfolio/positions", params, auth=True)
        if isinstance(data, dict):
            return data
        return {"market_positions": [], "cursor": None}
