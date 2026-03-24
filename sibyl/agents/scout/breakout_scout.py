"""
Breakout Scout Agent — multi-source sentiment aggregation and research synthesis.

PURPOSE:
    The Breakout Scout enriches trading signals with external context by
    aggregating sentiment data from multiple sources (Reddit, NewsAPI,
    Perplexity, Twitter).  It writes structured research packets to the
    `market_research` table, which the Signal Generator reads to boost
    or reduce confidence scores before routing.

TWO-PHASE ARCHITECTURE:

    PHASE 1 — DISCOVERY LOOP (every 15 minutes):
        Scans all active markets and ranks them by "breakout score":
            breakout_score = (volume_growth × 0.35) + (odds_velocity × 0.30)
                           + (listing_recency × 0.20) + (category_heat × 0.15)
        Markets scoring above the threshold (default: 52) enter the research queue.

    PHASE 2 — RESEARCH WORKER (triggered by discovery):
        For each queued market, runs concurrent source fetches:
            a. Reddit  — subreddit-specific search via PRAW
            b. NewsAPI — headline + description keyword search
            c. Perplexity — LLM-powered contextual analysis
            d. Twitter/X  — high-signal tweet search (≥100 likes)
        Then synthesizes all sources into a unified research packet via
        Claude Sonnet, producing:
            - sentiment_score (0.0–1.0)
            - sentiment_label (BULLISH / BEARISH / CONTESTED / NEUTRAL)
            - key_yes_args, key_no_args (JSON arrays)
            - notable_dissent
            - synthesis (human-readable summary)
            - source_breakdown (per-source scores)

FRESHNESS DECAY:
    Research packets decay in freshness every 2 hours (configurable).
    When freshness drops below 0.30 for a market with an active position,
    the scout automatically re-researches that market.

CONFIGURATION:
    config/breakout_scout_config.yaml

POLLING:
    Discovery: every 15 minutes.
    Research: triggered inline after discovery.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.scout")


class BreakoutScout(BaseAgent):
    """Discovers high-potential markets and enriches them with research."""

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="breakout_scout", db=db, config=config)
        self._scout_config: dict[str, Any] = {}

        # Discovery scoring weights
        self._weights: dict[str, float] = {}
        self._breakout_threshold: float = 52.0
        self._category_multipliers: dict[str, float] = {}

        # Research config
        self._research_config: dict[str, Any] = {}
        self._subreddits_always: list[str] = []
        self._subreddits_by_category: dict[str, list[str]] = {}

        # Freshness config
        self._freshness_decay: float = 0.15
        self._reresearch_threshold: float = 0.30

        # API clients (initialized in start())
        self._reddit = None  # praw.Reddit instance
        self._sonar_llm = None  # SonarLLMClient for research synthesis (replaces Anthropic)

    @property
    def poll_interval(self) -> float:
        """Discovery loop runs every 15 minutes (900 seconds)."""
        return float(self._scout_config.get("discovery", {}).get(
            "poll_interval_minutes", 15
        )) * 60

    async def start(self) -> None:
        """Load config and initialize API clients."""
        from sibyl.core.config import load_yaml

        try:
            self._scout_config = load_yaml("breakout_scout_config.yaml")
        except FileNotFoundError:
            self._scout_config = {}

        # ── Discovery config ─────────────────────────────────────────────
        disc = self._scout_config.get("discovery", {})
        self._weights = disc.get("score_weights", {
            "volume_growth_rate": 0.35,
            "odds_velocity": 0.30,
            "listing_recency": 0.20,
            "category_heat": 0.15,
        })
        self._breakout_threshold = float(disc.get("breakout_score_threshold", 52))
        self._category_multipliers = disc.get("category_heat_multipliers", {
            "active_election_cycle": 1.5,
            "major_sporting_event": 1.4,
            "active_fed_cycle": 1.3,
            "standard": 1.0,
        })

        # ── Research config ──────────────────────────────────────────────
        self._research_config = self._scout_config.get("research", {})
        self._subreddits_always = self._research_config.get("reddit_subreddits_always", [])
        self._subreddits_by_category = self._research_config.get(
            "reddit_subreddits_by_category", {}
        )

        # ── Freshness config ─────────────────────────────────────────────
        fresh = self._scout_config.get("freshness", {})
        self._freshness_decay = float(fresh.get("decay_amount_per_cycle", 0.15))
        self._reresearch_threshold = float(fresh.get(
            "active_position_reresearch_threshold", 0.30
        ))

        # ── Initialize Reddit client ─────────────────────────────────────
        reddit_id = os.environ.get("REDDIT_CLIENT_ID")
        reddit_secret = os.environ.get("REDDIT_CLIENT_SECRET")
        reddit_ua = os.environ.get("REDDIT_USER_AGENT", "sibyl-bot/0.1")
        if reddit_id and reddit_secret:
            try:
                import praw
                self._reddit = praw.Reddit(
                    client_id=reddit_id,
                    client_secret=reddit_secret,
                    user_agent=reddit_ua,
                )
                self.logger.info("Reddit client initialized")
            except Exception:
                self.logger.warning("Failed to initialize Reddit client")

        # ── Initialize Sonar LLM client (replaces Anthropic for synthesis) ──
        try:
            from sibyl.clients.sonar_llm_client import SonarLLMClient

            sonar = SonarLLMClient()
            if sonar.initialize():
                self._sonar_llm = sonar
                self.logger.info("Sonar LLM client initialized for research synthesis")
            else:
                self.logger.info("Sonar LLM unavailable (no PERPLEXITY_API_KEY)")
        except Exception:
            self.logger.warning("Failed to initialize Sonar LLM client")

        # ── Initialize Perplexity client ──────────────────────────────────
        self._perplexity = None
        try:
            from sibyl.clients.perplexity_client import PerplexityClient

            pplx = PerplexityClient()
            # Apply config overrides
            pplx_cfg = self._research_config.get("perplexity", {})
            pplx._daily_call_cap = int(pplx_cfg.get("daily_call_cap", 30))
            pplx._max_tokens = int(pplx_cfg.get("max_tokens", 300))
            pplx._model = pplx_cfg.get("model", "sonar")

            if pplx.initialize():
                self._perplexity = pplx
                self.logger.info(
                    "Perplexity client initialized (model=%s, cap=%d/day)",
                    pplx._model, pplx._daily_call_cap,
                )
            else:
                self.logger.info("Perplexity unavailable (no API key)")
        except Exception:
            self.logger.warning("Failed to initialize Perplexity client")

        self.logger.info(
            "Breakout Scout started (threshold=%.0f, reddit=%s, perplexity=%s, sonar_llm=%s)",
            self._breakout_threshold,
            "ready" if self._reddit else "unavailable",
            "ready" if self._perplexity else "unavailable",
            "ready" if self._sonar_llm else "unavailable",
        )

    async def run_cycle(self) -> None:
        """Run discovery + research pipeline."""
        # ── Phase 0: Freshness decay ─────────────────────────────────────
        await self._decay_freshness()

        # ── Phase 1: Discovery ───────────────────────────────────────────
        candidates = await self._discover_breakout_markets()

        # ── Phase 2: Research (for each candidate) ───────────────────────
        for market_id, title, category in candidates:
            await self._research_market(market_id, title, category or "other")

        # ── Phase 3: Re-research stale active positions ──────────────────
        await self._reresearch_stale_positions()

    async def stop(self) -> None:
        if self._sonar_llm:
            await self._sonar_llm.close()
        if self._perplexity:
            await self._perplexity.close()
        self.logger.info("Breakout Scout stopped")

    # ── Phase 1: Discovery ─────────────────────────────────────────────

    async def _discover_breakout_markets(self) -> list[tuple[str, str, str | None]]:
        """Scan active markets and rank by breakout score.

        Breakout score formula:
            score = Σ(weight_i × normalized_metric_i) × category_multiplier × 100

        Metrics:
            - volume_growth_rate: 24h volume change vs 7-day avg
            - odds_velocity:     price change speed (Δprice/Δtime)
            - listing_recency:   how new the market is (newer = higher)
            - category_heat:     multiplier based on current event cycles

        Returns:
            List of (market_id, title, category) tuples for markets scoring
            above the breakout threshold.
        """
        markets = await self.db.fetchall(
            "SELECT id, title, category, created_at FROM markets WHERE status = 'active'"
        )

        candidates = []
        for m in markets:
            market_id = m["id"]
            title = m["title"]
            category = m["category"]

            # ── Volume growth rate ───────────────────────────────────────
            vol_row = await self.db.fetchone(
                """SELECT
                     (SELECT COALESCE(AVG(volume_24h), 0) FROM prices
                      WHERE market_id = ? AND timestamp >= datetime('now', '-1 day'))
                     as recent_vol,
                     (SELECT COALESCE(AVG(volume_24h), 1) FROM prices
                      WHERE market_id = ? AND timestamp >= datetime('now', '-7 days'))
                     as avg_vol""",
                (market_id, market_id),
            )
            recent_vol = float(vol_row["recent_vol"]) if vol_row else 0
            avg_vol = float(vol_row["avg_vol"]) if vol_row else 1
            vol_growth = min((recent_vol / max(avg_vol, 1)) - 1.0, 2.0)  # Cap at 2x
            vol_growth = max(vol_growth, 0.0)  # Floor at 0

            # ── Odds velocity ────────────────────────────────────────────
            vel_rows = await self.db.fetchall(
                """SELECT yes_price, timestamp FROM prices
                   WHERE market_id = ? ORDER BY timestamp DESC LIMIT 10""",
                (market_id,),
            )
            if len(vel_rows) >= 2:
                newest = float(vel_rows[0]["yes_price"])
                oldest = float(vel_rows[-1]["yes_price"])
                odds_velocity = min(abs(newest - oldest), 0.3)  # Cap at 0.30
            else:
                odds_velocity = 0.0

            # ── Listing recency ──────────────────────────────────────────
            # Normalize: markets created in last 24h get 1.0, 7+ days get 0.0
            recency_row = await self.db.fetchone(
                """SELECT (julianday('now') - julianday(created_at)) as age_days
                   FROM markets WHERE id = ?""",
                (market_id,),
            )
            age_days = float(recency_row["age_days"]) if recency_row else 30
            listing_recency = max(1.0 - (age_days / 7.0), 0.0)

            # ── Category heat multiplier ─────────────────────────────────
            heat = self._category_multipliers.get(category or "standard", 1.0)

            # ── Compute breakout score ───────────────────────────────────
            w = self._weights
            raw_score = (
                w.get("volume_growth_rate", 0.35) * vol_growth
                + w.get("odds_velocity", 0.30) * (odds_velocity / 0.30)
                + w.get("listing_recency", 0.20) * listing_recency
                + w.get("category_heat", 0.15) * (heat / 1.5)
            )
            score = raw_score * heat * 100

            # ── Update market record ─────────────────────────────────────
            await self.db.execute(
                "UPDATE markets SET breakout_score = ?, updated_at = datetime('now') WHERE id = ?",
                (round(score, 2), market_id),
            )

            if score >= self._breakout_threshold:
                # Check if we already have fresh research
                existing = await self.db.fetchone(
                    """SELECT freshness_score FROM market_research
                       WHERE market_id = ? ORDER BY timestamp DESC LIMIT 1""",
                    (market_id,),
                )
                if not existing or float(existing["freshness_score"]) < 0.50:
                    candidates.append((market_id, title, category))

        await self.db.commit()

        if candidates:
            self.logger.info("Discovery found %d breakout candidates", len(candidates))
        return candidates

    # ── Phase 2: Research ──────────────────────────────────────────────

    async def _research_market(
        self, market_id: str, title: str, category: str
    ) -> None:
        """Run multi-source research on a single market.

        Fetches sentiment data from available sources, then synthesizes
        into a unified research packet via Claude LLM.
        """
        self.logger.info("Researching: %s", title)

        source_data: dict[str, Any] = {}

        # ── Reddit ───────────────────────────────────────────────────────
        reddit_sentiment = await self._fetch_reddit_sentiment(title, category)
        if reddit_sentiment:
            source_data["reddit"] = reddit_sentiment

        # ── NewsAPI ──────────────────────────────────────────────────────
        news_sentiment = await self._fetch_news_sentiment(title)
        if news_sentiment:
            source_data["newsapi"] = news_sentiment

        # ── Perplexity (contextual web research) ──────────────────────────
        perplexity_data = await self._fetch_perplexity_research(title, category)
        if perplexity_data:
            source_data["perplexity"] = perplexity_data

        if not source_data:
            self.logger.debug("No source data for %s — skipping synthesis", market_id)
            return

        # ── LLM Synthesis ────────────────────────────────────────────────
        research_packet = await self._synthesize_research(market_id, title, source_data)
        if not research_packet:
            return

        # ── Write to database ────────────────────────────────────────────
        await self.db.execute(
            """INSERT INTO market_research
               (market_id, sentiment_score, sentiment_label, confidence,
                source_breakdown, key_yes_args, key_no_args, notable_dissent,
                synthesis, freshness_score, routing_priority)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?)""",
            (
                market_id,
                research_packet.get("sentiment_score", 0.5),
                research_packet.get("sentiment_label", "NEUTRAL"),
                research_packet.get("confidence", 0.5),
                json.dumps(research_packet.get("source_breakdown", {})),
                json.dumps(research_packet.get("key_yes_args", [])),
                json.dumps(research_packet.get("key_no_args", [])),
                research_packet.get("notable_dissent", ""),
                research_packet.get("synthesis", ""),
                research_packet.get("routing_priority", "STANDARD"),
            ),
        )
        await self.db.commit()

        self.logger.info(
            "Research complete: %s → %s (%.0f%% confidence)",
            title,
            research_packet.get("sentiment_label", "?"),
            research_packet.get("confidence", 0) * 100,
        )

    async def _fetch_reddit_sentiment(
        self, title: str, category: str
    ) -> dict[str, Any] | None:
        """Search Reddit for market-relevant discussions.

        Uses PRAW to search subreddits relevant to the market's category.
        Extracts post titles, scores, and comment counts to gauge sentiment.

        Returns:
            Dict with "score" (0.0–1.0), "posts_found", "top_titles",
            or None if Reddit is unavailable.
        """
        if not self._reddit:
            return None

        try:
            # Build subreddit list: always-search + category-specific
            subs = list(self._subreddits_always)
            subs.extend(self._subreddits_by_category.get(category, []))
            subs.extend(self._subreddits_by_category.get("other", []))

            if not subs:
                return None

            # Search across all relevant subreddits
            search_query = title[:100]  # Truncate for search API
            posts = []

            for sub_name in subs[:5]:  # Limit to 5 subreddits per search
                try:
                    sub = self._reddit.subreddit(sub_name)
                    for post in sub.search(search_query, limit=5, time_filter="week"):
                        posts.append({
                            "title": post.title,
                            "score": post.score,
                            "comments": post.num_comments,
                            "subreddit": sub_name,
                        })
                except Exception:
                    continue  # Skip inaccessible subreddits

            if not posts:
                return None

            # Simple sentiment heuristic: higher engagement = more noteworthy
            total_score = sum(p["score"] for p in posts)
            avg_score = total_score / len(posts) if posts else 0

            # Normalize to 0.0–1.0 range (logarithmic scale for Reddit scores)
            import math
            normalized = min(math.log1p(avg_score) / 10.0, 1.0)

            return {
                "score": round(normalized, 3),
                "posts_found": len(posts),
                "top_titles": [p["title"] for p in sorted(
                    posts, key=lambda x: x["score"], reverse=True
                )[:3]],
            }

        except Exception:
            self.logger.exception("Reddit fetch failed for: %s", title)
            return None

    async def _fetch_news_sentiment(self, title: str) -> dict[str, Any] | None:
        """Fetch news sentiment using Perplexity Sonar as a web-grounded news source.

        Perplexity Sonar replaces NewsAPI — it searches the web in real-time
        and returns grounded news summaries with citations.  This eliminates
        the need for a separate NEWSAPI_KEY.

        Returns:
            Dict with "score" (0.0–1.0), "summary", "citations",
            or None if Perplexity is unavailable.
        """
        if not self._perplexity:
            return None

        try:
            # Use Perplexity's web-grounded research for news context
            result = await self._perplexity.research_market(
                title,
                context="Find recent news headlines and developments. Focus on facts.",
            )
            if not result:
                return None

            return {
                "score": result.get("score", 0.5),
                "summary": result.get("summary", ""),
                "citations": result.get("citations", []),
            }

        except Exception:
            self.logger.exception("News fetch via Perplexity failed for: %s", title)
            return None

    async def _fetch_perplexity_research(
        self, title: str, category: str
    ) -> dict[str, Any] | None:
        """Fetch contextual web research from Perplexity Sonar API.

        Perplexity provides grounded, citation-backed analysis that
        complements Reddit and NewsAPI with deeper context.  Calls are
        budget-capped (default 30/day ≈ $0.015/day at Sonar pricing).

        Returns:
            Dict with "score" (0.0–1.0), "summary", "key_factors",
            "citations", or None if Perplexity is unavailable.
        """
        if not self._perplexity:
            return None

        # Build context string for Perplexity
        context_parts = [f"Category: {category}"]

        # Add current odds if available
        price_row = await self.db.fetchone(
            """SELECT yes_price FROM prices
               WHERE market_id IN (SELECT id FROM markets WHERE title = ?)
               ORDER BY timestamp DESC LIMIT 1""",
            (title,),
        )
        if price_row:
            context_parts.append(f"Current odds: {float(price_row['yes_price']):.0%} YES")

        context = "; ".join(context_parts)

        result = await self._perplexity.research_market(title, context=context)
        if not result:
            return None

        return {
            "score": result.get("score", 0.5),
            "summary": result.get("summary", ""),
            "key_factors": result.get("key_factors", []),
            "citations": result.get("citations", []),
        }

    async def _synthesize_research(
        self,
        market_id: str,
        title: str,
        source_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Synthesize multi-source data into a unified research packet via Sonar LLM.

        Sends all source data to Perplexity Sonar with a structured prompt
        requesting JSON output with sentiment analysis.  Falls back to simple
        averaging if the LLM is unavailable.

        Returns:
            Research packet dict, or None if LLM is unavailable.
        """
        if not self._sonar_llm:
            # Fallback: compute a simple average without LLM
            return self._fallback_synthesis(source_data)

        prompt = f"""You are a prediction market research analyst. Analyze the following data
about the market "{title}" and produce a structured sentiment assessment.

SOURCE DATA:
{json.dumps(source_data, indent=2)}

Respond with ONLY valid JSON (no markdown, no explanation) matching this schema:
{{
    "sentiment_score": <float 0.0-1.0, where 0=strongly NO, 1=strongly YES>,
    "sentiment_label": "<BULLISH|BEARISH|CONTESTED|NEUTRAL>",
    "confidence": <float 0.0-1.0, your confidence in this assessment>,
    "key_yes_args": ["<top 2-3 arguments favoring YES>"],
    "key_no_args": ["<top 2-3 arguments favoring NO>"],
    "notable_dissent": "<any significant contrarian view, or empty string>",
    "synthesis": "<2-3 sentence summary of the overall sentiment landscape>",
    "routing_priority": "<HIGH|STANDARD|LOW>"
}}"""

        try:
            text = await self._sonar_llm.synthesize_research(prompt)
            if not text:
                self.logger.warning("Sonar returned empty for %s — using fallback", market_id)
                return self._fallback_synthesis(source_data)

            # Parse JSON from response (handle potential markdown wrapping)
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()

            packet = json.loads(text)

            # Add source breakdown
            packet["source_breakdown"] = {
                src: data.get("score", 0.5) for src, data in source_data.items()
            }

            return packet

        except json.JSONDecodeError:
            self.logger.warning("Sonar returned invalid JSON for %s — using fallback", market_id)
            return self._fallback_synthesis(source_data)
        except Exception:
            self.logger.exception("Sonar synthesis failed for %s", market_id)
            return self._fallback_synthesis(source_data)

    @staticmethod
    def _fallback_synthesis(source_data: dict[str, Any]) -> dict[str, Any]:
        """Simple fallback synthesis when LLM is unavailable.

        Averages source scores and classifies as NEUTRAL with low confidence.
        """
        scores = [d.get("score", 0.5) for d in source_data.values()]
        avg = sum(scores) / len(scores) if scores else 0.5

        if avg > 0.65:
            label = "BULLISH"
        elif avg < 0.35:
            label = "BEARISH"
        elif 0.45 <= avg <= 0.55:
            label = "NEUTRAL"
        else:
            label = "CONTESTED"

        return {
            "sentiment_score": round(avg, 3),
            "sentiment_label": label,
            "confidence": 0.35,  # Low confidence without LLM
            "source_breakdown": {src: d.get("score", 0.5) for src, d in source_data.items()},
            "key_yes_args": [],
            "key_no_args": [],
            "notable_dissent": "",
            "synthesis": f"Automated aggregate from {len(source_data)} sources (no LLM).",
            "routing_priority": "STANDARD",
        }

    # ── Freshness Decay ────────────────────────────────────────────────

    async def _decay_freshness(self) -> None:
        """Reduce freshness_score for all research packets.

        Called each cycle.  Freshness decays linearly toward 0.0.
        When it reaches 0, the research is considered stale.
        """
        await self.db.execute(
            """UPDATE market_research
               SET freshness_score = MAX(freshness_score - ?, 0.0)
               WHERE freshness_score > 0""",
            (self._freshness_decay,),
        )
        await self.db.commit()

    async def _reresearch_stale_positions(self) -> None:
        """Re-research markets with active positions and stale research.

        If a market has an OPEN position but its latest research freshness
        has dropped below the threshold, queue it for re-research.
        """
        stale = await self.db.fetchall(
            """SELECT DISTINCT p.market_id, m.title, m.category
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               LEFT JOIN market_research mr ON p.market_id = mr.market_id
               WHERE p.status = 'OPEN'
               GROUP BY p.market_id
               HAVING COALESCE(MAX(mr.freshness_score), 0) < ?""",
            (self._reresearch_threshold,),
        )

        for row in stale:
            await self._research_market(row["market_id"], row["title"], row["category"] or "other")
