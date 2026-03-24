---
project: Sibyl — Prediction Market Tracker & Autonomous Investing Agent
type: reference
status: active
started: 2026-03-17
last_updated: 2026-03-17
area: personal
tags:
  - sibyl
  - x-api
  - sentiment
  - nlp
  - data-pipeline
  - guardrails
  - ai-agent
related:
  - "[[Projects/sibyl-overview]]"
  - "[[Projects/tradebot-overview]]"
---

# Sibyl — X (Twitter) Sentiment Framework

> **Authoritative operating reference for the Sentiment & News Agent.** This document defines all procedures, data collection strategies, processing pipelines, guard rails, and integration touchpoints governing Sibyl's interaction with the X (Twitter) platform for public sentiment and news signal ingestion.

---

## 1. Role in the Sibyl Architecture

The **Sentiment & News Agent** is a component of Sibyl's shared Analysis Layer. It runs independently of both the SGE and ACE execution engines and feeds pre-processed, quality-scored sentiment signals upward to the Signal Generator, which then routes qualified signals to the appropriate engine.

```
X Platform (Public Stream)
        ↓
[Sentiment & News Agent]  ← this document governs everything here
        ↓
    Signal Generator
        ↓
    Signal Router
    ├── SGE (Stale Market, corroborating sentiment)
    └── ACE (Momentum, Volume Surge, breaking narrative)
```

The Sentiment & News Agent **does not execute trades** and **does not directly access the Portfolio Allocator**. It produces structured `SENTIMENT` signal records written to `sibyl.db → signals` table, tagged with `source: X`, a confidence score, a bias risk score, and an authenticity score. The Signal Generator consumes these like any other signal type.

---

## 2. Account & API Configuration

### 2.1 X Account

| Property | Value |
| :--- | :--- |
| **Handle** | `@SibylA71720` |
| **Purpose** | Dedicated project account — no personal activity |
| **Visibility** | Public (required for API v2 access) |
| **Usage** | API access only — no manual posting or engagement |

> [!WARNING]
> This account must never be used for manual posting, retweeting, or following. Its sole function is API access. Any manual activity risks polluting rate limit quotas, triggering platform review, or creating an unintended public identity for the project.

### 2.2 X Developer Console

| Property | Detail |
| :--- | :--- |
| **Access tier** | Basic (minimum) → Pro (recommended for real-time filtered stream) |
| **App type** | Read-only (no write permissions needed) |
| **Authentication** | Bearer Token (App-Only Auth) for all streaming and search |
| **Credential storage** | `sibyl/config/.env` → `X_BEARER_TOKEN` |
| **Secondary credentials** | `X_API_KEY`, `X_API_SECRET` (store even if unused — needed if OAuth 1.0a is ever required) |

> [!IMPORTANT]
> All credentials are stored exclusively in `config/.env`. They are never hardcoded, logged, committed to version control, or written to any database table. The `.env` file is listed in `.gitignore`. If credentials are compromised, rotate immediately via the X Developer Portal and update `.env` on Node 3.

### 2.3 Rate Limits (as of 2026 — verify in Developer Portal before deployment)

| Endpoint | Basic Tier | Pro Tier | Agent Behavior |
| :--- | :--- | :--- | :--- |
| Filtered Stream (v2) | 1 connection, 500k tweets/month | 1 connection, 1M tweets/month | Maintain persistent connection; reconnect with exponential backoff on drop |
| Recent Search (v2) | 60 requests/15 min | 300 requests/15 min | Batch all queries per cycle; never exceed 80% of quota |
| Tweet Lookup | 15 requests/15 min | 900 requests/15 min | Used only for author enrichment; cache results |

> [!CAUTION]
> Rate limit headers are returned on every API response (`x-rate-limit-remaining`, `x-rate-limit-reset`). The agent **must** read and respect these headers. On `429 Too Many Requests`, back off until `x-rate-limit-reset` timestamp, then retry. Never implement a fixed-interval retry loop without checking the reset timestamp.

---

## 3. Data Collection Strategy

### 3.1 Collection Mode: Filtered Stream (Primary)

The agent maintains a **single persistent filtered stream connection** to the X v2 Filtered Stream endpoint. This is the primary data collection mechanism. It provides a real-time feed of public tweets matching pre-configured rules without repeated polling.

**Stream endpoint:** `GET https://api.twitter.com/2/tweets/search/stream`
**Rules endpoint:** `POST/GET/DELETE https://api.twitter.com/2/tweets/search/stream/rules`

Rules are configured server-side and persist until explicitly deleted. The agent should verify its active rule set on startup and reconcile with `config/x_stream_rules.yaml`.

### 3.2 Stream Filter Rules

Rules are scoped to **prediction market-relevant topics** only. The agent must not collect general political commentary or unrelated content. Every rule targets a specific intersection of market category and signal intent.

**Rule construction principles:**
- Always combine a **topic keyword** with a **market-signal keyword** (volume, price, odds, prediction, bet, probability)
- Use `lang:en` on all rules to constrain to English-language content
- Use `-is:retweet` on all rules to eliminate amplified content from raw collection (retweets are processed separately as a *reach metric*, not as independent signal)
- Use `-is:nullcast` to exclude promoted/ad content

**Core rule set (store in `config/x_stream_rules.yaml`):**

```yaml
rules:
  # Prediction market meta-signals
  - tag: "polymarket_direct"
    value: "polymarket lang:en -is:retweet"
  - tag: "kalshi_direct"
    value: "kalshi lang:en -is:retweet"
  - tag: "prediction_market_general"
    value: "(\"prediction market\" OR \"prediction markets\") lang:en -is:retweet"

  # Political / election market triggers
  - tag: "political_odds"
    value: "(election OR poll OR senate OR congress) (odds OR probability OR betting OR percent chance) lang:en -is:retweet"

  # Economic / macro market triggers
  - tag: "macro_signals"
    value: "(fed OR \"federal reserve\" OR inflation OR recession OR GDP) (probability OR odds OR \"basis points\" OR forecast) lang:en -is:retweet"

  # Crypto market triggers (watch cross-exposure with TradeBot)
  - tag: "crypto_narrative"
    value: "(bitcoin OR ethereum OR crypto) (\"all time high\" OR crash OR halving OR regulation OR ETF) lang:en -is:retweet"

  # Science / tech triggers
  - tag: "tech_events"
    value: "(FDA OR \"clinical trial\" OR \"product launch\" OR IPO) (approved OR rejected OR delayed OR passed) lang:en -is:retweet"

  # Sports market triggers
  - tag: "sports_odds"
    value: "(NFL OR NBA OR MLB OR soccer OR championship) (odds OR spread OR line OR prediction) lang:en -is:retweet"
```

> [!NOTE]
> Rules consume your monthly tweet quota even when the agent is idle. During development and testing, disable non-essential rules. Enable the full rule set only in production on Node 3.

### 3.3 Collection Mode: Recent Search (Supplementary)

For markets that lack stream coverage or for backfilling signal gaps, the agent runs **targeted Recent Search queries** on a 15-minute cycle. This mode is throttled aggressively to preserve quota.

**Trigger conditions for a search query:**
- A market in `sibyl.db → markets` is flagged `high_priority = 1` AND has no stream coverage in the last 30 minutes
- A stale market signal is detected (odds unchanged >4h) — search is used to check for news absence confirmation
- Manual override via `config/markets_watchlist.yaml` flagging a specific market for supplemental search

**Search endpoint:** `GET https://api.twitter.com/2/tweets/search/recent`

The agent constructs market-specific queries from the market `title` field in `sibyl.db`, stripping stop words and combining with signal-intent modifiers. Queries are logged to `data/state/x_search_log.json` with timestamp and result count.

### 3.4 Tweet Fields to Collect

Every tweet collected must request these fields:

```
tweet.fields: id, text, created_at, author_id, public_metrics, 
              context_annotations, entities, possibly_sensitive,
              referenced_tweets, conversation_id, lang

user.fields: id, name, username, verified, verified_type,
             public_metrics, created_at, description

expansions: author_id, referenced_tweets.id, referenced_tweets.id.author_id
```

`public_metrics` (like count, retweet count, reply count, quote count, impression count) are essential for reach scoring and bot detection. `context_annotations` provide X's own topic classification, which is used as a corroborating signal in the processing pipeline.

---

## 4. Data Processing Pipeline

### 4.1 Pipeline Stages

```
Raw Tweet (stream or search)
    │
    ▼
[Stage 1: Ingestion & Deduplication]
    │  — Hash tweet ID; skip if already in x_raw buffer
    │  — Write to temporary x_raw queue (in-memory ring buffer)
    │
    ▼
[Stage 2: Guard Rail Pre-Screen]
    │  — Run authenticity check (Section 5.3)
    │  — Run radicalism screen (Section 5.2)
    │  — REJECT if either check fails; log to x_rejected with reason code
    │
    ▼
[Stage 3: Sentiment Scoring]
    │  — NLP sentiment classification (positive/negative/neutral)
    │  — Confidence score (0.0 – 1.0)
    │  — Market relevance extraction (which market does this tweet concern?)
    │
    ▼
[Stage 4: Bias Risk Assessment]
    │  — Apply bias risk model (Section 5.1)
    │  — Assign bias_risk_score (0.0 – 1.0)
    │  — Tweets with bias_risk_score > 0.70 are downweighted, not rejected
    │
    ▼
[Stage 5: Aggregation Window]
    │  — Buffer tweets per market per 5-minute window
    │  — Compute: net_sentiment, tweet_volume, reach_weighted_sentiment
    │
    ▼
[Stage 6: Signal Threshold Check]
    │  — IF net_sentiment shift exceeds threshold AND volume confirms:
    │    → Write SENTIMENT signal to sibyl.db → signals
    │  — ELSE: discard window; no signal generated
    │
    ▼
Signal Generator (consumes from signals table)
```

### 4.2 Sentiment Scoring Model

The agent uses a **two-pass approach**:

**Pass 1 — Rule-based pre-filter:** Check for irony indicators (sarcasm markers, common negation patterns, quote-tweet context). If strong irony indicators are present, invert the surface sentiment or discard with `ambiguous` flag.

**Pass 2 — NLP classification:** Apply a fine-tuned financial/political sentiment model. Recommended: `ProsusAI/finbert` (financial context) or a custom fine-tuned `distilbert` checkpoint. Output: `{label: POSITIVE|NEGATIVE|NEUTRAL, score: float}`.

**Reach weighting:** Raw tweet sentiment is weighted by `log(1 + impression_count)` to give more weight to high-reach content without allowing any single viral tweet to dominate. Formula:

```
reach_weight = log(1 + tweet.public_metrics.impression_count)
weighted_sentiment = sentiment_score × reach_weight
```

### 4.3 Market Relevance Extraction

Each tweet must be mapped to a specific market in `sibyl.db → markets`. The pipeline uses:

1. **Stream rule tag** — tweets from the filtered stream carry a `rule_tag` that provides a category hint
2. **Entity matching** — extract named entities (NER) from tweet text; match against market titles using fuzzy string matching (Levenshtein distance ≤ 2 on key tokens)
3. **X context annotations** — use `context_annotations.domain` from the tweet metadata as a corroborating category signal

If a tweet cannot be reliably mapped to a specific market, it is **discarded** — it contributes nothing to market-specific signals. Unmapped tweets are logged to `data/state/x_unmapped_log.json` for periodic review.

### 4.4 Aggregation & Signal Threshold

Tweets are buffered in **5-minute rolling windows per market**. At the close of each window, the agent computes:

```
window_tweet_count       = count of accepted tweets in window
net_sentiment            = mean(weighted_sentiment) across window
sentiment_shift          = net_sentiment[current] - net_sentiment[prev_window]
volume_z_score           = (window_tweet_count - rolling_30d_mean) / rolling_30d_std
```

A `SENTIMENT` signal is generated if **all** of the following are met:

| Condition | Threshold |
| :--- | :--- |
| `window_tweet_count` | ≥ 10 tweets |
| `abs(sentiment_shift)` | ≥ 0.15 (on a −1 to +1 scale) |
| `volume_z_score` | ≥ 1.5 (above average conversation volume) |
| Bias risk ceiling | `mean(bias_risk_score)` across window ≤ 0.60 |
| Authenticity floor | ≥ 70% of tweets in window pass authenticity check |

---

## 5. Guard Rails

### 5.1 Bias Risk Detection

Bias risk assessment runs on every accepted tweet and produces a `bias_risk_score` (0.0 = no detectable bias risk, 1.0 = high risk). This score **attenuates** the signal but does not reject it outright — high-bias-risk windows simply fail the aggregation threshold.

#### 5.1.1 Political Bias

**Risk:** Political prediction market signals are especially susceptible to partisan amplification. A coordinated partisan community can flood the stream with extreme sentiment without representing the broader public opinion.

**Detection approach:**
- Classify tweet author's political lean using follower graph inference or account description keyword analysis (maintain a `partisan_signal_keywords` list)
- If >60% of tweets in a window share the same inferred political lean, apply `political_homogeneity_penalty = 0.30` added to `bias_risk_score`
- Monitor for topic-specific language patterns that are strongly associated with single-party framing (e.g., specific epithets, hashtags, rally slogans) — flag with `partisan_language_flag`

**Mitigation:** Weight tweets from accounts with balanced follower graphs (i.e., accounts that are followed by people with diverse political leans) more heavily. Deprioritize accounts whose followers are >80% aligned to a single political bloc.

#### 5.1.2 Recency & Anchoring Bias

**Risk:** A single breaking news event can cause a sentiment spike that reverses within hours. Acting on the immediate spike rather than the stabilized signal leads to false positives.

**Detection approach:**
- Require at least **two consecutive 5-minute windows** with sustained sentiment shift before generating a signal (i.e., the shift must persist, not just appear once)
- If a sentiment spike occurs within 10 minutes of a known high-volume news event (detected by a sudden `volume_z_score` > 3.0), apply a **confirmation delay** of 15 minutes before allowing signal generation

#### 5.1.3 Narrative Cascade Bias (Echo Chamber)

**Risk:** A single influential account posts a take → followers retweet and quote-tweet → the agent sees high volume but it is all derived from one source.

**Detection approach:**
- Compute `conversation_diversity_score`: ratio of unique `conversation_id` values to total tweets in window. A score < 0.30 (i.e., most tweets share few conversation threads) triggers a `cascade_flag`
- If `cascade_flag` is set, add `0.25` to `bias_risk_score` and require volume threshold to be 2× normal before signal generation

#### 5.1.4 Source Concentration Bias

**Risk:** A small number of high-follower accounts dominate the sentiment window, making the signal dependent on their views rather than broad public opinion.

**Detection approach:**
- Compute `author_concentration`: if the top 3 authors by reach account for >50% of total `reach_weight` in the window, apply `source_concentration_penalty = 0.20` to `bias_risk_score`
- Never allow a single author to contribute >25% of the total reach-weighted sentiment in a window, regardless of their follower count

### 5.2 Radicalistic Sentiment Detection

**Risk:** Content expressing extreme, violent, or radicalistic sentiment can produce misleading signals and must be excluded from signal generation. Beyond signal quality, ingesting and processing this content without filtering it creates operational and ethical risk.

**Detection approach (multi-layer):**

**Layer 1 — Keyword screen:** Maintain a `radicalism_keywords.yaml` file containing patterns associated with calls to violence, extremist rhetoric, dehumanizing language, and insurrectionist framing. Tweets matching any pattern are **immediately rejected** with reason code `RADICAL_CONTENT`. This list must be reviewed and updated quarterly.

**Layer 2 — Sentiment extremity check:** If a tweet's raw sentiment score is in the extreme tails (score < −0.90 or > +0.90), apply a secondary check: does the extreme sentiment co-occur with high anger/fear emotion markers? If yes, add `0.40` to `bias_risk_score` before aggregation.

**Layer 3 — Account flag cross-reference:** Maintain a `flagged_accounts.yaml` blocklist of accounts previously identified as sources of extremist content. Tweets from flagged accounts are rejected without processing. The blocklist is updated whenever a tweet is manually reviewed and confirmed as extremist.

**Layer 4 — X Safety labels:** X applies `possibly_sensitive` labels and can apply account-level restrictions. Tweets with `possibly_sensitive: true` require passing Layers 1–3 before being accepted into the pipeline; any failure on those layers results in immediate rejection.

> [!WARNING]
> Radicalistic sentiment detection is not a signal attenuator — it is a **hard gate**. Any tweet that fails Layer 1 or Layer 2 (co-occurring extremity) is excluded from all processing. There is no downweighting path for radical content.

### 5.3 Fake & Injected Sentiment Detection (Coordinated Inauthentic Behavior)

**Risk:** Adversarial actors or market participants may attempt to manipulate Sibyl's signals by flooding X with coordinated fake sentiment targeting specific prediction markets. This is the highest-stakes guard rail in the system.

**Detection approach (five checks, all run in parallel):**

#### Check 1: Account Age & Activity Pattern
Reject tweets from accounts where **any** of the following are true:
- Account age < 90 days
- Followers < 20 AND following > 500 (follow-farming pattern)
- Tweet count < 50 (very new account with little history)
- Account has no profile photo (default avatar) — high bot indicator

Assign `authenticity_penalty = 0.50` per flag; accounts with total penalty ≥ 0.50 are rejected.

#### Check 2: Posting Velocity
Reject tweets where the author has posted > 50 times in the last hour (abnormal human posting rate). Log to `x_rejected` with code `HIGH_VELOCITY_AUTHOR`.

#### Check 3: Linguistic Fingerprinting
Cluster tweets in the current window by semantic similarity (cosine similarity on sentence embeddings). If >30% of tweets in a window have pairwise similarity > 0.85, flag the window as `COORDINATED_NARRATIVE` and reject the entire window — this indicates templated or near-identical messaging, a hallmark of coordinated campaigns.

#### Check 4: Temporal Clustering (Burst Detection)
If > 40% of the tweets in a 5-minute window arrive within a 30-second burst, flag as `TEMPORAL_BURST`. This pattern is consistent with bot networks triggering on a schedule. Apply `authenticity_penalty = 0.40` to all tweets in the window; if the window mean falls below 0.70, discard the window entirely.

#### Check 5: Cross-Window Volume Anomaly
Compare window tweet volume to the 30-day rolling mean for that market-topic pair. If volume exceeds 5× the rolling mean with no corresponding event detected in RSS/news feeds (i.e., no external corroboration), flag the spike as `UNSUPPORTED_VOLUME_SPIKE` and require external corroboration before generating a signal.

> [!IMPORTANT]
> All five checks run in parallel. The final `authenticity_score` for a tweet is:
> `authenticity_score = 1.0 − sum(all_applied_penalties)` clamped to [0.0, 1.0]
>
> Tweets with `authenticity_score < 0.50` are **rejected**. The aggregation window additionally requires that ≥70% of tweets in the window pass this threshold for a signal to be generated.

---

## 6. Signal Output Format

When a sentiment window passes all thresholds, the agent writes a structured record to `sibyl.db → signals`:

```sql
INSERT INTO signals (
    market_id,
    timestamp,
    signal_type,        -- 'SENTIMENT'
    source,             -- 'X'
    confidence,         -- float 0.0–1.0 (derived from sentiment_shift magnitude + volume_z_score)
    ev_estimate,        -- NULL (Sentiment & News Agent does not compute EV — Signal Generator does)
    routed_to,          -- NULL at write time — Signal Router assigns after reading
    metadata            -- JSON blob (see below)
) VALUES (...);
```

**metadata JSON schema:**
```json
{
  "window_start": "ISO8601",
  "window_end": "ISO8601",
  "tweet_count": 42,
  "net_sentiment": 0.31,
  "sentiment_shift": 0.22,
  "sentiment_direction": "POSITIVE",
  "volume_z_score": 2.1,
  "mean_bias_risk_score": 0.18,
  "mean_authenticity_score": 0.87,
  "reach_weighted_sentiment": 0.29,
  "top_rule_tags": ["political_odds", "prediction_market_general"],
  "cascade_flag": false,
  "political_homogeneity_flag": false,
  "coordinated_narrative_flag": false,
  "unsupported_volume_spike_flag": false,
  "rejected_tweet_count": 3,
  "rejection_reasons": {"RADICAL_CONTENT": 1, "LOW_AUTHENTICITY": 2}
}
```

**Confidence score derivation:**
```
base_confidence = normalize(abs(sentiment_shift), 0.15, 1.0) × 0.50
volume_bonus    = normalize(volume_z_score, 1.5, 5.0) × 0.30
quality_bonus   = mean_authenticity_score × 0.20

confidence = base_confidence + volume_bonus + quality_bonus
           = clamped to [0.0, 1.0]
```

### 6.1 Signal Engine Routing Guidance

The Signal Router determines final routing, but the Sentiment & News Agent should include a `routing_hint` in the metadata to guide the router:

| Condition | Routing Hint |
| :--- | :--- |
| Large sentiment shift (≥ 0.30) + high volume | `ACE_PREFERRED` → Momentum |
| Sustained moderate shift (0.15–0.30) across 2+ windows | `SGE_PREFERRED` → Stale Market corroboration |
| Low volume, sentiment neutral → market absence confirmed | `SGE_PREFERRED` → Stale Market confirmation |
| High bias risk (0.40–0.60 range, under threshold) | `CAUTION_FLAG` → Signal Generator may downweight |

---

## 7. Database Schema — Sentiment Tables

The following additions extend `sibyl.db` for X sentiment data:

```sql
-- Raw tweet buffer (ephemeral — cleared after processing)
CREATE TABLE x_raw (
    tweet_id        TEXT PRIMARY KEY,
    collected_at    TIMESTAMP,
    rule_tag        TEXT,
    author_id       TEXT,
    text            TEXT,
    created_at      TIMESTAMP,
    impression_count INTEGER,
    retweet_count   INTEGER,
    like_count      INTEGER,
    raw_json        TEXT    -- full tweet JSON for reprocessing
);

-- Rejected tweets log (persistent — for audit and model improvement)
CREATE TABLE x_rejected (
    tweet_id        TEXT,
    rejected_at     TIMESTAMP,
    reason_code     TEXT,   -- RADICAL_CONTENT | LOW_AUTHENTICITY | HIGH_VELOCITY_AUTHOR | etc.
    authenticity_score REAL,
    bias_risk_score REAL,
    rule_tag        TEXT
);

-- Per-window aggregations (persistent — for trend analysis)
CREATE TABLE x_sentiment_windows (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT,
    window_start    TIMESTAMP,
    window_end      TIMESTAMP,
    tweet_count     INTEGER,
    rejected_count  INTEGER,
    net_sentiment   REAL,
    sentiment_shift REAL,
    volume_z_score  REAL,
    bias_risk_mean  REAL,
    authenticity_mean REAL,
    signal_generated INTEGER,  -- 1 if signal written to signals table, 0 otherwise
    signal_id       INTEGER    -- FK to signals.id if generated
);

-- Author quality cache (persistent — avoids re-fetching author data)
CREATE TABLE x_author_cache (
    author_id       TEXT PRIMARY KEY,
    username        TEXT,
    followers_count INTEGER,
    following_count INTEGER,
    tweet_count     INTEGER,
    account_age_days INTEGER,
    verified        INTEGER,
    quality_score   REAL,   -- precomputed authenticity score component
    last_updated    TIMESTAMP
);

-- Blocklist (persistent — manually maintained)
CREATE TABLE x_blocklist (
    author_id       TEXT PRIMARY KEY,
    username        TEXT,
    added_at        TIMESTAMP,
    reason          TEXT
);
```

---

## 8. Operational Procedures

### 8.1 Startup Sequence

On agent initialization, execute in order:

1. **Load credentials** from `config/.env` — fail fast if `X_BEARER_TOKEN` is missing or invalid
2. **Verify stream rules** — fetch active rules from X API; compare against `config/x_stream_rules.yaml`; add missing rules, delete unknown rules, log any discrepancy
3. **Load blocklist** — read `x_blocklist` table into memory for fast lookup
4. **Load author cache** — pre-populate in-memory LRU cache (max 10,000 entries) from `x_author_cache` table
5. **Load rolling baselines** — compute 30-day mean and std for tweet volume per market-topic pair from `x_sentiment_windows`
6. **Open stream connection** — connect to filtered stream endpoint with reconnect handler
7. **Log startup** to `system_state` table: `{"agent": "sentiment_news", "status": "online", "stream_connected": true}`

### 8.2 Stream Reconnection Policy

The X filtered stream is a persistent HTTP connection. Drops are expected. The agent must implement:

```
On disconnect:
    attempt 1: wait 1s, reconnect
    attempt 2: wait 5s, reconnect
    attempt 3: wait 30s, reconnect
    attempt 4+: wait 60s, reconnect (cap)
    After 10 consecutive failures: alert via system_state; fall back to Recent Search mode
```

Log every reconnect to `system_state` with timestamp and attempt number. If fallback to Recent Search mode is triggered, set `{"stream_connected": false, "mode": "polling"}` in `system_state`.

### 8.3 Guard Rail Review Cadence

| Guard Rail Component | Review Frequency | Responsibility |
| :--- | :--- | :--- |
| `radicalism_keywords.yaml` | Quarterly | Manual review by Rafael |
| `flagged_accounts.yaml` (blocklist) | Ongoing — add on detection | Automated + manual confirmation |
| `x_rejected` audit | Weekly | Review rejection rate trends; flag unusual spikes |
| Bias detection thresholds | Monthly | Review `x_sentiment_windows` where `bias_risk_mean > 0.50` |
| Stream rules | Per market category change | Update when new market categories are added to Sibyl |
| Author cache | Rolling 30-day TTL | Stale entries auto-refreshed on next author lookup |

### 8.4 Monitoring & Alerting

Write the following metrics to `system_state` every 5 minutes, consumed by the Grafana/Prometheus stack on Node 3:

| Metric | Description |
| :--- | :--- |
| `x_stream_connected` | Boolean — stream connection health |
| `x_tweets_per_minute` | Rolling 5-min tweet ingestion rate |
| `x_rejection_rate` | % of tweets rejected in last 15 minutes |
| `x_signals_generated_today` | Count of SENTIMENT signals written to signals table |
| `x_radical_detections_today` | Count of RADICAL_CONTENT rejections |
| `x_coordinated_narrative_flags_today` | Count of windows flagged for coordinated behavior |
| `x_rate_limit_remaining_pct` | Remaining API quota as % of monthly cap |

> [!WARNING]
> Alert thresholds (trigger immediate review):
> - `x_rejection_rate` > 40% sustained for > 15 minutes → possible rule misconfiguration or coordinated attack
> - `x_coordinated_narrative_flags_today` > 5 → active manipulation attempt; escalate to manual review
> - `x_rate_limit_remaining_pct` < 20% → quota management failure; switch to minimal polling mode

### 8.5 Error Handling

| Error | Agent Response |
| :--- | :--- |
| HTTP 401 Unauthorized | Log `CREDENTIAL_FAILURE`; halt agent; alert via `system_state`; do not retry until credentials are rotated |
| HTTP 429 Too Many Requests | Read `x-rate-limit-reset` header; sleep until reset; do not process during sleep |
| HTTP 503 Service Unavailable | Apply reconnection policy (Section 8.2) |
| Malformed tweet JSON | Log `PARSE_ERROR` with raw payload to `x_rejected`; continue processing stream |
| NLP model failure | Log `NLP_ERROR`; skip sentiment scoring for affected tweet; do not write partial records to `signals` |
| Database write failure | Log to local file `data/state/x_write_errors.log`; buffer up to 1,000 records in memory; retry on next cycle |

---

## 9. Configuration Files

### `config/x_stream_rules.yaml`
Defines the active X filtered stream rules. Modified by Rafael; synced to X API on agent startup.

### `config/x_sentiment_config.yaml`
Agent tuning parameters:

```yaml
sentiment:
  aggregation_window_minutes: 5
  min_tweets_per_window: 10
  sentiment_shift_threshold: 0.15
  volume_z_score_threshold: 1.5
  bias_risk_ceiling: 0.60
  authenticity_floor_pct: 0.70

bias:
  political_homogeneity_threshold: 0.60
  political_homogeneity_penalty: 0.30
  cascade_diversity_threshold: 0.30
  cascade_penalty: 0.25
  source_concentration_threshold: 0.50
  source_concentration_penalty: 0.20

authenticity:
  min_account_age_days: 90
  min_followers: 20
  max_following_to_followers_ratio: 25.0
  min_tweet_history: 50
  max_posts_per_hour: 50
  linguistic_similarity_threshold: 0.85
  coordinated_cluster_pct: 0.30
  burst_pct_threshold: 0.40
  burst_window_seconds: 30
  volume_spike_multiplier: 5.0
  min_tweet_authenticity_score: 0.50

routing:
  large_shift_threshold: 0.30
  sustained_windows_required: 2
  confirmation_delay_minutes: 15
  volume_spike_confirmation_delay_minutes: 15
```

### `config/radicalism_keywords.yaml`
Pattern list for Layer 1 radicalism detection. **Not committed to version control.** Stored locally on Node 3 only.

### `data/state/x_search_log.json`
Rolling log of all Recent Search queries, timestamps, and result counts. Retained for 30 days.

---

## 10. Integration Checklist

Before enabling the Sentiment & News Agent in production:

- [ ] X Developer account confirmed at appropriate tier (Basic minimum, Pro recommended)
- [ ] `X_BEARER_TOKEN` stored in `config/.env` and verified with a test API call
- [ ] `x_stream_rules.yaml` configured and rules synced to X API
- [ ] `radicalism_keywords.yaml` populated with initial keyword set
- [ ] All sentiment schema tables created in `sibyl.db`
- [ ] NLP model downloaded and path configured in `x_sentiment_config.yaml`
- [ ] Rolling baselines initialized (requires at least 7 days of passive collection before signal generation is enabled)
- [ ] `system_state` write confirmed — Grafana dashboard shows agent metrics
- [ ] Dry-run for 7 days: collect, process, and score without writing to `signals` table — review rejection rates and flag rates manually
- [ ] Guard rail threshold calibration: adjust `x_sentiment_config.yaml` based on dry-run data
- [ ] Signal Generator confirmed to read `source: X` signals from `signals` table
- [ ] Signal Router confirmed to handle `SENTIMENT` signal type with routing hint logic

---

## Log

**2026-03-17:** Framework document created. Covers X account/API configuration, filtered stream strategy, data processing pipeline, all guard rails (bias detection, radicalism screening, coordinated inauthentic behavior detection), signal output format, database schema extensions, operational procedures, and integration checklist. Resolves Open Question: "Phase 2: Add a News/Sentiment agent — what data source?" → X (Twitter) via v2 Filtered Stream API is the primary source for this framework.

---

## Related Notes
- [[Projects/sibyl-overview]] — Parent project; Sentiment & News Agent appears in shared Analysis Layer
- [[Projects/tradebot-overview]] — Sibling project; crypto sentiment signals may be shared
