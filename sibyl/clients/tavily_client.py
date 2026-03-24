"""
Tavily API Client — web search for prediction market research.

PURPOSE:
    Thin async wrapper around the Tavily Python SDK that the Breakout Scout
    uses as an optional parallel web search source alongside Perplexity Sonar.
    Returns structured {title, url, content} results that merge directly into
    the existing research pipeline.

PRICING (as of 2026-03):
    - 1,000 free API credits/month (no credit card required)
    - Basic search: 1 credit per call
    - Advanced search: 2 credits per call

AUTHENTICATION:
    TAVILY_API_KEY env var.  When absent, the client silently disables itself.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("sibyl.clients.tavily")


class TavilyClient:
    """Async Tavily search client for market research queries.

    Usage:
        client = TavilyClient()
        if client.initialize():
            results = await client.search_market(
                "Will the Fed cut rates in June 2026?",
                max_results=5,
                search_depth="basic",
            )
            print(results)  # [{"title": ..., "url": ..., "content": ...}, ...]
        await client.close()
    """

    def __init__(self) -> None:
        self._client: Any | None = None  # AsyncTavilyClient instance
        self._max_results: int = 5
        self._search_depth: str = "basic"

    def initialize(
        self,
        max_results: int = 5,
        search_depth: str = "basic",
    ) -> bool:
        """Load API key and create the async Tavily client.

        Returns:
            True if TAVILY_API_KEY is present and the client is ready.
        """
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.info("TAVILY_API_KEY not set — Tavily client disabled")
            return False

        try:
            from tavily import AsyncTavilyClient as _AsyncTavilyClient

            self._client = _AsyncTavilyClient(api_key=api_key)
            self._max_results = max_results
            self._search_depth = search_depth
            logger.info(
                "Tavily client initialized (max_results=%d, search_depth=%s)",
                self._max_results,
                self._search_depth,
            )
            return True
        except ImportError:
            logger.warning(
                "tavily-python not installed — run: pip install 'sibyl[tavily]'"
            )
            return False
        except Exception:
            logger.exception("Failed to initialize Tavily client")
            return False

    @property
    def available(self) -> bool:
        return self._client is not None

    async def close(self) -> None:
        """Clean up resources (AsyncTavilyClient has no explicit close)."""
        self._client = None

    async def search_market(
        self,
        market_title: str,
        max_results: int | None = None,
        search_depth: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search the web for information about a prediction market.

        Args:
            market_title:  The prediction market question/title.
            max_results:   Override default max_results.
            search_depth:  Override default search_depth ("basic" or "advanced").

        Returns:
            List of dicts with "title", "url", "content" keys,
            or an empty list on failure.
        """
        if not self._client:
            return []

        query = f"prediction market: {market_title}"
        if len(query) > 400:
            query = query[:400]

        try:
            response = await self._client.search(
                query=query,
                max_results=max_results or self._max_results,
                search_depth=search_depth or self._search_depth,
                topic="general",
            )

            results = []
            for item in response.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", ""),
                    "score": item.get("score", 0.0),
                })

            logger.debug(
                "Tavily returned %d results for '%s'",
                len(results),
                market_title[:50],
            )
            return results

        except Exception:
            logger.exception("Tavily search failed for: %s", market_title[:50])
            return []
