# AugurSight.ai

> **Fully autonomous prediction market investing system for Polymarket and Kalshi.**
> Monitors markets 24/7, discovers opportunities through multi-source intelligence, and executes positions across two parallel capital engines — without human intervention.

---

AugurSight is a multi-agent autonomous trading system purpose-built for binary prediction markets. It continuously monitors [Polymarket](https://polymarket.com) and [Kalshi](https://kalshi.com) for price inefficiencies, informed trader activity, and breakout opportunities — then autonomously enters, manages, and exits positions through a **dual-engine architecture** that separates stable compounding from aggressive alpha capture.

The system is designed to run 24/7 on a self-hosted Kubernetes cluster, manage its own risk exposure, and improve signal accuracy over time through a Phase 2 learning layer.

---

## Dual-Engine Architecture

All capital is deployed through two parallel engines, each with its own risk policy, signal preferences, and execution style:

```
Total Portfolio
├── Stable Growth Engine (SGE)
│   Consistent, EV-positive positions. Patient execution.
│   Preferred signals: Arbitrage, Mean Reversion, Liquidity Vacuum
│
└── Alpha Capture Engine (ACE)
    High-conviction, fast-cycle opportunities.
    Preferred signals: Momentum, Volume Surge, Stale Market
```

A **Portfolio Allocator** sits above both engines, enforcing the 70/30 split at all times, blocking cross-engine correlated exposure, and issuing circuit breakers if either engine hits its drawdown threshold.

---

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│  🔍 Breakout Scout           External intelligence layer│
│  Discovers new markets       News · Reddit · X · Perplexity
└──────────────────────────┬──────────────────────────────┘
                           │ research packets
                           ▼
┌─────────────────────────────────────────────────────────┐
│  🔭 Market Intelligence      Surveillance + Reasoning   │
│  Whale detection · Volume anomaly · Order book depth    │
│  LLM reasoning pass → enriched signals                  │
└──────────────────────────┬──────────────────────────────┘
                           │ signals
                           ▼
              Signal Generator + Signal Router
              ┌─────────────┴─────────────┐
              ▼                           ▼
         SGE (70%)                   ACE (30%)
         Executors                   Executors
              └─────────────┬─────────────┘
                            │ open positions
                            ▼
┌─────────────────────────────────────────────────────────┐
│  🛡️  Position Lifecycle Manager   Full position custody │
│  Stop Guard · EV Monitor · Exit Optimizer               │
│  Resolution Tracker · Correlation Scanner               │
└──────────────────────────┬──────────────────────────────┘
                           │ system state
                           ▼
┌─────────────────────────────────────────────────────────┐
│  🌡️  Portfolio Health Narrator   Human oversight layer  │
│  6-hour digest → augursight-log.md + push notifications │
└─────────────────────────────────────────────────────────┘
```

---

## Agent Layer

AugurSight's intelligence is distributed across four specialized agents that run in parallel:

**🔭 Market Intelligence Agent** — The system's surveillance and reasoning core. Three detection modes run continuously: whale trade detection (unusually large bets), volume anomaly detection (z-score spikes above 30-day rolling baseline), and order book depth monitoring (spread expansion, liquidity walls, vacuum detection). Detection events feed an async LLM reasoning worker that assesses signal actionability, generates counter-theses, and routes enriched signals to the appropriate engine. When multiple detection modes fire on the same market simultaneously, the signal is escalated as a high-conviction composite.

**🛡️ Position Lifecycle Manager** — Owns every open position from entry to close. Five sub-routines operate in parallel within a single process: a Stop Guard running on a 5–10 second hard-stop loop (isolated thread, cannot be blocked); an EV Monitor recalculating expected value on all positions continuously; an Exit Optimizer looking for EV capture, inversion, and momentum stall exit triggers; a Resolution Tracker watching convergence and late-stage opportunity windows; and a Correlation Scanner detecting when multiple positions are exposed to the same underlying real-world event.

**🔍 Breakout Scout Agent** — The only agent that looks outside the currently-tracked market universe. Runs a 15-minute discovery loop across both platforms, scoring new and rapidly-emerging markets against quantitative breakout criteria (volume growth, odds velocity, listing recency, category heat). Qualifying markets receive a full multi-source research pipeline: authoritative news (NewsAPI), synthesized consensus (Perplexity sonar-pro), high-signal social (X/Twitter v2 API), and community sentiment (Reddit PRAW). Results are synthesized by an LLM into a structured public consensus packet — BULLISH, BEARISH, CONTESTED, or NEUTRAL — and injected into the Market Intelligence Agent's reasoning context.

**🌡️ Portfolio Health Narrator** — The human oversight layer. Every 6 hours, generates a plain-English digest covering both engines' positions and P&L, Allocator status and active correlation blocks, Scout pipeline activity, and a one-sentence system health verdict. Pushes to a configured notification channel (ntfy.sh, Slack, or Grafana). If two or more active alerts are present, the digest fires immediately rather than waiting for the next scheduled window. Future integration with OpenClaw planned.

---

## Signal Types

| Signal | Engine | Trigger |
|:--|:--|:--|
| Arbitrage | SGE | Same event priced differently across Polymarket and Kalshi |
| Mean Reversion | SGE | Odds at extreme with declining volume |
| Liquidity Vacuum | SGE | Wide spread + low depth near resolution |
| Momentum | ACE | Strong directional price move with volume confirmation |
| Volume Surge | ACE | z-score spike implying informed trader activity |
| Stale Market | ACE | Price flat despite new real-world information |
| High-Conviction Arb | ACE | Cross-platform spread > 8% with high liquidity |

---

## Tech Stack

| Layer          | Technology                                                                      |
| :------------- | :------------------------------------------------------------------------------ |
| Language       | Python                                                                          |
| Database       | SQLite (WAL mode) — `augursight.db`                                             |
| Markets        | Polymarket CLOB REST/WebSocket · Kalshi REST/WebSocket                          |
| Intelligence   | Perplexity sonar-pro · X/Twitter v2 API · Reddit PRAW · NewsAPI                 |
| LLM reasoning  | Claude Haiku (routine signals) · Claude Sonnet (high-conviction / whale events) |
| Infrastructure | Kubernetes host (homelab)                                                       |
| Monitoring     | Grafana · Prometheus · ntfy.sh                                                  |
| Config         | YAML config files                                                               |

---

## Project Status

> **Phase 1 — In Design.** Architecture, agent specifications, signal framework, risk policies, and database schemas are complete. Active development begins next.

**Phase 1 goal:** All four agents live, both engines executing real positions, 30+ closed positions in the `performance` table, and 14 consecutive stable days in production.

**Phase 2** (post Phase 1 stability) adds three learning-layer agents:
- **📋 Opportunity Queue Manager** — manages and re-prioritizes deferred signals by EV decay rate
- **📐 Signal Quality Calibrator** — recalibrates confidence thresholds from resolved position outcomes (human review required before any changes are applied)
- **🔬 Post-Mortem Agent** — structured close-analysis per position, feeding the Calibrator and building long-term signal memory

---

## Future Roadmap — TradeBot Integration

AugurSight is planned for eventual integration as a **subsidiary prediction market module** within [TradeBot](https://github.com/vibecodeisthefuture/algorithmic-tradebot), an autonomous multi-asset algorithmic trading system running on the same homelab infrastructure. In the integrated architecture, TradeBot's Manager Agent becomes the parent orchestrator and controls the capital allocated to prediction markets. AugurSight's Portfolio Allocator operates within that allocation, preserving the 70/30 SGE/ACE split internally.

Key integration points include bidirectional signal sharing (TradeBot's crypto news feeds AugurSight's ACE; AugurSight's economic event odds feed TradeBot's macro-sensitive strategies), unified circuit breaker coordination, and a shared analytics layer for cross-system performance reporting. Both systems already share the same Kubernetes cluster, VLAN, and monitoring stack.

The goal is loose coupling — AugurSight runs fully standalone today, and integration signals will be optional enrichment rather than hard dependencies.

---

## Documentation

| Note | Contents |
|:--|:--|
| `augursight-overview.md` | Full architecture, signal framework, risk policies, platform specs, database schema |
| `augursight-agents-graduated.md` | Detailed agent specifications — internal architecture, config stubs, I/O contracts, Phase 2 roadmap |
| `augursight-agent-brainstorm.md` | Full brainstorm of 20 agents across 5 layers; priority rankings; open questions |
| `dev-pivot-augursight-focus.md` | Development focus decision record; TradeBot pause rationale; integration vision |

---

*Self-hosted · Homelab-deployed · Kubernetes*
