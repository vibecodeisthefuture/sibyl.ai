"""
X (Twitter) API v2 Client — read-only access for sentiment collection.

PURPOSE:
    Thin async wrapper around X API v2 endpoints used by the Sentiment
    & News Agent.  Supports two collection modes:

    1. FILTERED STREAM (preferred) — persistent SSE connection that
       delivers tweets matching server-side rules in real time.
       Requires Basic tier ($200/mo) minimum.

    2. RECENT SEARCH (fallback) — polling-based queries for tweets
       from the last 7 days.  Also requires Basic tier but consumes
       fewer resources when stream is unavailable.

RATE LIMIT STRATEGY (Basic tier: $200/mo):
    - Recent Search: 60 req/15min → agent uses max 48 (80% headroom)
    - Tweet read budget: ~10,000 tweets/month → 333/day → 14/hour
    - Agent tracks remaining quota via x-rate-limit-* response headers
    - On 429 (rate limited): sleep until x-rate-limit-reset timestamp

AUTHENTICATION:
    Bearer Token (App-Only Auth) via X_BEARER_TOKEN env var.
    OAuth 1.0a credentials stored but unused (X_API_KEY, X_API_SECRET,
    X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET).

CONFIGURATION:
    All credentials in .env.  Never hardcoded or logged.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("sibyl.clients.x")

# ── X API v2 Base URLs ─────────────────────────────────────────────
BASE_URL = "https://api.twitter.com/2"
STREAM_URL = f"{BASE_URL}/tweets/search/stream"
STREAM_RULES_URL = f"{BASE_URL}/tweets/search/stream/rules"
RECENT_SEARCH_URL = f"{BASE_URL}/tweets/search/recent"

# Default tweet fields for every request
DEFAULT_TWEET_FIELDS = (
    "id,text,created_at,author_id,public_metrics,"
    "context_annotations,entities,possibly_sensitive,"
    "referenced_tweets,conversation_id,lang"
)
DEFAULT_USER_FIELDS = (
    "id,name,username,verified,verified_type,"
    "public_metrics,created_at,description"
)
DEFAULT_EXPANSIONS = "author_id,referenced_tweets.id"


class XClient:
    """Async X API v2 client for read-only tweet collection.

    Usage:
        client = XClient()
        await client.initialize()

        # Polling mode
        tweets = await client.recent_search("polymarket prediction", max_results=10)

        # Stream mode (async iterator)
        async for tweet in client.filtered_stream():
            process(tweet)

        await client.close()
    """

    def __init__(self) -> None:
        self._bearer_token: str = ""
        self._http: httpx.AsyncClient | None = None

        # Rate limit tracking
        self._rate_limit_remaining: int = 60
        self._rate_limit_reset: float = 0.0
        self._daily_tweets_read: int = 0
        self._daily_reset_hour: int = -1

        # Stream state
        self._stream_available: bool = False

    async def initialize(self) -> bool:
        """Load credentials and create HTTP client.

        Returns:
            True if credentials are valid and client is ready.
        """
        self._bearer_token = os.environ.get("X_BEARER_TOKEN", "")
        if not self._bearer_token:
            logger.warning("X_BEARER_TOKEN not set — X client disabled")
            return False

        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "Authorization": f"Bearer {self._bearer_token}",
                "User-Agent": "Sibyl/0.1",
            },
        )

        # Test the connection
        try:
            resp = await self._http.get(
                f"{BASE_URL}/users/me",
            )
            # 401 = bad token, 403 = no user context (expected for app-only)
            # Both 200 and 403 mean the token is valid
            if resp.status_code in (200, 403):
                logger.info("X client initialized (bearer token valid)")
                return True
            elif resp.status_code == 401:
                logger.error("X_BEARER_TOKEN is invalid (401 Unauthorized)")
                return False
            else:
                logger.warning("X auth check returned %d — proceeding cautiously", resp.status_code)
                return True
        except Exception:
            logger.exception("Failed to verify X credentials")
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http:
            await self._http.aclose()

    # ── Rate Limit Management ──────────────────────────────────────────

    def _update_rate_limits(self, headers: httpx.Headers) -> None:
        """Extract and store rate limit info from response headers."""
        remaining = headers.get("x-rate-limit-remaining")
        reset = headers.get("x-rate-limit-reset")

        if remaining is not None:
            self._rate_limit_remaining = int(remaining)
        if reset is not None:
            self._rate_limit_reset = float(reset)

        if self._rate_limit_remaining <= 2:
            logger.warning(
                "X rate limit nearly exhausted: %d remaining, resets at %s",
                self._rate_limit_remaining,
                time.strftime("%H:%M:%S", time.localtime(self._rate_limit_reset)),
            )

    async def _wait_for_rate_limit(self) -> None:
        """Sleep until rate limit resets if we're at the limit."""
        if self._rate_limit_remaining <= 1 and self._rate_limit_reset > time.time():
            wait_seconds = self._rate_limit_reset - time.time() + 1
            logger.info("Rate limited — waiting %.0fs until reset", wait_seconds)
            import asyncio
            await asyncio.sleep(min(wait_seconds, 900))  # Cap at 15 min

    def _track_daily_budget(self, tweet_count: int) -> None:
        """Track daily tweet read budget."""
        import datetime
        now_hour = datetime.datetime.now(datetime.timezone.utc).hour
        if now_hour == 0 and self._daily_reset_hour != 0:
            self._daily_tweets_read = 0
            self._daily_reset_hour = 0

        self._daily_tweets_read += tweet_count

    @property
    def daily_tweets_remaining(self) -> int:
        """Estimated tweets remaining in daily budget."""
        return max(300 - self._daily_tweets_read, 0)

    @property
    def rate_limit_remaining(self) -> int:
        return self._rate_limit_remaining

    # ── Recent Search ──────────────────────────────────────────────────

    async def recent_search(
        self,
        query: str,
        max_results: int = 10,
        since_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search for recent tweets matching a query (last 7 days).

        X API v2: GET /2/tweets/search/recent

        Args:
            query:       Search query string (X query syntax).
            max_results: Number of tweets to return (10-100).
            since_id:    Only return tweets newer than this tweet ID.

        Returns:
            List of tweet dicts with author data expanded.
        """
        if not self._http:
            return []

        await self._wait_for_rate_limit()

        # Check daily budget
        if self._daily_tweets_read >= 300:
            logger.debug("Daily tweet budget exhausted (%d read)", self._daily_tweets_read)
            return []

        params: dict[str, Any] = {
            "query": query,
            "max_results": min(max_results, 100),
            "tweet.fields": DEFAULT_TWEET_FIELDS,
            "user.fields": DEFAULT_USER_FIELDS,
            "expansions": DEFAULT_EXPANSIONS,
        }
        if since_id:
            params["since_id"] = since_id

        try:
            resp = await self._http.get(RECENT_SEARCH_URL, params=params)
            self._update_rate_limits(resp.headers)

            if resp.status_code == 429:
                logger.warning("429 rate limited on recent_search")
                return []
            if resp.status_code == 401:
                logger.error("401 on recent_search — token may be invalid")
                return []
            if resp.status_code == 403:
                logger.warning("403 on recent_search — Basic tier may be required")
                return []
            if resp.status_code != 200:
                logger.warning("recent_search returned %d: %s", resp.status_code, resp.text[:200])
                return []

            data = resp.json()
            tweets = data.get("data", [])
            includes = data.get("includes", {})

            # Merge author data into tweets
            users_by_id = {u["id"]: u for u in includes.get("users", [])}
            for tweet in tweets:
                author = users_by_id.get(tweet.get("author_id", ""), {})
                tweet["_author"] = author

            self._track_daily_budget(len(tweets))
            logger.debug("recent_search('%s'): %d tweets", query[:50], len(tweets))
            return tweets

        except Exception:
            logger.exception("recent_search failed for query: %s", query[:50])
            return []

    # ── Filtered Stream ────────────────────────────────────────────────

    async def filtered_stream(self) -> AsyncIterator[dict[str, Any]]:
        """Connect to filtered stream and yield tweets as they arrive.

        X API v2: GET /2/tweets/search/stream

        This is a persistent SSE connection. Tweets arrive in real-time
        matching the rules configured via sync_stream_rules().

        Yields:
            Tweet dicts with author data expanded.
        """
        if not self._http:
            return

        params = {
            "tweet.fields": DEFAULT_TWEET_FIELDS,
            "user.fields": DEFAULT_USER_FIELDS,
            "expansions": DEFAULT_EXPANSIONS,
        }

        try:
            async with self._http.stream(
                "GET", STREAM_URL, params=params, timeout=None
            ) as resp:
                if resp.status_code == 403:
                    logger.warning("Filtered stream returned 403 — requires Basic tier or higher")
                    self._stream_available = False
                    return
                if resp.status_code == 429:
                    logger.warning("Filtered stream rate limited (429)")
                    self._stream_available = False
                    return
                if resp.status_code != 200:
                    logger.warning("Stream returned %d", resp.status_code)
                    self._stream_available = False
                    return

                self._stream_available = True
                logger.info("Filtered stream connected")

                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue  # Heartbeat (empty line)

                    try:
                        payload = json.loads(line)
                        tweet = payload.get("data", {})
                        includes = payload.get("includes", {})
                        matching_rules = payload.get("matching_rules", [])

                        # Attach rule tags
                        tweet["_rule_tags"] = [r.get("tag", "") for r in matching_rules]

                        # Merge author data
                        users_by_id = {u["id"]: u for u in includes.get("users", [])}
                        author = users_by_id.get(tweet.get("author_id", ""), {})
                        tweet["_author"] = author

                        self._track_daily_budget(1)
                        yield tweet

                    except json.JSONDecodeError:
                        logger.debug("Stream: non-JSON line skipped")
                        continue

        except httpx.ReadTimeout:
            logger.info("Stream read timeout — will reconnect")
            self._stream_available = False
        except Exception:
            logger.exception("Filtered stream error")
            self._stream_available = False

    # ── Stream Rule Management ─────────────────────────────────────────

    async def get_stream_rules(self) -> list[dict[str, Any]]:
        """Fetch currently active stream rules from X API.

        Returns:
            List of rule dicts: [{"id": "...", "value": "...", "tag": "..."}]
        """
        if not self._http:
            return []

        try:
            resp = await self._http.get(STREAM_RULES_URL)
            if resp.status_code != 200:
                logger.warning("get_stream_rules returned %d", resp.status_code)
                return []

            data = resp.json()
            return data.get("data", [])
        except Exception:
            logger.exception("Failed to fetch stream rules")
            return []

    async def sync_stream_rules(self, desired_rules: list[dict[str, str]]) -> None:
        """Reconcile stream rules with desired config.

        Adds missing rules, deletes unknown rules. Each rule dict should
        have "value" and "tag" keys.

        Args:
            desired_rules: List of {"value": "...", "tag": "..."} dicts.
        """
        if not self._http:
            return

        current_rules = await self.get_stream_rules()
        current_by_tag = {r.get("tag", ""): r for r in current_rules}
        desired_by_tag = {r["tag"]: r for r in desired_rules}

        # Delete rules not in desired set
        to_delete = [
            r["id"] for tag, r in current_by_tag.items()
            if tag not in desired_by_tag
        ]
        if to_delete:
            try:
                await self._http.post(
                    STREAM_RULES_URL,
                    json={"delete": {"ids": to_delete}},
                )
                logger.info("Deleted %d stale stream rules", len(to_delete))
            except Exception:
                logger.exception("Failed to delete stream rules")

        # Add rules not in current set
        to_add = [
            {"value": r["value"], "tag": r["tag"]}
            for tag, r in desired_by_tag.items()
            if tag not in current_by_tag
        ]
        if to_add:
            try:
                resp = await self._http.post(
                    STREAM_RULES_URL,
                    json={"add": to_add},
                )
                if resp.status_code == 201:
                    logger.info("Added %d new stream rules", len(to_add))
                else:
                    logger.warning("add_stream_rules returned %d: %s", resp.status_code, resp.text[:200])
            except Exception:
                logger.exception("Failed to add stream rules")

    # ── Author Lookup ──────────────────────────────────────────────────

    async def lookup_user(self, user_id: str) -> dict[str, Any] | None:
        """Fetch user profile by ID (for author enrichment/cache refresh).

        Args:
            user_id: X user ID string.

        Returns:
            User dict or None if unavailable.
        """
        if not self._http:
            return None

        await self._wait_for_rate_limit()

        try:
            resp = await self._http.get(
                f"{BASE_URL}/users/{user_id}",
                params={"user.fields": DEFAULT_USER_FIELDS},
            )
            self._update_rate_limits(resp.headers)

            if resp.status_code == 200:
                return resp.json().get("data")
            return None
        except Exception:
            return None
