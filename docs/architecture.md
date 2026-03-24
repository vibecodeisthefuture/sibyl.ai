# Sibyl.ai — Architecture & Design Decisions

## Architectural Decisions

- **Kalshi-only trading**: Polymarket geo-restricted (US). Polymarket data used for divergence detection only; all actual bets placed on Kalshi
- **Paper mode first**: `OrderExecutor` defaults to paper mode (`--mode paper`). Live mode requires explicit `--mode live` flag
- **Agent scoping via CLI**: `python -m sibyl --agents monitor|intelligence|execution|portfolio|advanced|all`
- **Kelly Criterion sizing**: Position size = min(kelly_raw, kelly_fraction_cap, max_position_pct) × available_capital
- **Circuit breaker**: 3 stop-losses within 15 minutes → engine circuit breaker TRIGGERED → no new orders
- **Dual engine architecture**: SGE (Stable Growth, 70% capital, conservative) vs ACE (Alpha Capture, 30% capital, aggressive)
- **In-memory detection queue**: MarketIntelligenceAgent → SignalGenerator uses `_detection_queue` (list); SignalGenerator → SignalRouter uses `signals` DB table
- **Rate limiter per platform**: Each client has its own `RateLimiter` instance calibrated to platform-specific free-tier limits
- **SQLite with WAL mode**: Enables concurrent reads from multiple agents; `busy_timeout` = 5s
- **Allocator vs EngineStateManager separation**: Allocator sets the BUDGET (total_capital); EngineStateManager tracks SPENDING (deployed/available). Clear ownership prevents race conditions.
- **High-water mark persistence**: HWM stored in system_state to survive restarts. Drawdown is always measured from the all-time peak, not the session peak.
- **Risk Dashboard → system_state pattern**: All risk metrics written as key-value pairs to system_state. The web dashboard (Sprint 5) will read these without coupling to agent internals.
- **Live mode graceful degradation**: If Kalshi credentials are missing in live mode, OrderExecutor falls back to paper mode rather than crashing.
- **LLM-optional agents**: Both BreakoutScout and Narrator gracefully degrade when ANTHROPIC_API_KEY is missing — Scout uses simple score averaging, Narrator uses template-based digests.
- **Freshness decay for research**: Research packets lose 0.15 freshness per scout cycle; stale research on active positions triggers automatic re-research. Prevents trading on outdated intelligence.
- **Arbitrage minimum spread**: Cross-platform ARBITRAGE signals require ≥ 8% spread to account for execution fees and slippage. EV is discounted 30% from raw spread for execution risk.
- **X API Basic tier design**: Agent designed for $200/mo Basic tier (10K tweets/month, 7-day search, 60 req/15min). Budget guards: 300 tweets/day cap (10% headroom), 48 queries/15min (80% of limit). Filtered Stream preferred, auto-fallback to Recent Search polling.
- **Keyword sentiment (FinBERT deferred)**: Using keyword-based financial sentiment scoring with reach-weighting. FinBERT integration deferred to GPU workstation availability. Irony marker detection dampens/inverts scores for sarcastic content.
- **Perplexity cost optimization**: Sonar model at ~$0.0005/call, gated behind breakout threshold + freshness caching. At 10-20 calls/day, monthly cost is $0.15-$0.30. Daily call cap (default 30) prevents runaway spending.
- **6-stage sentiment pipeline**: Ingestion → Guard Rails → Scoring → Bias → Aggregation → Signal. Each stage has independent thresholds. Guard rails (radicalism + authenticity) are hard gates; bias assessment is a penalty modifier.
- **Category-specific strategies**: Each of Kalshi's 10 market categories gets tailored signal adjustments, sizing, and routing preferences. Politics discounts confidence (polls lie), Sports boosts whale signals (sharp money), Mentions uses tiny positions (high variance). Category is the *most important* dimension for strategy differentiation.
- **Signal weight blending**: Category signal weights are blended into confidence at 30% factor (weight_factor=0.3) to prevent extreme swings while still meaningfully differentiating signal quality per vertical.
- **Correlation penalty**: Categories with high intra-category correlation (Politics=0.15, Economics=0.12, Crypto=0.10) reduce position sizing when multiple positions are active in the same category, preventing cascading losses from a single catalyst.
- **Holographic design system**: Dashboard uses Priscey-inspired visual framework (DM Sans + JetBrains Mono, #0F0E1A bg, gold→rose→purple→blue gradient) for premium feel and information density.
- **Backtesting mirrors live pipeline**: BacktestEngine replays signals through the same category adjustment → routing → Kelly sizing → correlation penalty chain as live OrderExecutor. Ensures backtest results are representative of real behavior.
- **Dynamic correlation penalty**: Portfolio-value-responsive sizing reduction. Penalty *increases* when portfolio shrinks (defensive) and *decreases* when portfolio grows (allows concentration in winning categories). Formula: `effective = base / clamp(balance/starting, 0.5, 2.0)`. Floor at 10% prevents complete position elimination.
- **Category performance as system_state**: Category win rates, ROI, and trade counts persisted as JSON in system_state for dashboard consumption and future auto-tuning. Same key-value pattern used by RiskDashboard.
- **Zero-dependency demo dashboard**: Demo mode uses vanilla JS + inline SVG charts instead of React/Recharts CDN. This guarantees the dashboard renders from `file://` without any network requests. The production dashboard retains React/Recharts for richer interactivity when served over HTTP.
- **Investment policy as pure-function module**: PolicyEngine has no DB dependency — it loads a YAML config and exposes enforcement methods. This makes it testable in isolation (83 unit tests) and allows the same policy logic to run in backtest, paper, and live modes.
- **Policy graceful degradation**: If `investment_policy_config.yaml` is missing, agents log a warning and continue without policy enforcement. Enables incremental rollout.
- **SQLite migration pattern**: New columns added via ALTER TABLE with PRAGMA table_info checks to avoid duplicate-column errors. Safe for incremental schema evolution.
- **Override protocol is fully autonomous**: Section 17 override requires confidence ≥0.90, EV ≥20%, 3+ independent sources — no human gate. Performance tracked separately with auto-calibration (raise threshold by 0.02 if overrides underperform over 90 days).
- **PostgreSQL strategy — multi-user future**: SQLite for dev; PostgreSQL as the production backend behind a database abstraction layer. Long-term: Sibyl will be a locally-hosted service platform with per-user data isolation (separate schemas or tenant_id), PostgreSQL RBAC, and PgBouncer connection pooling. Migration path: abstraction layer → asyncpg backend → Docker Compose Postgres service → homelab deployment with NAS persistence.
- **Sonar LLM consolidation (Sprint 15)**: Perplexity Sonar replaces both Anthropic Claude (BreakoutScout synthesis + Narrator digests) and NewsAPI (news sentiment). Single API key (PERPLEXITY_API_KEY) now powers all LLM and news-search tasks. Cost: ~$0.30-$0.60/month at projected usage (synthesis + digest + research). Eliminates $0/mo ANTHROPIC_API_KEY dependency and $0/mo NEWSAPI_KEY dependency. Sonar's web-grounded search is a strict superset of NewsAPI for our use case.
- **PipelineAgent scheduling pattern**: PipelineManager wrapped in BaseAgent lifecycle via PipelineAgent adapter. Runs on 15-minute cycle with category filtering support. Stats written to system_state after each run for dashboard consumption.

## System Workflow (End-to-End Signal Pipeline)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  DATA INGESTION (Sprint 1)                                              │
│                                                                         │
│  PolymarketMonitor ──┐                                                  │
│       (5s poll)      ├──▶ markets, prices, orderbook, trades ──▶ SQLite│
│  KalshiMonitor ──────┘                                                  │
│       (5s poll)                                                         │
│                                                                         │
│  CrossPlatformSync (30s) ──▶ fuzzy match ──▶ divergence alerts         │
│                              (arb_divergence_* → system_state)          │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│  INTELLIGENCE (Sprint 2 + 7 + 8)                                        │
│                                                                         │
│  MarketIntelligence (5s) ──▶ Whale/Volume/OrderBook detections          │
│       │                                                                 │
│       ▼                                                                 │
│  SignalGenerator ──▶ composite score + EV ──▶ SIGNAL (+ ARBITRAGE scan)│
│       │                                                                 │
│       ▼                                                                 │
│  SignalRouter ──▶ SGE_ROUTED / ACE_ROUTED / BOTH / DEFERRED            │
│                                                                         │
│  BreakoutScout (15m) ──▶ Reddit + NewsAPI + Perplexity ──▶ LLM synth   │
│       │                  ──▶ market_research table (freshness-decayed)  │
│       │                                                                 │
│  XSentimentAgent (5m) ──▶ X Stream/Search ──▶ 6-stage pipeline         │
│       │                  ──▶ SENTIMENT signals (shift + volume + bias)  │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│  EXECUTION (Sprint 3)                                                   │
│                                                                         │
│  OrderExecutor ──▶ Kelly sizing ──▶ paper fill / Kalshi live order     │
│       │                                                                 │
│  PositionLifecycle (5 sub-routines):                                    │
│       A: Stop Guard (7s)         D: Resolution Tracker (300s)           │
│       B: EV Monitor (90-300s)    E: Correlation Scanner (10m)           │
│       C: Exit Optimizer (120s)                                          │
│       │                                                                 │
│  EngineStateManager ──▶ deployed capital, circuit breakers              │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│  PORTFOLIO & RISK (Sprint 4)                                            │
│                                                                         │
│  PortfolioAllocator ──▶ balance sync, SGE/ACE splits, rebalancing       │
│  RiskDashboard ──▶ drawdown, win rate, Sharpe, exposure ──▶ system_state│
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│  OBSERVABILITY (Sprint 5 + 7)                                           │
│                                                                         │
│  Notifier ──▶ ntfy.sh push (signals, stops, breakers, drawdown)         │
│  Narrator (6h) ──▶ Claude Haiku digest + alert escalation               │
│  Dashboard ──▶ FastAPI + React SPA @ :8088 (auto-refresh 10s)           │
└─────────────────────────────────────────────────────────────────────────┘
```

## External API Cost Breakdown

### X / Twitter API (XSentimentAgent)

| Tier | Monthly Cost | Read Access | Filtered Stream | Recent Search | Sibyl Usage |
|------|-------------|-------------|-----------------|---------------|-------------|
| Free | $0 | NO (write-only) | NO | NO | Not usable |
| **Basic** | **$200/mo** | **10,000 tweets/mo** | **NO** | **Yes (7-day, 60 req/15min)** | **Target tier** |
| Pro | $5,000/mo | 1,000,000 tweets/mo | Yes (real-time SSE) | Yes (full archive) | Future upgrade |

**Basic tier budget optimization (implemented):**

- 10,000 tweets/month ÷ 30 days = ~333 tweets/day raw allowance
- Agent caps at 300 tweets/day (10% headroom for safety)
- 48 queries per 15-min window (80% of Basic's 60-request limit)
- High-priority markets (top 10 by breakout_score) searched every 15 min
- Low-priority markets deferred to next cycle
- Filtered Stream rules pre-configured but only activate on Pro tier; agent auto-falls back to Recent Search polling on Basic

**Effective cost per signal:** $200/mo fixed. At ~10 SENTIMENT signals/day (estimated from 300 tweets through the 6-stage funnel), effective cost is ~$0.67/signal/day.

**Status:** Agent built and tested. Idle until X Basic subscription is purchased.

### Perplexity Sonar API (BreakoutScout)

| Model | Input Cost | Output Cost | Tokens/Call (est.) | Cost/Call |
|-------|-----------|-------------|-------------------|-----------|
| **Sonar** | **$1/M tokens** | **$1/M tokens** | **~500 total** | **~$0.0005** |
| Sonar Pro | $3/M tokens | $15/M tokens | ~800 total | ~$0.014 |

**Cost projections at various usage levels:**

| Daily Calls | Monthly Calls | Monthly Cost (Sonar) | Monthly Cost (Sonar Pro) |
|-------------|---------------|---------------------|-------------------------|
| 5 | ~150 | **$0.08** | $2.10 |
| 10 | ~300 | **$0.15** | $4.20 |
| 20 | ~600 | **$0.30** | $8.40 |
| 30 (cap) | ~900 | **$0.45** | $12.60 |

**Cost controls (implemented):**

- Daily call cap: 30 calls/day (configurable in `breakout_scout_config.yaml`)
- Event-driven, not time-driven: only fires when a market crosses the breakout_score threshold (≥ 52) AND existing research is stale (freshness < 0.50)
- Compact prompts: ~200 input tokens, max 300 output tokens
- Freshness caching: same market not re-queried until research decays
- Expected typical usage: 5-15 calls/day → **$0.08-$0.23/month**
- Worst-case (cap hit daily): $0.45/month

**Status:** Client built and integrated into BreakoutScout. Requires PERPLEXITY_API_KEY in `.env` to activate. Pay-per-use, no subscription required.

### Combined Monthly API Cost Summary

| Service | Tier | Monthly Cost | Status |
|---------|------|-------------|--------|
| Polymarket API | Free | $0 | Active (read-only) |
| Kalshi API | Free/Basic | $0 | Active (read + trade) |
| Perplexity Sonar | Pay-per-use | ~$0.50 | **Active** (research + synthesis + digests) |
| ntfy.sh | Free | $0 | Active |
| X / Twitter | Basic | $200.00 | Deferred (30-day test without) |
| ~~NewsAPI~~ | ~~Free~~ | ~~$0~~ | **Replaced by Sonar** (Sprint 15) |
| ~~Anthropic (Claude)~~ | ~~Pay-per-use~~ | ~~Variable~~ | **Replaced by Sonar** (Sprint 15) |
| **Total (current)** | | **~$0.50/mo** | Sonar is only paid service |
| **Total (with X)** | | **~$200.50/mo** | X is the dominant cost |

> **Sprint 15 cost optimization:** Perplexity Sonar now handles all LLM tasks
> (BreakoutScout synthesis, Narrator digests) AND news search (replacing NewsAPI).
> This eliminates the Anthropic API key dependency entirely. At ~100 calls/day
> across synthesis + digest + research, monthly Sonar cost is ~$0.50.
>
> **ROI note:** The $200/mo X Basic tier is the single largest operating cost.
> Stakeholder decision: defer X subscription for 30 days to test baseline system
> performance without it, then evaluate.
