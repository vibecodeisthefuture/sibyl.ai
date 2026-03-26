# Sibyl.ai

> **Autonomous prediction market trading agent for Kalshi — currently focused on crypto bracket markets (BTC, ETH, SOL, XRP).**
> Real-time price streaming via Hyperliquid, spread-aware execution, and continuous bracket pricing across 15-minute, hourly, and daily timeframes.

---

Sibyl is a multi-agent autonomous trading system purpose-built for binary prediction markets on [Kalshi](https://kalshi.com). It continuously prices crypto bracket markets using real-time data from [Hyperliquid](https://hyperliquid.xyz), identifies edge via a probabilistic bracket model, and autonomously enters, manages, and exits positions through a hardened execution layer.

The system runs 24/7, manages its own risk exposure through per-category risk profiles, and is designed for self-hosted deployment on a Kubernetes cluster.

---

## The Name

*Sibyl* draws from Roman mythology. The Sibyls were prophetic women believed to be conduits of Apollo — the god of truth and foresight — who delivered divine predictions about the future. They were consulted before consequential decisions, their oracles sought precisely because they could interpret patterns and probabilities invisible to ordinary observation.

The name reflects the system's core purpose: not to gamble on outcomes, but to identify where the market's implied probability diverges from what available evidence actually suggests — and act on that gap before others do.

---

## Current Focus: Crypto Bracket Markets

Sibyl is currently operating in **crypto-only mode**, targeting all BTC, ETH, SOL, and XRP bracket markets on Kalshi across three timeframes:

| Timeframe | Volatility Model | Example |
|:---|:---|:---|
| 15-minute | σ × √(15/1440) ≈ 0.3% | BTC ±$250 brackets |
| Hourly | σ × √(60/1440) ≈ 0.6% | ETH ±$25 brackets |
| Daily | Full daily σ | SOL ±$2.50 brackets |

**Real-time data pipeline** (Hyperliquid, free, no auth):
- 1-second spot prices (allMids)
- 5-second L2 order book snapshots (bid/ask depth, imbalance, wall detection)
- 60-second micro-candles (1m OHLCV + buy pressure) and predicted funding rates
- 300-second historical funding + hourly candles for volatility

**Bracket model enrichments** (capped at ±5% total adjustment):
- Order book imbalance (±3%)
- Cross-exchange funding sentiment (±2%)
- Buy pressure momentum (±2%)

### Planned Expansion

**Weather markets** are the next category planned for re-enablement once the crypto pipeline proves profitable in live trading. Weather has well-defined temperature/rain/snow brackets on Kalshi with established liquidity. Each category receives its own risk profile before activation.

Additional categories (economics, sports, financial, culture, science) remain locked with preserved settings and can be selectively re-enabled as market conditions warrant.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  📡 HyperliquidPriceAgent        Real-time data streaming    │
│  1s prices · 5s L2 book · 60s candles · 300s funding         │
│  Writes to 5 DB tables → pipeline reads from DB              │
└───────────────────────────┬──────────────────────────────────┘
                            │ DB-first architecture
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  🔮 CryptoPipeline              Bracket model + EV engine    │
│  Timeframe-aware vol · Spread-deducted EV · Enriched conf    │
│  BRACKET_MODEL signals for every active market with edge     │
└───────────────────────────┬──────────────────────────────────┘
                            │ signals
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  ⚙️  SGE (Stable Growth Engine)  Signal routing + execution  │
│  Per-category risk profiles · Kelly sizing · Spread-crossing │
│  5-poll fill confirmation · Ghost trade prevention           │
└───────────────────────────┬──────────────────────────────────┘
                            │ positions
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  🛡️ Position Lifecycle Manager   6 sub-routines in parallel  │
│  A: Stop Guard (5-10s loop) · B: EV Monitor                 │
│  C: Exit Optimizer · D: Resolution Tracker                   │
│  E: Correlation Scanner · F: Position Reconciliation (15m)   │
└───────────────────────────┬──────────────────────────────────┘
                            │ state
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  🌡️ Portfolio Health Narrator    6h digest → ntfy.sh         │
│  Immediate alert if ≥2 active alerts                         │
└──────────────────────────────────────────────────────────────┘
```

---

## Execution Hardening (Sprint 22)

Five classes of real-money bugs fixed — informed by analysis of common prediction market bot failures:

1. **Ghost Trade Prevention** — 5-poll fill confirmation; canceled/expired orders abort cleanly
2. **Actual Fill-Price P&L** — entry/exit prices from Kalshi fill data, not snapshots
3. **Spread-Crossing Entries** — limit orders at `best_ask` (YES) / `1-best_bid` (NO) for immediate fills
4. **Position Reconciliation** — 15-min sync between DB and actual Kalshi portfolio
5. **Spread-Aware EV** — EV reduced by half-spread cost; eliminates phantom edge on wide spreads

---

## Key Stats

| Metric | Value |
|:---|:---|
| Sprints Completed | 22 |
| Tests | 528 (108 core passing) |
| Active Pipelines | 1 (crypto) — 7 locked |
| Engine | SGE 95% capital |
| Agents | 19 total |
| DB Tables | 5 new (crypto_spot_prices, crypto_volatility, crypto_order_book, crypto_funding, crypto_micro_candles) |
| Hyperliquid Rate Budget | ~237/1200 weight/min (20%) |
| PositionLifecycleManager Sub-routines | 6 |

---

## Tech Stack

| Layer | Technology |
|:---|:---|
| Language | Python 3.12+ |
| Database | SQLite (WAL mode) — `sibyl.db` |
| Markets | Kalshi REST API |
| Real-time Data | Hyperliquid Info API (free, no auth, 1s polling) |
| LLM Reasoning | Claude Haiku / Sonnet (Anthropic API) |
| Infrastructure | Docker → Kubernetes (homelab) |
| Monitoring | Grafana · Prometheus · ntfy.sh |
| Config | YAML config files |

---

## Quick Start

```bash
# Clone
git clone https://github.com/vibecodeisthefuture/sibyl.ai.git
cd sibyl.ai

# Install
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your Kalshi API key and other credentials

# Paper mode (recommended first)
python -m sibyl --mode paper --agents all

# Live mode
python -m sibyl --mode live --agents all
```

---

## Documentation

| File | Contents |
|:---|:---|
| `progress_summary.md` | Sprint-by-sprint development index |
| `docs/roadmap.md` | Sprint 22+ timeline, category re-enablement strategy |
| `docs/architecture.md` | Architectural decisions, system workflow, API costs |
| `docs/file_registry.md` | Complete file listing by category |
| `docs/sprint_log.md` | Full sprint history with implementation details |

---

*Self-hosted · Homelab-deployed · Kubernetes · Crypto-first*
