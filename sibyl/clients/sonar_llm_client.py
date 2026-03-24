"""
Sonar LLM Client — General-purpose synthesis and digest service for Sibyl.

PURPOSE:
    Standalone async wrapper around Perplexity's Sonar API for two use cases:

    1. synthesize_research() — Multi-source synthesis of research summaries
       (replaces Anthropic Claude Sonnet for BreakoutScout)
    2. generate_digest() — Portfolio snapshot summarization (replaces Claude Haiku
       for Narrator)

    Unlike the market-research-focused PerplexityClient, this client is optimized
    for structured synthesis (JSON output) and narrative generation without web
    grounding. Both methods disable return_citations to focus on synthesis logic
    rather than citation tracking.

PRICING (as of 2026-03):
    - Sonar:       $1/M input tokens, $1/M output tokens
    - No free tier. Pro subscribers get $5/mo API credit.
    - Tier 0: 50 RPM, pay-per-use.

USAGE PATTERNS:
    - synthesis: temperature=0.1, max_tokens=600 (factual, structured JSON)
    - digest: temperature=0.3, max_tokens=550 (slightly creative for narratives)
    - Both disable return_citations for cleaner, synthesis-focused output

AUTHENTICATION:
    PERPLEXITY_API_KEY env var. Never hardcoded or logged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("sibyl.clients.sonar_llm")

PERPLEXITY_CHAT_URL = "https://api.perplexity.ai/chat/completions"


class SonarLLMClient:
    """Async Sonar LLM client for synthesis and digest tasks.

    A standalone general-purpose client (not inheriting from PerplexityClient)
    that handles multi-source synthesis and portfolio digest generation.
    Optimized for structured output without web grounding.

    Usage:
        client = SonarLLMClient()
        if client.initialize():
            # Synthesis: structured JSON synthesis of research
            result = await client.synthesize_research(
                "Analyze sentiment across these market summaries: ..."
            )
            print(result)  # JSON string or None

            # Digest: narrative portfolio summary
            digest = await client.generate_digest(
                "Summarize this portfolio snapshot: ..."
            )
            print(digest)  # narrative text or None

        await client.close()
    """

    def __init__(
        self,
        model: str = "sonar",
        daily_call_cap: int = 100,
    ) -> None:
        """Initialize the Sonar LLM client.

        Args:
            model: Perplexity model to use. Defaults to "sonar" (cheapest).
            daily_call_cap: Max API calls per day. Defaults to 100 (higher than
                            research-only client since this handles more tasks).
        """
        self._api_key: str = ""
        self._http: httpx.AsyncClient | None = None
        self._model: str = model
        self._daily_call_cap: int = daily_call_cap
        self._calls_today: int = 0

    def initialize(self) -> bool:
        """Load API key and create HTTP client.

        Returns:
            True if API key is present and client is ready.
        """
        self._api_key = os.environ.get("PERPLEXITY_API_KEY", "")
        if not self._api_key:
            logger.info("PERPLEXITY_API_KEY not set — Sonar client disabled")
            return False

        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        logger.info(
            "Sonar LLM client initialized (model=%s, daily_cap=%d)",
            self._model,
            self._daily_call_cap,
        )
        return True

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http:
            await self._http.aclose()

    @property
    def available(self) -> bool:
        """True if client is initialized and ready."""
        return self._http is not None

    @property
    def calls_remaining_today(self) -> int:
        """Remaining API calls allowed today."""
        return max(self._daily_call_cap - self._calls_today, 0)

    def reset_daily_counter(self) -> None:
        """Reset daily call counter (called at UTC midnight)."""
        self._calls_today = 0

    async def synthesize_research(self, prompt: str) -> str | None:
        """Send a structured synthesis prompt to Sonar and return text response.

        Used by BreakoutScout for multi-source synthesis of research summaries.
        Low temperature (0.1) for factual, structured output. No web grounding.

        Args:
            prompt: The synthesis prompt (e.g., multi-source sentiment analysis).

        Returns:
            Text response from Sonar (typically JSON), or None if unavailable/error.
        """
        if not self._http:
            return None

        if self._calls_today >= self._daily_call_cap:
            logger.debug(
                "Daily Sonar call cap reached (%d)", self._daily_call_cap
            )
            return None

        # System prompt guides Sonar toward structured synthesis
        system_prompt = (
            "You are a research synthesis expert. Analyze the provided content "
            "and output structured, factual insights in JSON format. Be concise "
            "and focus on key patterns, themes, and sentiment indicators."
        )

        try:
            resp = await self._http.post(
                PERPLEXITY_CHAT_URL,
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 600,
                    "temperature": 0.1,  # Factual, structured output
                    "return_citations": False,  # No web grounding for synthesis
                },
            )

            self._calls_today += 1

            if resp.status_code == 401:
                logger.error("Sonar API key invalid (401)")
                return None
            if resp.status_code == 429:
                logger.warning("Sonar rate limited (429)")
                return None
            if resp.status_code != 200:
                logger.warning(
                    "Sonar returned %d: %s", resp.status_code, resp.text[:200]
                )
                return None

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return None

            content = choices[0].get("message", {}).get("content", "")

            logger.debug(
                "Sonar synthesis completed (call #%d today)",
                self._calls_today,
            )
            return content.strip()

        except httpx.TimeoutException:
            logger.warning("Sonar synthesis timeout")
            self._calls_today += 1
            return None
        except Exception:
            logger.exception("Sonar synthesis request failed")
            self._calls_today += 1
            return None

    async def generate_digest(self, prompt: str) -> str | None:
        """Send a portfolio snapshot prompt to Sonar and return concise digest.

        Used by Narrator for narrative portfolio summaries. Slightly higher
        temperature (0.3) for natural prose. No web grounding.

        Args:
            prompt: The portfolio snapshot prompt (e.g., portfolio performance
                   summary with metrics).

        Returns:
            Concise digest text from Sonar, or None if unavailable/error.
        """
        if not self._http:
            return None

        if self._calls_today >= self._daily_call_cap:
            logger.debug(
                "Daily Sonar call cap reached (%d)", self._daily_call_cap
            )
            return None

        # System prompt guides Sonar toward concise, engaging narrative
        system_prompt = (
            "You are a portfolio analyst and storyteller. Given a portfolio "
            "snapshot, write a concise, engaging narrative summary highlighting "
            "performance trends, key positions, and outlook. Be clear and "
            "accessible to investors."
        )

        try:
            resp = await self._http.post(
                PERPLEXITY_CHAT_URL,
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 550,
                    "temperature": 0.3,  # Slightly creative for natural prose
                    "return_citations": False,  # No web grounding for digest
                },
            )

            self._calls_today += 1

            if resp.status_code == 401:
                logger.error("Sonar API key invalid (401)")
                return None
            if resp.status_code == 429:
                logger.warning("Sonar rate limited (429)")
                return None
            if resp.status_code != 200:
                logger.warning(
                    "Sonar returned %d: %s", resp.status_code, resp.text[:200]
                )
                return None

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return None

            content = choices[0].get("message", {}).get("content", "")

            logger.debug(
                "Sonar digest generated (call #%d today)",
                self._calls_today,
            )
            return content.strip()

        except httpx.TimeoutException:
            logger.warning("Sonar digest generation timeout")
            self._calls_today += 1
            return None
        except Exception:
            logger.exception("Sonar digest request failed")
            self._calls_today += 1
            return None
