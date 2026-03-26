"""
Async HTTP client for the Kalshi Trading API v2.

Kalshi is the PRIMARY execution platform for Sibyl.  This client handles
both public data endpoints (market listings, prices) and authenticated
trading operations (balance, positions, order placement).

KALSHI API OVERVIEW:
    Base URL: https://api.elections.kalshi.com/trade-api/v2
    Docs:     https://trading-api.readme.io/reference

    Kalshi organizes markets into "events":
      - EVENT: A real-world question (e.g., "Fed March 2026 Rate Decision")
      - MARKET: A specific outcome within an event (e.g., "25bps cut", "50bps cut")
      - TICKER: Each market has a unique ticker string (e.g., "FED-RATE-MAR-25BP")

AUTHENTICATION (RSA-PSS):
    Kalshi uses RSA-PSS (SHA-256) signatures for API authentication.
    This is more secure than simple API keys — each request is individually signed.

    How it works:
        1. You generate an RSA key pair (Kalshi provides a key ID).
        2. For each request, you create a "message" string:
           message = str(timestamp_ms) + HTTP_METHOD + path
        3. You sign this message with your private key using RSA-PSS.
        4. You send three headers with the request:
           - KALSHI-ACCESS-KEY: Your key ID
           - KALSHI-ACCESS-TIMESTAMP: Unix timestamp in milliseconds
           - KALSHI-ACCESS-SIGNATURE: Base64-encoded RSA-PSS signature

    If no key is provided, the client runs in public-only mode (no auth headers).

ORDERBOOK NORMALIZATION:
    Kalshi's orderbook format is unique — it only shows BIDS for both YES and NO:
      - YES bids: [price_cents, quantity] — someone wants to BUY YES at that price
      - NO bids:  [price_cents, quantity] — someone wants to BUY NO at that price

    Since buying NO at X¢ = selling YES at (100-X)¢, this client converts:
      - YES bids → standard "bids" array
      - NO bids  → standard "asks" array (price = 1.0 - no_price)

    All prices are also converted from cents (0–100) to decimals (0.0–1.0).

RATE LIMITING & RETRIES:
    Same pattern as Polymarket: token-bucket rate limiter + 3 retries.

USAGE:
    # Public-only mode (no auth):
    client = KalshiClient()
    events = await client.get_events(status="open")
    book = await client.get_orderbook("FED-RATE-MAR-25BP")

    # Authenticated mode:
    client = KalshiClient(key_id="abc123", private_key_path="/path/to/key.pem")
    balance = await client.get_balance()  # Returns dollars as float
    positions = await client.get_positions()
    await client.close()
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

# Default Kalshi API base URL (v2 — current as of 2026)
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Kalshi API rate limit tiers
KALSHI_BASIC_TIER = {"read_per_second": 20.0, "write_per_second": 10.0}
KALSHI_ADVANCED_TIER = {"read_per_second": 30.0, "write_per_second": 30.0}


class TieredRateLimiter:
    """Tiered token-bucket rate limiter for Kalshi API access tiers.

    Kalshi API Tiers:
        Basic:    20 read/s, 10 write/s
        Advanced: 30 read/s, 30 write/s
    """

    def __init__(self, read_per_second: float = 20.0, write_per_second: float = 10.0) -> None:
        self._read_interval = 1.0 / read_per_second
        self._write_interval = 1.0 / write_per_second
        self._read_last: float = 0.0
        self._write_last: float = 0.0
        self._read_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def acquire_read(self) -> None:
        """Wait until it's safe to make the next read (GET) request."""
        async with self._read_lock:
            now = time.monotonic()
            wait = self._read_interval - (now - self._read_last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._read_last = time.monotonic()

    async def acquire_write(self) -> None:
        """Wait until it's safe to make the next write (POST/PUT/DELETE) request."""
        async with self._write_lock:
            now = time.monotonic()
            wait = self._write_interval - (now - self._write_last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._write_last = time.monotonic()


class _LegacyRateLimiter:
    """Simple token-bucket rate limiter (legacy).  Same implementation as Polymarket.

    See polymarket_client.py for detailed explanation.
    Kept for backward compatibility with code that may pass rate_limit= kwarg.
    """

    def __init__(self, max_per_second: float = 10.0) -> None:
        self._interval = 1.0 / max_per_second
        self._last: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until it's safe to make the next request."""
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


def _load_private_key(path_or_pem: str) -> rsa.RSAPrivateKey:
    """Load RSA private key from a PEM file or from embedded PEM text.

    Kalshi provides a private key file when you create an API key.
    The file may contain extra text (instructions, comments) — this function
    extracts just the PEM block between the BEGIN/END markers.

    Args:
        path_or_pem: Either a file path or a PEM-encoded string.

    Returns:
        RSA private key object for signing requests.

    Raises:
        ValueError: If no RSA private key block is found in the file.
        TypeError:  If the key is not RSA.
    """
    path = Path(path_or_pem)
    if path.exists():
        # Read from file — Kalshi key files often contain extra text,
        # so we extract just the PEM block between BEGIN/END markers.
        text = path.read_text(encoding="utf-8")
        start = text.find("-----BEGIN RSA PRIVATE KEY-----")
        end = text.find("-----END RSA PRIVATE KEY-----")
        if start == -1 or end == -1:
            raise ValueError(f"No RSA private key found in {path}")
        pem_text = text[start : end + len("-----END RSA PRIVATE KEY-----")]
        pem_bytes = pem_text.encode("utf-8")
    else:
        # Treat the input as inline PEM text
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

    The signed message is: str(timestamp_ms) + METHOD + path
    Example: "1679000000000GET/markets/FED-RATE-MAR-25BP"

    IMPORTANT: The path used for signing EXCLUDES query parameters.
    So if the URL is /markets?status=open, only "/markets" is signed.

    Args:
        private_key:  RSA private key object.
        timestamp_ms: Current Unix timestamp in milliseconds.
        method:       HTTP method (GET, POST, etc.) — uppercased.
        path:         URL path without query string.

    Returns:
        Base64-encoded signature string.
    """
    message = f"{timestamp_ms}{method.upper()}{path}"
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            # IMPORTANT: Kalshi requires DIGEST_LENGTH (32 bytes for SHA-256),
            # NOT MAX_LENGTH.  MAX_LENGTH produces a different signature that
            # Kalshi rejects with INCORRECT_API_KEY_SIGNATURE.
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


class KalshiClient:
    """Async client for the Kalshi Trading API v2.

    Supports both public data endpoints and authenticated operations.
    Authentication is OPTIONAL — if no key_id/private_key_path is provided,
    the client runs in public-only mode (data fetching only, no trading).

    Key concepts:
        - "event_ticker": ID for an event grouping (e.g., "ECON-FED-RATE")
        - "ticker": ID for a specific market within an event
        - Prices are in CENTS (0–100) in the API, converted to decimals (0.0–1.0)
    """

    def __init__(
        self,
        key_id: str | None = None,
        private_key_path: str | None = None,
        base_url: str = BASE_URL,
        tier: str = "basic",
        rate_limit: float | None = None,
        timeout: float = 15.0,
    ) -> None:
        """Initialize the Kalshi client.

        Args:
            key_id:           Your Kalshi API key ID (from Kalshi dashboard).
            private_key_path: Path to the RSA private key file (PEM format).
            base_url:         API base URL (default: Kalshi v2 production).
            tier:             API tier ("basic" or "advanced", default: "basic").
            rate_limit:       (Deprecated) Legacy parameter for backward compatibility.
                             If provided, creates a _LegacyRateLimiter with this limit.
                             Otherwise, uses tier presets with TieredRateLimiter.
            timeout:          HTTP request timeout in seconds (default: 15).
        """
        self._key_id = key_id
        self._private_key: rsa.RSAPrivateKey | None = None

        # Load private key if path is provided
        if private_key_path:
            self._private_key = _load_private_key(private_key_path)
            logger.info("Kalshi RSA key loaded from %s", private_key_path)

        self._base_url = base_url.rstrip("/")  # Remove trailing slash

        # Initialize rate limiter based on tier or legacy rate_limit parameter
        if rate_limit is not None:
            # Backward compatibility: use legacy rate limiter
            logger.info("Using legacy rate limiter with max_per_second=%.1f", rate_limit)
            self._rate = _LegacyRateLimiter(rate_limit)
        else:
            # Use tiered rate limiter with presets
            tier_lower = tier.lower()
            if tier_lower == "basic":
                limits = KALSHI_BASIC_TIER
            elif tier_lower == "advanced":
                limits = KALSHI_ADVANCED_TIER
            else:
                raise ValueError(f"Invalid tier: {tier}. Must be 'basic' or 'advanced'.")
            logger.info(
                "Kalshi client initialized with %s tier: %d read/s, %d write/s",
                tier_lower,
                int(limits["read_per_second"]),
                int(limits["write_per_second"]),
            )
            self._rate = TieredRateLimiter(**limits)

        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None  # Lazily created

    @property
    def is_authenticated(self) -> bool:
        """True if this client has credentials for authenticated requests."""
        return self._key_id is not None and self._private_key is not None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create the HTTP client on first use."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client.  Call this when shutting down."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        """Generate the three authentication headers for a signed request.

        If the client is not authenticated, returns an empty dict (no headers).

        IMPORTANT: The path signed MUST be the FULL path including the API prefix
        (e.g., /trade-api/v2/portfolio/balance), NOT just the relative path
        (/portfolio/balance).  Kalshi's server reconstructs the full path from
        the request and verifies the signature against it.

        The headers are:
          KALSHI-ACCESS-KEY:       Your API key ID
          KALSHI-ACCESS-TIMESTAMP: Current Unix timestamp (milliseconds)
          KALSHI-ACCESS-SIGNATURE: RSA-PSS signature of (timestamp + method + full_path)
        """
        if not self.is_authenticated:
            return {}

        # Build the FULL path for signing.  If path is relative (/events),
        # prepend the API prefix extracted from the base_url.
        # Example: base_url = "https://api.elections.kalshi.com/trade-api/v2"
        #          path = "/events"
        #          sign_path = "/trade-api/v2/events"
        from urllib.parse import urlparse
        parsed = urlparse(self._base_url)
        api_prefix = parsed.path.rstrip("/")  # e.g., "/trade-api/v2"
        sign_path = f"{api_prefix}{path}" if not path.startswith(api_prefix) else path

        ts_ms = int(time.time() * 1000)
        sig = _sign_request(self._private_key, ts_ms, method, sign_path)  # type: ignore[arg-type]
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
        """Rate-limited HTTP request with retry on transient errors.

        Same retry pattern as Polymarket (3 attempts, exponential backoff).
        On 429 responses, re-signs the request (timestamp changes each retry).

        Rate limiting is tiered by HTTP method:
        - GET requests use read rate limit
        - POST/PUT/DELETE requests use write rate limit
        """
        # Apply appropriate rate limit tier based on HTTP method
        if isinstance(self._rate, TieredRateLimiter):
            if method.upper() == "GET":
                await self._rate.acquire_read()
            else:
                await self._rate.acquire_write()
        else:
            # Legacy rate limiter (backward compatibility)
            await self._rate.acquire()  # type: ignore[attr-defined]

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
                    # Re-sign on retry (timestamp has changed)
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
        """Convenience wrapper for GET requests."""
        return await self._request("GET", path, params=params, auth=auth)

    async def _post(self, path: str, json_body: dict | None = None, auth: bool = True) -> Any:
        """Convenience wrapper for POST requests (authenticated by default)."""
        return await self._request("POST", path, json_body=json_body, auth=auth)

    # ── Events ────────────────────────────────────────────────────────
    # Events group related markets together.  For example, "Fed March Rate
    # Decision" is an event containing markets like "25bps cut?" and "50bps cut?".

    async def get_events(
        self,
        limit: int = 100,
        cursor: str | None = None,
        status: str | None = None,
        with_nested_markets: bool = True,
        series_ticker: str | None = None,
        category: str | None = None,
        min_close_ts: int | None = None,
    ) -> dict:
        """Fetch paginated event listing.

        Args:
            limit:                Max events to return (max 200 per Kalshi API).
            cursor:               Pagination cursor from a previous response.
            status:               Filter by status ("unopened", "open", "closed", "settled").
            with_nested_markets:  If True, include nested market data in response.
            series_ticker:        Filter by series ticker (e.g., "KXHIGHCHI").
            category:             Filter by Kalshi category (e.g., "Climate and Weather").
            min_close_ts:         Unix timestamp — only return events with at least one
                                  market closing after this time.  Filters out stale/expired
                                  events at the API level, reducing payload size.

        Returns:
            Dict with "events" (list) and "cursor" (string or None).
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        if with_nested_markets:
            params["with_nested_markets"] = "true"
        if series_ticker:
            params["series_ticker"] = series_ticker
        if category:
            params["category"] = category
        if min_close_ts is not None:
            params["min_close_ts"] = min_close_ts
        data = await self._get("/events", params)
        if isinstance(data, dict):
            return data
        return {"events": [], "cursor": None}

    async def get_event(self, event_ticker: str) -> dict | None:
        """Fetch a single event by its ticker string.

        Args:
            event_ticker: Event identifier (e.g., "ECON-FED-RATE-MAR").

        Returns:
            Event dict with title, category, nested markets, etc.
        """
        data = await self._get(f"/events/{event_ticker}")
        if isinstance(data, dict):
            return data.get("event", data)
        return None

    # ── Markets ───────────────────────────────────────────────────────
    # Individual prediction markets within events.

    async def get_markets(
        self,
        limit: int = 100,
        cursor: str | None = None,
        event_ticker: str | None = None,
        series_ticker: str | None = None,
        status: str | None = None,
        min_close_ts: int | None = None,
        max_close_ts: int | None = None,
        tickers: str | None = None,
    ) -> dict:
        """Fetch paginated market listing.

        Args:
            limit:           Max markets to return (max 1000 per Kalshi API).
            cursor:          Pagination cursor.
            event_ticker:    Filter by parent event.
            series_ticker:   Filter by series ticker (e.g., "KXBTC").
            status:          Filter by status ("unopened", "open", "paused",
                             "closed", "settled").
            min_close_ts:    Unix timestamp — only return markets closing after
                             this time.  Core filter for excluding expired markets.
            max_close_ts:    Unix timestamp — only return markets closing before
                             this time.  Limits how far into the future to look.
            tickers:         Comma-separated list of specific market tickers to
                             retrieve (e.g., "KXBTC-26MAR2717-B82650,KXBTC-...").

        Returns:
            Dict with "markets" (list) and "cursor" (string or None).
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        if status:
            params["status"] = status
        if min_close_ts is not None:
            params["min_close_ts"] = min_close_ts
        if max_close_ts is not None:
            params["max_close_ts"] = max_close_ts
        if tickers:
            params["tickers"] = tickers
        data = await self._get("/markets", params)
        if isinstance(data, dict):
            return data
        return {"markets": [], "cursor": None}

    async def get_market(self, ticker: str) -> dict | None:
        """Fetch a single market by its ticker.

        Args:
            ticker: Market identifier (e.g., "FED-RATE-MAR-25BP").

        Returns:
            Market dict with title, status, yes_ask, volume, open_interest, etc.
        """
        data = await self._get(f"/markets/{ticker}")
        if isinstance(data, dict):
            return data.get("market", data)
        return None

    # ── Order Book ────────────────────────────────────────────────────

    async def get_orderbook(self, ticker: str, depth: int | None = None) -> dict | None:
        """Fetch and NORMALIZE the L2 order book for a market.

        KALSHI'S UNIQUE FORMAT:
            Kalshi only shows BIDS for both YES and NO outcomes:
              yes: [[price_cents, qty_cents], ...]  → people wanting to BUY YES
              no:  [[price_cents, qty_cents], ...]  → people wanting to BUY NO

        THIS METHOD NORMALIZES TO STANDARD FORMAT:
            bids: [{price: 0.60, size: 1.00}, ...]  ← YES bids (direct mapping)
            asks: [{price: 0.65, size: 1.50}, ...]  ← Derived from NO bids
                  (because buying NO at 35¢ = selling YES at 65¢)

        All prices converted from cents (0–100) to decimal (0.0–1.0).

        Args:
            ticker: Market ticker string.
            depth:  Optional max price levels to return.

        Returns:
            Dict with "bids", "asks", and "raw" (original Kalshi data).
        """
        params: dict[str, Any] = {}
        if depth:
            params["depth"] = depth
        data = await self._get(f"/markets/{ticker}/orderbook", params)
        if not isinstance(data, dict):
            return None

        orderbook = data.get("orderbook", data)

        # Convert YES bids → standard bids
        bids = []
        for level in orderbook.get("yes", []):
            try:
                bids.append({
                    "price": float(level[0]) / 100.0,  # Cents → decimal
                    "size": float(level[1]) / 100.0,
                })
            except (TypeError, ValueError, IndexError):
                continue

        # Convert NO bids → standard asks (ask = 1.0 - no_bid_price)
        asks = []
        for level in orderbook.get("no", []):
            try:
                no_price = float(level[0]) / 100.0
                asks.append({
                    "price": 1.0 - no_price,  # NO bid at 35¢ → YES ask at 65¢
                    "size": float(level[1]) / 100.0,
                })
            except (TypeError, ValueError, IndexError):
                continue
        # Sort asks ascending by price (lowest ask first, like a real order book)
        asks.sort(key=lambda x: x["price"])

        return {"bids": bids, "asks": asks, "raw": orderbook}

    # ── Trades ────────────────────────────────────────────────────────

    async def get_trades(
        self,
        ticker: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict:
        """Fetch recent trades for a market.

        Args:
            ticker: Optional market ticker to filter by.
            limit:  Max trades to return (default: 100).
            cursor: Pagination cursor.

        Returns:
            Dict with "trades" (list of trade objects) and "cursor".
            Each trade has: taker_side, count, yes_price/no_price.
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

    # ── Authenticated: Portfolio (requires API key) ───────────────────
    # These endpoints require RSA-PSS authentication.
    # If the client is not authenticated, they return safe defaults.

    async def get_balance(self) -> float | None:
        """Get account balance in dollars (requires authentication).

        Returns:
            Account balance as a float (e.g., 500.00), or None if not authenticated.
            Kalshi returns balance in cents; this method converts to dollars.
        """
        if not self.is_authenticated:
            logger.warning("Cannot get balance — not authenticated")
            return None
        data = await self._get("/portfolio/balance", auth=True)
        if isinstance(data, dict):
            return float(data.get("balance", 0)) / 100.0  # Cents → dollars
        return None

    async def get_positions(
        self,
        limit: int = 100,
        cursor: str | None = None,
        settlement_status: str | None = None,
    ) -> dict:
        """Get current portfolio positions (requires authentication).

        Args:
            limit:             Max positions to return.
            cursor:            Pagination cursor.
            settlement_status: Filter (e.g., "unsettled", "settled").

        Returns:
            Dict with "market_positions" (list) and "cursor".
            Returns empty list if not authenticated.
        """
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

    # ── Authenticated: Order Placement (requires API key) ─────────────
    # These methods actually PLACE TRADES on Kalshi.  Only called in live mode.

    async def place_order(
        self,
        ticker: str,
        side: str,
        size: int,
        price_cents: int,
        order_type: str = "limit",
    ) -> dict | None:
        """Place an order on Kalshi (requires authentication).

        IMPORTANT: This spends real money in live mode!
        In paper mode, the Order Executor simulates fills without calling this.

        Args:
            ticker:      Market ticker (e.g., "FED-RATE-MAR-25BP").
            side:        "yes" or "no" — which outcome to buy.
            size:        Number of contracts to buy (integer, min 1).
            price_cents: Limit price in cents (1-99). E.g., 65 = $0.65.
            order_type:  "limit" (default) or "market".

        Returns:
            Order response dict with "order_id", "status", etc.
            Returns None if not authenticated.

        Example:
            # Buy 10 YES contracts at $0.55
            result = await client.place_order(
                ticker="FED-RATE-MAR-25BP",
                side="yes",
                size=10,
                price_cents=55,
            )
            print(result["order"]["order_id"])
        """
        if not self.is_authenticated:
            logger.warning("Cannot place order — not authenticated")
            return None

        body = {
            "ticker": ticker,
            "action": "buy",
            "side": side.lower(),
            "count": size,
            "type": order_type,
        }
        # Only include price for limit orders (market orders fill at best available)
        if order_type == "limit":
            body["yes_price"] = price_cents if side.lower() == "yes" else None
            body["no_price"] = price_cents if side.lower() == "no" else None

        data = await self._post("/portfolio/orders", json_body=body)
        if isinstance(data, dict):
            logger.info(
                "Order placed: %s %s %d@%d¢ on %s",
                side, order_type, size, price_cents, ticker,
            )
            return data
        return None

    async def sell_position(
        self,
        ticker: str,
        side: str,
        size: int,
        price_cents: int | None = None,
        order_type: str = "market",
    ) -> dict | None:
        """Sell (close) an existing position on Kalshi (requires authentication).

        Sprint 20.5: Critical fix — PositionLifecycleManager now calls this
        when stop-loss, exit optimizer, or resolution tracker triggers an exit.
        Without this, positions were only closed in the local DB but remained
        open on the exchange.

        On Kalshi, selling is done by placing an order with action="sell".
        To exit a YES position, sell YES. To exit a NO position, sell NO.

        Args:
            ticker:      Market ticker (e.g., "KXBTCD-26MAR24-T87500-B87999").
            side:        "yes" or "no" — which side to sell (must match held position).
            size:        Number of contracts to sell (integer, min 1).
            price_cents: Limit price in cents (1-99). None for market orders.
            order_type:  "market" (default for exits) or "limit".

        Returns:
            Order response dict with "order_id", "status", etc.
            Returns None if not authenticated or on error.
        """
        if not self.is_authenticated:
            logger.warning("Cannot sell position — not authenticated")
            return None

        body: dict[str, Any] = {
            "ticker": ticker,
            "action": "sell",
            "side": side.lower(),
            "count": size,
            "type": order_type,
        }
        if order_type == "limit" and price_cents is not None:
            body["yes_price"] = price_cents if side.lower() == "yes" else None
            body["no_price"] = price_cents if side.lower() == "no" else None

        data = await self._post("/portfolio/orders", json_body=body)
        if isinstance(data, dict):
            logger.info(
                "Position sold: %s %s %d contracts on %s",
                side, order_type, size, ticker,
            )
            return data
        return None

    async def get_order(self, order_id: str) -> dict | None:
        """Get order status by ID (requires authentication).

        Sprint 20.5: Used for fill confirmation after placing orders.

        Args:
            order_id: The Kalshi order ID to query.

        Returns:
            Order dict with status, filled count, etc. None if not authenticated.
        """
        if not self.is_authenticated:
            logger.warning("Cannot get order — not authenticated")
            return None

        data = await self._get(f"/portfolio/orders/{order_id}", auth=True)
        if isinstance(data, dict):
            return data
        return None

    async def cancel_order(self, order_id: str) -> dict | None:
        """Cancel a resting order on Kalshi (requires authentication).

        Args:
            order_id: The Kalshi order ID to cancel.

        Returns:
            Cancellation response dict, or None if not authenticated.
        """
        if not self.is_authenticated:
            logger.warning("Cannot cancel order — not authenticated")
            return None

        data = await self._request(
            "DELETE", f"/portfolio/orders/{order_id}", auth=True,
        )
        if isinstance(data, dict):
            logger.info("Order cancelled: %s", order_id)
            return data
        return None

