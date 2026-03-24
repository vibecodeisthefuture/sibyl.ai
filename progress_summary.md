---
project: Sibyl.ai
repo: https://github.com/vibecodeisthefuture/sibyl.ai
date: 2026-03-23
sprint_completed: 19
test_count: 528
test_status: all_passing
latest_commit: pending
current_focus: "Sprint 20: Crypto-Only Pivot"
related:
  - config/system_config.yaml
  - config/market_intelligence_config.yaml
  - config/position_lifecycle_config.yaml
  - config/sge_config.yaml
  - config/ace_config.yaml
  - config/portfolio_allocator_config.yaml
  - config/risk_dashboard_config.yaml
  - config/narrator_config.yaml
  - config/breakout_scout_config.yaml
  - config/x_sentiment_config.yaml
  - config/x_stream_rules.yaml
  - config/category_strategies.yaml
  - config/investment_policy_config.yaml
  - docs/phase_5_domain_deployment.md
  - sprint11_stakeholder_summary.md
---

# Sibyl.ai — Development Progress Index

Sibyl.ai is a autonomous prediction market trading agent built on Kalshi with 19 completed sprints. The system has 528 unit tests (all passing), dual-engine architecture (SGE/ACE), 8 category signal pipelines, and a complete policy enforcement framework. Current focus: crypto-only pivot after first live trading test ($171.42 final capital, 26 executions, 30,042 signals).

## Quick Reference

- **docs/sprint_log.md** — Complete history of all 19 sprints with implementation details
- **docs/roadmap.md** — Next steps, Sprint 20+ timeline, PostgreSQL migration strategy
- **docs/architecture.md** — Architectural decisions, system workflow diagram, API costs
- **docs/file_registry.md** — Complete file listing by category (agents, pipelines, clients, tests, config)

## Current Status

- **Sprint Completed:** 19 (First live trading test executed 2026-03-22)
- **Test Count:** 528 (all passing, zero regressions)
- **Current Focus:** Sprint 20 — Crypto-Only Pivot (disable 7 non-crypto pipelines, single SGE engine, fix data plumbing)
- **Live Account:** $171.42 total capital ($200.21 HWM), -$4.30 daily P&L

## Recent Changes (Last 2 Sprints)

**Sprint 18:** Fixed SGE_BLITZ CHECK constraint across 4 tables (positions, performance, executions, engine_state). Full 18-agent stack now starts with ZERO errors. Gap-fill rate limit optimization reduced 429 errors from 100+ to ~18 per cycle.

**Sprint 19:** Executed first-ever live trading test with $200 funded Kalshi account (5h52m, 26 real trades). Fixed critical bugs: pipeline market filtering (29K → 7K actionable), portfolio allocator capital erosion. Generated 30,042 signals across 8 pipelines, verified Kelly sizing, policy enforcement, and risk management. Identified 4 bugs: position P&L tracking, duplicate position rows, ACE/BLITZ engines idle, X sentiment offline.

## Key Stats

- **Data Sources:** 19 async clients across 8 categories (FRED, BLS, ESPN, CoinGecko, etc.)
- **Pipelines:** 8 (crypto, economics, weather, sports, culture, science, geopolitics, financial)
- **Agents:** 18 total (data, intelligence, execution, portfolio, analytics, notifications, research, blitz)
- **Policy Sections:** 19 (tier classification, quality floors, avoidance rules, caps, overrides)
- **Categories:** 12 (Politics, Sports, Culture, Crypto, Climate, Economics, Mentions, Companies, Financials, Weather, Tech & Science, Geopolitics)

## Next Immediate Actions

1. Disable all non-crypto pipelines (system_config.yaml)
2. Disable ACE + BLITZ engines; SGE = 95% capital
3. Implement per-category risk profiles
4. Align routing/execution thresholds (eliminate dead zones)
5. 8-hour crypto-only live test (target >50 trades, break-even P&L)
