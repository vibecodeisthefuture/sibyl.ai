# Sibyl.ai — Comprehensive System Audit Report

**Date:** 2026-04-01
**Auditor Role:** Senior Quantitative Researcher & Prediction Market Strategist
**Scope:** Full four-pillar audit (Signal Alpha, Execution Logic, Hedging & Correlation, Feedback Loops)
**System Version:** Sprint 28 (post-LT5)
**Capital at Risk:** ~$100 ($191.69 deposited, -47.9% all-time)

---

## Executive Summary

Sibyl is a structurally ambitious autonomous prediction market trader with solid engineering fundamentals — async multi-agent architecture, config-driven risk parameters, comprehensive position lifecycle management, and full outcome tracking in SQLite. The codebase reflects 28 sprints of iterative hardening based on live test failures.

However, the system suffers from **five systemic deficiencies** that collectively explain the -47.9% drawdown:

1. **The signal model is mathematically naive.** A normal-distribution CDF applied to crypto — an asset class defined by fat tails and volatility clustering — produces systematic edge overestimation of 20-50%. The model has no regime detection, no mean-reversion term, and a degenerate sigma floor that inflates short-duration edge estimates.

2. **Execution ignores fees in P&L and mis-sizes NO positions.** Every reported P&L figure is overstated because fee deductions are absent from position accounting. The Kelly formula computes payout incorrectly for NO-side trades, undersizing them by 2-5x. A documented exit strategy (momentum stall) was never implemented.

3. **Correlation management operates at the wrong granularity.** The system treats "Crypto" as a single category, not distinguishing BTC from ETH from SOL. Ten BTC brackets at different strikes and timeframes are ten "independent" positions under current logic — but a single BTC move hits all of them simultaneously. Per-underlying concentration limits do not exist.

4. **Zero operational feedback loops exist.** The system records every outcome with precision (signals, positions, performance table with correct/incorrect flags), but no agent reads this data to update any parameter. Confidence calibration, Kelly optimization, signal-type ranking, and edge threshold adaptation are all absent. Every model improvement to date has been a human manually editing YAML after analyzing live test results.

5. **Architecture complexity exceeds capital scale.** 8 pipelines, 3 execution engines, ~20 agents, LLM narration, X sentiment analysis, and a breakout scout — servicing $100 in capital. Each additional component is a surface for bugs (and has been: orphan positions, hybrid mode states, gate logic inversions, accumulation overflows). Simplification would reduce bug surface area and cognitive load.

**Estimated net impact of known issues:** The combination of edge overestimation (Pillar 1), fee omission (Pillar 2), and correlation blindness (Pillar 3) means the system's *reported* positive-EV trades likely have **true expected value near zero or negative** after accounting for model error, fees, and correlated drawdowns.

---

## Table of Contents

1. [Pillar 1: Signal Alpha](#pillar-1-signal-alpha)
2. [Pillar 2: Execution Logic](#pillar-2-execution-logic)
3. [Pillar 3: Hedging & Correlation](#pillar-3-hedging--correlation)
4. [Pillar 4: Feedback Loops](#pillar-4-feedback-loops)
5. [Cross-Pillar Issue Matrix](#cross-pillar-issue-matrix)
6. [Prioritized Remediation Roadmap](#prioritized-remediation-roadmap)
7. [Appendix: Detailed Bug Registry](#appendix-detailed-bug-registry)

---

## Pillar 1: Signal Alpha

### 1.1 Model Architecture

Sibyl estimates bracket probabilities using a **lognormal drift-diffusion model**:

```
sigma_t = daily_vol * sqrt(minutes_remaining / 1440)
z = (threshold - spot_price) / (spot_price * sigma_t)
model_prob = CDF(z)  [standard normal]
edge = model_prob - market_price - half_spread - fee_cost
```

**Source:** `crypto_pipeline.py:1391-1405`

The model is fed by a multi-source volatility stack:

- **Priority 1:** Realized vol from `crypto_volatility` table (Hyperliquid 5-min updates)
- **Priority 2:** Micro-candle vol (1-min bars, 15-min window) for sub-20-min markets
- **Priority 3:** CoinGecko 24h price change as fallback

### 1.2 Critical Findings

#### FINDING S-1: Normal CDF on Fat-Tailed Assets [CRITICAL]

The entire signal model rests on a Gaussian assumption. Crypto assets exhibit:

- **Kurtosis of 5-15x** normal (BTC daily returns have kurtosis ~8-12)
- **Volatility clustering** (GARCH effects) — high-vol days cluster together
- **Gap risk** — overnight/weekend moves that violate continuous-path assumptions
- **Mean-reversion at intraday scales** — 15-min brackets exhibit mean-reversion, not random walk

The normal CDF **systematically overestimates** tail bracket probabilities. A bracket at 3-sigma under the normal model might be 1.5-sigma under the true distribution. This means:

- Edge estimates on extreme brackets (< 15c or > 85c) are inflated by **30-100%**
- The model generates phantom alpha that doesn't exist in the market

**Impact:** Every signal the system generates carries a positive bias in estimated edge. This is the foundational flaw that cascades through sizing, execution, and P&L.

#### FINDING S-2: Degenerate Sigma Floor on Short Timeframes [CRITICAL]

```python
sigma_t = max(sigma_t, 0.003)  # 0.3% minimum — crypto_pipeline.py:1393
```

A 0.3% sigma on a 15-minute bracket is effectively zero volatility. At sigma=0.003, a 2-sigma move is 0.6% — meaning the model assigns near-certainty to brackets within 0.6% of the current price. This produces **wildly inflated edge estimates** for tight, short-duration brackets — precisely the high-frequency markets Sibyl targets most.

**Impact:** 15-minute bracket edge estimates may be 2-5x overstated when the floor binds.

#### FINDING S-3: Micro-Volatility Blend is Arbitrary [MEDIUM]

```python
# crypto_pipeline.py:1385-1389
if timeframe == "15min" and micro_data.get("micro_vol_daily"):
    effective_vol = micro_daily * 0.7 + daily_vol * 0.3  # 70/30 blend
```

The 70/30 weighting between micro-candle vol and daily vol has no empirical justification. During volatility regime shifts (e.g., FOMC announcement, exchange hack), micro-vol lags the new regime by the full 15-minute lookback window. The blend applies **only to 15-min markets**, not hourly — creating a discontinuity in vol estimation at the 20-minute boundary.

#### FINDING S-4: Fee Multiplier Semantics Are Wrong [HIGH]

```python
# base_pipeline.py:393
fee_cost = fee_per_contract * fee_multiplier  # 0.014 * 2.0 = 0.028
```

The code treats `fee_multiplier=2.0` as "pay the fee twice" (2.8% threshold). The stated intent is "safety margin," but the implementation creates a **fixed 2.8% edge floor** regardless of trade direction or holding period. For a taker buy at 30c:

- Actual roundtrip fee: ~1.4% (0.7% per side)
- Applied threshold: 2.8%
- **Result:** 50% of marginally-profitable trades are rejected unnecessarily

Conversely, for positions held to settlement (no sell-side fee), the 2x multiplier is overly conservative. The fee model should distinguish between roundtrip exits and settlement exits.

#### FINDING S-5: FLB Config Values Are Dead Code [MEDIUM]

The investment policy config defines per-timeframe longshot rejection floors:

```yaml
# investment_policy_config.yaml:426-432
longshot_reject_by_timeframe:
  "15min": 0.05    # 5c floor
  "hourly": 0.10   # 10c floor
  "weekly": 0.30   # 30c floor
```

But the signal generator **hard-rejects at 10c for ALL timeframes** (`crypto_pipeline.py:1461`). The config-driven tiered floors — which are the Sprint 29 fix documented in the strategy overview — are never read during signal generation. The executor reads them, but signals for 5c-10c brackets on 15-min markets are killed before they reach the executor.

#### FINDING S-6: Confidence Adjustments Stack Without Correlation Accounting [MEDIUM]

Four independent adjustments are summed into confidence:

| Adjustment | Source | Max Impact | Correlation |
|------------|--------|------------|-------------|
| Order Book Imbalance (HL) | Hyperliquid perp book | +3% | High with OBI |
| Funding Rate | Hyperliquid funding | +2% | High with Pressure |
| Micro Buy Pressure | 1-min candle buy/sell vol | +2% | High with Funding |
| Kalshi OBI | Kalshi bracket book | +4% | High with HL OBI |

**Total possible uplift:** +11% confidence from adjustments alone.

These four signals are **highly correlated** — they all measure directional sentiment through different lenses of the same market microstructure. Summing them without correlation discounting inflates confidence by an estimated **5-8%** on average. The Kalshi OBI adjustment claims "62% directional accuracy" but maps this to an 8% confidence weight — a mathematical non-sequitur (62% accuracy = ~0.18 bits of information, not 8% confidence).

#### FINDING S-7: No Crypto Calibration Offset [MEDIUM]

Every other category has a calibration offset (weather: -0.09, culture: -0.09, sports: -0.05), but crypto has `calibration_offset: 0.00`. Given findings S-1 through S-6, crypto confidence is overestimated by 10-20%. A **-0.10 to -0.15 offset** would be appropriate based on the structural biases identified.

### 1.3 Signal Alpha Verdict

| Aspect | Rating | Notes |
|--------|--------|-------|
| Model foundation | Poor | Normal CDF is wrong distributional assumption for crypto |
| Volatility estimation | Fair | Multi-source stack is good; scaling and floors are flawed |
| Edge computation | Poor | Fee semantics wrong, spread data stale, no impact model |
| Confidence calibration | Poor | Stacked adjustments inflate by 5-8%; no crypto offset |
| Arbitrage detection | Fair | Buy-all-YES arb is sound; targeted-NO correctly disabled |
| FLB handling | Fair | Concept correct; implementation has dead code and linear approximation |

**Overall: The system generates signals with a systematic positive bias in estimated edge. True alpha is likely 20-50% lower than reported.**

---

## Pillar 2: Execution Logic

### 2.1 Order Flow Architecture

```
ROUTED signal → OrderExecutor (3s cycle, batch of 5)
  → Kelly sizing → Correlation penalty → Policy gate
  → Price gates (floor/ceiling/NO premium)
  → Contract cap → Taker order on Kalshi
  → Async fill tracking (60s timeout)
  → Position recorded → PositionLifecycleManager takes over
```

The execution layer is well-architected with multiple risk gates applied sequentially. The async fire-and-forget model (Sprint 23B) solved the original serial bottleneck (0.44 orders/min → 10+/min).

### 2.2 Critical Findings

#### FINDING E-1: NO Position Kelly Sizing is Broken [CRITICAL]

```python
# order_executor.py:548
payout = (1.0 / current_price) - 1.0 if current_price > 0 else 0
```

This formula computes payout based on the YES price regardless of position side. For a NO position:

- YES price = 70c → `current_price` = 70c
- Payout computed: `(1/0.70) - 1 = 0.43` (43% return)
- **Actual NO payout:** Entry at 30c, win = 70c profit → `(1/0.30) - 1 = 2.33` (233% return)

The Kelly formula then produces a fraction based on a 43% payout instead of 233%, **undersizing NO positions by 2-5x**. Given that Sprint 28 disabled most NO trading (BRACKET_ARB targeted-NO removed, 50c NO premium gate), this bug is partially masked — but any NO position that passes the gates is systematically undersized.

**Fix:**

```python
if side == "YES":
    payout = (1.0 / current_price) - 1.0
else:
    no_price = 1.0 - current_price
    payout = (1.0 / no_price) - 1.0 if no_price > 0 else 0
```

#### FINDING E-2: Zero Fee Accounting in P&L [HIGH]

No P&L calculation in the entire codebase deducts fees:

```python
# position_lifecycle.py:1009-1020
def _compute_pnl_with_price(pos, current_price):
    entry = float(pos["entry_price"])
    size = float(pos["size"])
    if pos["side"] == "YES":
        return (current_price - entry) * size
    else:
        return (entry - current_price) * size
```

Kalshi charges taker fees on every transaction. With $29.35 in cumulative fees on ~$100 portfolio (29.4% fee drag), this omission means:

- **All reported P&L is overstated** by the fee amount
- **Risk dashboard metrics (Sharpe, drawdown, win rate) are incorrect**
- **The performance table's `pnl` column does not reflect actual returns**
- **The system cannot distinguish between strategy losses and fee losses**

The `executions` table has no `fee_amount` column. Fees are invisible to the system.

#### FINDING E-3: Momentum Stall Exit — Documented But Not Implemented [MEDIUM]

The position lifecycle docstring (line 463) and config (`position_lifecycle_config.yaml:18-19`) define a momentum stall exit:

```yaml
momentum_stall_cycles: 4
momentum_stall_threshold: 0.005
```

But the actual Sub-C Exit Optimizer code contains only two exit triggers:

1. EV Capture (>80% of max profit achieved)
2. Time Decay (losing position within 15 min of settlement)

The momentum stall trigger was **never coded**. This means positions that flatline (no price movement for extended periods) are never exited, locking capital in stagnant markets until settlement or stop-loss.

#### FINDING E-4: Correlation Block Flags Are Write-Only [MEDIUM]

Position lifecycle Sub-E writes correlation block flags to `system_state`:

```python
# position_lifecycle.py:691
await self.db.execute(
    "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES (?, ?, ...)",
    (f"corr_block_{event_id}", f"BLOCKED: {exposure_pct:.1%}"),
)
```

But `OrderExecutor` **never reads these flags**. The correlation scanner detects dangerous concentration, writes a warning, and the executor ignores it. The 7% event-level block threshold is purely decorative.

#### FINDING E-5: Partial Fill Handling Missing [MEDIUM]

When checking pending orders for fills (`order_executor.py:269-284`), the code extracts `average_fill_price` but **never checks `filled_count`**. If an order for 5 contracts partially fills 2:

- Position is recorded as if all 5 filled
- Remaining 3 contracts are neither cancelled nor resubmitted
- Portfolio exposure tracking is incorrect

#### FINDING E-6: Orphan Positions Default to SGE Engine [MEDIUM]

During reconciliation, positions found on Kalshi but not in the DB are created with `engine='SGE'` hardcoded (`position_lifecycle.py:777`). If the orphan was actually an ACE position, it's misattributed — affecting engine capital tracking, circuit breaker thresholds, and portfolio allocation.

#### FINDING E-7: EV Monitor Ignores Entry Price [LOW]

Sub-B recomputes EV using current market price but doesn't factor in the position's entry price:

```python
# position_lifecycle.py:429-437
if current_price < 0.50:
    potential_profit = 1.0 - current_price
    potential_loss = current_price
```

A position entered at 60c has 40c upside and 60c downside, but the recomputation treats it as if entered at the current price. This produces false EV shift alerts.

#### FINDING E-8: No Orderbook Freshness Validation [LOW]

Taker pricing reads `best_ask`/`best_bid` from the orderbook table but never validates data freshness. If the orderbook hasn't been updated in 5+ minutes (monitor lag, rate limiting), the executor prices orders against stale data — potentially crossing a spread that no longer exists.

### 2.3 Execution Logic Verdict

| Aspect | Rating | Notes |
|--------|--------|-------|
| Order flow architecture | Good | Well-structured gates, async fills, batch processing |
| Kelly implementation | Poor | NO-side payout bug; systematic undersizing |
| Fee handling | Absent | Zero fee accounting anywhere in P&L |
| Position lifecycle | Fair | 5 of 6 sub-routines work; momentum stall missing |
| Reconciliation | Fair | Handles ghosts/orphans; engine attribution wrong |
| Slippage management | Poor | Logged but not persisted or used in sizing |
| Fill management | Poor | No partial fill handling; all-or-nothing assumption |

**Overall: The execution framework is architecturally sound but has two high-severity bugs (NO sizing, fee omission) that corrupt position sizing and all downstream performance metrics.**

---

## Pillar 3: Hedging & Correlation

### 3.1 Capital Allocation Chain

```
Kalshi Balance (live sync every 120s)
  → 5% Cash Reserve
  → Allocable Capital = Total - Reserve
  → Engine Split: SGE 95% / ACE 0% (Sprint 20: crypto-only = SGE-only)
  → Per-Category Cap: Crypto = 95% of engine
  → Per-Market Cap: 5% per market_id
  → Correlation Penalty: 10% per additional position in category
  → Kelly Sizing → Contract Cap → Order
```

### 3.2 Critical Findings

#### FINDING H-1: No Per-Underlying Concentration Limit [CRITICAL]

The system has **no mechanism to limit exposure to a single underlying asset** (BTC, ETH, SOL, XRP). Controls exist at two levels:

- **Per-market-id:** 5% cap (e.g., KXBTC_15MIN_T85000)
- **Per-category:** 95% cap (all of "Crypto")

But nothing in between. A concrete scenario on $100 capital:

| Position | Market ID | Underlying | Exposure |
|----------|-----------|------------|----------|
| 1 | KXBTC_15MIN_T84000 | BTC | $5.00 |
| 2 | KXBTC_15MIN_T85000 | BTC | $5.00 |
| 3 | KXBTC_HOURLY_T84000 | BTC | $5.00 |
| 4 | KXBTC_DAILY_T83000 | BTC | $5.00 |
| 5 | KXBTC_WEEKLY_T82000 | BTC | $5.00 |
| **Total BTC** | | | **$25.00 (25%)** |

Each position passes the 5% per-market gate. Total crypto is 25% < 95%. But **25% of capital is exposed to a single BTC directional move**. If BTC drops 3% in an hour, all five positions lose simultaneously.

The correlation penalty (10% base, count-based) reduces the 6th BTC position's size slightly, but it's applied at the **category level** (Crypto), not the underlying level (BTC). Adding an ETH position triggers the same penalty as adding another BTC position — despite ETH having ~0.7 correlation with BTC vs. ~1.0 for BTC-to-BTC brackets.

#### FINDING H-2: Event-Level Correlation Scanner Likely Non-Functional for Crypto [HIGH]

Sub-E monitors exposure by `event_id` from the markets table:

```python
# position_lifecycle.py:644-703
# Group positions by event_id → sum exposure → block if >7%
```

But **Kalshi crypto bracket markets may not populate `event_id` consistently**. The scanner depends on the `markets` table having correct `event_id` values, which are set during market discovery. If `event_id` is null or inconsistent for crypto brackets, the scanner silently does nothing — the 3%/7% thresholds never trigger.

**Verification needed:** Query the markets table for crypto brackets and check `event_id` population rate.

#### FINDING H-3: Hedging Is Architecturally Blocked [HIGH]

The `reject_duplicate_exposure` avoidance rule (`investment_policy_config.yaml:382`) blocks both engines from holding positions on the same market. This means:

- SGE YES + ACE NO on the same bracket = **REJECTED** (duplicate exposure)
- SGE YES + SGE NO on the same bracket = **ALLOWED** (single engine)

True hedging (offsetting directional risk) would require cross-engine positions, which are blocked. Same-engine hedging defeats the capital isolation purpose of multi-engine architecture.

In practice, with ACE at 0% allocation (Sprint 20), this is moot — but it's a structural impediment to any future hedging strategy.

#### FINDING H-4: Drawdown Doesn't Trigger Automatic Sizing Reduction [HIGH]

The risk dashboard computes drawdown levels:

```
CLEAR: <5%     → Normal operations
WARNING: 5-10% → Log alert
CAUTION: 10-20% → "Reduce new position sizing 50%" (documented)
CRITICAL: >20% → "Halt all new positions" (documented)
```

But **the OrderExecutor does not read the drawdown level from `system_state`**. The documented 50% sizing reduction at CAUTION is not implemented. Only the circuit breaker (3 stops in 15 minutes) actually halts execution — and it's a blunt instrument with no auto-reset.

#### FINDING H-5: Correlation Penalty Uses Position Count, Not Dollar Exposure [MEDIUM]

```python
# order_executor.py:947-950
multiplier = max(0.10, 1.0 - effective_penalty * existing_count)
```

A $0.50 position and a $5.00 position in the same category contribute equally to the penalty. Ten micro-positions ($0.50 each = $5 total) produce the same penalty as one $5 position — despite having identical economic exposure. The penalty should be dollar-weighted.

#### FINDING H-6: No Cross-Asset Correlation Awareness [MEDIUM]

The system makes no distinction between:

- BTC + BTC brackets (correlation ~1.0)
- BTC + ETH brackets (correlation ~0.6-0.8)
- BTC + SOL brackets (correlation ~0.5-0.7)
- Crypto + Sports (correlation ~0.0)

All are penalized at the same 10% rate per position within the "Crypto" category. A proper correlation matrix would:

1. Heavily penalize same-underlying concentration
2. Moderately penalize correlated-underlying concentration
3. Minimally penalize uncorrelated positions

### 3.3 Hedging & Correlation Verdict

| Aspect | Rating | Notes |
|--------|--------|-------|
| Capital allocation | Good | Balance sync, reserve, engine splits all work |
| Per-market limits | Good | 5% cap per market_id is enforced |
| Per-underlying limits | Absent | No BTC/ETH/SOL-level caps exist |
| Correlation penalty | Poor | Category-level, count-based, ignores actual correlation |
| Event-level monitoring | Uncertain | Scanner exists but may not fire for crypto |
| Hedging capability | Blocked | Architecture prevents cross-engine hedging |
| Drawdown response | Partial | Computed but not applied to sizing |

**Overall: The system can accumulate 25%+ exposure to a single underlying (BTC) through multiple bracket positions, with no mechanism to detect or limit this concentration. A single adverse BTC move can simultaneously trigger losses across all positions.**

---

## Pillar 4: Feedback Loops

### 4.1 Data Infrastructure

Sibyl has **excellent data collection** for learning:

| Table | Records | Useful For |
|-------|---------|------------|
| `signals` | Every generated signal | Signal type performance, confidence calibration |
| `positions` | Every position opened/closed | Entry/exit analysis, holding period optimization |
| `performance` | Every resolved position | Accuracy tracking, correct/incorrect flags |
| `executions` | Every order placed | Fill analysis, slippage measurement |
| `engine_state` | Per-engine capital snapshots | Capital efficiency, engine comparison |

The `performance` table specifically includes `correct` (INTEGER), `pnl`, `ev_estimated`, and `ev_realized` columns — the exact data needed for model calibration.

### 4.2 Critical Findings

#### FINDING F-1: Zero Operational Feedback Loops [CRITICAL]

Despite the data infrastructure, **no agent reads the performance table to update any model parameter**. The complete list of parameters that *should* adapt but don't:

| Parameter | Current Value | Should Adapt Based On | Status |
|-----------|---------------|----------------------|--------|
| Confidence formula coefficients | 0.55 + edge_ratio * 0.06 | Actual accuracy vs predicted | Static |
| Calibration offset (crypto) | 0.00 | Brier score divergence | Static |
| Kelly fraction | 0.12 | True win/loss ratio | Static (manually cut in Sprint 28) |
| Edge thresholds by timeframe | 0.5%-1.5% | Hit rate by timeframe | Static |
| Signal type weights | 1.0-1.5 per type | Per-type win rate | Static |
| FLB scaling function | Linear 0.3-1.3x | Empirical price-to-outcome curve | Static |
| Correlation penalty base | 0.10 | Realized inter-position correlation | Static |
| Stop-loss percentage | 35% | Optimal stop analysis on closed positions | Static |

**Impact:** The system trades today with identical parameters to Sprint 28, regardless of how those parameters perform. If 15-minute BTC brackets have a 35% hit rate at 0.65 confidence (well below the 65% implied), the system continues to trade them at the same rate and size.

#### FINDING F-2: Calibration Tool Exists But Is Disconnected [HIGH]

A CLI calibration tool exists at `sibyl/tools/calibrate_confidence.py` (462 lines) that can:

- Query historical signals and resolutions
- Compute per-pipeline calibration curves
- Calculate Brier scores
- Suggest adjustment multipliers

But it:

- Runs **manually** (`python -m sibyl.tools.calibrate_confidence`)
- **Does not apply** its suggestions to config files
- **Does not output** machine-readable results that could be consumed by an agent
- Has **no scheduled execution** (no cron, no agent loop)

The tool produces the data needed for calibration but stops at "here's a report." The last mile — applying the adjustments — is entirely manual.

#### FINDING F-3: Win Rate is Computed But Never Consumed [HIGH]

The risk dashboard computes a 7-day rolling win rate:

```python
# risk_dashboard.py:221-237
async def _compute_win_rate_7d(self) -> float:
    row = await self.db.fetchone(
        """SELECT COUNT(*) as total,
           COALESCE(SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END), 0) as wins
           FROM performance
           WHERE resolved = 1 AND resolved_at >= datetime('now', '-7 days')"""
    )
```

This metric is written to `system_state` as `risk_win_rate_7d` — and then **nothing reads it**. A win rate of 30% and 70% produce identical system behavior. No agent consumes this metric to adjust confidence thresholds, Kelly fractions, or signal routing.

#### FINDING F-4: Override Protocol Auto-Calibration is Specified But Not Coded [MEDIUM]

The investment policy config describes an auto-calibration mechanism:

```yaml
# investment_policy_config.yaml:277-291
override_protocol:
  calibration_period_days: 90
  confidence_raise_increment: 0.02
  # "if overrides underperform over 90 days, raise confidence threshold"
```

No code implements this. The `override_log` table records overrides, but no agent queries it against performance data to trigger threshold adjustments.

#### FINDING F-5: No Per-Asset/Timeframe Performance Bucketing [MEDIUM]

The system can answer "what is our overall win rate?" but **cannot answer without custom SQL:**

- "What is our hit rate on 15-min BTC brackets?"
- "Which signal type performs best for hourly ETH?"
- "Is our confidence calibrated for daily brackets?"

The `performance` table has `signal_id` (links to `signals.timeframe` and `signals.signal_type`) and `position_id` (links to `positions.market_id`), so the data exists. But no materialized view, no scheduled aggregation, and no agent computes these breakdowns.

#### FINDING F-6: Kelly Fraction Was Manually Optimized (Proof of Missing Automation) [INFORMATIONAL]

The Sprint 28 Kelly reduction is documented with empirical justification:

```yaml
# investment_policy_config.yaml:392-395
kelly_fraction: 0.12  # Sprint 28: LT1-LT4: 1-3 contracts (+30-60% ROI)
                       # vs 11+ contracts (-3.8% ROI). Was 0.30.
```

This is a textbook feedback loop — **performed by a human**. The system had the data (position size vs. outcome), the conclusion was clear (oversizing destroys returns), and the fix was applied (60% Kelly cut). But the system itself couldn't perform this analysis or apply the fix. It required a human to run a live test, analyze results, and edit YAML.

### 4.3 Feedback Loops Verdict

| Aspect | Rating | Notes |
|--------|--------|-------|
| Data collection | Excellent | Every signal, position, and outcome is logged |
| Outcome tracking | Excellent | `performance` table with correct/incorrect flags |
| Automated calibration | Absent | No agent reads performance data to update params |
| Win rate tracking | Partial | Computed but not consumed |
| Per-timeframe analysis | Absent | Data exists but no aggregation |
| Per-asset analysis | Absent | Data exists but no bucketing |
| Kelly optimization | Absent | Manual only (Sprint 28) |
| Confidence recalibration | Absent | CLI tool exists but disconnected |

**Overall: Sibyl has best-in-class data infrastructure for learning and zero operational feedback loops. The system records everything it needs to self-improve, then ignores it.**

---

## Cross-Pillar Issue Matrix

The following matrix maps how issues in one pillar compound with issues in others:

| Interaction | Pillars | Compound Effect |
|-------------|---------|-----------------|
| Overestimated edge + No fee accounting | S-1,S-4 + E-2 | Signals report +3% edge; after fees and model error, true edge is -1%. System trades confidently into losses. |
| Overestimated confidence + No calibration feedback | S-6,S-7 + F-1 | Confidence inflated 10-20% by stacked adjustments; never corrected by outcome data. System remains persistently overconfident. |
| No per-underlying limits + Correlated positions | H-1 + H-5,H-6 | BTC accumulates to 25%+ via multiple brackets; correlation penalty treats them as independent. Single BTC move wipes all positions simultaneously. |
| NO sizing bug + Fee omission | E-1 + E-2 | NO positions undersized 2-5x AND their P&L is overstated by fee amount. Double error on NO trades. |
| Dead FLB config + No feedback | S-5 + F-1 | Sprint 29 tiered floors are configured but not applied in signal generation; no feedback loop would detect this discrepancy. |
| Momentum stall unimplemented + No learning | E-3 + F-1 | Capital locked in stagnant positions; system can't learn optimal holding periods because it never measures them. |
| Drawdown sizing not applied + No auto-calibration | H-4 + F-1 | Portfolio can draw down 20%+ without reducing position sizes; no feedback to tighten parameters during losing streaks. |

---

## Prioritized Remediation Roadmap

Issues are ranked by **impact on capital preservation** (preventing further losses) and **implementation complexity**.

### Tier 0: Stop the Bleeding (Do Before Next Live Test)

| # | Issue | Finding | Fix | Effort |
|---|-------|---------|-----|--------|
| 1 | Fee accounting in P&L | E-2 | Add `fee_amount` column to executions; deduct fees in `_compute_pnl_with_price()` | 2-3 hours |
| 2 | NO Kelly sizing | E-1 | Fix payout formula to use NO price when side=NO | 30 min |
| 3 | Correlation block enforcement | E-4 | OrderExecutor reads `corr_block_*` flags from system_state before placing orders | 1 hour |
| 4 | Crypto calibration offset | S-7 | Set `calibration_offset: -0.10` for crypto in investment_policy_config.yaml | 5 min |
| 5 | FLB config activation | S-5 | Wire `longshot_reject_by_timeframe` config into crypto_pipeline signal generation | 1 hour |

**Expected impact:** These five fixes address the worst capital leaks. Fee accounting alone would have identified the $29.35 fee drag as a distinct cost center (rather than lumped with strategy losses). The calibration offset reduces overconfident entries by ~10%.

### Tier 1: Structural Improvements (Next 1-2 Sprints)

| # | Issue | Finding | Fix | Effort |
|---|-------|---------|-----|--------|
| 6 | Per-underlying concentration cap | H-1 | Extract underlying (BTC/ETH/SOL/XRP) from market ticker; enforce 15% max per underlying | 4-6 hours |
| 7 | Drawdown-driven sizing reduction | H-4 | OrderExecutor reads drawdown level; applies 0.5x multiplier at CAUTION, 0x at CRITICAL | 2-3 hours |
| 8 | Momentum stall implementation | E-3 | Track 4-cycle price movement; exit if change < 0.5% per cycle | 3-4 hours |
| 9 | Auto-calibration agent | F-1,F-2 | New agent runs weekly; queries performance table; updates calibration_offset per category if Brier divergence > 0.10 | 8-12 hours |
| 10 | Partial fill handling | E-5 | Check `filled_count` on order confirmation; record actual fill size; resubmit or cancel remainder | 3-4 hours |

### Tier 2: Alpha Improvement (Next 3-5 Sprints)

| # | Issue | Finding | Fix | Effort |
|---|-------|---------|-----|--------|
| 11 | Replace normal CDF with fat-tail model | S-1 | Student-t distribution (df=4-6) or Extreme Value Distribution for tail brackets | 12-16 hours |
| 12 | Dollar-weighted correlation penalty | H-5 | Replace count-based penalty with `SUM(exposure)/avg_size` weighting | 2-3 hours |
| 13 | Per-underlying correlation matrix | H-6 | Compute rolling 30-day correlation (BTC-ETH, BTC-SOL, etc.); apply as penalty multiplier | 8-12 hours |
| 14 | Dynamic Kelly optimization | F-1 | Agent computes optimal Kelly from rolling 30-day win/loss distribution; updates config if divergence > 15% | 8-12 hours |
| 15 | Signal type performance ranking | F-4,F-5 | Track per-signal-type hit rate; dynamically adjust `signal_weights` in category_strategies.yaml | 6-8 hours |

### Tier 3: Strategic Enhancements (Post-Profitability)

| # | Issue | Fix | Effort |
|---|-------|-----|--------|
| 16 | Volatility regime detection (GARCH) | Replace sqrt(t) scaling with GARCH(1,1) conditional volatility | 16-20 hours |
| 17 | Limit order strategy for high-edge signals | Post 1c inside spread for signals with >3x min_edge and >30min to expiry | 8-12 hours |
| 18 | Cross-engine hedging | Allow opposite-direction positions across engines when explicitly flagged | 4-6 hours |
| 19 | Real-time performance dashboard queries | Materialized views for per-asset, per-timeframe, per-signal-type breakdowns | 6-8 hours |
| 20 | Backtest-to-production pipeline | Auto-apply backtesting insights to live config with human approval gate | 12-16 hours |

---

## Appendix: Detailed Bug Registry

| ID | Severity | Pillar | File:Line | Description | Status |
|----|----------|--------|-----------|-------------|--------|
| BUG-001 | CRITICAL | Execution | order_executor.py:548 | NO position payout uses YES price → undersizing 2-5x | Open |
| BUG-002 | HIGH | Execution | position_lifecycle.py:1009-1020 | P&L computation ignores fees entirely | Open |
| BUG-003 | HIGH | Signal | base_pipeline.py:393 | Fee multiplier 2.0x is semantically wrong (should be 1.0x for settlement exits) | Open |
| BUG-004 | HIGH | Hedging | position_lifecycle.py:691 + order_executor.py | Correlation block flags written but never read by executor | Open |
| BUG-005 | HIGH | Hedging | risk_dashboard.py + order_executor.py | CAUTION drawdown sizing reduction documented but not implemented | Open |
| BUG-006 | MEDIUM | Execution | position_lifecycle.py:463 | Momentum stall exit documented in config and docstring but not coded | Open |
| BUG-007 | MEDIUM | Signal | crypto_pipeline.py:1461 vs investment_policy_config.yaml:426 | Tiered FLB rejection floors in config are dead code; hard 10c floor in pipeline | Open |
| BUG-008 | MEDIUM | Execution | order_executor.py:269-284 | Partial fills not handled; `filled_count` never checked | Open |
| BUG-009 | MEDIUM | Execution | position_lifecycle.py:777 | Orphan positions hardcoded to SGE engine | Open |
| BUG-010 | MEDIUM | Execution | position_lifecycle.py:429-437 | EV Monitor recomputation ignores entry price | Open |
| BUG-011 | LOW | Execution | order_executor.py:654-666 | Contract cap tier lookup has redundant fallback | Open |
| BUG-012 | LOW | Signal | crypto_pipeline.py:1393 | Sigma floor 0.003 is degenerate for 15-min markets | Open |
| BUG-013 | LOW | Execution | order_executor.py:619-631 | No orderbook freshness validation before taker pricing | Open |

---

## Conclusion

Sibyl's -47.9% drawdown is not the result of a single catastrophic failure but the **compound effect of systematic biases** across all four pillars:

1. **Signal generation overestimates edge** by 20-50% (normal CDF on fat tails, stacked confidence adjustments, no crypto calibration offset)
2. **Execution corrupts sizing and P&L** (NO Kelly bug, zero fee accounting, missing exit strategy)
3. **Risk management operates at the wrong granularity** (category-level, not underlying-level; count-based, not dollar-weighted)
4. **No mechanism exists to detect or correct these errors automatically** (zero feedback loops despite excellent data infrastructure)

The system is well-engineered in structure but mis-calibrated in substance. The five Tier 0 fixes (fee accounting, NO Kelly, correlation enforcement, crypto offset, FLB activation) can be implemented in a single sprint and would address the most acute capital leaks. The auto-calibration agent (Tier 1, Item 9) is the highest-leverage strategic investment — it would convert Sibyl's excellent outcome data into the continuous model improvement that's currently performed manually every 2-3 sprints.

The path to profitability requires fixing the math before adding features.

---

*Report generated 2026-04-01. Based on source code review of Sprint 28 codebase.*
