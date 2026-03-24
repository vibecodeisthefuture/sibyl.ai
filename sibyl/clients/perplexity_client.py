"""
Perplexity API Client — LLM-powered contextual research for prediction markets.

PURPOSE:
    Thin async wrapper around Perplexity's Sonar API that the Breakout Scout
    uses for contextual analysis of prediction markets.  Returns grounded,
    web-sourced research summaries that complement Reddit and NewsAPI data.

PRICING (as of 2026-03):
    - Sonar:       $1/M input tokens, $1/M output tokens
    - Sonar Pro:   $3/M input, $15/M output (deeper reasoning)
    - No free tier.  Pro subscribers get $5/mo API credit.
    - Tier 0: 50 RPM, pay-per-use.

COST OPTIMIZATION:
    - Use "sonar" model (cheapest) unless high-conviction research needed
    - Compact prompts (~200 tokens input) + short responses (max 300 tokens)
    - ~500 total tokens per call → ~$0.0005 per call
    - At 10 calls/day → ~$0.15/month; at 20 calls/day → ~$0.30/month
    - Gated behind breakout_score threshold so only promising markets get queried
    - Freshness caching means same market isn't re-queried until research decays

AUTHENTICATION:
    PERPLEXITY_API_KEY env var.  Never hardcoded or logged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("sibyl.clients.perplexity")

PERPLEXITY_CHAT_URL = "https://api.perplexity.ai/chat/completions"


class PerplexityClient:
    """Async Perplexity API client for market research queries.

    Usage:
        client = PerplexityClient()
        if client.initialize():
            result = await client.research_market(
                "Will the Fed cut rates in June 2026?",
                context="Prediction market, current odds 62% YES"
            )
            print(result)  # {"summary": "...", "citations": [...]}
        await client.close()
    """

    def __init__(self) -> None:
        self._api_key: str = ""
        self._http: httpx.AsyncClient | None = None
        self._model: str = "sonar"  # cheapest model
        self._max_tokens: int = 300  # keep responses compact
        self._calls_today: int = 0
        self._daily_call_cap: int = 30  # safety cap

    def initialize(self) -> bool:
        """Load API key and create HTTP client.

        Returns:
            True if API key is present and client is ready.
        """
        self._api_key = os.environ.get("PERPLEXITY_API_KEY", "")
        if not self._api_key:
            logger.info("PERPLEXITY_API_KEY not set — Perplexity client disabled")
            return False

        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        logger.info("Perplexity client initialized (model=%s, max_tokens=%d)",
                     self._model, self._max_tokens)
        return True

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http:
            await self._http.aclose()

    @property
    def available(self) -> bool:
        return self._http is not None

    @property
    def calls_remaining_today(self) -> int:
        return max(self._daily_call_cap - self._calls_today, 0)

    def reset_daily_counter(self) -> None:
        """Reset daily call counter (called at UTC midnight)."""
        self._calls_today = 0

    async def research_market(
        self,
        market_title: str,
        context: str = "",
        model_override: str | None = None,
    ) -> dict[str, Any] | None:
        """Query Perplexity for contextual analysis of a prediction market.

        Sends a compact, structured prompt requesting grounded research
        with citations.  Returns a parsed response dict.

        Args:
            market_title:   The prediction market question/title.
            context:        Optional context (current odds, category, etc.)
            model_override: Override the default model (e.g., "sonar-pro").

        Returns:
            Dict with "summary", "sentiment_hint", "key_factors",
            "citations", or None if unavailable/capped.
        """
        if not self._http:
            return None

        if self._calls_today >= self._daily_call_cap:
            logger.debug("Daily Perplexity call cap reached (%d)", self._daily_call_cap)
            return None

        model = model_override or self._model

        # Compact prompt designed to minimize token usage while extracting
        # maximum value for prediction market context
        system_prompt = (
            "You are a prediction market research analyst. "
            "Give a concise, factual assessment grounded in recent events. "
            "Focus on: key factors affecting the outcome, recent developments, "
            "and any consensus or controversy among experts."
        )

        user_prompt = f'Market: "{market_title}"'
        if context:
            user_prompt += f"\nContext: {context}"
        user_prompt += (
            "\n\nProvide: 1) 2-sentence summary of current situation, "
            "2) sentiment hint (BULLISH/BEARISH/NEUTRAL/CONTESTED), "
            "3) top 3 key factors as a comma-separated list."
        )

        try:
            resp = await self._http.post(
                PERPLEXITY_CHAT_URL,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": self._max_tokens,
                    "temperature": 0.1,  # factual, low creativity
                    "return_citations": True,
                },
            )

            self._calls_today += 1

            if resp.status_code == 401:
                logger.error("Perplexity API key invalid (401)")
                return None
            if resp.status_code == 429:
                logger.warning("Perplexity rate limited (429)")
                return None
            if resp.status_code != 200:
                logger.warning("Perplexity returned %d: %s",
                               resp.status_code, resp.text[:200])
                return None

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return None

            content = choices[0].get("message", {}).get("content", "")
            citations = data.get("citations", [])

            # Parse the response into structured fields
            result = self._parse_research_response(content, citations)

            logger.debug("Perplexity research for '%s': %s (call #%d today)",
                         market_title[:50], result.get("sentiment_hint", "?"),
                         self._calls_today)
            return result

        except httpx.TimeoutException:
            logger.warning("Perplexity timeout for: %s", market_title[:50])
            self._calls_today += 1  # Count failed calls too
            return None
        except Exception:
            logger.exception("Perplexity request failed for: %s", market_title[:50])
            self._calls_today += 1
            return None

    @staticmethod
    def _parse_research_response(
        content: str, citations: list[str]
    ) -> dict[str, Any]:
        """Parse Perplexity's free-text response into structured fields.

        Extracts sentiment hint and key factors from the response text.
        Falls back to NEUTRAL if parsing fails.
        """
        content_upper = content.upper()

        # Detect sentiment hint
        sentiment_hint = "NEUTRAL"
        for label in ("BULLISH", "BEARISH", "CONTESTED"):
            if label in content_upper:
                sentiment_hint = label
                break

        # Extract key factors (look for numbered list or comma-separated)
        key_factors: list[str] = []
        lines = content.strip().split("\n")
        for line in lines:
            stripped = line.strip()
            # Look for numbered items like "1)", "2)", "3)" or "1.", "2.", "3."
            if stripped and len(stripped) > 3:
                if stripped[0].isdigit() and stripped[1] in ".)" :
                    key_factors.append(stripped[2:].strip().rstrip(","))

        # If no numbered items found, try comma-separated in last line
        if not key_factors and lines:
            last_line = lines[-1].strip()
            if "," in last_line and len(last_line) < 500:
                key_factors = [f.strip() for f in last_line.split(",") if f.strip()]

        return {
            "summary": content.strip(),
            "sentiment_hint": sentiment_hint,
            "key_factors": key_factors[:5],  # Cap at 5
            "citations": citations[:5],  # Cap at 5
            "score": _sentiment_hint_to_score(sentiment_hint),
        }


def _sentiment_hint_to_score(hint: str) -> float:
    """Convert sentiment hint to a 0.0-1.0 score."""
    return {
        "BULLISH": 0.72,
        "BEARISH": 0.28,
        "CONTESTED": 0.50,
        "NEUTRAL": 0.50,
    }.get(hint, 0.50)
