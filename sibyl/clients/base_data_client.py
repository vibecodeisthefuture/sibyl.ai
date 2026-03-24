"""
Base Data Client — shared async HTTP client infrastructure for all data source clients.

All category-specific data clients inherit from this base class to get:
- Managed httpx.AsyncClient lifecycle (init/close)
- Standard rate limiting via token bucket
- Automatic retry with exponential backoff on 429/5xx
- Consistent logging and error handling
- Environment variable loading for API keys
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("sibyl.clients.base")


class BaseDataClient:
    """Async HTTP client with rate limiting, retries, and lifecycle management.

    Subclasses must set:
        - self._base_url: str
        - self._name: str (for logging)

    Subclasses may override:
        - _build_headers() → dict: default request headers
        - _build_params() → dict: default query parameters (e.g., API key)
    """

    def __init__(self, name: str, base_url: str, requests_per_second: float = 2.0) -> None:
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._http: httpx.AsyncClient | None = None
        self._initialized = False

        # Rate limiting (token bucket)
        self._rps = requests_per_second
        self._last_request_time: float = 0.0
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0

        # Retry config
        self._max_retries = 3
        self._retry_base_delay = 1.0  # seconds

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def name(self) -> str:
        return self._name

    def _get_env(self, key: str, default: str = "") -> str:
        """Load an environment variable."""
        return os.environ.get(key, default)

    def _build_headers(self) -> dict[str, str]:
        """Override to add default headers (e.g., Authorization)."""
        return {"Accept": "application/json"}

    def _build_params(self) -> dict[str, str]:
        """Override to add default query params (e.g., api_key=...)."""
        return {}

    def initialize(self) -> bool:
        """Initialize the HTTP client. Returns True if ready."""
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._build_headers(),
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )
        self._initialized = True
        logger.info("%s client initialized (base_url=%s)", self._name, self._base_url)
        return True

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http:
            await self._http.aclose()
            self._http = None
            self._initialized = False

    async def _throttle(self) -> None:
        """Enforce rate limit between requests."""
        if self._min_interval <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        """Make an HTTP request with rate limiting and retries.

        Args:
            method:    HTTP method (GET, POST).
            path:      URL path (appended to base_url).
            params:    Query parameters (merged with defaults).
            json_body: JSON request body (for POST).
            headers:   Additional headers (merged with defaults).

        Returns:
            Parsed JSON response, or None on failure.
        """
        if not self._http:
            logger.error("%s: not initialized", self._name)
            return None

        # Merge default params
        merged_params = {**self._build_params(), **(params or {})}
        merged_headers = {**(headers or {})}

        for attempt in range(self._max_retries):
            await self._throttle()
            try:
                resp = await self._http.request(
                    method,
                    path,
                    params=merged_params,
                    json=json_body,
                    headers=merged_headers,
                )

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code == 429:
                    delay = self._retry_base_delay * (2 ** attempt)
                    logger.warning(
                        "%s: rate limited (429), retrying in %.1fs (attempt %d/%d)",
                        self._name, delay, attempt + 1, self._max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code >= 500:
                    delay = self._retry_base_delay * (2 ** attempt)
                    logger.warning(
                        "%s: server error %d, retrying in %.1fs (attempt %d/%d)",
                        self._name, resp.status_code, delay, attempt + 1, self._max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                # Client error (4xx) — don't retry
                logger.error(
                    "%s: HTTP %d for %s %s: %s",
                    self._name, resp.status_code, method, path, resp.text[:200],
                )
                return None

            except httpx.TimeoutException:
                delay = self._retry_base_delay * (2 ** attempt)
                logger.warning(
                    "%s: timeout on %s %s, retrying in %.1fs",
                    self._name, method, path, delay,
                )
                await asyncio.sleep(delay)
            except httpx.HTTPError as e:
                logger.error("%s: HTTP error on %s %s: %s", self._name, method, path, e)
                return None

        logger.error("%s: all %d retries exhausted for %s %s", self._name, self._max_retries, method, path)
        return None

    async def get(self, path: str, params: dict[str, Any] | None = None, **kwargs) -> dict[str, Any] | list[Any] | None:
        """Convenience GET request."""
        return await self._request("GET", path, params=params, **kwargs)

    async def post(self, path: str, json_body: dict[str, Any] | None = None, **kwargs) -> dict[str, Any] | list[Any] | None:
        """Convenience POST request."""
        return await self._request("POST", path, json_body=json_body, **kwargs)

    async def health_check(self) -> dict[str, Any]:
        """Override in subclasses to verify API connectivity.

        Returns:
            {"ok": bool, "service": str, "detail": str}
        """
        return {"ok": False, "service": self._name, "detail": "health_check not implemented"}
