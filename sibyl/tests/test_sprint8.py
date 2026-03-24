"""
Tests for Sprint 8 — X Sentiment Agent + Perplexity Integration.

Tests:
    - XSentimentAgent: sentiment scoring, radicalism detection, authenticity scoring,
      bias risk, search query building, market mapping, volume z-score,
      window aggregation, signal generation thresholds
    - XClient: rate limit tracking, daily budget
    - PerplexityClient: response parsing, sentiment hint scoring, daily cap
    - BreakoutScout Perplexity integration: source data merging
"""

import asyncio
import json
import math
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def db(event_loop):
    from sibyl.core.database import DatabaseManager

    async def _setup():
        db = DatabaseManager(":memory:")
        await db.initialize()
        return db

    return event_loop.run_until_complete(_setup())


@pytest.fixture
def config():
    return {
        "polling": {
            "price_snapshot_interval_seconds": 5,
            "position_sync_interval_seconds": 15,
        },
        "platforms": {
            "polymarket": {"rate_limit_per_second": 80},
            "kalshi": {"rate_limit_per_second": 8},
        },
        "cross_platform": {
            "similarity_threshold": 0.55,
            "price_divergence_alert_pct": 0.05,
        },
        "notifications": {
            "enabled": True,
            "channel": "ntfy",
            "ntfy_server": "https://ntfy.sh",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# XSentimentAgent — Sentiment Scoring
# ═══════════════════════════════════════════════════════════════════════════


def test_sentiment_positive_keywords():
    """Positive keywords produce positive sentiment score."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._auth_config = {}
    score = agent._score_sentiment("This stock is bullish and will rally hard")
    assert score > 0.0, f"Expected positive, got {score}"


def test_sentiment_negative_keywords():
    """Negative keywords produce negative sentiment score."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._auth_config = {}
    score = agent._score_sentiment("Markets are bearish, expect a crash and collapse")
    assert score < 0.0, f"Expected negative, got {score}"


def test_sentiment_neutral_text():
    """Text with no sentiment keywords returns 0.0."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._auth_config = {}
    score = agent._score_sentiment("The weather today is cloudy in Seattle")
    assert score == 0.0


def test_sentiment_irony_dampening():
    """Irony markers dampen/invert sentiment."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._auth_config = {}
    # Without irony
    normal = agent._score_sentiment("Bullish rally incoming")
    # With irony
    ironic = agent._score_sentiment("Bullish rally incoming lol")
    assert abs(ironic) < abs(normal), "Irony should dampen sentiment magnitude"


def test_sentiment_mixed_keywords():
    """Mixed positive/negative keywords produce partial score."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._auth_config = {}
    # 2 positive (bullish, growth), 1 negative (risk)
    score = agent._score_sentiment("Bullish growth despite risk")
    assert score > 0.0, "Net positive should win"


# ═══════════════════════════════════════════════════════════════════════════
# XSentimentAgent — Radicalism Detection
# ═══════════════════════════════════════════════════════════════════════════


def test_radicalism_detection_positive():
    """Known radical patterns are detected."""
    import re
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent, RADICAL_PATTERNS_DEFAULT
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._radical_patterns = [re.compile(p, re.IGNORECASE) for p in RADICAL_PATTERNS_DEFAULT]
    assert agent._check_radicalism("There will be a civil war soon") is True


def test_radicalism_detection_negative():
    """Normal text does not trigger radicalism filter."""
    import re
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent, RADICAL_PATTERNS_DEFAULT
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._radical_patterns = [re.compile(p, re.IGNORECASE) for p in RADICAL_PATTERNS_DEFAULT]
    assert agent._check_radicalism("The Fed will likely cut rates next month") is False


# ═══════════════════════════════════════════════════════════════════════════
# XSentimentAgent — Authenticity Scoring
# ═══════════════════════════════════════════════════════════════════════════


def test_authenticity_established_account():
    """Established accounts get high authenticity."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._auth_config = {
        "min_account_age_days": 90,
        "min_followers": 20,
        "min_tweet_history": 50,
        "max_following_to_followers_ratio": 25.0,
    }

    old_date = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    tweet = {
        "_author": {
            "created_at": old_date,
            "public_metrics": {
                "followers_count": 500,
                "following_count": 200,
                "tweet_count": 1000,
            },
        }
    }
    score = agent._compute_authenticity(tweet)
    assert score >= 0.80, f"Established account should score high, got {score}"


def test_authenticity_new_bot_account():
    """New account with bot-like behavior gets low authenticity."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._auth_config = {
        "min_account_age_days": 90,
        "min_followers": 20,
        "min_tweet_history": 50,
        "max_following_to_followers_ratio": 25.0,
    }

    recent_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    tweet = {
        "_author": {
            "created_at": recent_date,
            "public_metrics": {
                "followers_count": 3,
                "following_count": 2000,
                "tweet_count": 10,
            },
        }
    }
    score = agent._compute_authenticity(tweet)
    assert score <= 0.25, f"Bot-like account should score low, got {score}"


def test_authenticity_no_author_data():
    """Missing author data gets moderate score."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._auth_config = {}
    tweet = {}
    score = agent._compute_authenticity(tweet)
    assert score == 0.50


# ═══════════════════════════════════════════════════════════════════════════
# XSentimentAgent — Bias Risk
# ═══════════════════════════════════════════════════════════════════════════


def test_bias_extreme_sentiment():
    """Extreme sentiment text triggers bias flag."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._auth_config = {}
    # All positive keywords, no negative → raw_score = 1.0, abs > 0.90
    tweet = {"text": "bullish rally surge soar moon breakout win winning"}
    score = agent._compute_bias_risk(tweet)
    assert score > 0.0, f"Extreme sentiment should trigger bias, got {score}"


def test_bias_normal_text():
    """Normal text has zero or low bias."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._auth_config = {}
    tweet = {"text": "The weather is nice today"}
    score = agent._compute_bias_risk(tweet)
    assert score == 0.0


def test_bias_sensitive_content():
    """Possibly sensitive tweets get extra bias."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._auth_config = {}
    tweet = {"text": "Some normal text", "possibly_sensitive": True}
    score = agent._compute_bias_risk(tweet)
    assert score >= 0.10


# ═══════════════════════════════════════════════════════════════════════════
# XSentimentAgent — Search Query Building
# ═══════════════════════════════════════════════════════════════════════════


def test_build_search_query():
    """Builds a valid search query from market title."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    query = XSentimentAgent._build_search_query("Will the Fed cut interest rates in June 2026?")
    assert "lang:en" in query
    assert "-is:retweet" in query
    assert "Fed" in query
    assert "will" not in query.lower().split("(")[1]  # stop words removed


def test_build_search_query_empty():
    """Empty or all-stopword titles return empty string."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    query = XSentimentAgent._build_search_query("Will the a to in on")
    assert query == ""


# ═══════════════════════════════════════════════════════════════════════════
# XSentimentAgent — Market Mapping
# ═══════════════════════════════════════════════════════════════════════════


def test_map_to_market_search():
    """Search polling tweets get direct market mapping."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    tweet = {"_search_market_id": "mkt_123"}
    assert agent._map_to_market(tweet) == "mkt_123"


def test_map_to_market_stream():
    """Stream tweets map via rule tags."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    tweet = {"_rule_tags": ["polymarket_direct", "macro_signals"]}
    result = agent._map_to_market(tweet)
    assert result == "x_category_polymarket_direct"


def test_map_to_market_unmapped():
    """Tweets with no mapping info return None."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    tweet = {}
    assert agent._map_to_market(tweet) is None


# ═══════════════════════════════════════════════════════════════════════════
# XSentimentAgent — Volume Z-Score
# ═══════════════════════════════════════════════════════════════════════════


def test_volume_z_score_first_window():
    """First window returns 0.0 (no baseline yet)."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._volume_baselines = {}
    z = agent._compute_volume_z_score("mkt_1", 20)
    assert z == 0.0
    assert "mkt_1" in agent._volume_baselines


def test_volume_z_score_spike():
    """Large volume spike produces positive z-score."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._volume_baselines = {}
    # Establish baseline with low volume
    agent._compute_volume_z_score("mkt_1", 5)
    agent._compute_volume_z_score("mkt_1", 6)
    agent._compute_volume_z_score("mkt_1", 5)
    agent._compute_volume_z_score("mkt_1", 4)
    # Now a big spike
    z = agent._compute_volume_z_score("mkt_1", 50)
    assert z > 1.0, f"Volume spike should produce high z-score, got {z}"


# ═══════════════════════════════════════════════════════════════════════════
# XSentimentAgent — Ingestion Deduplication
# ═══════════════════════════════════════════════════════════════════════════


def test_ingestion_dedup():
    """Duplicate tweet IDs are rejected."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._seen_ids = set()
    agent._tweet_buffer = []
    agent._max_buffer_size = 2000

    agent._ingest_tweet({"id": "t1", "text": "hello"})
    agent._ingest_tweet({"id": "t1", "text": "hello"})  # duplicate
    agent._ingest_tweet({"id": "t2", "text": "world"})
    assert len(agent._tweet_buffer) == 2


def test_ingestion_ring_buffer():
    """Buffer evicts oldest when full."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    agent = XSentimentAgent.__new__(XSentimentAgent)
    agent._seen_ids = set()
    agent._tweet_buffer = []
    agent._max_buffer_size = 5

    for i in range(7):
        agent._ingest_tweet({"id": f"t{i}", "text": f"tweet {i}"})

    assert len(agent._tweet_buffer) == 5
    assert agent._tweet_buffer[0]["id"] == "t2"  # oldest surviving


# ═══════════════════════════════════════════════════════════════════════════
# XSentimentAgent — Window Close & Signal Generation (DB integration)
# ═══════════════════════════════════════════════════════════════════════════


def test_close_window_below_threshold(db, config, event_loop):
    """Window with too few tweets does not generate signal."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent

    agent = XSentimentAgent(db=db, config=config)
    agent._x_config = {"poll_interval_seconds": 300}
    agent._sentiment_config = {
        "aggregation_window_minutes": 5,
        "min_tweets_per_window": 10,
        "sentiment_shift_threshold": 0.15,
        "volume_z_score_threshold": 1.5,
        "bias_risk_ceiling": 0.60,
        "authenticity_floor_pct": 0.70,
    }
    agent._bias_config = {
        "cascade_diversity_threshold": 0.30,
        "cascade_penalty": 0.25,
        "source_concentration_threshold": 0.50,
        "source_concentration_penalty": 0.20,
        "political_homogeneity_threshold": 0.60,
        "political_homogeneity_penalty": 0.30,
    }
    agent._volume_baselines = {}
    agent._previous_net_sentiment = {}

    # Only 3 tweets — below min_tweets threshold of 10
    now = datetime.now(timezone.utc)
    agent._window_start["test_mkt"] = now - timedelta(minutes=6)
    agent._windows["test_mkt"] = [
        {
            "tweet_id": f"t{i}", "text": "bullish",
            "author_id": f"a{i}", "sentiment_score": 0.5,
            "weighted_sentiment": 0.5, "reach_weight": 1.0,
            "authenticity_score": 0.9, "bias_risk_score": 0.0,
            "impression_count": 100, "conversation_id": f"c{i}",
            "rule_tags": ["test"], "timestamp": now.isoformat(),
        }
        for i in range(3)
    ]

    async def _run():
        await agent._close_window("test_mkt")
        # Check no signal was generated
        row = await db.fetchone("SELECT COUNT(*) as cnt FROM signals WHERE signal_type = 'SENTIMENT'")
        assert row["cnt"] == 0

    event_loop.run_until_complete(_run())


def test_close_window_writes_record(db, config, event_loop):
    """Window close always writes a window record to x_sentiment_windows."""
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent

    agent = XSentimentAgent(db=db, config=config)
    agent._x_config = {"poll_interval_seconds": 300, "routing": {"large_shift_threshold": 0.30}}
    agent._sentiment_config = {
        "min_tweets_per_window": 3,  # lowered for test
        "sentiment_shift_threshold": 0.01,
        "volume_z_score_threshold": 0.0,
        "bias_risk_ceiling": 1.0,
        "authenticity_floor_pct": 0.0,
    }
    agent._bias_config = {
        "cascade_diversity_threshold": 0.30,
        "cascade_penalty": 0.25,
        "source_concentration_threshold": 0.50,
        "source_concentration_penalty": 0.20,
        "political_homogeneity_threshold": 0.60,
        "political_homogeneity_penalty": 0.30,
    }
    agent._volume_baselines = {}
    agent._previous_net_sentiment = {"test_mkt": -0.5}  # So shift is large

    now = datetime.now(timezone.utc)
    agent._window_start["test_mkt"] = now - timedelta(minutes=6)
    agent._windows["test_mkt"] = [
        {
            "tweet_id": f"t{i}", "text": "bullish rally surge",
            "author_id": f"a{i}", "sentiment_score": 0.8,
            "weighted_sentiment": 3.2, "reach_weight": 4.0,
            "authenticity_score": 0.9, "bias_risk_score": 0.0,
            "impression_count": 500, "conversation_id": f"c{i}",
            "rule_tags": ["test"], "timestamp": now.isoformat(),
        }
        for i in range(5)
    ]

    async def _run():
        await agent._close_window("test_mkt")
        row = await db.fetchone("SELECT COUNT(*) as cnt FROM x_sentiment_windows")
        assert row["cnt"] == 1, "Window record should be written"

    event_loop.run_until_complete(_run())


# ═══════════════════════════════════════════════════════════════════════════
# XClient — Rate Limit & Budget
# ═══════════════════════════════════════════════════════════════════════════


def test_xclient_rate_limit_tracking():
    """Rate limit headers are parsed correctly."""
    from sibyl.clients.x_client import XClient
    import httpx
    client = XClient()
    headers = httpx.Headers({"x-rate-limit-remaining": "15", "x-rate-limit-reset": "1700000000"})
    client._update_rate_limits(headers)
    assert client._rate_limit_remaining == 15
    assert client._rate_limit_reset == 1700000000.0


def test_xclient_daily_budget():
    """Daily budget tracking counts tweets correctly."""
    from sibyl.clients.x_client import XClient
    client = XClient()
    client._daily_tweets_read = 0
    client._track_daily_budget(50)
    assert client._daily_tweets_read == 50
    assert client.daily_tweets_remaining == 250


def test_xclient_daily_budget_exhausted():
    """Daily budget at limit returns 0 remaining."""
    from sibyl.clients.x_client import XClient
    client = XClient()
    client._daily_tweets_read = 300
    assert client.daily_tweets_remaining == 0


# ═══════════════════════════════════════════════════════════════════════════
# PerplexityClient — Response Parsing
# ═══════════════════════════════════════════════════════════════════════════


def test_perplexity_parse_bullish():
    """Perplexity response with BULLISH hint is parsed correctly."""
    from sibyl.clients.perplexity_client import PerplexityClient
    result = PerplexityClient._parse_research_response(
        "The market sentiment is strongly BULLISH. Experts agree rates will be cut.\n"
        "1) Federal Reserve dovish signals\n"
        "2) Inflation cooling rapidly\n"
        "3) Employment data weakening",
        citations=["https://reuters.com/fed-analysis"]
    )
    assert result["sentiment_hint"] == "BULLISH"
    assert result["score"] == 0.72
    assert len(result["key_factors"]) >= 2
    assert len(result["citations"]) == 1


def test_perplexity_parse_bearish():
    """Perplexity response with BEARISH hint is parsed correctly."""
    from sibyl.clients.perplexity_client import PerplexityClient
    result = PerplexityClient._parse_research_response(
        "Outlook is BEARISH given rising inflation.",
        citations=[]
    )
    assert result["sentiment_hint"] == "BEARISH"
    assert result["score"] == 0.28


def test_perplexity_parse_neutral():
    """Perplexity response with no clear hint defaults to NEUTRAL."""
    from sibyl.clients.perplexity_client import PerplexityClient
    result = PerplexityClient._parse_research_response(
        "The situation is complex with many factors at play.",
        citations=[]
    )
    assert result["sentiment_hint"] == "NEUTRAL"
    assert result["score"] == 0.50


def test_perplexity_daily_cap():
    """Client respects daily call cap."""
    from sibyl.clients.perplexity_client import PerplexityClient
    client = PerplexityClient()
    client._daily_call_cap = 5
    client._calls_today = 5
    assert client.calls_remaining_today == 0


def test_perplexity_sentiment_hint_scores():
    """All sentiment hints map to expected scores."""
    from sibyl.clients.perplexity_client import _sentiment_hint_to_score
    assert _sentiment_hint_to_score("BULLISH") == 0.72
    assert _sentiment_hint_to_score("BEARISH") == 0.28
    assert _sentiment_hint_to_score("CONTESTED") == 0.50
    assert _sentiment_hint_to_score("NEUTRAL") == 0.50
    assert _sentiment_hint_to_score("UNKNOWN") == 0.50


# ═══════════════════════════════════════════════════════════════════════════
# BreakoutScout — Perplexity Integration
# ═══════════════════════════════════════════════════════════════════════════


def test_scout_fallback_synthesis_with_perplexity():
    """Fallback synthesis includes Perplexity data when present."""
    from sibyl.agents.scout.breakout_scout import BreakoutScout
    source_data = {
        "reddit": {"score": 0.6},
        "perplexity": {"score": 0.72, "summary": "BULLISH analysis"},
    }
    result = BreakoutScout._fallback_synthesis(source_data)
    assert result["sentiment_score"] > 0.5  # Avg of 0.6 and 0.72 = 0.66 → BULLISH
    assert result["sentiment_label"] == "BULLISH"
    assert "perplexity" in result["source_breakdown"]


def test_scout_fallback_synthesis_perplexity_only():
    """Fallback synthesis works with only Perplexity data."""
    from sibyl.agents.scout.breakout_scout import BreakoutScout
    source_data = {
        "perplexity": {"score": 0.28},
    }
    result = BreakoutScout._fallback_synthesis(source_data)
    assert result["sentiment_label"] == "BEARISH"
    assert result["source_breakdown"]["perplexity"] == 0.28


# ═══════════════════════════════════════════════════════════════════════════
# Database Schema — New Tables Exist
# ═══════════════════════════════════════════════════════════════════════════


def test_x_tables_exist(db, event_loop):
    """All 5 X sentiment tables are created by database.initialize()."""
    expected_tables = [
        "x_raw", "x_rejected", "x_sentiment_windows",
        "x_author_cache", "x_blocklist",
    ]

    async def _check():
        for table in expected_tables:
            row = await db.fetchone(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            assert row is not None, f"Table '{table}' should exist"

    event_loop.run_until_complete(_check())


def test_x_raw_insert(db, event_loop):
    """Can insert a tweet into x_raw."""
    async def _run():
        await db.execute(
            """INSERT INTO x_raw (tweet_id, author_id, text, created_at,
               retweet_count, reply_count, like_count, quote_count, impression_count,
               rule_tag)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("t_123", "a_456", "Test tweet", "2026-03-18T12:00:00Z",
             5, 2, 10, 1, 500, "polymarket_direct"),
        )
        await db.commit()
        row = await db.fetchone("SELECT * FROM x_raw WHERE tweet_id = 't_123'")
        assert row is not None
        assert row["text"] == "Test tweet"

    event_loop.run_until_complete(_run())


def test_x_sentiment_window_insert(db, event_loop):
    """Can insert a window record into x_sentiment_windows."""
    async def _run():
        await db.execute(
            """INSERT INTO x_sentiment_windows
               (market_id, window_start, window_end, tweet_count, rejected_count,
                net_sentiment, sentiment_shift, volume_z_score, bias_risk_mean,
                authenticity_mean, reach_weighted_sentiment,
                cascade_flag, political_homogeneity_flag, signal_generated, signal_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("mkt_1", "2026-03-18T12:00:00", "2026-03-18T12:05:00",
             25, 3, 0.35, 0.20, 2.1, 0.15, 0.85, 0.42, 0, 0, 1, 42),
        )
        await db.commit()
        row = await db.fetchone("SELECT * FROM x_sentiment_windows WHERE market_id = 'mkt_1'")
        assert row is not None
        assert row["tweet_count"] == 25
        assert float(row["net_sentiment"]) == 0.35

    event_loop.run_until_complete(_run())
