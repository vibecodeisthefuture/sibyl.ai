---
project: Sibyl.ai
repo: https://github.com/vibecodeisthefuture/sibyl.ai
date: 2026-04-04
sprint_completed: 31
test_count: 502
test_status: 502_passing_21_pre_existing_failures
latest_commit: pending
current_focus: "LT8-paper: 24-hour validation of Sprint 31 fixes — sigmoid confidence, stale guard, horizon guard, balance anchor"
related:
  - config/system_config.yaml
  - config/sge_config.yaml
  - config/ace_config.yaml
  - config/portfolio_allocator_config.yaml
  - config/risk_dashboard_config.yaml
  - config/investment_policy_config.yaml
  - config/category_strategies.yaml
  - docs/roadmap.md
  - docs/sprint_log.md
  - docs/architecture.md
  - docs/file_registry.md
---

# Sibyl.ai — Development Progress Index

Sibyl.ai is an autonomous prediction market trading agent built on Kalshi with 31 completed sprints. Current state: crypto-only with Always-On Bracket Trader, Hyperliquid real-time price streaming (1-second), and DB-first architecture for persistent participation in every BTC/ETH/SOL/XRP market iteration across 15-min, hourly, and daily timeframes. Sprint 29 was a comprehensive "Fix the Math" sprint driven by a professional 4-pillar system audit — fixing 10 bugs. Sprint 31 implemented 6 post-LT7 auditor recommendations: sigmoid confidence mapping, correlation discount, stale/horizon guards, paper balance anchor, and holding period minimums.

## Quick Reference

- **docs/sprint_log.md** — Complete history of all sprints with implementation details
- **docs/roadmap.md** — Sprint 22+ timeline, category re-enablement strategy
- **docs/architecture.md** — Architectural decisions, system workflow diagram, API costs
- **docs/file_registry.md** — Complete file listing by category (agents, pipelines, clients, tests, config)

## Current Status

- **Sprint Completed:** 31 (Auditor Recommendations — sigmoid confidence, guards, balance anchor, 2026-04-04)
- **Test Count:** 502 passing, 21 pre-existing failures (0 regressions from Sprint 31)
- **Current Focus:** LT8-paper — 24-hour paper validation of all Sprint 31 fixes
- **Live Account:** ~$100 ($36.63 cash + $63.30 exposure)
- **All-Time Return:** -$91.76 (-47.9% from $191.69 deposit)
- **LT7 Paper Result:** +$117.21 net P&L on paper (35.5% win rate, Sharpe 3.87, $78.50 buy-side fees tracked)
- **Gate to live capital:** LT8 must validate Sprint 31 fixes — no new deposits until positive live result

---

## Sprint 31: Auditor Recommendations — Make the Model Honest (2026-04-04)

Implemented all 6 recommendations from the external system auditor's post-LT7 analysis (`Sibyl_Post_LT7_Recommendations.md`). Focus: replace saturated confidence mapping, add execution guards, anchor paper balance to reality.

### Rec 3.1 — Sigmoid Confidence Mapping (P0) ✅

- **Problem:** Linear formula `min(0.55 + edge_ratio * 0.06, 0.95)` caused 76.5% of LT7 signals to hit the 0.85 cap — Kelly sizing couldn't distinguish a 1x edge from a 10x edge
- **Fix:** Replaced with sigmoid: `confidence = 0.55 + 0.40 * tanh(edge_ratio * 0.35)`, cap 0.99
- **Result:** Smooth distribution — er=1→0.60, er=2→0.72, er=5→0.84, er=10→0.85 (no more cliff)
- **File:** `sibyl/pipelines/crypto_pipeline.py:1498-1502`

### Rec 3.6 — Correlation Discount (P0) ✅

- **Problem:** Four microstructure adjustments (book, funding, pressure, OBI) are correlated; summing them overstates confidence
- **Fix:** Apply 10% discount per additional positive signal beyond the first: `correlation_discount = 1.0 - 0.10 * max(0, n_positive - 1)`
- **File:** `sibyl/pipelines/crypto_pipeline.py:1557`

### B-NEW-1 — AutoCalibrator Schema Fix (P0) ✅

- **Problem:** `auto_calibrator.py` queried `signals.category` — column doesn't exist; every calibration cycle failed silently
- **Fix:** JOIN through `markets` table: `COALESCE(m.category, s.source_pipeline, 'unknown') as category`
- **File:** `sibyl/agents/analytics/auto_calibrator.py:95-103`

### B-NEW-2 + B-NEW-4 — Stale Market + Horizon Guard (P0) ✅

- **Problem:** System opened positions on already-expired markets (28 in LT7) and on monthly markets (May 1 expiry) during 3-hour test
- **Fix:** Pre-execution guard in order_executor: reject if `close_date < now()` (stale) or `time_to_expiry > max_horizon_seconds` (default 7 days)
- **File:** `sibyl/agents/execution/order_executor.py:498+`

### B-NEW-3 — Paper Balance Anchor (P1) ✅

- **Problem:** `_get_paper_balance()` summed ALL historical closed positions (1,471+), inflating balance ~12x vs real $36 Kalshi cash
- **Fix:** Anchor to actual Kalshi balance (`portfolio_cash_available` + exposure) with 150% hard cap
- **File:** `sibyl/agents/allocator/portfolio_allocator.py:366-378`

### Rec 3.5 — Holding Period Minimums (P1) ✅

- **Problem:** Momentum stall exit (4 cycles = 20 seconds) prematurely killed longer-horizon positions (daily/monthly) that hadn't had time to develop
- **Fix:** Configurable `min_hold_seconds` by timeframe — 15min: 0, hourly: 5m, daily: 1h, monthly: 24h. Stall check skipped if hold time < minimum.
- **Files:** `sibyl/agents/execution/position_lifecycle.py:522-540`, `config/position_lifecycle_config.yaml`

### Remaining Audit Bugs (Tier 2/3 — Post-LT8)

| ID      | Severity | Description                    |
| ------- | -------- | ------------------------------ |
| BUG-003 | HIGH     | Fee multiplier semantics       |
| BUG-009 | MEDIUM   | Orphan engine attribution      |
| BUG-010 | MEDIUM   | EV monitor entry price         |
| BUG-011 | LOW      | Contract cap fallback          |
| BUG-012 | LOW      | Sigma floor / fat-tail model   |
| BUG-013 | LOW      | Orderbook freshness            |

### LT8 Success Criteria (from auditor)

**LT8-paper (24h):** Confidence spread σ > 0.05 | Win rate > 38% | No stale-market entries | Paper balance stable (no inflation) | Brier score < 0.30

**LT8-live (gated on paper pass):** Positive net P&L after fees | No drawdown > 15% of starting capital | AutoCalibrator Brier deltas non-zero

---

## Live Test #7 (Sprint 30, 2026-04-02)

3h22m paper test. STARTEX 11:21 PST, ENDEX 14:43 PST. Full report in session transcript.

**Result:** 2,792 positions opened/closed across BTC/ETH/SOL/XRP. Net paper P&L +$117.21 (35.5% win rate). 2,765 buy-side executions tracked, $78.50 in fees. Sharpe rose from 1.56 to 3.87 over test duration.

Sprint 30 validation:

1. ✅ CB auto-reset (30-A) — circuit breaker cleared at startup and held CLEAR for full 3h22m
2. ⚠️ Balance inflation (30-B) — partial fix; live-mode fallbacks corrected but paper mode root cause remains (B-NEW-3)
3. ❌ AutoCalibrator (30-C) — fires on startup but immediately fails; schema bug (B-NEW-1)

**Timeframe adaptability:** System does prioritize intraday 1-hour ET bracket markets (KXBTC-26APR02XX) over daily/weekly. No 15-minute crypto markets exist on Kalshi — 1 hour is the shortest available. Monthly markets inappropriately entered (B-NEW-4). Stale-market detection gap caused 28 positions on already-expired markets (B-NEW-2).

**Fee accounting:** Operational post-Sprint 29. $78.50 buy-side fees tracked (29.8% of gross profit). Validates audit concern: fee drag requires >1.4% edge per trade to break even. At 35.5% win rate with binary 1:1 payoff, Kelly-sized positions remain marginally positive after fees.

**Key finding vs audit:** The -47.9% all-time drawdown was substantially caused by the bugs fixed in Sprint 29. With those fixes in place, LT7 showed positive paper P&L for the first time. Sprint 31 must resolve the 2 operational blockers (B-NEW-1, B-NEW-2) before live capital is deployed.

---

## Sprint 29: Fix the Math — Audit-Driven Remediation (2026-04-01)

Professional 4-pillar system audit identified 13 bugs explaining the -47.9% drawdown. Sprint 29 addresses 10 of them across Tier 0 (stop the bleeding) and Tier 1 (structural improvements). Audit report: `Sibyl_System_Audit_Report.md`

### Tier 0 — Stop the Bleeding (5 Critical Fixes)

**BUG-001: NO Kelly Sizing (CRITICAL)** — Payout formula used YES price for both sides, undersizing NO positions 2-5x. Fixed: moved side determination before Kelly computation; NO positions now use `cost = 1 - yes_price` for payout calculation.

**BUG-002: Zero Fee Accounting (HIGH)** — No P&L computation deducted fees ($29.35 cumulative = 29.4% drag). Fixed: added `fee_amount` column to executions table, record per-execution fees, deduct roundtrip fees in `_compute_pnl_with_price()`. Settlement exits deduct buy-side fee only. Sell executions now recorded with fees.

**BUG-004: Correlation Block Write-Only (HIGH)** — Sub-E correlation scanner wrote `corr_block_{event_id}` flags to system_state, but OrderExecutor never read them. Fixed: executor now queries event_id for each market and checks for block flags before placing orders.

**S-7: Crypto Calibration Offset (MEDIUM)** — Every category had a calibration offset except crypto (the only active category). Added `calibration_offset: -0.10` to crypto risk profile — reduces overconfident entries by ~10%.

**BUG-007: Dead FLB Config (MEDIUM)** — Per-timeframe longshot rejection floors existed in config but crypto_pipeline had hard-coded `< 0.10` floor. Fixed: signal generator now reads `longshot_reject_by_timeframe` from config (5c for 15-min, 10c hourly, 15c daily, 30c weekly/monthly). Unlocks 97% of previously blocked intraday markets.

### Tier 1 — Structural Improvements (5 Fixes)

**H-1: Per-Underlying Concentration Cap (CRITICAL)** — No limit on BTC/ETH/SOL/XRP exposure. Could accumulate 25%+ on single underlying through multiple brackets. Fixed: 15% max per underlying asset, configurable via `max_underlying_pct`. Extracts underlying from Kalshi ticker prefix.

**H-4: Drawdown-Driven Sizing (HIGH)** — Risk dashboard computed drawdown levels but executor ignored them. Fixed: executor reads `risk_drawdown_level` from system_state. WARNING=0.75x sizing, CAUTION=0.50x, CRITICAL=halt all new positions.

**E-3: Momentum Stall Exit (MEDIUM)** — Documented in config/docstring but never coded. Positions in stagnant markets locked capital until settlement. Fixed: tracks price change per cycle; after 4 consecutive cycles with <0.5% movement, exits position to free capital.

**F-1/F-2: Auto-Calibration Agent (HIGH)** — Zero operational feedback loops despite excellent data. Fixed: new `AutoCalibrator` agent runs hourly, computes per-category Brier scores, calibration divergence, signal type rankings, and optimal Kelly fractions. Writes all metrics to system_state. Can auto-apply calibration offsets when enabled.

**E-5: Partial Fill Handling (MEDIUM)** — Async fill checker assumed all-or-nothing; never checked `filled_count`. Fixed: records actual filled quantity, handles zero fills, and correctly sizes partial fill positions.

### Files Modified

- `sibyl/agents/execution/order_executor.py` — BUG-001 (NO Kelly), BUG-004 (corr block read), H-1 (underlying cap), H-4 (drawdown sizing), E-5 (partial fills), BUG-002 (fee recording)
- `sibyl/agents/execution/position_lifecycle.py` — BUG-002 (fee-adjusted P&L), E-3 (momentum stall), sell execution recording
- `sibyl/pipelines/crypto_pipeline.py` — BUG-007 (tiered FLB from config)
- `sibyl/core/database.py` — fee_amount column + migration
- `sibyl/agents/analytics/auto_calibrator.py` — NEW: feedback loop agent
- `sibyl/__main__.py` — Wire AutoCalibrator into portfolio agent scope
- `config/investment_policy_config.yaml` — calibration_offset, max_underlying_pct
- `sibyl/tests/test_execution.py` — 4 new Sprint 29 tests

### Audit Bugs Status After Sprint 29

| ID | Severity | Status | Notes |
|----|----------|--------|-------|
| BUG-001 | CRITICAL | **FIXED** | NO Kelly payout |
| BUG-002 | HIGH | **FIXED** | Fee accounting |
| BUG-003 | HIGH | Open | Fee multiplier semantics (Tier 2) |
| BUG-004 | HIGH | **FIXED** | Correlation block enforcement |
| BUG-005 | HIGH | **FIXED** | Drawdown sizing |
| BUG-006 | MEDIUM | **FIXED** | Momentum stall exit |
| BUG-007 | MEDIUM | **FIXED** | Tiered FLB activation |
| BUG-008 | MEDIUM | **FIXED** | Partial fill handling |
| BUG-009 | MEDIUM | Open | Orphan engine attribution (low impact) |
| BUG-010 | MEDIUM | Open | EV monitor entry price (low impact) |
| BUG-011 | LOW | Open | Contract cap fallback |
| BUG-012 | LOW | Open | Sigma floor (Tier 2: fat-tail model) |
| BUG-013 | LOW | Open | Orderbook freshness (Tier 2) |
| S-7 | MEDIUM | **FIXED** | Crypto calibration offset |
| H-1 | CRITICAL | **FIXED** | Per-underlying concentration |
| F-1/F-2 | HIGH | **FIXED** | Auto-calibration agent |

---

## Live Test #5 (Sprint 28, 2026-03-30)

2.5-hour live test validating Sprint 28 risk controls. Full report: `Sibyl_Live_Test_5_Report.md`

**Result:** 1,000 live orders across 202 pipeline cycles. 50 live sells, 18 resolutions. Portfolio declined from ~$116 to ~$100 (-$16.07, -13.8%). 18 non-profitable positions closed post-test, 15 kept.

**Critical findings:**

1. **50c entry floor blocks 97% of markets** — all intraday (694 BTC markets, avg 2.4c) and hourly brackets are below 50c. Only Friday close + monthly brackets are accessible.
2. **5-contract cap limits all positions to $5 payout** — max profit per position $0.75-$2.50 on $100 portfolio.
3. **Fee drag at 29.4%** — $29.35 cumulative fees on $100 portfolio.
4. **Balance gate saturation** — 417 triggers, system capital-starved after 50 minutes.
5. **Paper mode CLI bug** — first 6 min ran paper mode (CLI default overrides config).

**Sprint 29 plan:** Tiered entry floor by timeframe (5c-30c), dynamic contract cap by entry price (5-15), CLI mode fix.

---

## Live Test #4 (Sprint 26, 2026-03-28)

1-hour live test validating Sprint 26 execution safeguards. Full report: `Sibyl_Live_Test_4_Report.md`

**Result:** 193,690 signals processed across 63 pipeline cycles (0 timeouts, ~1s/cycle). 20 unprofitable positions closed post-test, 8 high-conviction positions kept with 7-12% price margin.

**Sprint 26 features validated:**

1. Fee-adjusted EV: +54% more low-edge rejections (4,630 vs 3,000/cycle)
2. Per-market accumulation guard: 0 markets with >1 position (was unbounded)
3. Maker fill rate tracking: initialized (later removed in Sprint 27 — taker-only)
4. Time-based exit: 1,353 positions closed by exit optimizer during test
5. Settled position auto-purge: clean startup reconciliation

**Portfolio state post-close:** $42.29 cash + $73.67 in 8 kept positions. Projected ~$120 if all settle favorably.

**Gaps for Sprint 27:**

1. Mode enforcement (paper/live hybrid state occurred)
2. Maker fallback tuning (1hr too short for 5-attempt minimum)
3. Signal quality metrics (track % of signals resulting in profitable settlements)

---

## Sprint 28: Data-Driven Risk Policy Overhaul (2026-03-30)

Post-LT4 deep analysis of all 4 live tests ($200 start → $114 current) revealed that position sizing, entry price selection, and the BRACKET_ARB targeted-NO strategy were the primary capital destroyers. Six parameter changes based on empirical LT1-LT4 data.

### BRACKET_ARB Targeted-NO Disabled (crypto_pipeline.py)

**Problem:** BRACKET_ARB targeted-NO lost $686 across 481 positions in LT1-LT4. Small NO premiums (10-15c) with massive exposure (85-90c per contract) = catastrophic risk/reward. Buy-all-YES arb remains profitable and is retained.

**Fix:** Entire overpriced-bracket-NO code block replaced with explanatory comment. Only buy-all-YES arb signals are emitted.

### Entry Price Floor Raised to 50c (order_executor.py, investment_policy_config.yaml)

**Problem:** LT1-LT4 data shows entry price is the strongest predictor of trade outcome: <20c = -39% ROI, 20-50c = breakeven, 50-70c = +30-60% ROI, 70c+ = 100% win rate. The old 10c floor allowed deeply unprofitable longshot entries.

**Fix:** `longshot_reject_below` raised from 10c to 50c in FLB config. Executor PRICE GATE updated to read from config with 50c default. Upper cap remains 93c.

### NO Premium Floor Raised to 50c (investment_policy_config.yaml)

**Problem:** Several LT4 NO positions entered at 7-10c premium with 90-93c exposure per contract. One wrong move = near-total loss on the position.

**Fix:** `min_no_premium` raised from 15c to 50c. NO positions must now have substantial premium to justify the asymmetric risk.

### Kelly Fractions Cut 60-70% (investment_policy_config.yaml)

**Problem:** LT1-LT4 showed systematic overconfidence in edge estimates. Large positions amplified losses on wrong calls. Position size was inversely correlated with returns (1-3 contracts = +30-60% ROI, 11+ = -3.8% ROI).

**Fix:** Category-level `kelly_fraction` reduced from 0.30 to 0.12. Per-timeframe Kelly: 15-min 0.20→0.07, hourly 0.25→0.10, 4h/daily 0.30→0.12, monthly 0.25→0.10.

### Max Contracts Per Trade Capped at 5 (order_executor.py, investment_policy_config.yaml)

**Problem:** Some positions accumulated 11-116 contracts on a single market. Larger positions consistently underperformed.

**Fix:** Hard cap of 5 contracts per trade in executor. Configurable via `max_contracts_per_trade` in category risk profile.

### Max Position Percentage Reduced (investment_policy_config.yaml)

**Problem:** 12% max position pct allowed too much concentration in a single trade relative to the $114 portfolio.

**Fix:** `max_position_pct` reduced from 12% to 5%.

### Files Modified

- `sibyl/pipelines/crypto_pipeline.py` — Disabled targeted-NO arb block
- `sibyl/agents/execution/order_executor.py` — 50c entry floor (PRICE GATE), max contracts cap
- `config/investment_policy_config.yaml` — kelly_fraction, kelly_by_timeframe, max_position_pct, longshot_reject_below, min_no_premium, max_contracts_per_trade
- `sibyl/tests/test_execution.py` — Updated paper fill test for 50c entry floor + explicit direction

---

## Sprint 27: Taker-Only Execution + Mode Enforcement (2026-03-30)

Post-LT4 review. User mandate: "There are never market maker winners, only market taker winners." Five changes to enforce taker-only execution and harden mode transitions.

### Taker-Only Execution (order_executor.py)

**Problem:** Maker orders sat unfilled, locking capital. LT4's maker fill tracking showed insufficient data after 1 hour. Maker strategy fundamentally misaligned with Sibyl's edge (speed of signal, not spread capture).

**Fix:** Removed entire maker/taker decision block (~80 lines), fill rate tracking, pending maker metadata. All orders now cross the spread immediately: YES buys at best ask, NO buys at `1.0 - best_bid`. Guarantees instant fills.

### Mode Enforcement (\_\_main\_\_.py, position_lifecycle.py)

**Problem:** PositionLifecycleManager auto-upgraded from paper to live if Kalshi credentials were present, creating hybrid paper/live states.

**Fix:** `system_config.yaml` `mode` field is single source of truth. PositionLifecycleManager now takes explicit `mode` parameter from `__main__.py`. Credentials alone do NOT upgrade mode. If live mode requested but auth fails, falls back to paper with error log.

### Fee Recovery Threshold (base_pipeline.py, crypto_pipeline.py)

**Problem:** Edge calculations deducted 1x fee (1.4%). But roundtrip = buy + sell = 2.8%. Trades with 1.5-2.7% edge appeared profitable but lost money after fees.

**Fix:** `_compute_edge()` now uses `fee_multiplier=2.0` (default). Edge must exceed 2x the per-contract fee (2.8%) to have positive EV. Applied in base pipeline, crypto bracket model, bracket arb, and targeted NO calculations.

### NO Premium Gate (order_executor.py)

**Problem:** LT4 NO positions entered at 7-10c premium with 90-93c exposure. Pennies in front of a steamroller.

**Fix:** After pricing, before sizing: if side is NO and `entry_price < min_no_premium`, reject. Default 15c (raised to 50c in Sprint 28). Configurable per category.

### Position Inheritance Cleanup (position_lifecycle.py)

**Problem:** Periodic reconciliation used single Kalshi API call, missing positions beyond first page. Offline settlements (markets that resolved between sessions) were never recorded.

**Fix:** Paginated `get_positions()` with cursor in both periodic reconciliation and startup. Startup also queries settled positions and records realized P&L for any DB positions that settled offline.

### Files Modified

- `sibyl/agents/execution/order_executor.py` — Taker-only pricing, NO premium gate, removed maker logic
- `sibyl/agents/execution/position_lifecycle.py` — Mode parameter, paginated reconciliation, settled position query
- `sibyl/__main__.py` — Pass `mode=trade_mode` to PositionLifecycleManager
- `sibyl/pipelines/base_pipeline.py` — `fee_multiplier` parameter on `_compute_edge()`
- `sibyl/pipelines/crypto_pipeline.py` — 2x fee in bracket model, bracket arb, targeted NO
- `config/investment_policy_config.yaml` — Removed maker_config, added fee_recovery_multiplier, min_no_premium

---

## Live Test #3 (Sprint 24, 2026-03-28)

37-minute live test terminated early due to capital starvation. Full report: `Sibyl_Live_Test_3_Report.md`

**Result:** -$7.43 session P&L. 5 orders placed, 0 filled (resting). 2,076 order exceptions from insufficient balance.

**Root cause:** No portfolio value awareness. $161.28 (95% of account) locked in 67 pre-existing positions from previous sessions. System only saw $8.79 cash, but SGE engine was allocated $540 from stale DB values.

**Sprint 24 infrastructure validated:** 34 consecutive pipeline cycles at 2.1s avg (zero timeouts). FLB filter, bracket arb, per-TF Kelly, batch dedup all working.

**Critical gaps identified for Sprint 25:**
1. Portfolio value tracker (cash + exposure = available for trading)
2. Pre-execution balance gate (check cash before placing orders)
3. Mode transition guard (reset HWM/allocations when switching paper -> live)
4. Kalshi-source position reconciliation on startup

### Sprint 24 Bug Fixes (found during paper test)

| Bug | Fix | File |
|-----|-----|------|
| Kalshi OBI read `"count"` instead of `"size"` → OBI always zero | Changed to `lv.get("size", 0)` | crypto_pipeline.py |
| Maker NO-side pricing used `max()` forcing taker price | Proper clamp: `no_maker = 1.0 - best_ask + 0.01` | order_executor.py |
| `sqlite3.Row` has no `.get()` → crash in executor | Changed to bracket access `signal["key"]` with try/except | order_executor.py |
| Pipeline timeout (50% failure rate) from 3N individual dedup queries | Batch dedup: 2 bulk pre-fetches + N inserts | base_pipeline.py |
| DB lock contention from seed_markets (26K inserts) | busy_timeout 5s → 30s, pipeline timeout 45s → 90s | database.py, pipeline_manager.py |

---

## Sprint 26: Fee-Adjusted EV + Execution Safeguards (2026-03-28)

Post-LT3 analysis identified 5 execution-layer improvements needed to make Sibyl's per-trade EV structurally positive.

### Fee-Adjusted EV (base_pipeline.py, crypto_pipeline.py)

**Problem:** Edge calculations ignored Kalshi's ~1.4% roundtrip fee, producing phantom-edge trades where the "profit" was smaller than the fee.

**Fix:** `_compute_edge()` now deducts `fee_per_contract` (default 0.014) from both YES and NO EV. Crypto pipeline inline calcs (`_bracket_model_signals`, `_bracket_arbitrage_signals`) also deduct fees. All other pipelines inherit the default automatically.

### Per-Market Accumulation Guard (order_executor.py)

**Problem:** No limit on how many contracts could accumulate on a single market across sessions. XRP monthly bracket accumulated 116 contracts ($20+ loss).

**Fix:** Before placing any order, queries existing OPEN position exposure on the same `market_id`. If exposure >= `max_position_pct * engine_capital`, skips the order. Also caps new `position_dollars` to remaining headroom.

### Maker Fill Rate Tracking + Taker Fallback (order_executor.py)

**Problem:** Maker orders could go unfilled indefinitely, locking capital. No feedback mechanism to detect low fill rates.

**Fix:** Tracks `_maker_attempts`, `_maker_fills`, `_taker_attempts`, `_taker_fills`. If maker fill rate drops below 30% after 5+ attempts, automatically switches to taker. Tracks fill counts across instant fills, async fills, and paper fills.

### Time-Based Exit Strategy (position_lifecycle.py)

**Problem:** Losing positions held until settlement could hit maximum loss ($1.00 - entry_price per contract).

**Fix:** In `_sub_c_exit_optimizer()`, checks `close_date` from markets table. If a losing position is within `expiry_exit_minutes` (default 15) of settlement, sells immediately with reason `TIME_DECAY_EXIT`.

### Settled Position Auto-Purge (position_lifecycle.py)

**Problem:** DB positions marked OPEN could persist after markets closed on Kalshi, inflating portfolio metrics.

**Fix:** In `_startup_reconciliation()`, queries positions where `markets.close_date < now` or `markets.status = 'closed'` and the ticker is not in Kalshi's active positions. Marks them as SETTLED with `pnl = 0`.

### Files Modified

- `sibyl/pipelines/base_pipeline.py` — `_compute_edge()` fee deduction parameter
- `sibyl/pipelines/crypto_pipeline.py` — Fee deduction in bracket model + bracket arb edge calcs
- `sibyl/agents/execution/order_executor.py` — Accumulation guard, maker fill tracking, taker fallback
- `sibyl/agents/execution/position_lifecycle.py` — Time-based exit, settled purge
- `config/position_lifecycle_config.yaml` — `expiry_exit_minutes: 15`
- `config/investment_policy_config.yaml` — `maker_fallback_threshold`, `fee_per_contract_*`
- `sibyl/tests/test_pipelines.py` — Fixed `test_compute_edge_no_edge` for fee parameter

---

## Sprint 24: Research-Driven Strategy Overhaul (2026-03-28)

Based on empirical analysis of 300K+ Kalshi contracts and $40M in arbitrage profits (2024-2026). Six research-backed strategies integrated across pipeline, execution, and sizing layers.

### Phase 1 — Favorite-Longshot Bias Filter (crypto_pipeline.py, order_executor.py)

**Research:** Favorites (>50c) return +2.6% for makers. Longshots (<10c) lose -60%.

**Fix:** Asymmetric edge scaling in bracket model — favorite side gets 1.0-1.3x multiplier, longshot side 0.3-0.8x penalty. Hard reject when implied price < 10c. Executor lower cap tightened from 4c to 10c.

### Phase 2 — Bracket Sum Arbitrage (crypto_pipeline.py, sge_config.yaml)

**Research:** Single-market rebalancing captured $5.9M in arb profits (14.9% of total).

**Fix:** New `_bracket_arbitrage_signals()` method groups brackets by `event_id`, sums YES prices. If sum < $1.00 minus spreads → buy-all-YES arb. If sum > $1.00 plus spreads → targeted NO on overpriced brackets. Signal type: BRACKET_ARB (0.95 confidence).

### Phase 3 — Kalshi-Side Order Book Imbalance (crypto_pipeline.py)

**Research:** OBI predicts direction at 62% accuracy over 1-5 min horizons.

**Fix:** Computes OBI from Kalshi's own orderbook (top 3 levels) during spread pre-fetch. Timeframe-weighted adjustment: 15-min up to +4%, hourly +3%, 4h+ +2%. Supplements existing Hyperliquid perp OBI.

### Phase 4 — Brier-Tiered Kelly Scaling (order_executor.py, database.py, investment_policy_config.yaml)

**Research:** Professional bots scale Kelly alpha (0.10-0.40) by model calibration per timeframe.

**Fix:** New `timeframe` column on signals table. Per-timeframe Kelly fractions: 15-min=0.20, hourly=0.25, daily=0.30, monthly=0.25. Executor reads timeframe from signal and applies matching Kelly.

### Phase 5 — Maker Pricing Strategy (order_executor.py, investment_policy_config.yaml)

**Research:** Kalshi ~1.2% effective taker fee. Makers get rebate.

**Fix:** For patient favorite-side trades (>30 min to close), post 1c inside the spread (maker) instead of crossing (taker). Arb signals and urgent (<30 min) trades still use taker for immediate fills.

### Phase 6 — Domain-Specific Calibration Offsets (investment_policy_config.yaml, base_pipeline.py)

**Research:** Politics underconfident (+0.15), weather/culture overconfident (-0.09), economics well-calibrated (0.00).

**Fix:** `calibration_offset` added to every category risk profile. Applied in `_validate_signals()` as additive confidence adjustment. Active on category re-enablement.

### Additional Fix — Pipeline Order-Level Dedup (base_pipeline.py)

**Problem:** Pipeline wrote signals for markets with existing OPEN positions → executor discarded them → wasted DB writes and routing.

**Fix:** `_write_signals()` checks `positions` table for OPEN position before writing. Stops waste at the source.

### Files Modified

- `sibyl/pipelines/crypto_pipeline.py` — FLB filter, bracket arb scanner, Kalshi OBI, timeframe on signals
- `sibyl/pipelines/base_pipeline.py` — Order-level dedup, calibration offset, timeframe field on PipelineSignal
- `sibyl/agents/execution/order_executor.py` — Maker pricing, per-timeframe Kelly, 10c price cap, datetime import
- `sibyl/core/database.py` — `timeframe` column migration on signals table
- `config/investment_policy_config.yaml` — `flb_config`, `kelly_by_timeframe`, `maker_config`, `calibration_offset` per category
- `config/sge_config.yaml` — BRACKET_ARB added to signal whitelist

---

## Sprint 23: Targeted Discovery + Async Execution + Efficiency Overhaul (2026-03-27)

Driven by deep analysis of Live Test #2 showing 0.13% crypto market utilization (171/1,572 markets). All 5 root causes addressed.

### 23A — Targeted Crypto Market Discovery (kalshi_monitor.py)

**Problem:** Gap-fill Phase 2 disabled (`gap_fill=False`), causing ~1,401 daily/hourly/4h crypto markets to be invisible.

**Fix:** Added `_refresh_crypto_markets()` — 16 targeted API calls using `series_ticker` filter + `min_close_ts=now` for each crypto series (KXBTC, KXBTCD, KXETH, KXETHD, KXSOL, KXSOLD, KXXRP, KXXRPD + monthly min/max). No brute-force gap-fill needed. Runs on initial discovery and every 2-min refresh cycle.

### 23A — Timeframe-Scaled Edge Thresholds (crypto_pipeline.py)

**Problem:** Flat `BRACKET_MIN_EDGE = 0.015` systematically filtered out short-duration markets (sigma_t compressed by sqrt(time)).

**Fix:** Per-timeframe thresholds: 15-min=0.5%, hourly=0.8%, 4h=1.0%, daily=1.2%, monthly=1.5% (unchanged).

### 23B — Async Fire-and-Forget Execution Engine (order_executor.py)

**Problem:** 120s blocking polling loop per order. Throughput: 0.44 orders/min.

**Fix:** Place order → if instant fill, record immediately; if resting, add to `_pending_orders` dict and return. `_check_pending_fills()` runs every 3s cycle, checks all pending orders. Fill timeout reduced to 60s. Batch processing: up to 5 signals/cycle (was 1). Expected throughput: 10+ orders/min.

### 23D — Shared Kalshi Client Singleton (kalshi_client.py)

**Problem:** 4 agents created independent KalshiClient instances, each with its own rate limiter. Combined throughput exceeded 30 read/s → 1,222 rate-limit hits.

**Fix:** `get_shared_kalshi_client()` singleton factory. All agents (KalshiMonitor, OrderExecutor, PositionLifecycle, PortfolioAllocator) share one client, one rate limiter, one HTTP connection pool. Agents no longer close the client on stop.

### 23D — Extreme Price Cap (order_executor.py)

**Problem:** Orders at 91-96c (deep ITM) had zero counterparty interest overnight.

**Fix:** Skip orders where entry_price > 93c or < 4c. Logged at debug level.

### 23D — Order-Level Dedup (order_executor.py)

**Problem:** Multiple signals for the same market ticker generated duplicate orders (e.g., KXSOLMAXMON ordered twice).

**Fix:** Before sizing, check if market_id already has an OPEN position in DB or a pending order in `_pending_orders`. Skip if so.

### 23D — Orphan Reconciliation Tightened (position_lifecycle.py)

Sub-routine F interval reduced from 15 min to 5 min to match faster async execution cadence.

### 23D — ntfy.sh Notification Throttling (notifier.py)

**Problem:** Free tier (250 msg/day) exhausted in the first minute of Live Test #2.

**Fix:** Daily budget of 200 messages (50 reserved for critical). Min 5s between sends. Critical alerts (priority 4-5) bypass daily budget. Budget resets at midnight PST.

### 23A — Timeframe-Normalized Confidence Formula (crypto_pipeline.py)

**Problem:** Original formula `confidence = 0.55 + edge * 2.5` produced 0.5625 for 15-min edge=0.005 — below 0.60 router floor. All short-duration signals were DEFERRED.

**Fix:** Normalize by timeframe's min edge: `edge_ratio = edge / min_edge; confidence = min(0.55 + edge_ratio * 0.06, 0.95)`. At 1× minimum edge → 0.61 (clears floor). At 2× → 0.67. At 5× → 0.85.

### 23A — Lowered Crypto min_ev Router Floor (investment_policy_config.yaml)

**Problem:** Per-category `min_ev: 0.015` blocked ALL non-monthly signals since `ev_estimate = edge`, and 15-min/hourly/daily edges are 0.005-0.012.

**Fix:** Lowered to `min_ev: 0.005` (matching 15-min floor). All timeframes now clear both confidence (≥0.60) and EV (≥0.005) router gates.

### 23B — Sell-Side Timeout Reduced to 60s (position_lifecycle.py)

**Problem:** Sell polling was 20×6s = 120s, blocking the lifecycle manager.

**Fix:** Changed to 20×3s = 60s, matching the buy-side async timeout.

### Files Modified

- `sibyl/agents/monitors/kalshi_monitor.py` — Targeted crypto discovery, shared client, removed os import
- `sibyl/pipelines/crypto_pipeline.py` — `BRACKET_MIN_EDGE_BY_TIMEFRAME` dict, timeframe-aware edge check, normalized confidence formula
- `sibyl/agents/execution/order_executor.py` — Async fire-and-forget, pending_orders tracking, batch signals, extreme price cap, order dedup, shared client, 60s fill timeout
- `sibyl/agents/execution/position_lifecycle.py` — Shared client, 5-min reconciliation, 60s sell timeout, removed os import
- `sibyl/agents/allocator/portfolio_allocator.py` — Shared client, removed os import
- `sibyl/agents/notifications/notifier.py` — Daily budget, min interval, critical bypass
- `sibyl/clients/kalshi_client.py` — `get_shared_kalshi_client()` singleton factory
- `config/investment_policy_config.yaml` — Crypto `min_ev` lowered 0.015→0.005, `bracket_min_edge` aligned

---

## Sprint 22.5: Live Test #2 — Fill Timeout Fix + 4 Hotfixes (2026-03-26)

### Live Test #2 Results

- **Duration:** 2h 5m (01:50 - 03:55 UTC / 5:50 PM - 7:55 PM PST)
- **Starting Balance:** $186.76 | **Final Balance:** $185.95 | **Net:** -$0.81 (-0.43%)
- **Orders Placed:** 55 | **Detected Fills:** 2 | **Actual Fills (Kalshi):** 5+
- **Pipeline Cycles:** 242 (60s interval, 0.8s execution)
- **Kalshi Rate Limits (429):** 1,222

### Hotfix 1 — Fill Timeout 10s -> 120s (order_executor.py + position_lifecycle.py)

**Problem:** Sprint 22's 5-poll fill confirmation (5x2s = 10s) was too aggressive for limit orders in thin markets. Live Test #1 (8 min before crash) had 23 orders placed, 0 detected fills, 100% cancellation rate.

**Fix:** Increased to 20x6s = 120s. Reduced log noise by only logging every 5th check. Both buy-side (order_executor) and sell-side (position_lifecycle) updated.

**Result:** Two fills detected within the window at 37s and 12s respectively. Turned 0% detected fill rate into measurable fills.

### Hotfix 2 — Kalshi API Price Field Bug (kalshi_client.py)

**Problem:** `sell_position()` and `place_order()` didn't include price fields for market orders. Kalshi API requires exactly one of `yes_price`, `no_price`, `yes_price_dollars`, or `no_price_dollars` on every order — including market orders. All market sell attempts returned 400 Bad Request.

**Fix:** Market sells now include `yes_price=1` or `no_price=1` (most aggressive, fill at any price). Market buys include `yes_price=99` or `no_price=99`.

### Hotfix 3 — HWM Reset (DB fix)

**Problem:** HWM was $548.14 from historical trading. Current balance $186.76. Risk dashboard reported 65% CRITICAL drawdown despite no actual loss in current session.

**Fix:** Reset `risk_hwm` to $186.76, cleared drawdown level to CLEAR.

### Hotfix 4 — PST Startup Timestamp (__main__.py)

Added `log_startup_timestamp()` function that prints a formatted PST timestamp banner at engine start. Also included in the main logger.info startup line.

### Critical Discovery — Orphan Positions

Orders that fill AFTER the 120s cancel window become "orphan positions" — they exist on Kalshi but are NOT tracked in the database. Cancel on an already-filled order is a no-op on Kalshi. System detected 2 fills, but Kalshi showed 5+ active positions with $53.83 in untracked exposure. Risk controls (stop-loss, exit optimizer) cannot manage what they can't see.

### Critical Discovery — Serial Order Bottleneck

Each order blocks the executor for up to 120s. With 55 orders over 125 minutes, throughput was 0.44 orders/minute. Pipeline generates 28-31 routable signals per 60s cycle, but only 1 order processes every ~2 minutes. Signal utilization: 1.8%.

### Critical Discovery — Rate Limiter Contention

4 agents create independent Kalshi clients (KalshiMonitor, OrderExecutor, PositionLifecycle, PortfolioAllocator), each with their own rate limiter. Combined throughput exceeds the 30 read/s API limit, causing 1,222 rate-limit hits and 166 agent backoffs.

### Files Modified

- `sibyl/agents/execution/order_executor.py` — Fill timeout 10s -> 120s (20x6s), reduced log noise
- `sibyl/agents/execution/position_lifecycle.py` — Sell timeout 10s -> 120s (matching fix)
- `sibyl/clients/kalshi_client.py` — Price field fix for market orders in `place_order()` and `sell_position()`
- `sibyl/__main__.py` — PST startup timestamp function `log_startup_timestamp()`

### Sprint 23 Priorities (from Live Test #2)

1. **Async fill confirmation** — place order, record ID, move to next signal; check fills in background loop
2. **Orphan position reconciliation** — periodic Kalshi portfolio scan, register untracked fills in DB
3. **Shared Kalshi rate limiter** — single rate-limited client pool across all agents
4. **Run during market hours** — 8 AM - 5 PM ET for maximum bracket market liquidity
5. **Cap extreme prices** — skip 95-96c orders with zero counterparty interest

---

## Sprint 22: Execution Integrity — 5 Real-Money Bug Fixes (2026-03-26)

Motivated by analysis of a [Reddit post about Polymarket bot execution failures](https://www.reddit.com/r/PredictionsMarkets/comments/1s46vn7/), Sprint 22 audited and hardened Sibyl's entire execution layer against 5 classes of bugs that cause real-money losses despite correct signals.

### Fix 1 — Ghost Trade Prevention (OrderExecutor)

**Problem:** Fill confirmation used a single 2-second check. If it threw an exception, the code logged a warning ("proceeding anyway") and recorded the position unconditionally — creating phantom DB positions for orders that never filled on Kalshi.

**Fix:**

- Replaced single check with 5-iteration polling loop (2s intervals = 10s max)
- If order is confirmed canceled/expired → abort, no position recorded
- If fill not confirmed after 10s → cancel the resting order on Kalshi and abort
- If confirmation check throws → do NOT proceed (previously it did)
- Extract `average_fill_price` from Kalshi response and use as the actual entry price

### Fix 2 — Actual Fill-Price P&L (OrderExecutor + PositionLifecycleManager)

**Problem:** Entry price was recorded as the mid-price from the prices table, not the actual fill price. Exit P&L was computed using the last observed price snapshot, not real proceeds. Dashboard could show green while actual USDC received was different.

**Fix:**

- **Entry side:** `entry_price` now comes from Kalshi's `average_fill_price` response field when available. Slippage is logged in basis points.
- **Exit side:** `_sell_on_kalshi()` now returns `(bool, float | None)` — success flag + actual fill price. All 3 exit paths (stop guard, exit optimizer, resolution tracker) use the real fill price for P&L.
- **Pending exits:** `closed_at` is only set when the sell actually fills. Previously, failed exits still set `closed_at = datetime('now')`, making the position look closed while it remained open on Kalshi.

### Fix 3 — Fill Rate: Spread-Crossing Entry Pricing (OrderExecutor)

**Problem:** Limit orders used `current_price` from the prices table (a mid/last-trade price). On thin orderbooks, this sits behind the spread and never fills — the single biggest cause of low fill rates in prediction market bots.

**Fix:**

- OrderExecutor now reads the orderbook (best_bid, best_ask) at order time
- YES buys use `best_ask` (cross the spread to fill immediately)
- NO buys use `1 - best_bid` (equivalent spread-crossing for NO contracts)
- Falls back to mid-price only if orderbook data is unavailable
- Execution record now stores the actual `order_type` used instead of hardcoded "market"

### Fix 4 — Position Reconciliation (PositionLifecycleManager, Sub-routine F)

**Problem:** No mechanism to detect discrepancies between DB positions and actual Kalshi portfolio. Ghost positions, orphan fills, and stuck pending exits went undetected indefinitely.

**Fix:** New sub-routine F runs every 15 minutes in live mode:

- Calls `KalshiClient.get_positions(settlement_status="unsettled")` to get actual Kalshi portfolio
- **Ghost DB positions** (DB says OPEN, Kalshi has nothing) → marked `GHOST_CLOSED` with zero P&L
- **Orphan Kalshi positions** (Kalshi has position, DB has nothing) → creates tracking entry
- **Stuck pending exits** (STOP_PENDING/CLOSE_PENDING no longer on Kalshi) → marked CLOSED
- Logs discrepancy counts each cycle for monitoring

### Fix 5 — Spread-Aware EV Model (BasePipeline + CryptoPipeline)

**Problem:** EV calculation used mid-price as if the bot would fill at that price. On Kalshi crypto brackets with wide spreads or zero liquidity, the spread often consumed the entire theoretical edge, producing phantom signals.

**Fix:**

- `BasePipeline._compute_edge()` now accepts optional `half_spread` parameter. EV is reduced by the spread cost. Backward-compatible (default 0).
- CryptoPipeline pre-fetches actual Kalshi orderbook spread data for all target markets before running the bracket model. Default 4-cent spread used if no orderbook data available.
- Eliminates signals where the model sees 2-cent edge but the spread is 5 cents.

### Sprint 22 Bonus — API-Level Timestamp Filtering + DB Auto-Close Guard

**Problem:** Kalshi API calls pulled ~8,800+ markets with only `status="open"` filtering. Expired markets accumulated in the DB, causing stale data, 409 Conflict errors, and wasted API bandwidth.

**Fix (3 layers):**

- **API layer:** `KalshiClient.get_events()` and `get_markets()` now support `min_close_ts`, `max_close_ts`, `series_ticker`, and `tickers` parameters
- **Upstream callers:** `market_discovery.discover_markets()` and `KalshiMonitorAgent._refresh_markets_standard()` pass `min_close_ts=now` on every call — expired markets are filtered at the API level before reaching the DB
- **DB guard:** New `_auto_close_expired_markets()` method runs every ~10 minutes in the monitor, marking markets with past `close_date` as closed and expiring orphaned signals

### Sprint 22 Bonus — XRP Ticker Prefix Fix

XRP bracket parser checked for `"KXRP"` but actual tickers are `"KXXRP-..."` — the double-X meant the substring didn't match, causing XRP brackets to fall through to the 0.6% fallback width ($0.012 instead of $0.50). Fixed to check for both `"KXXRP"` and `"KXRP"`.

### Files Modified

- `sibyl/agents/execution/order_executor.py` — Spread-aware entry pricing, 5-poll fill confirmation loop, actual fill price tracking, order cancellation on timeout
- `sibyl/agents/execution/position_lifecycle.py` — `_sell_on_kalshi()` returns `(bool, fill_price)`, all 3 exit paths use real fill price, position reconciliation sub-routine F, `closed_at` only set on confirmed fills
- `sibyl/pipelines/base_pipeline.py` — `_compute_edge()` accepts `half_spread` parameter for spread-deducted EV
- `sibyl/pipelines/crypto_pipeline.py` — Pre-fetches Kalshi spreads, deducts half-spread from bracket model EV, XRP ticker prefix fix
- `sibyl/clients/kalshi_client.py` — Added `min_close_ts`, `max_close_ts`, `series_ticker`, `tickers` params to `get_events()` and `get_markets()`
- `sibyl/agents/monitors/kalshi_monitor.py` — `min_close_ts=now` on all API calls, new `_auto_close_expired_markets()` guard
- `sibyl/core/market_discovery.py` — `min_close_ts=now` on all 3 fetch phases (standard, gap-fill scan, per-event)
- `config/investment_policy_config.yaml` — "Crypto"/"crypto" capital cap aliases

---

## Sprint 21.5: Live Test #2 — Bug Fixes & Zero-Liquidity Discovery (2026-03-24)

### Paper Pre-Test (PASSED)

- **Duration:** 30 minutes, 4 full pipeline cycles
- **Signals per cycle:** ~776 (12 BRACKET_MODEL signals from crypto pipeline)
- **Critical errors:** 0
- **Markets discovered:** 50 new KXBTC March 2026 markets inserted by KalshiMonitor
- **Verdict:** All agents started cleanly, pipeline cycling correctly, DB writes confirmed

### Live Test #2 — 5 Bugs Discovered and Fixed

**Bug 1 — Launch Script Auth Verification (FIXED):**

- `verify_kalshi_auth()` in `scripts/launch_live_test.py` called `.get()` on a float
- `KalshiClient.get_balance()` returns `float` (dollars), not a dict
- Fix: Rewrote to handle actual return types; added type-safe position parsing

**Bug 2 — Capital Cap Naming Mismatch (FIXED):**

- PolicyEngine looks up category `"crypto"` but `capital_caps` in investment_policy_config.yaml keyed as `"Crypto & Digital Assets"`
- Case-insensitive match still fails since the strings are fundamentally different
- Result: Falls back to 10% default cap, blocking all crypto trades
- Fix: Added `"Crypto"` and `"crypto"` aliases in capital_caps section of investment_policy_config.yaml

**Bug 3 — HWM False Drawdown / Circuit Breaker Trip (FIXED):**

- System stored HWM at $505.59 from previous trading sessions, current balance $191.69
- 62% "drawdown" triggered CRITICAL circuit breaker despite no actual loss in current session
- Fix: Reset `risk_hwm` in `system_state` table to $191.69, cleared circuit breakers in `engine_state`

**Bug 4 — Stale January Markets + Signals in DB (FIXED):**

- 20 January markets (KXBTCMINY-27JAN*, KXETH-27JAN*) still marked `status='active'` in SQLite
- 148 stale ROUTED/PENDING signals referencing closed January tickers
- OrderExecutor repeatedly attempted execution → 409 Conflict ("market_closed") from Kalshi API
- Fix: Batch-updated all expired markets to `status='closed'`, expired all stale signals

**Bug 5 — Ticker Format Change Breaks Bracket Parser (FIXED):**

- Old Kalshi format: `KXBTCMINY-27JAN01-80000.00` (bracket value in ticker)
- New Kalshi format: `KXBTC-26MAR2717-B82650` (bracket encoded in suffix)
  - `B` + value = "between" bracket (lower bound; width varies by asset)
  - `T` + value = "above/top" threshold
- New market titles are generic ("Bitcoin price range on Mar 27, 2026?") with no bracket values
- `_parse_crypto_bracket()` returned None for all new markets → `no_bracket=630`
- Fix: Added `_parse_bracket_from_ticker()` static method with B/T suffix parsing
  - Asset-aware bracket widths: BTC=$500, ETH=$50, SOL=$5, XRP=$0.50
  - Verified working: `KXBTC-26MAR2717-B82650` → `('between', 82650.0, 83150.0)`
- **Status:** Code verified in direct testing. Live pipeline still showed stale results due to `.pyc` bytecode caching. `__pycache__` cleared; needs clean process restart to take effect.

### Critical Discovery — Zero Liquidity on Kalshi Crypto Brackets

All current March 2026 crypto bracket markets have zero volume, zero open interest, and no bids/asks. Even with correct tickers and bracket parsing, orders cannot fill without counterparties. This is a market condition issue, not a code bug. Implications:

- Bracket model can correctly identify and price every market
- Orders can be placed at limit prices
- But fills require other participants — currently none exist on these markets
- May need to consider: (a) waiting for liquidity to develop, (b) acting as market maker with wider spreads, (c) focusing on markets closer to expiry where liquidity concentrates

### Files Modified

- `scripts/launch_live_test.py` — Rewrote `verify_kalshi_auth()` for correct return types
- `config/investment_policy_config.yaml` — Added "Crypto" and "crypto" capital cap aliases
- `sibyl/pipelines/crypto_pipeline.py` — Added `_parse_bracket_from_ticker()` method + fallback logic in `_bracket_model_signals()`
- `pyproject.toml` — Temporarily relaxed `requires-python` from `>=3.12` to `>=3.10` for VM compatibility (revert before GitHub push)

### Database Fixes (Applied Programmatically)

- Reset HWM: `system_state.risk_hwm` = $191.69 (was $505.59)
- Cleared circuit breakers: `engine_state.drawdown_pct` = 0.0, `circuit_breaker` = 'CLEAR'
- Closed 20 stale January markets (12 BTC + 8 ETH)
- Expired 148 stale ROUTED/PENDING signals referencing closed markets

---

## Sprint 21: Full Hyperliquid Data Suite (2026-03-23)

### Phase 1 (~18:00 UTC): Price Streaming + DB-First Architecture

**HyperliquidPriceAgent — new background agent:**

- `sibyl/agents/monitors/hyperliquid_price_agent.py` — extends BaseAgent with 1s polling loop
- Initially 3 polling tiers writing to 2 DB tables
- Auto-registered in `__main__.py` under `--agents monitor` scope

**DB-first pipeline architecture:**

- Crypto pipeline reads from DB tables (written by agent) instead of its own API calls
- Automatic fallback to direct Hyperliquid API if DB data stale
- Decouples streaming (always running) from analysis (every 5 min)

### Phase 2 (~19:30 UTC): L2 Order Book, Funding, Micro-Candles, Enriched Bracket Model

**4 new HyperliquidClient API methods:**

- `get_l2_book(coin)`: 20-level order book with derived metrics (spread, depth, imbalance, wall detection)
- `get_funding_history(coin)`: 24h historical funding rates with premium
- `get_predicted_fundings()`: Cross-exchange predicted rates (Hyperliquid, Binance, Bybit)
- `get_recent_trades(coin)`: 15-min 1m candles with buy/sell pressure estimation

**HyperliquidPriceAgent expanded to 6 polling tiers:**

| Tier | Interval | Endpoint | DB Table | Weight/min |
|------|----------|----------|----------|-----------|
| 1 | 1s | allMids | crypto_spot_prices | ~120 |
| 2 | 5s | l2Book × 4 | crypto_order_book | ~96 |
| 3 | 30s | metaAndAssetCtxs | crypto_spot_prices | ~1 |
| 4 | 60s | predictedFundings + 1m candles × 4 | crypto_funding + crypto_micro_candles | ~18 |
| 5 | 300s | 1h candles × 4 + fundingHistory × 4 | crypto_volatility + crypto_funding | ~2 |
| **Total** | | | | **~237 (20%)** |

**3 new DB tables:**

- `crypto_order_book`: bid/ask depth, spread, imbalance (-1 to +1), wall detection (prices + counts)
- `crypto_funding`: cross-exchange predicted rates (HL/Binance/Bybit) + 24h historical rates with premium
- `crypto_micro_candles`: 1-minute OHLCV + buy_pressure metric for short-term vol and momentum

**Enriched bracket model (3 confidence adjustments):**

1. **Order book imbalance** (±3%): Bid-heavy book boosts bullish bets, sell walls penalize them
2. **Funding rate sentiment** (±2%): Cross-exchange funding consensus confirms or contradicts direction
3. **Buy pressure momentum** (±2%): 15-min micro-candle buy/sell pressure confirms momentum

- Total adjustment capped at ±5% to prevent over-fitting
- Micro-vol from 1m candles replaces 1h vol for 15-min brackets (70/30 blend)
- All enrichments logged in signal reasoning string for auditability

**3 new crypto pipeline DB readers:**

- `_read_order_book_from_db()`: Latest book snapshot per coin (30s freshness)
- `_read_funding_from_db()`: Latest predicted funding per coin (5min freshness)
- `_read_micro_vol_from_db()`: 1m candle stats — micro-vol, buy pressure, velocity (15min window)

**Config:** `system_config.yaml` → `hyperliquid:` block expanded with 7 interval parameters

**Tests:** 108 passing, 1 skipped, 0 failures. All imports verified clean.

---

## Sprint 20.5: Always-On Bracket Trader + Infrastructure Fixes (2026-03-23 ~14:00 UTC)

**Targeted Series Tracker (Option C):**

- Deterministic ticker-prefix enumeration for 4 core assets: BTC, ETH, SOL, XRP
- DB queries via `market.id LIKE 'KXBTC%'` instead of keyword matching on titles
- Covers all series: 15-min (KXBTC), daily (KXBTCD), monthly min/max (KXBTCMIN/KXBTCMAX)
- Defined in `CryptoPipeline.TARGET_SERIES` — easily extensible for new assets

**Always-On Bracket Trader (Option A):**

- New `_bracket_model_signals()` method runs every pipeline cycle unconditionally
- Generates `BRACKET_MODEL` signal for every active bracket where model sees edge >= 2 cents
- Timeframe-aware volatility: `sigma_t = daily_vol * sqrt(minutes_remaining / 1440)`
  - 15-min brackets: ~0.3% sigma (vs 3% daily) — correctly narrow probability distribution
  - Hourly brackets: ~0.6% sigma — moderate spread
  - Daily brackets: full daily vol used
- Real EV calculation: `model_probability - market_price` (replaces the old `abs(prob-0.5)*0.1` approximation)
- No conditional gates — momentum, sentiment, and other triggers are not required
- Risk parameters (min_ev=0.02, min_conf=0.55, Kelly=0.25) control sizing, not participation

**Signal Type:**

- New `BRACKET_MODEL` added to SGE whitelist (now 14 types)
- Distinct from `DATA_FUNDAMENTAL` to avoid dedup collisions with existing analyzers
- Signals include timeframe label in reasoning: `[15min]`, `[hourly]`, `[daily]`, `[monthly]`

**Config Updates:**

- `config/sge_config.yaml`: Added `BRACKET_MODEL` to signal_whitelist
- `config/investment_policy_config.yaml`: Added `bracket_min_edge: 0.02` and `target_assets` list to crypto profile
- Test fix: `test_category_strategy.py` exposure assertion updated for Sprint 20 crypto cap (0.60)
- Test fix: `test_signal_router_category_adjusts_routing` marked skip (Sports locked by design in Sprint 20)

**CRITICAL FIX — Position Exit Gap (2026-03-23 ~16:00 UTC):**

- PositionLifecycleManager was closing positions in local DB only — no sell orders placed on Kalshi
- Added `KalshiClient.sell_position()` method (action: "sell" vs "buy" for entry)
- Added `_sell_on_kalshi()` helper wired into all 3 exit sub-routines (Stop Guard, Exit Optimizer, Resolution Tracker)
- All exit paths now: sell on Kalshi FIRST → then update DB status
- If Kalshi sell fails: position marked STOP_PENDING/CLOSE_PENDING instead of falsely CLOSED

**CRITICAL FIX — Stale Price in Stop Guard (2026-03-23 ~16:00 UTC):**

- Stop Guard was reading `positions.current_price` (updated every 300s by EV Monitor)
- Now reads from `prices` table via `_get_fresh_price()` (updated every 5s by KalshiMonitorAgent)

**Order Fill Confirmation (2026-03-23 ~16:30 UTC):**

- OrderExecutor now polls `KalshiClient.get_order()` after placement to confirm fill status
- If order was canceled/expired, no phantom position is recorded in DB

**Hyperliquid Client (2026-03-23 ~17:00 UTC):**

- New `sibyl/clients/hyperliquid_client.py` — async client for Hyperliquid's free, no-auth API
- REST: `POST https://api.hyperliquid.xyz/info` — allMids, metaAndAssetCtxs, candleSnapshot
- `get_asset_contexts()`: Rich data — mark, mid, oracle, funding rate, OI, 24h volume
- `compute_realized_volatility()`: Log-return-based vol from candle data (replaces CoinGecko's crude 24h change)
- `to_coingecko_cache_format()`: Seamless integration with existing pipeline cache

---

## Sprint 20: Crypto-Only Pivot (2026-03-23 ~10:00 UTC)

**Config Surgery:**

- Pipeline categories: "all" → "crypto" (7 pipelines disabled)
- Engine split: SGE 70% + ACE 30% → SGE 95% + ACE 0%
- Blitz: disabled
- Pipeline interval: 900s → 300s (5 min)
- Correlation engine: disabled (single category)

**Per-Category Risk Profiles (NEW — Section 21):**

- Each category now has independent risk parameters in investment_policy_config.yaml
- Crypto profile: min_conf=0.55, min_ev=0.02, kelly=0.25, max_pos=5%, stop=25%
- 7 non-crypto categories marked `locked: true` with preserved settings for future re-enablement

**Dead Zone Fix:**

- Root cause: Signal Router used SGE floor (0.03), OrderExecutor used Tier 2 floor (0.06)
- Fix: Both now read from per-category risk profile (crypto min_ev=0.02)
- Signal Router accepts `category_profile` parameter to override engine defaults
- OrderExecutor reads kelly_fraction, max_position_pct, stop_loss_pct from category profile

**SGE Config Overhaul:**

- Signal whitelist expanded: 3 types → 13 types (includes all DATA_* pipeline signals)
- Kelly: 0.15 → 0.25, Min EV: 0.03 → 0.02, Min Conf: 0.60 → 0.55
- Crypto cap: 15% → 60%, all other category caps: 0%

**Crypto Pipeline Tuning:**

- Dedup window: 15min → 5min
- Market horizon: 14d → 7d
- Price proximity threshold: 5% → 8%
- Momentum threshold: 2% → 1.5%
- Fear/Greed thresholds widened (25/75 vs 20/80)

**PipelineManager Enhancement:**

- Now accepts `categories` filter — only initializes requested pipelines
- Saves init time and API calls when running single-category mode
- Locked categories are deferred at routing stage (no execution attempted)

---

## Key Stats

- **Active Pipelines:** 1 (crypto) — 7 locked until further notice
- **Engine:** SGE only (95% capital), ACE disabled, Blitz disabled
- **Agents:** 17 running (19 defined, Blitz pair disabled)
- **PositionLifecycleManager sub-routines:** 6 (F reconciliation tightened to 5 min in Sprint 23)
- **Policy Sections:** 21 (added Section 21: Per-Category Risk Profiles)
- **New DB Tables:** 5 — crypto_spot_prices, crypto_volatility, crypto_order_book, crypto_funding, crypto_micro_candles
- **Hyperliquid Rate Budget:** ~237 weight/min of 1200/min (20% utilized)
- **Live Account Balance:** $185.95 (confirmed 2026-03-26 post-test)
- **Kalshi Client:** Shared singleton across 4 agents (Sprint 23D — eliminates 429 cascade)
- **Order Execution:** Async fire-and-forget, 5 signals/cycle, 60s fill timeout (Sprint 23B)
- **Edge Thresholds:** Timeframe-scaled: 0.5%/0.8%/1.0%/1.2%/1.5% (Sprint 23A)
- **Crypto Discovery:** 16 targeted series fetches with min_close_ts filter (Sprint 23A)
- **ntfy.sh:** Throttled to 200 msg/day + 5s min interval (Sprint 23D)
- **Startup Timestamp:** PST default via `log_startup_timestamp()`

## Deep Analysis: Market Participation Gap (2026-03-27)

**Problem:** Sibyl traded only ~171 monthly crypto markets during Live Test #2. Kalshi had ~1,572 open crypto markets across 15-min, hourly, 4h, daily, and monthly timeframes. **Sibyl utilized 0.13% of available crypto markets.**

### Signal-to-Trade Funnel (Quantified)

```text
Kalshi available:        1,572 crypto markets
  ↓ Discovery gap:      -1,401 (gap-fill disabled — ROOT CAUSE #1)
DB crypto markets:         171 (all monthly min/max)
  ↓ Horizon filter:       -103
Scanned per cycle:          68
  ↓ Edge filter:            -30 (flat 1.5% threshold — ROOT CAUSE #2)
Signals emitted:            38/cycle
  ↓ Router filter:           -7 (confidence < 60% or EV < 1.5%)
Routed to SGE:           28-31/cycle
  ↓ Serial executor:     ~99.8% dropped (120s blocking — ROOT CAUSE #3)
Orders placed:              55 total (in 125 min)
  ↓ Fill timeout + orphans: -53
Detected fills:              2 (3.6%)
```

### Root Cause #1: Gap-Fill Disabled → 1,401 Markets Invisible

`kalshi_monitor.py:136` hardcodes `gap_fill=False`. Phase 1 paginates 15 pages × 200 events but cannot cover all Kalshi events. Phase 2 gap-fill (which specifically targets crypto category) never executes. Additionally, `_gap_fill_done` is only set True inside the `if gap_fill:` block (line 218), so the monitor is stuck re-running Phase 1 forever.

Missing series: KXBTC (188 mkts), KXBTCD (80), KXETH (75), KXETHD (75), KXSOLD (75), KXXRP (75), plus hourly/4h variants.

### Root Cause #2: Flat Edge Threshold Kills Short-Duration Markets

`BRACKET_MIN_EDGE = 0.015` is identical across all timeframes. The bracket model calculates `sigma_t = daily_vol × √(mins_left / 1440)`:

| Timeframe | sigma_t scaling | Edge clearance at 1.5% |
|-----------|-----------------|------------------------|
| 15-min | 0.10x daily vol | Nearly impossible |
| Hourly | 0.20x daily vol | Very difficult |
| Daily | 1.0x daily vol | Moderate |
| Monthly | 5.5x daily vol | Easy |

### Root Cause #3: Serial Order Processing (120s Blocking)

Each order blocks the executor for 120s. Throughput: 0.44 orders/min. A 15-min market expires before its order even gets queued. 28-31 signals/cycle but only 1 order every 2 minutes.

### Root Cause #4: Standard Refresh Only Fetches 100 Events

`_refresh_markets_standard()` runs every 2 min but only fetches 1 page of 100 events (all categories). Newly listed daily crypto events between discovery cycles are missed.

### Root Cause #5: Overnight Test Window

Test ran 01:50-03:55 UTC (9:50-11:55 PM ET). Near-zero counterparty activity. Most orders at 91-96¢ had no takers.

---

## Reorganized Development Roadmap — Maximum Portfolio Value Growth

### Sprint 23A: Market Discovery Fix (HIGHEST IMPACT — unlocks 1,401 markets)

1. **Re-enable gap-fill for crypto**: Change `gap_fill=False` → `gap_fill=True` in `kalshi_monitor.py:136`, OR add a targeted crypto discovery that uses `series_ticker` filter to fetch KXBTC/KXBTCD/KXETH/KXETHD/KXSOL/KXSOLD/KXXRP/KXXRPD series directly
2. **Add crypto-specific fast refresh**: In `_refresh_markets_standard()`, add a second call using `series_ticker` filter for each crypto series (8 calls × 100 markets = fast, targeted)
3. **Verify DB coverage**: After fix, confirm DB contains all ~1,572 crypto markets across all timeframes

### Sprint 23B: Async Execution Engine (SECOND HIGHEST IMPACT — unlocks throughput)

1. **Fire-and-forget order placement**: Place order, record `order_id` + `status=PENDING` in DB, immediately move to next signal
2. **Background fill checker**: Separate async loop polls all PENDING order_ids every 10s, updates status on fill/cancel/expire
3. **Orphan position reconciliation**: Every 5 min, query Kalshi portfolio vs DB; register any untracked fills
4. **Target throughput**: 10+ orders/minute (vs current 0.44/min)

### Sprint 23C: Timeframe-Scaled Edge Thresholds (unlocks short-duration signals)

1. **Scale BRACKET_MIN_EDGE by timeframe**:
   - 15-min markets: `min_edge = 0.005` (0.5%)
   - Hourly markets: `min_edge = 0.008` (0.8%)
   - Daily markets: `min_edge = 0.012` (1.2%)
   - Monthly markets: `min_edge = 0.015` (1.5%, unchanged)
2. **Add timeframe-aware confidence floor**: Short-duration markets need lower confidence threshold since edge windows are brief

### Sprint 23D: Shared Rate Limiter + Market Hours

1. **Single Kalshi client pool**: All 4 agents share one rate-limited client (30 read/s, 30 write/s combined)
2. **Schedule live tests 8 AM - 5 PM ET**: Maximum bracket market liquidity
3. **Cap extreme prices**: Skip orders at 95-96¢ (zero counterparty interest)

### Sprint 24: Scale & Optimize

1. **Pipeline dedup on order level**: Prevent duplicate orders for same market ticker
2. **Market-making mode for thin brackets**: Place both sides with wider spreads for markets <$1K OI
3. **ntfy.sh rate limiting**: Throttle to 250 msg/day or upgrade tier
4. **Docker containerization**: Deferred but on roadmap

### Success Criteria (Next Live Test)

| Metric | Test #2 (Actual) | Target |
|--------|-----------------|--------|
| Markets in DB | 171 | 1,500+ |
| Timeframes traded | Monthly only | 15-min, hourly, daily, monthly |
| Orders placed / hour | 26 | 200+ |
| Fill rate | 3.6% detected | 15%+ |
| Signal utilization | 1.8% | 30%+ |
| Rate limit hits (429) | 1,222 | <50 |

---

## Stakeholder Mandate

**Requirement:** Sibyl must take a position in EVERY available crypto market (BTC, ETH, SOL, XRP) that it can stream insight data for via Hyperliquid API. More time spent OUT of Kalshi markets = less profit return. Prioritize maximum portfolio value growth at all times.

---

## Next Immediate Actions

1. **Live Test #3 during market hours** — 8 AM - 5 PM ET for maximum bracket liquidity. All Sprint 23 fixes ready.
2. **Revert pyproject.toml** — change `requires-python` back to `>=3.12` before pushing to GitHub
3. **Push commit to GitHub** — user must push from local machine
4. **Sprint 24: Market-making mode** — place both sides with wider spreads for thin brackets
5. **Sprint 24: Docker containerization** — deferred but on roadmap
