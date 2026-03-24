"""
X (Twitter) Sentiment & News Agent — real-time public sentiment for prediction markets.

PURPOSE:
    Collects, filters, scores, and aggregates public sentiment from X (Twitter)
    to produce SENTIMENT signals that feed into the Signal Generator.  This is a
    standalone Analysis Layer component that does NOT execute trades.

ARCHITECTURE:
    Implements the full 6-stage pipeline from sibyl-x-sentiment-framework.md:

    Stage 1: Ingestion & Deduplication
        → Collect tweets via Filtered Stream or Recent Search
        → Deduplicate by tweet_id

    Stage 2: Guard Rail Pre-Screen
        → Authenticity check (account age, velocity, coordinated behavior)
        → Radicalism screen (keyword filter + extremity check)

    Stage 3: Sentiment Scoring
        → Keyword-based financial sentiment (FinBERT deferred to GPU workstation)
        → Reach-weighted: sentiment × log(1 + impressions)

    Stage 4: Bias Risk Assessment
        → Political homogeneity, cascade/echo chamber, source concentration

    Stage 5: Aggregation Window
        → 5-minute rolling windows per market
        → Compute: net_sentiment, tweet_volume, reach_weighted_sentiment

    Stage 6: Signal Threshold Check
        → Generate SENTIMENT signal if shift + volume + quality thresholds met

COLLECTION MODE STRATEGY (Basic tier $200/mo):
    - Preferred: Filtered Stream (real-time, low query cost)
    - Fallback:  Recent Search polling (15-min cycle for high-priority markets)
    - Budget:    ~333 tweets/day read limit → agent tracks and respects this

POLLING: Every 5 minutes (processes buffered tweets + closes aggregation windows).
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.sentiment")

# ── Keyword-Based Sentiment Lexicon ───────────────────────────────────
# Financial/prediction-market sentiment keywords.
# Future: replace with FinBERT when GPU workstation is online.
POSITIVE_KEYWORDS = {
    "bullish", "surge", "rally", "soar", "moon", "breakout", "win", "winning",
    "approved", "passed", "confirmed", "likely", "strong", "growth", "boost",
    "upside", "outperform", "beat", "exceed", "optimistic", "favorable",
    "green", "profit", "gain", "up", "higher", "rising", "positive",
    "certain", "guaranteed", "landslide", "dominate", "crush",
}
NEGATIVE_KEYWORDS = {
    "bearish", "crash", "dump", "tank", "plunge", "collapse", "lose", "losing",
    "rejected", "failed", "denied", "unlikely", "weak", "decline", "fall",
    "downside", "underperform", "miss", "disappoint", "pessimistic", "risk",
    "red", "loss", "down", "lower", "dropping", "negative", "uncertain",
    "doomed", "disaster", "recession", "default", "crisis",
}
IRONY_MARKERS = {
    "lol", "lmao", "🤣", "😂", "/s", "sure thing", "totally",
    "right...", "yeah right", "as if", "copium",
}

# ── Radicalism Keywords (Layer 1 hard gate) ───────────────────────────
# Patterns that trigger immediate rejection. Kept minimal here;
# the full list lives in config/radicalism_keywords.yaml (not committed).
RADICAL_PATTERNS_DEFAULT = [
    r"\bcivil\s+war\b",
    r"\bexecute\b.*\bpolitician",
    r"\bkill\s+(all|them|every)\b",
    r"\bbomb\b.*\b(government|capitol)\b",
    r"\bgenocide\b",
    r"\bethnic\s+cleansing\b",
]


class XSentimentAgent(BaseAgent):
    """Collects X sentiment data and produces SENTIMENT trading signals.

    Implements the full 6-stage pipeline:
        Ingestion → Guard Rails → Scoring → Bias → Aggregation → Signal
    """

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="x_sentiment", db=db, config=config)
        self._x_config: dict[str, Any] = {}
        self._sentiment_config: dict[str, Any] = {}
        self._bias_config: dict[str, Any] = {}
        self._auth_config: dict[str, Any] = {}

        # X API client
        self._x_client = None
        self._stream_task: asyncio.Task | None = None
        self._stream_mode: bool = False

        # In-memory tweet buffer (ring buffer, max 2000)
        self._tweet_buffer: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._max_buffer_size: int = 2000

        # Aggregation: market_id → list of processed tweets
        self._windows: dict[str, list[dict]] = defaultdict(list)
        self._window_start: dict[str, datetime] = {}
        self._previous_net_sentiment: dict[str, float] = {}

        # Author cache (in-memory LRU, max 10k)
        self._author_cache: dict[str, dict] = {}
        self._blocklist: set[str] = set()

        # Radicalism patterns (compiled regex)
        self._radical_patterns: list[re.Pattern] = []

        # Rolling baselines for volume z-score
        self._volume_baselines: dict[str, dict] = {}  # market_id → {mean, std, count}

        # Search state for polling mode
        self._last_search_ids: dict[str, str] = {}  # query → last tweet_id

        # Stats for monitoring
        self._tweets_processed: int = 0
        self._tweets_rejected: int = 0
        self._signals_generated: int = 0

    @property
    def poll_interval(self) -> float:
        return float(self._x_config.get("poll_interval_seconds", 300))

    async def start(self) -> None:
        """Initialize X client, load configs, sync stream rules."""
        from sibyl.core.config import load_yaml

        # ── Load config ───────────────────────────────────────────────
        try:
            self._x_config = load_yaml("x_sentiment_config.yaml")
        except FileNotFoundError:
            self._x_config = {}

        self._sentiment_config = self._x_config.get("sentiment", {})
        self._bias_config = self._x_config.get("bias", {})
        self._auth_config = self._x_config.get("authenticity", {})
        collection = self._x_config.get("collection", {})

        # ── Compile radicalism patterns ───────────────────────────────
        patterns = RADICAL_PATTERNS_DEFAULT
        try:
            radical_yaml = load_yaml("radicalism_keywords.yaml")
            patterns = radical_yaml.get("patterns", patterns)
        except FileNotFoundError:
            pass
        self._radical_patterns = [re.compile(p, re.IGNORECASE) for p in patterns]

        # ── Load blocklist from DB ────────────────────────────────────
        rows = await self.db.fetchall("SELECT author_id FROM x_blocklist")
        self._blocklist = {r["author_id"] for r in rows}

        # ── Load author cache from DB ─────────────────────────────────
        cache_rows = await self.db.fetchall(
            "SELECT * FROM x_author_cache ORDER BY last_updated DESC LIMIT 10000"
        )
        for row in cache_rows:
            self._author_cache[row["author_id"]] = {
                "username": row["username"],
                "followers_count": row["followers_count"],
                "following_count": row["following_count"],
                "tweet_count": row["tweet_count"],
                "account_age_days": row["account_age_days"],
                "verified": bool(row["verified"]),
                "quality_score": float(row["quality_score"]) if row["quality_score"] else 0.5,
            }

        # ── Initialize X client ───────────────────────────────────────
        from sibyl.clients.x_client import XClient
        self._x_client = XClient()
        client_ok = await self._x_client.initialize()

        if not client_ok:
            self.logger.warning("X client failed to initialize — agent will idle")
            return

        # ── Sync stream rules ─────────────────────────────────────────
        preferred_mode = collection.get("preferred_mode", "stream")
        if preferred_mode == "stream":
            try:
                rules_yaml = load_yaml("x_stream_rules.yaml")
                enabled_rules = [
                    r for r in rules_yaml.get("rules", [])
                    if r.get("enabled", True)
                ]
                if enabled_rules:
                    await self._x_client.sync_stream_rules(enabled_rules)
            except FileNotFoundError:
                pass

            # Try to start stream
            self._stream_task = asyncio.create_task(
                self._run_stream(), name="x-stream"
            )
            self._stream_mode = True
            self.logger.info("X Sentiment Agent started (stream mode)")
        else:
            self.logger.info("X Sentiment Agent started (search polling mode)")

    async def run_cycle(self) -> None:
        """Process buffered tweets and close aggregation windows.

        In stream mode: tweets arrive via background _run_stream() task
        and are deposited in _tweet_buffer. run_cycle() processes them.

        In search mode: run_cycle() also triggers search queries for
        high-priority markets before processing.
        """
        # ── Search polling (if stream not active) ──────────────────────
        if not self._stream_mode or (self._stream_task and self._stream_task.done()):
            if self._stream_task and self._stream_task.done():
                self._stream_mode = False
                self.logger.info("Stream disconnected — falling back to search polling")

            await self._poll_via_search()

        # ── Process buffered tweets through pipeline ───────────────────
        if self._tweet_buffer:
            tweets = list(self._tweet_buffer)
            self._tweet_buffer.clear()
            await self._process_tweets(tweets)

        # ── Close mature aggregation windows ──────────────────────────
        window_minutes = int(self._sentiment_config.get("aggregation_window_minutes", 5))
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=window_minutes)

        closed_markets = []
        for market_id, start_time in list(self._window_start.items()):
            if start_time <= cutoff:
                closed_markets.append(market_id)

        for market_id in closed_markets:
            await self._close_window(market_id)

        # ── Update monitoring metrics ─────────────────────────────────
        await self._write_monitoring_metrics()

    async def stop(self) -> None:
        """Shutdown stream and X client."""
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

        if self._x_client:
            await self._x_client.close()

        self.logger.info(
            "X Sentiment Agent stopped (processed=%d, rejected=%d, signals=%d)",
            self._tweets_processed, self._tweets_rejected, self._signals_generated,
        )

    # ═══════════════════════════════════════════════════════════════════
    # COLLECTION: Stream + Search
    # ═══════════════════════════════════════════════════════════════════

    async def _run_stream(self) -> None:
        """Background task: maintain persistent stream connection.

        Implements reconnection policy from framework doc Section 8.2:
            attempt 1: wait 1s
            attempt 2: wait 5s
            attempt 3: wait 30s
            attempt 4+: wait 60s (cap)
            After 10 failures: fall back to search mode
        """
        backoff_schedule = [1, 5, 30] + [60] * 7  # 10 attempts total
        attempt = 0

        while attempt < 10:
            try:
                async for tweet in self._x_client.filtered_stream():
                    attempt = 0  # Reset on successful data
                    self._ingest_tweet(tweet, source="stream")
            except asyncio.CancelledError:
                return
            except Exception:
                self.logger.exception("Stream error (attempt %d)", attempt + 1)

            if attempt < len(backoff_schedule):
                wait = backoff_schedule[attempt]
            else:
                wait = 60
            attempt += 1
            self.logger.info("Stream reconnecting in %ds (attempt %d/10)", wait, attempt)
            await asyncio.sleep(wait)

        self.logger.warning("Stream failed after 10 attempts — agent will use search polling")
        self._stream_mode = False

    async def _poll_via_search(self) -> None:
        """Poll via Recent Search for high-priority markets.

        Budget-aware: respects daily tweet limit and rate limits.
        Constructs market-specific queries from active market titles.
        """
        if not self._x_client or self._x_client.daily_tweets_remaining <= 0:
            return

        # Get active markets that need sentiment coverage
        markets = await self.db.fetchall(
            """SELECT id, title, category FROM markets
               WHERE status = 'active'
               ORDER BY breakout_score DESC NULLS LAST
               LIMIT 10"""
        )

        if not markets:
            return

        for market in markets:
            if self._x_client.daily_tweets_remaining <= 0:
                break

            market_id = market["id"]
            title = market["title"]

            # Build search query from market title
            query = self._build_search_query(title)
            if not query:
                continue

            since_id = self._last_search_ids.get(market_id)
            tweets = await self._x_client.recent_search(
                query=query,
                max_results=10,
                since_id=since_id,
            )

            if tweets:
                self._last_search_ids[market_id] = tweets[0].get("id", "")
                for tweet in tweets:
                    tweet["_rule_tags"] = [market.get("category", "search")]
                    tweet["_search_market_id"] = market_id
                    self._ingest_tweet(tweet, source="search")

    @staticmethod
    def _build_search_query(title: str) -> str:
        """Build an X search query from a market title.

        Extracts significant keywords and combines with signal modifiers.
        """
        stop_words = {
            "will", "the", "a", "an", "to", "in", "on", "at", "by", "for",
            "of", "is", "be", "it", "this", "that", "what", "who", "how",
            "when", "where", "which", "or", "and", "not", "no", "yes",
            "do", "does", "did", "has", "have", "had", "was", "were",
            "are", "been", "being", "before", "after", "than",
        }
        words = [
            w for w in re.sub(r"[^\w\s]", "", title).split()
            if w.lower() not in stop_words and len(w) > 2
        ]
        if not words:
            return ""

        # Use top 3-4 keywords + lang:en filter
        keywords = " ".join(words[:4])
        return f"({keywords}) lang:en -is:retweet"

    # ═══════════════════════════════════════════════════════════════════
    # STAGE 1: Ingestion & Deduplication
    # ═══════════════════════════════════════════════════════════════════

    def _ingest_tweet(self, tweet: dict, source: str = "stream") -> None:
        """Add tweet to buffer if not a duplicate."""
        tweet_id = tweet.get("id", "")
        if not tweet_id or tweet_id in self._seen_ids:
            return

        self._seen_ids.add(tweet_id)
        tweet["_source"] = source

        # Enforce ring buffer size
        if len(self._tweet_buffer) >= self._max_buffer_size:
            self._tweet_buffer.pop(0)
            # Trim seen_ids periodically
            if len(self._seen_ids) > self._max_buffer_size * 2:
                self._seen_ids = set(t.get("id", "") for t in self._tweet_buffer)

        self._tweet_buffer.append(tweet)

    # ═══════════════════════════════════════════════════════════════════
    # STAGES 2-4: Guard Rails + Scoring + Bias
    # ═══════════════════════════════════════════════════════════════════

    async def _process_tweets(self, tweets: list[dict]) -> None:
        """Run tweets through stages 2-4 and buffer into aggregation windows."""
        for tweet in tweets:
            self._tweets_processed += 1

            # ── Stage 2: Guard Rail Pre-Screen ─────────────────────────
            # 2a: Radicalism screen (hard gate)
            text = tweet.get("text", "")
            if self._check_radicalism(text):
                await self._reject_tweet(tweet, "RADICAL_CONTENT")
                continue

            # 2b: Authenticity check
            authenticity_score = self._compute_authenticity(tweet)
            if authenticity_score < float(self._auth_config.get("min_tweet_authenticity_score", 0.50)):
                await self._reject_tweet(tweet, "LOW_AUTHENTICITY", authenticity_score=authenticity_score)
                continue

            # Check blocklist
            author_id = tweet.get("author_id", "")
            if author_id in self._blocklist:
                await self._reject_tweet(tweet, "BLOCKLISTED")
                continue

            # ── Stage 3: Sentiment Scoring ─────────────────────────────
            sentiment_score = self._score_sentiment(text)
            metrics = tweet.get("public_metrics", {})
            impressions = metrics.get("impression_count", 0)
            reach_weight = math.log1p(impressions)
            weighted_sentiment = sentiment_score * reach_weight

            # ── Stage 4: Bias Risk Assessment ──────────────────────────
            bias_risk_score = self._compute_bias_risk(tweet)

            # ── Buffer into aggregation window ─────────────────────────
            market_id = self._map_to_market(tweet)
            if not market_id:
                continue  # Unmapped tweet — discard

            processed = {
                "tweet_id": tweet.get("id", ""),
                "text": text,
                "author_id": author_id,
                "sentiment_score": sentiment_score,
                "weighted_sentiment": weighted_sentiment,
                "reach_weight": reach_weight,
                "authenticity_score": authenticity_score,
                "bias_risk_score": bias_risk_score,
                "impression_count": impressions,
                "conversation_id": tweet.get("conversation_id", ""),
                "rule_tags": tweet.get("_rule_tags", []),
                "timestamp": tweet.get("created_at", ""),
            }

            if market_id not in self._window_start:
                self._window_start[market_id] = datetime.now(timezone.utc)
            self._windows[market_id].append(processed)

    def _check_radicalism(self, text: str) -> bool:
        """Layer 1: keyword screen for radical/violent content.

        Returns True if tweet should be REJECTED.
        """
        for pattern in self._radical_patterns:
            if pattern.search(text):
                return True
        return False

    def _compute_authenticity(self, tweet: dict) -> float:
        """Compute authenticity score for a tweet (0.0-1.0).

        Checks from framework doc Section 5.3:
            1. Account age & activity pattern
            2. Posting velocity (checked at window level)
            3-5. Linguistic/temporal clustering (checked at window level)

        Returns:
            1.0 - sum(penalties), clamped to [0.0, 1.0].
        """
        penalties = 0.0
        author = tweet.get("_author", {})
        if not author:
            return 0.50  # No author data — moderate penalty

        # Account age
        created_at = author.get("created_at", "")
        if created_at:
            try:
                account_date = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - account_date).days
                min_age = int(self._auth_config.get("min_account_age_days", 90))
                if age_days < min_age:
                    penalties += 0.50
            except (ValueError, TypeError):
                pass

        pub_metrics = author.get("public_metrics", {})
        followers = pub_metrics.get("followers_count", 0)
        following = pub_metrics.get("following_count", 0)
        tweet_count = pub_metrics.get("tweet_count", 0)

        # Low followers + high following (follow-farming)
        min_followers = int(self._auth_config.get("min_followers", 20))
        if followers < min_followers and following > 500:
            penalties += 0.50

        # Very new account with little history
        min_tweets = int(self._auth_config.get("min_tweet_history", 50))
        if tweet_count < min_tweets:
            penalties += 0.25

        # Following/follower ratio
        max_ratio = float(self._auth_config.get("max_following_to_followers_ratio", 25.0))
        if followers > 0 and following / followers > max_ratio:
            penalties += 0.20

        # No profile picture (default avatar indicator)
        # X API v2 doesn't directly expose this; skip for now

        return max(1.0 - penalties, 0.0)

    def _score_sentiment(self, text: str) -> float:
        """Keyword-based sentiment scoring.

        Returns float from -1.0 (strongly negative) to +1.0 (strongly positive).
        0.0 = neutral.

        Future: replace with FinBERT when GPU workstation is online.
        """
        text_lower = text.lower()
        words = set(re.findall(r'\b\w+\b', text_lower))

        # Check for irony markers → reduce confidence
        irony_detected = bool(words & IRONY_MARKERS) or any(m in text_lower for m in IRONY_MARKERS)

        pos_count = len(words & POSITIVE_KEYWORDS)
        neg_count = len(words & NEGATIVE_KEYWORDS)
        total = pos_count + neg_count

        if total == 0:
            return 0.0

        raw_score = (pos_count - neg_count) / total

        # Irony: dampen or invert
        if irony_detected:
            raw_score *= -0.3  # Partially invert with low confidence

        return max(min(raw_score, 1.0), -1.0)

    def _compute_bias_risk(self, tweet: dict) -> float:
        """Compute per-tweet bias risk score (0.0-1.0).

        Individual tweet checks. Window-level bias checks (political
        homogeneity, cascade, source concentration) run in _close_window().
        """
        bias = 0.0
        text = tweet.get("text", "")

        # Check for extreme sentiment (tail check)
        sentiment = self._score_sentiment(text)
        if abs(sentiment) > 0.90:
            bias += 0.15  # Extreme tail — may be radicalized

        # Possibly sensitive content flag
        if tweet.get("possibly_sensitive"):
            bias += 0.10

        return min(bias, 1.0)

    def _map_to_market(self, tweet: dict) -> str | None:
        """Map a tweet to a specific market in the database.

        Uses:
            1. _search_market_id if set (from search polling)
            2. Rule tag → category → market matching
            3. Fuzzy title matching on tweet text

        Returns:
            market_id string or None if unmappable.
        """
        # Direct mapping from search polling
        search_market = tweet.get("_search_market_id")
        if search_market:
            return search_market

        # Rule tag gives us a category hint
        rule_tags = tweet.get("_rule_tags", [])

        # For stream tweets, we need to match against active markets.
        # This is done asynchronously in _process_tweets; for now, use
        # a simple heuristic: if we have rule_tags, create a synthetic
        # market_id from the first tag. The actual market matching happens
        # at the window close stage where we look up real markets.
        if rule_tags:
            return f"x_category_{rule_tags[0]}"

        return None

    # ═══════════════════════════════════════════════════════════════════
    # STAGES 5-6: Aggregation & Signal Generation
    # ═══════════════════════════════════════════════════════════════════

    async def _close_window(self, market_id: str) -> None:
        """Close a 5-minute aggregation window and evaluate signal thresholds.

        Framework doc Section 4.4: A SENTIMENT signal is generated if ALL of:
            - window_tweet_count ≥ 10
            - abs(sentiment_shift) ≥ 0.15
            - volume_z_score ≥ 1.5
            - mean(bias_risk_score) ≤ 0.60
            - ≥ 70% of tweets pass authenticity check
        """
        tweets = self._windows.pop(market_id, [])
        window_start = self._window_start.pop(market_id, datetime.now(timezone.utc))
        window_end = datetime.now(timezone.utc)

        if not tweets:
            return

        tweet_count = len(tweets)
        min_tweets = int(self._sentiment_config.get("min_tweets_per_window", 10))

        # ── Compute window metrics ─────────────────────────────────────
        sentiment_scores = [t["sentiment_score"] for t in tweets]
        weighted_sentiments = [t["weighted_sentiment"] for t in tweets]
        bias_scores = [t["bias_risk_score"] for t in tweets]
        auth_scores = [t["authenticity_score"] for t in tweets]
        reach_weights = [t["reach_weight"] for t in tweets]

        net_sentiment = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0.0
        total_reach = sum(reach_weights)
        reach_weighted_sentiment = (
            sum(weighted_sentiments) / total_reach if total_reach > 0 else 0.0
        )
        bias_risk_mean = sum(bias_scores) / len(bias_scores) if bias_scores else 0.0
        authenticity_mean = sum(auth_scores) / len(auth_scores) if auth_scores else 0.0
        auth_pass_pct = len([a for a in auth_scores if a >= 0.50]) / len(auth_scores)

        # Sentiment shift from previous window
        prev_sentiment = self._previous_net_sentiment.get(market_id, 0.0)
        sentiment_shift = net_sentiment - prev_sentiment
        self._previous_net_sentiment[market_id] = net_sentiment

        # Volume z-score (rolling baseline)
        volume_z_score = self._compute_volume_z_score(market_id, tweet_count)

        # ── Window-level bias checks ───────────────────────────────────
        # Cascade detection (conversation diversity)
        conversation_ids = set(t["conversation_id"] for t in tweets if t["conversation_id"])
        cascade_flag = False
        cascade_threshold = float(self._bias_config.get("cascade_diversity_threshold", 0.30))
        if tweet_count > 0 and len(conversation_ids) / tweet_count < cascade_threshold:
            cascade_flag = True
            bias_risk_mean += float(self._bias_config.get("cascade_penalty", 0.25))

        # Source concentration
        author_reach: dict[str, float] = defaultdict(float)
        for t in tweets:
            author_reach[t["author_id"]] += t["reach_weight"]
        top_3_reach = sorted(author_reach.values(), reverse=True)[:3]
        conc_threshold = float(self._bias_config.get("source_concentration_threshold", 0.50))
        if total_reach > 0 and sum(top_3_reach) / total_reach > conc_threshold:
            bias_risk_mean += float(self._bias_config.get("source_concentration_penalty", 0.20))

        # Political homogeneity (simplified — based on sentiment agreement)
        pos_count = len([s for s in sentiment_scores if s > 0.1])
        neg_count = len([s for s in sentiment_scores if s < -0.1])
        homo_threshold = float(self._bias_config.get("political_homogeneity_threshold", 0.60))
        political_flag = False
        if tweet_count > 0:
            dominant_pct = max(pos_count, neg_count) / tweet_count
            if dominant_pct > homo_threshold:
                political_flag = True
                bias_risk_mean += float(self._bias_config.get("political_homogeneity_penalty", 0.30))

        # ── Write window record ────────────────────────────────────────
        signal_generated = 0
        signal_id = None

        # ── Signal threshold check ─────────────────────────────────────
        shift_threshold = float(self._sentiment_config.get("sentiment_shift_threshold", 0.15))
        volume_threshold = float(self._sentiment_config.get("volume_z_score_threshold", 1.5))
        bias_ceiling = float(self._sentiment_config.get("bias_risk_ceiling", 0.60))
        auth_floor = float(self._sentiment_config.get("authenticity_floor_pct", 0.70))

        thresholds_met = (
            tweet_count >= min_tweets
            and abs(sentiment_shift) >= shift_threshold
            and volume_z_score >= volume_threshold
            and bias_risk_mean <= bias_ceiling
            and auth_pass_pct >= auth_floor
        )

        if thresholds_met:
            signal_id = await self._generate_sentiment_signal(
                market_id=market_id,
                window_start=window_start,
                window_end=window_end,
                tweet_count=tweet_count,
                net_sentiment=net_sentiment,
                sentiment_shift=sentiment_shift,
                volume_z_score=volume_z_score,
                bias_risk_mean=bias_risk_mean,
                authenticity_mean=authenticity_mean,
                reach_weighted_sentiment=reach_weighted_sentiment,
                rule_tags=list(set(
                    tag for t in tweets for tag in t.get("rule_tags", [])
                )),
                cascade_flag=cascade_flag,
                political_flag=political_flag,
            )
            if signal_id:
                signal_generated = 1
                self._signals_generated += 1

        # Write window record to DB
        await self.db.execute(
            """INSERT INTO x_sentiment_windows
               (market_id, window_start, window_end, tweet_count, rejected_count,
                net_sentiment, sentiment_shift, volume_z_score, bias_risk_mean,
                authenticity_mean, reach_weighted_sentiment,
                cascade_flag, political_homogeneity_flag,
                signal_generated, signal_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                market_id,
                window_start.isoformat(),
                window_end.isoformat(),
                tweet_count,
                0,  # rejected_count tracked at tweet level
                round(net_sentiment, 4),
                round(sentiment_shift, 4),
                round(volume_z_score, 4),
                round(bias_risk_mean, 4),
                round(authenticity_mean, 4),
                round(reach_weighted_sentiment, 4),
                int(cascade_flag),
                int(political_flag),
                signal_generated,
                signal_id,
            ),
        )
        await self.db.commit()

        if signal_generated:
            self.logger.info(
                "SENTIMENT signal generated for %s: shift=%.3f, volume_z=%.2f",
                market_id, sentiment_shift, volume_z_score,
            )

    async def _generate_sentiment_signal(
        self,
        market_id: str,
        window_start: datetime,
        window_end: datetime,
        tweet_count: int,
        net_sentiment: float,
        sentiment_shift: float,
        volume_z_score: float,
        bias_risk_mean: float,
        authenticity_mean: float,
        reach_weighted_sentiment: float,
        rule_tags: list[str],
        cascade_flag: bool,
        political_flag: bool,
    ) -> int | None:
        """Write a SENTIMENT signal to the signals table.

        Returns signal ID if written, None otherwise.
        """
        # Confidence derivation (framework doc Section 6):
        # base_confidence = normalize(abs(shift), 0.15, 1.0) × 0.50
        # volume_bonus    = normalize(volume_z, 1.5, 5.0) × 0.30
        # quality_bonus   = authenticity_mean × 0.20
        base_confidence = min(abs(sentiment_shift) / 1.0, 1.0) * 0.50
        volume_bonus = min((volume_z_score - 1.5) / 3.5, 1.0) * 0.30
        quality_bonus = authenticity_mean * 0.20
        confidence = max(min(base_confidence + volume_bonus + quality_bonus, 1.0), 0.0)

        # Routing hint
        large_shift = float(self._x_config.get("routing", {}).get("large_shift_threshold", 0.30))
        if abs(sentiment_shift) >= large_shift and volume_z_score >= 2.0:
            routing_hint = "ACE_PREFERRED"
        elif abs(sentiment_shift) < large_shift:
            routing_hint = "SGE_PREFERRED"
        else:
            routing_hint = "STANDARD"

        sentiment_direction = "POSITIVE" if sentiment_shift > 0 else "NEGATIVE"

        metadata = json.dumps({
            "source": "X",
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "tweet_count": tweet_count,
            "net_sentiment": round(net_sentiment, 4),
            "sentiment_shift": round(sentiment_shift, 4),
            "sentiment_direction": sentiment_direction,
            "volume_z_score": round(volume_z_score, 4),
            "mean_bias_risk_score": round(bias_risk_mean, 4),
            "mean_authenticity_score": round(authenticity_mean, 4),
            "reach_weighted_sentiment": round(reach_weighted_sentiment, 4),
            "top_rule_tags": rule_tags[:5],
            "cascade_flag": cascade_flag,
            "political_homogeneity_flag": political_flag,
            "routing_hint": routing_hint,
        })

        # Resolve to actual market_id if this was a category-based mapping
        actual_market_id = market_id
        if market_id.startswith("x_category_"):
            # Try to find the best matching real market
            resolved = await self._resolve_category_market(market_id)
            if resolved:
                actual_market_id = resolved
            else:
                self.logger.debug("Could not resolve category %s to a market", market_id)
                return None

        reasoning = (
            f"SENTIMENT ({sentiment_direction}): shift={sentiment_shift:+.3f}, "
            f"volume_z={volume_z_score:.2f}, tweets={tweet_count}, "
            f"authenticity={authenticity_mean:.2f}, bias_risk={bias_risk_mean:.2f}"
        )

        try:
            cursor = await self.db.execute(
                """INSERT INTO signals
                   (market_id, signal_type, confidence, ev_estimate, status,
                    detection_modes_triggered, reasoning)
                   VALUES (?, 'SENTIMENT', ?, NULL, 'PENDING', ?, ?)""",
                (actual_market_id, round(confidence, 4), "X_SENTIMENT", reasoning),
            )
            await self.db.commit()
            # Get the inserted signal ID
            row = await self.db.fetchone(
                "SELECT last_insert_rowid() as id"
            )
            return int(row["id"]) if row else None
        except Exception:
            self.logger.exception("Failed to write SENTIMENT signal")
            return None

    async def _resolve_category_market(self, category_market_id: str) -> str | None:
        """Resolve a category-based market_id to a real market.

        Looks up active markets in the same category and returns
        the one with the highest breakout score.
        """
        category = category_market_id.replace("x_category_", "")

        # Try matching by category tag
        row = await self.db.fetchone(
            """SELECT id FROM markets
               WHERE status = 'active' AND category LIKE ?
               ORDER BY breakout_score DESC NULLS LAST
               LIMIT 1""",
            (f"%{category}%",),
        )
        return row["id"] if row else None

    # ═══════════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════════

    def _compute_volume_z_score(self, market_id: str, tweet_count: int) -> float:
        """Compute volume z-score relative to rolling baseline.

        Uses exponential moving average for the baseline to avoid
        needing 30 days of history on first startup.
        """
        baseline = self._volume_baselines.get(market_id)
        if not baseline:
            self._volume_baselines[market_id] = {
                "mean": float(tweet_count),
                "var": 1.0,
                "count": 1,
            }
            return 0.0

        mean = baseline["mean"]
        var = baseline["var"]
        count = baseline["count"]

        # EMA update (alpha = 0.1)
        alpha = 0.1
        new_mean = mean * (1 - alpha) + tweet_count * alpha
        new_var = var * (1 - alpha) + (tweet_count - mean) ** 2 * alpha
        baseline["mean"] = new_mean
        baseline["var"] = max(new_var, 0.01)  # Floor to avoid div by 0
        baseline["count"] = count + 1

        std = math.sqrt(baseline["var"])
        if std < 0.01:
            return 0.0

        return (tweet_count - new_mean) / std

    async def _reject_tweet(
        self, tweet: dict, reason: str,
        authenticity_score: float = 0.0, bias_risk_score: float = 0.0,
    ) -> None:
        """Log a rejected tweet to x_rejected table."""
        self._tweets_rejected += 1
        try:
            await self.db.execute(
                """INSERT INTO x_rejected
                   (tweet_id, reason_code, authenticity_score, bias_risk_score, rule_tag)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    tweet.get("id", "unknown"),
                    reason,
                    authenticity_score,
                    bias_risk_score,
                    ",".join(tweet.get("_rule_tags", [])),
                ),
            )
            # Batch commits (don't commit per-reject)
        except Exception:
            pass  # Non-critical — don't crash on audit log failure

    async def _write_monitoring_metrics(self) -> None:
        """Write monitoring metrics to system_state."""
        metrics = {
            "x_stream_connected": "1" if self._stream_mode else "0",
            "x_tweets_processed_total": str(self._tweets_processed),
            "x_tweets_rejected_total": str(self._tweets_rejected),
            "x_signals_generated_total": str(self._signals_generated),
            "x_daily_tweets_read": str(self._x_client.daily_tweets_remaining if self._x_client else 0),
        }
        for key, value in metrics.items():
            await self.db.execute(
                """INSERT OR REPLACE INTO system_state (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))""",
                (key, value),
            )
        await self.db.commit()
