---
project: Sibyl — Prediction Market Tracker & Autonomous Investing Agent
status: active
started: 2026-03-11
area: personal
type: project
tags:
  - project
  - prediction-markets
  - automation
  - ai-agent
  - polymarket
  - kalshi
  - python
  - homelab
  - sibyl
related:
  - "[[Projects/tradebot-overview]]"
  - "[[Projects/homelab_plan]]"
---

# Sibyl — Prediction Market Tracker & Autonomous Investing Agent

> Multi-agent autonomous system that continuously monitors **Polymarket** and **Kalshi** for market trends, price inefficiencies, and trade opportunities — then executes positions 100% autonomously across two parallel engines: a **Stable Growth Engine (SGE)** managing 70% of the portfolio and an **Alpha Capture Engine (ACE)** deploying the remaining 30% on high-conviction aggressive opportunities.

---

## Goal

Build and operate a fully autonomous prediction market investing system with a **dual-engine architecture**. The **Stable Growth Engine (SGE)** deploys 70% of capital into consistent, EV-positive positions with disciplined risk management — compounding returns steadily over time. The **Alpha Capture Engine (ACE)** deploys 30% of capital into high-conviction, high-upside opportunities where aggressive position sizing and fast execution can generate outsized returns. Both engines share the same data and analysis infrastructure but operate under distinct risk policies, signal preferences, and execution strategies — managed by a top-level Portfolio Allocator that enforces the 70/30 capital split at all times.

---

## Scope

**In scope:**
- Real-time monitoring of Polymarket and Kalshi markets (price, volume, liquidity, order book)
- Trend detection, momentum analysis, and market inefficiency identification across both platforms
- Autonomous signal generation routed to the appropriate engine based on signal type and confidence
- Dual-engine execution: SGE (conservative, diversified) + ACE (aggressive, concentrated)
- Portfolio Allocator enforcing the 70/30 capital split and per-engine circuit breakers
- Dynamic risk management with per-engine exposure caps and portfolio-level circuit breakers
- Performance analytics and signal quality tracking separately per engine
- Integration with TradeBot's existing homelab infrastructure (Node 3, VLAN 20)

**Out of scope:**
- Manual/discretionary trade placement
- Fundamental research (e.g., reading news to form political opinions)
- Markets requiring legal identity verification beyond current account setup
- Options or derivatives on prediction market positions

---

## Dual-Engine Architecture

### Capital Allocation

```
Total Portfolio (100%)
├── Stable Growth Engine (SGE) ────────── 70% of capital
│   └── Goal: Consistent compounding via disciplined, diversified EV+ positions
└── Alpha Capture Engine (ACE) ─────────  30% of capital
    └── Goal: Hyper-scaling through concentrated, high-conviction opportunities
```

### Engine Comparison

| Dimension | Stable Growth Engine (SGE) | Alpha Capture Engine (ACE) |
|:---|:---|:---|
| **Capital allocation** | 70% of total portfolio | 30% of total portfolio |
| **Risk policy** | CONSERVATIVE → MODERATE | AGGRESSIVE |
| **Kelly fraction** | 0.15× fractional Kelly | 0.35× fractional Kelly |
| **Min EV threshold** | +3% | +6% |
| **Min confidence** | 0.60 | 0.68 |
| **Max single position** | 2% of engine capital | 8% of engine capital |
| **Max platform exposure** | 25% of engine capital | 45% of engine capital |
| **Position style** | Diversified, long-duration | Concentrated, fast-cycle |
| **Circuit breaker** | −10% engine drawdown | −18% engine drawdown |
| **Preferred signals** | Arbitrage, Mean Reversion, Liquidity Vacuum | Momentum, Volume Surge, Stale Market |
| **Execution cadence** | Patient — waits for best fill | Aggressive — prioritizes speed |
| **Rebalance trigger** | Daily | Real-time on signal |

### Portfolio Allocator

A top-level **Portfolio Allocator** agent sits above both engines and is responsible for:

- Enforcing the 70/30 capital split at all times
- Reallocating capital between engines after positions close to restore the target ratio
- Blocking engine activity if either engine's circuit breaker trips
- Reporting combined P&L, total exposure, and cross-engine risk to the Analytics Agent
- Preventing correlated risk concentration across both engines simultaneously (e.g., both engines long on the same market or event)

The Allocator does not generate signals or execute trades — it only manages capital boundaries and inter-engine risk.

---

## Architecture Overview

### Core Loop (Per Engine)

```
Monitor → Analyze → Signal → Route → Validate → Execute → Track → Report
                                ↓
                    [Portfolio Allocator]
                    ├── SGE (70% capital)
                    └── ACE (30% capital)
```

### Agent Hierarchy

```
Portfolio Allocator (70/30 capital enforcer + cross-engine risk guard)
│
├── SHARED INFRASTRUCTURE
│   ├── Data Layer
│   │   ├── Polymarket Monitor Agent    — streams market data, orderbooks, trade activity
│   │   ├── Kalshi Monitor Agent        — streams market data, orderbooks, trade activity
│   │   └── Cross-Platform Sync Agent  — unifies data; detects platform divergence
│   └── Analysis Layer
│       ├── Trend Analysis Agent        — detects momentum, volume surges, odds drift
│       ├── Arbitrage Scout Agent       — identifies pricing discrepancies across platforms
│       └── Sentiment & News Agent      — lightweight macro signal layer (Phase 2)
│
├── Signal Router                       — classifies signals by type + confidence; routes to SGE or ACE
│
├── STABLE GROWTH ENGINE (SGE — 70%)
│   ├── SGE Signal Filter              — enforces min EV +3%, confidence ≥ 0.60, signal whitelist
│   ├── SGE Executor
│   │   ├── Polymarket SGE Executor    — places conservative positions via CLOB API
│   │   └── Kalshi SGE Executor        — places conservative positions via REST API
│   └── SGE Portfolio Tracker          — enforces CONSERVATIVE/MODERATE risk policy + circuit breaker
│
├── ALPHA CAPTURE ENGINE (ACE — 30%)
│   ├── ACE Signal Filter              — enforces min EV +6%, confidence ≥ 0.68, momentum priority
│   ├── ACE Executor
│   │   ├── Polymarket ACE Executor    — aggressive CLOB order placement, prioritizes speed
│   │   └── Kalshi ACE Executor        — aggressive REST order placement
│   └── ACE Portfolio Tracker          — enforces AGGRESSIVE risk policy + circuit breaker
│
└── Analytics Agent                    — unified performance reporting across both engines
```

---

## Platform Overview

### Polymarket

| Property | Detail |
|:---|:---|
| **Type** | Decentralized prediction market (Polygon blockchain) |
| **Settlement** | USDC |
| **Token standard** | ERC-1155 Conditional Tokens |
| **API** | CLOB (Central Limit Order Book) REST API + WebSocket stream |
| **Key endpoint** | `https://clob.polymarket.com` |
| **Auth** | API key + Ethereum wallet signing (private key required) |
| **Order types** | Limit and market orders on binary outcome tokens |
| **Minimum position** | ~$1 USDC equivalent |

### Kalshi

| Property | Detail |
|:---|:---|
| **Type** | US-regulated (CFTC-licensed) event contract exchange |
| **Settlement** | USD (real money, regulated) |
| **API** | REST API + WebSocket |
| **Key endpoint** | `https://trading-api.kalshi.com/trade-api/v2` |
| **Auth** | Email/password JWT or API key |
| **Order types** | Limit and market orders on YES/NO binary contracts |
| **Minimum position** | $0.01 per contract |
| **Regulation** | CFTC-regulated — US persons may participate |

---

## Data Infrastructure

### Live Data Streams (Per Platform)

| Data Type | Polymarket | Kalshi | Update Frequency |
|:---|:---|:---|:---|
| Market list & metadata | ✅ REST | ✅ REST | Every 5 minutes |
| Current odds (YES price) | ✅ CLOB REST | ✅ REST | Every 30 seconds |
| Order book (L2) | ✅ WebSocket | ✅ WebSocket | Real-time |
| Recent trades | ✅ REST | ✅ REST | Every 60 seconds |
| Volume (24h) | ✅ REST | ✅ REST | Every 5 minutes |
| Open interest | ✅ REST | ✅ REST | Every 5 minutes |
| My open positions | ✅ REST | ✅ REST | Every 60 seconds |
| My trade history | ✅ REST | ✅ REST | On demand / daily sync |

### Database Schema (SQLite — `sibyl.db`)

```
sibyl.db
├── markets            — all tracked markets (id, platform, title, category, close_date, status)
├── prices             — historical price snapshots per market (market_id, timestamp, yes_price, volume, oi)
├── orderbook          — L2 orderbook snapshots (market_id, timestamp, bids, asks as JSON)
├── trades_log         — all observed market trades (market_id, timestamp, side, size, price)
├── signals            — generated signals (market_id, timestamp, signal_type, confidence, ev_estimate, routed_to: SGE|ACE|BOTH)
├── positions          — open positions (market_id, platform, engine: SGE|ACE, side, size, entry_price, current_price, pnl)
├── executions         — trade execution log (signal_id, engine, timestamp, platform, order_id, fill_price, size)
├── performance        — per-signal outcome tracking (signal_id, engine, resolved, correct, pnl, ev_realized)
├── engine_state       — per-engine capital, exposure, circuit_breaker_status, drawdown (engine: SGE|ACE)
└── system_state       — overall risk policy, allocator status, last sync timestamps
```

> **Note:** All `positions`, `executions`, and `performance` records are tagged with `engine: SGE | ACE` to enable fully separate performance accounting per engine.

---

## Signal Generation & Routing Framework

### Signal Types & Engine Routing

| Signal | Description | Min Confidence | Routed To |
|:---|:---|:---|:---|
| **Arbitrage** | Same event priced differently on Polymarket vs. Kalshi | ≥ 0.70 | SGE (primary) |
| **Mean Reversion** | Odds at extreme with declining volume — likely overcorrected | ≥ 0.60 | SGE |
| **Liquidity Vacuum** | Large spread + low volume near resolution — EV opportunity | ≥ 0.62 | SGE |
| **Momentum** | Odds moving strongly in one direction with volume confirmation | ≥ 0.68 | ACE (primary) |
| **Volume Surge** | Unusual trading activity spike — informed trader signal | ≥ 0.70 | ACE |
| **Stale Market** | Price hasn't moved despite new real-world information | ≥ 0.65 | ACE |
| **High-Conviction Arb** | Arbitrage with spread > 8% + high liquidity | ≥ 0.80 | ACE |
| **Dual-Engine** | Signal qualifies under both engines' criteria | varies | Allocator decides split |

### Signal Router Logic

```
Signal generated by Signal Generator
    ↓
[Signal Router]
    ├── IF signal_type IN [Arbitrage, Mean Reversion, Liquidity Vacuum]
    │   AND confidence ≥ SGE_threshold
    │   AND SGE capital available
    │   → Route to SGE Signal Filter
    │
    ├── IF signal_type IN [Momentum, Volume Surge, Stale Market, High-Conviction Arb]
    │   AND confidence ≥ ACE_threshold
    │   AND ACE capital available
    │   → Route to ACE Signal Filter
    │
    ├── IF signal qualifies for BOTH engines
    │   → Send to Portfolio Allocator for split decision
    │
    └── IF no engine has available capital
        → Log as DEFERRED; retry on next cycle
```

### EV Calculation (Per Engine)

**SGE (conservative sizing):**
```
EV = (P_win × (1 - entry_price)) - (P_loss × entry_price)
Position size = 0.15 × Kelly fraction × SGE_available_capital
Min EV threshold: +3%
```

**ACE (aggressive sizing):**
```
EV = (P_win × (1 - entry_price)) - (P_loss × entry_price)
Position size = 0.35 × Kelly fraction × ACE_available_capital
Min EV threshold: +6%
```

---

## Risk Management Framework

### Per-Engine Risk Policy

| Risk Dimension | SGE (70% of portfolio) | ACE (30% of portfolio) |
|:---|:---|:---|
| **Active risk policy** | MODERATE (default) → CONSERVATIVE | AGGRESSIVE |
| **Max single position** | 2% of engine capital | 8% of engine capital |
| **Max per-platform exposure** | 25% of engine capital | 45% of engine capital |
| **Max total engine exposure** | 55% of engine capital | 80% of engine capital |
| **Circuit breaker** | −10% engine drawdown | −18% engine drawdown |
| **Daily loss limit** | −4% of engine capital | −8% of engine capital |
| **Per-market stop** | Exit at −35% of position value | Exit at −50% of position value |
| **Kelly fraction** | 0.15× | 0.35× |

### Portfolio-Level Circuit Breakers (Allocator)

- **Total portfolio circuit breaker**: Halt all new entries across both engines if combined drawdown exceeds −15%
- **Cross-engine correlation block**: Prevent both engines from holding simultaneous long exposure on the same underlying event at >5% combined capital
- **Capital floor**: If either engine drops below 50% of its target allocation due to losses, the Allocator pauses that engine and flags for manual review before resuming
- **Rebalance trigger**: If either engine drifts more than ±5% from its target allocation (70%/30%), the Allocator queues a rebalance on next position close

### Market Category Caps (Shared — % of each engine's capital)

| Category | SGE Cap | ACE Cap | Rationale |
|:---|:---|:---|:---|
| Politics / Elections | 12% | 20% | High variance; ACE can capitalize on momentum events |
| Sports | 20% | 25% | Well-priced; SGE steady, ACE for volume surges |
| Crypto / Finance | 15% | 25% | Correlated with TradeBot — watch total combined exposure |
| Science / Tech | 15% | 15% | Lower liquidity — same cap both engines |
| Economics / Macro | 15% | 10% | Long resolution; ACE avoids slow-cycle markets |
| Other / Misc | 10% | 10% | Catch-all cap |

---

## Automation Workflows

1. **Continuous Market Scan** — Monitor Agents poll both platforms every 30s; update `prices` and `orderbook` tables; flag unusual volume/spread changes
2. **Signal Generation Loop** — Signal Generator runs every 60s on latest data; scores all tracked markets; writes qualifying signals to `signals` table with engine routing tag
3. **Signal Routing** — Signal Router classifies each new signal and dispatches to the appropriate engine's filter queue; deferred signals are retried on next cycle
4. **SGE Execution Loop** — SGE Executor Agents consume SGE-routed signals; apply MODERATE risk policy; place patient limit orders; log to `executions` with `engine=SGE`
5. **ACE Execution Loop** — ACE Executor Agents consume ACE-routed signals; apply AGGRESSIVE risk policy; prioritize speed (market orders on high-conviction entries); log to `executions` with `engine=ACE`
6. **Per-Engine Position Monitor** — Each engine's Portfolio Tracker runs every 2 minutes; checks P&L vs. engine-specific stops and daily limits; enforces circuit breakers independently
7. **Portfolio Allocator Cycle** — Allocator runs every 5 minutes; checks 70/30 capital split drift; blocks cross-engine correlated exposure; queues rebalances as needed
8. **Market Resolution Tracker** — Detects resolved markets; calculates final P&L per engine; updates `performance` table; feeds outcome back to signal quality model
9. **Cross-Platform Arbitrage Check** — Cross-Platform Sync Agent compares pricing on matching events every 5 minutes; generates `ARBITRAGE` signal (routed to SGE) or `HIGH-CONVICTION ARB` signal (routed to ACE) based on spread size
10. **Daily Analytics Digest** — Analytics Agent generates daily performance summary at midnight: win rate, EV realized vs. estimated, top/worst signals, and **separate P&L breakdowns for SGE and ACE**
11. **Weekly Review Log** — Every Monday: writes structured performance entry to `sibyl-log.md`; includes engine-level attribution and flags underperforming signal types per engine

---

## Key Files & Directories

```
sibyl/
├── README.md                                — Quick navigation
├── agents/
│   ├── allocator/                           — Portfolio Allocator: 70/30 enforcement + cross-engine risk
│   ├── monitors/
│   │   ├── polymarket_monitor/              — Polymarket data ingestion agent
│   │   └── kalshi_monitor/                  — Kalshi data ingestion agent
│   ├── analysis/
│   │   ├── trend_analysis/                  — Momentum, drift, volume analysis
│   │   ├── arbitrage_scout/                 — Cross-platform pricing divergence
│   │   └── sentiment_news/                  — (Phase 2) Macro signal layer
│   ├── signal_generator/                    — EV-weighted signal ranking engine
│   ├── signal_router/                       — Routes signals to SGE or ACE based on type + confidence
│   ├── sge/                                 — Stable Growth Engine (70%)
│   │   ├── sge_signal_filter/               — SGE-specific signal validation (EV ≥ 3%, conf ≥ 0.60)
│   │   ├── executors/
│   │   │   ├── polymarket_sge_executor/     — Patient CLOB order placement for SGE
│   │   │   └── kalshi_sge_executor/         — Patient REST order placement for SGE
│   │   └── sge_portfolio_tracker/           — MODERATE risk policy + SGE circuit breaker
│   ├── ace/                                 — Alpha Capture Engine (30%)
│   │   ├── ace_signal_filter/               — ACE-specific signal validation (EV ≥ 6%, conf ≥ 0.68)
│   │   ├── executors/
│   │   │   ├── polymarket_ace_executor/     — Aggressive CLOB order placement for ACE
│   │   │   └── kalshi_ace_executor/         — Aggressive REST order placement for ACE
│   │   └── ace_portfolio_tracker/           — AGGRESSIVE risk policy + ACE circuit breaker
│   └── analytics/                           — Unified performance reporting (per-engine breakdowns)
├── data/
│   ├── sibyl.db                             — SQLite blackboard (all system state, engine-tagged)
│   └── state/
│       ├── allocator_state.json             — 70/30 split status, rebalance queue, cross-engine locks
│       ├── sge_policy.json                  — SGE active risk policy + circuit breaker status
│       ├── ace_policy.json                  — ACE active risk policy + circuit breaker status
│       └── open_positions.json              — Live snapshot of all open positions (engine-tagged)
├── config/
│   ├── system_config.yaml                   — All system-wide settings (thresholds, caps, schedules)
│   ├── sge_config.yaml                      — SGE-specific thresholds, Kelly fraction, signal whitelist
│   ├── ace_config.yaml                      — ACE-specific thresholds, Kelly fraction, signal whitelist
│   ├── markets_watchlist.yaml               — Manually curated high-priority markets
│   └── .env                                 — API credentials (never hardcoded)
└── docs/
    ├── SIGNAL_REGISTRY.md                   — Log of all generated signals, engine routing, and outcomes
    ├── PLATFORM_NOTES.md                    — API quirks, rate limits, gotchas per platform
    ├── DATA_SCHEMAS.md                      — Full table schemas for sibyl.db
    ├── SGE_PLAYBOOK.md                      — SGE operating principles, signal preferences, tuning notes
    └── ACE_PLAYBOOK.md                      — ACE operating principles, high-conviction criteria, tuning notes
```

---

## Integration with TradeBot

Sibyl runs as a **sibling project** to TradeBot on the same homelab infrastructure. Key integration points:

| Touchpoint | Detail |
|:---|:---|
| **Infrastructure** | Node 3, VLAN 20, same Kubernetes cluster |
| **Shared signals** | Crypto market signals from TradeBot's Market News Agent can feed Sibyl's crypto/finance monitoring — consumed by ACE preferentially |
| **Risk awareness** | Portfolio Allocator is aware of TradeBot's total crypto exposure to prevent double-exposure across both systems |
| **Shared analytics stack** | Both projects write to a shared `analytics/` module for unified performance reporting |
| **Homelab monitoring** | Same Grafana/Prometheus stack on Node 3 monitors both systems; SGE and ACE tracked as separate services |

---

## Current Status

*As of 2026-03-11*

| Layer | Component | Status |
|:---|:---|:---:|
| Allocator | Portfolio Allocator | ⬜ Not started |
| Data | Polymarket Monitor Agent | ⬜ Not started |
| Data | Kalshi Monitor Agent | ⬜ Not started |
| Data | Cross-Platform Sync Agent | ⬜ Not started |
| Analysis | Trend Analysis Agent | ⬜ Not started |
| Analysis | Arbitrage Scout Agent | ⬜ Not started |
| Signal | Signal Generator Agent | ⬜ Not started |
| Signal | Signal Router | ⬜ Not started |
| SGE | SGE Signal Filter | ⬜ Not started |
| SGE | Polymarket SGE Executor | ⬜ Not started |
| SGE | Kalshi SGE Executor | ⬜ Not started |
| SGE | SGE Portfolio Tracker | ⬜ Not started |
| ACE | ACE Signal Filter | ⬜ Not started |
| ACE | Polymarket ACE Executor | ⬜ Not started |
| ACE | Kalshi ACE Executor | ⬜ Not started |
| ACE | ACE Portfolio Tracker | ⬜ Not started |
| Portfolio | Analytics Agent | ⬜ Not started |

> Update status as: ⬜ Not started → 🔄 In progress → 🧪 Testing → ✅ Live

---

## Open Questions

- [ ] What Polymarket wallet/account will be used? (Private key management strategy)
- [ ] What is the total starting capital, and how does the 70/30 split translate to dollar amounts?
- [ ] Should ACE and SGE use the same Polymarket/Kalshi accounts, or separate accounts for cleaner capital isolation?
- [ ] Should Sibyl share the TradeBot SQLite instance or use a separate `sibyl.db`?
- [ ] How should the Allocator handle a rebalance when one engine has open positions that can't immediately be closed?
- [ ] Phase 2: Add a News/Sentiment agent — what data source? (GDELT, NewsAPI, Perplexity?) — Signals from this layer would be routed preferentially to ACE.
- [ ] Should ACE be allowed to take short positions (NO side) on momentum plays, or only long (YES)?
- [ ] How to handle Kalshi's mandatory settlement delays on resolved contracts across both engines?
- [ ] Rate limit strategy: Polymarket CLOB is ~10 req/s; Kalshi is 10 req/s — sufficient when both SGE and ACE executors are running concurrently?
- [ ] Should the Signal Router log all routing decisions (including deferred signals) for post-hoc engine performance attribution?

---

## Next Actions

- [ ] **Set up `sibyl/` repo** — Initialize project structure per directory layout above; create `sge/` and `ace/` subdirectories
- [ ] **Create `sibyl.db`** — Implement schema with `engine` column on `positions`, `executions`, and `performance` tables
- [ ] **Build Portfolio Allocator** — Implement 70/30 capital split enforcement, cross-engine correlation block, and rebalance queue
- [ ] **Build Polymarket + Kalshi Monitor Agents** — Connect to APIs; begin streaming shared market data
- [ ] **Build Trend Analysis + Arbitrage Scout Agents** — Shared analysis layer feeding the Signal Generator
- [ ] **Build Signal Generator + Signal Router** — Implement EV calculation + signal type classification + engine routing logic
- [ ] **Build SGE Signal Filter + SGE Portfolio Tracker** — Apply SGE-specific thresholds and MODERATE risk policy
- [ ] **Build ACE Signal Filter + ACE Portfolio Tracker** — Apply ACE-specific thresholds and AGGRESSIVE risk policy
- [ ] **Paper-run both engines (signal-only mode)** — Run both engines in dry-run mode for 2–4 weeks; evaluate EV accuracy and routing quality before enabling execution
- [ ] **Build Executor Agents (SGE + ACE per platform)** — Wire order placement once signal quality is validated for each engine independently
- [ ] **Deploy to Node 3 / VLAN 20** — Migrate from local dev to homelab Kubernetes cluster; run SGE and ACE as separate services

---

## Log

**2026-03-11**: Project initialized. Architecture, platform specs, data schema, signal framework, and risk policy documented. Repo not yet created — documentation phase complete. Ready for Phase 1 build.

**2026-03-11**: Architecture restructured into dual-engine model. Single monolithic system split into **Stable Growth Engine (SGE)** managing 70% of portfolio capital under MODERATE risk policy, and **Alpha Capture Engine (ACE)** managing 30% of portfolio capital under AGGRESSIVE risk policy. Portfolio Allocator agent added as top-level capital enforcer. Signal Router added between Signal Generator and execution layer to classify and dispatch signals by engine. Database schema updated to tag all positions, executions, and performance records by engine. Agent hierarchy, risk framework, directory structure, and next actions updated to reflect dual-engine design.

**2026-03-16**: Project renamed from AugurSight to **Sibyl** — named after the prophetic women of Roman mythology, linked to Apollo, who delivered divine predictions.

---

## Related Notes
- [[Projects/tradebot-overview]] — Sibling project; shared infrastructure, risk awareness, and analytics stack
- [[Projects/homelab_plan]] — Infrastructure context (Node 3 / VLAN 20 / Kubernetes cluster)
- [[Projects/tradebot-predictions-agent-brainstorm]] — Forecasting models applicable to signal confidence scoring in both SGE and ACE
