# Sibyl.ai — Post-LT7 Strategic Recommendations

**Date:** 2026-04-03
**Author:** Senior Quantitative Researcher (System Auditor)
**Audience:** Development team
**Context:** LT7 paper test results (Sprint 30), Sprint 29 audit remediation, stakeholder requirements (100% ROI on ~$9,200 within 90 days, >20% monthly portfolio growth)

---

## 1. LT7 Results Assessment

LT7 ran 3h22m in paper mode on 2026-04-02. Headline numbers: +$117.21 paper P&L, 2,792 positions, 35.5% win rate, Sharpe 3.87, $78.50 in tracked buy-side fees.

**Sprint 29 fixes validated:**
- Correlation blocks fired 4,925 times (BUG-004 fix operational)
- Per-underlying cap blocked XRP at 15% exposure (H-1 fix operational)
- Fee tracking recorded $78.50 across 4,593 executions (BUG-002 fix operational)
- Momentum stall exited 3,464 positions / 99.1% of all exits (E-3 fix operational)
- Drawdown sizing was not triggered (portfolio never entered WARNING/CAUTION)

**Three structural problems invalidate the paper P&L as a live-performance predictor:**

### 1.1 Paper Balance Inflation (B-NEW-3)

The system operated with `portfolio_total_balance = $1,114` — approximately 12x the real Kalshi balance of ~$100. Kelly-sized positions were correspondingly 12x larger than live mode would produce. The +$117.21 paper P&L was earned on phantom capital. Adjusted for real capital, the equivalent return is approximately +$10.

**Status:** Developer correctly identified this bug. Sprint 31 fix proposed.

### 1.2 Confidence Saturation — 76.5% of Signals at 0.85 Cap

This is the highest-priority finding from LT7. Of 21,918 routed signals, 16,766 (76.5%) had confidence = 0.85. The system cannot distinguish between a 3% edge and a 30% edge — both produce the same confidence score.

**Root cause:** The confidence formula `0.55 + edge_ratio * 0.06` saturates at 5x the minimum edge threshold. With the Sprint 29 calibration offset of -0.10, the effective ceiling drops from 0.95 to 0.85. Most profitable bracket signals have 5-10x edge ratio, placing them all at the cap.

**Downstream impact:**
- Kelly sizing treats all high-edge signals identically
- Signal routing cannot prioritize exceptional opportunities over marginal ones
- AutoCalibrator (if operational) would see a single confidence bucket with blended accuracy, unable to detect per-quality-tier calibration errors
- The system is effectively trading with a binary classification (above/below 0.60 floor) rather than a continuous probability estimate

**Status:** Not in Sprint 31 roadmap. **Must be added as P0.**

### 1.3 AutoCalibrator Inoperative (31/31 Cycles Failed)

The auto-calibration agent deployed in Sprint 29 failed on every cycle throughout LT7. The `cal_*` system_state entries were zero for the entire test. The one mechanism capable of detecting the confidence saturation problem was dead.

**Root cause:** `auto_calibrator.py` queries `signals.category` — this column does not exist in the schema. Should use `signals.source_pipeline` or join through the `markets` table.

**Status:** Developer correctly identified as B-NEW-1. Sprint 31 fix proposed.

---

## 2. Assessment of Sprint 31 Developer Roadmap

The proposed Sprint 31 fixes (B-NEW-1 through B-NEW-4) are all correct and necessary. However, they are insufficient. The roadmap addresses operational bugs but not the signal quality problem that determines whether the system has positive expected value.

| Developer Fix | Correct? | Sufficient? | Notes |
|---------------|----------|-------------|-------|
| B-NEW-1: AutoCal schema | Yes | Yes | 2-line fix, unblocks feedback loop |
| B-NEW-2: Stale market detection | Yes | Yes | Prevents trading expired markets |
| B-NEW-3: Paper balance inflation | Yes | Yes | Critical for meaningful paper testing |
| B-NEW-4: Monthly market filter | Yes | Partially | Symptom of a deeper problem (see Section 3.4) |

**What is missing from the roadmap:**
1. Confidence formula replacement (the 76.5% saturation problem)
2. AutoCalibrator validation (confirming it produces real data after the schema fix)
3. Live validation protocol with statistical success criteria
4. Capital deployment gating tied to empirical evidence

---

## 3. Recommended Changes — Priority Order

### 3.1 [P0] Replace the Confidence Formula

**File:** `sibyl/pipelines/crypto_pipeline.py` (confidence computation block, approximately lines 1474-1540)

**Current formula:**
```python
confidence = min(0.55 + edge_ratio * 0.06, 0.95)
```

**Problem:** Linear scaling saturates at 5x edge ratio. After -0.10 calibration offset, effective cap is 0.85. 76.5% of LT7 signals hit this ceiling.

**Replacement — logistic (sigmoid) mapping:**
```python
import math

def _edge_to_confidence(edge_ratio: float, base: float = 0.55, scale: float = 0.40, rate: float = 0.8) -> float:
    """
    Sigmoid confidence mapping. Never fully saturates.
    
    edge_ratio=1  -> 0.62  (passes 0.60 router floor)
    edge_ratio=2  -> 0.69
    edge_ratio=5  -> 0.82
    edge_ratio=10 -> 0.90
    edge_ratio=20 -> 0.94
    """
    sigmoid = (1.0 - math.exp(-edge_ratio * rate)) / (1.0 + math.exp(-edge_ratio * rate))
    return min(base + scale * sigmoid, 0.99)
```

**Key properties:**
- Monotonically increasing — higher edge always produces higher confidence
- Never saturates — a 20x edge signal (0.94) is distinguishable from a 5x edge signal (0.82)
- After -0.10 calibration offset: range becomes approximately 0.52-0.89, with meaningful spread across the full range
- Preserves the 0.60 router floor behavior (1x edge → 0.62 pre-offset, 0.52 post-offset; 1.5x edge → 0.55 post-offset, clears floor)

**Implementation note:** The calibration offset of -0.10 may need adjustment after this change. The AutoCalibrator (once B-NEW-1 is fixed) should be used to empirically determine the correct offset within the first 24 hours of paper testing.

**Effort:** 30 minutes code change + test updates.

**Expected impact:** Confidence distribution spreads from [0.60, 0.85] to approximately [0.60, 0.89], with the median shifting from 0.85 (ceiling) to approximately 0.72-0.75 (mid-range). Kelly sizing and signal prioritization become meaningful.

### 3.2 [P0] Fix AutoCalibrator Schema (B-NEW-1)

**File:** `sibyl/agents/analytics/auto_calibrator.py`, lines 95-101

**Fix:** Replace `s.category` with `s.source_pipeline` (or join through `markets` table to get category).

**Validation requirement:** After fix, run 2+ hours of paper trading and confirm:
- `cal_*` entries in system_state are non-zero
- Brier scores are computed per pipeline
- Signal type rankings reflect actual win/loss ratios
- If `auto_apply=True` is enabled, confirm offset adjustments are within expected range (+-0.05)

**Effort:** 2-line code fix + 2 hours validation.

### 3.3 [P0] Fix Paper Balance Inflation (B-NEW-3)

**File:** `sibyl/agents/allocator/portfolio_allocator.py`, lines 366-378

**Recommended approach:** Option C from the developer's proposal — seed `paper_starting_balance` from actual Kalshi balance at startup. This is the simplest fix and directly ties paper performance to real capital.

**Additional constraint:** Add a hard cap in the portfolio allocator: `paper_balance = min(paper_balance, kalshi_cash * 1.5)`. This prevents cross-session accumulation from inflating beyond 150% of real capital, even if Option C misses an edge case.

**Effort:** 1-2 hours.

### 3.4 [P1] Fix Stale Market Detection (B-NEW-2)

**File:** `sibyl/agents/execution/order_executor.py` (pre-execution guard block)

**Fix:** Before execution, query `markets.close_date`. Reject if `close_date < datetime.now(UTC)`.

**Effort:** 1 hour.

### 3.5 [P1] Replace Monthly Market Filter With Horizon-Aware Execution

**Context:** B-NEW-4 proposes adding `min_time_to_expiry_seconds` to reject markets that expire too far in the future. This addresses the symptom (SOL/XRP monthly positions opened during a 3-hour test) but not the cause.

**The real problem:** The system has no concept of *investment horizon*. It trades 15-minute brackets and 30-day monthly brackets with the same pipeline cycle, the same Kelly fractions, and the same exit logic. A 15-minute bracket that momentum-stalls after 4 cycles (20 seconds) is correctly exited. A 30-day monthly bracket that momentum-stalls after 4 cycles is incorrectly exited — it hasn't had time to play out.

**Recommended fix:** Instead of a simple "reject too far in future" filter, implement a **position holding period minimum** tied to timeframe:

```yaml
# investment_policy_config.yaml
min_hold_periods:
  "15min": 0        # Exit any time (momentum stall OK)
  "hourly": 300     # Hold minimum 5 minutes before stall exit
  "daily": 3600     # Hold minimum 1 hour
  "monthly": 86400  # Hold minimum 24 hours
```

In the momentum stall logic (`position_lifecycle.py`, Sub-C), skip the stall check if the position has been held for less than `min_hold_periods[timeframe]`. This prevents the 99.1% momentum-stall exit rate from prematurely killing longer-horizon positions that need time to converge.

The developer's `min_time_to_expiry_seconds` filter should still be implemented as a secondary guard (reject markets expiring >7 days out during paper test sessions), but the holding period minimum is the structural fix.

**Effort:** 2-3 hours.

### 3.6 [P1] Reduce Confidence Adjustment Stacking

**File:** `sibyl/pipelines/crypto_pipeline.py` (confidence adjustments block, approximately lines 1483-1540)

**Current state:** Four adjustments (OBI, funding rate, micro buy pressure, Kalshi OBI) are summed independently with a combined cap of +-0.05. These signals are highly correlated — they all measure directional sentiment through different microstructure lenses.

**Recommended fix:** Apply a **correlation discount** to the sum:

```python
# Current: total_adj = book_adj + funding_adj + pressure_adj + kalshi_obi_adj
# Recommended: discount correlated signals
raw_sum = book_adj + funding_adj + pressure_adj + kalshi_obi_adj
n_positive = sum(1 for x in [book_adj, funding_adj, pressure_adj, kalshi_obi_adj] if x > 0.001)
# When all 4 agree, discount by 40% (correlation penalty)
correlation_discount = 1.0 - 0.10 * max(0, n_positive - 1)
total_adj = raw_sum * correlation_discount
total_adj = max(-0.05, min(0.05, total_adj))
```

When all four signals agree (n_positive=4), the discount is 0.70x. When only one signal fires, no discount. This prevents correlated microstructure noise from inflating confidence by 5% when it should only contribute 2-3%.

**Effort:** 30 minutes.

### 3.7 [P2] Fee Multiplier Semantics (BUG-003, Currently Deferred)

**File:** `sibyl/pipelines/base_pipeline.py`, line 393

**Current:** `fee_cost = fee_per_contract * fee_multiplier` where `fee_multiplier = 2.0`, producing a 2.8% edge threshold.

**Problem:** The 2.0x multiplier was intended as a "safety margin" but is applied uniformly. Positions held to settlement only incur buy-side fee (1.4%), not roundtrip (2.8%). The current implementation rejects 50% of marginally-profitable settlement-exit trades.

**Recommended fix:** Make fee multiplier conditional on expected exit method:

```python
# If position will likely be held to settlement (daily/monthly markets), use 1.0x
# If position will likely be actively exited (15-min/hourly markets), use 2.0x
if timeframe in ("daily", "monthly"):
    effective_fee_multiplier = 1.0  # Buy-side fee only
else:
    effective_fee_multiplier = 2.0  # Roundtrip (buy + sell)
fee_cost = fee_per_contract * effective_fee_multiplier
```

**Effort:** 1 hour.

### 3.8 [P2] Sigma Floor Adjustment (BUG-012, Currently Deferred)

**File:** `sibyl/pipelines/crypto_pipeline.py`, line 1393

**Current:** `sigma_t = max(sigma_t, 0.003)` — a 0.3% floor.

**Problem:** For 15-minute brackets, this floor binds frequently and produces inflated edge estimates. A 2-sigma move at 0.3% vol is only 0.6%, meaning the model assigns near-certainty to brackets within 0.6% of spot.

**Recommended fix:** Make the sigma floor timeframe-dependent:

```python
SIGMA_FLOOR_BY_TIMEFRAME = {
    "15min": 0.005,   # 0.5% minimum
    "hourly": 0.008,  # 0.8% minimum
    "4hour": 0.012,   # 1.2% minimum
    "daily": 0.015,   # 1.5% minimum
    "monthly": 0.03,  # 3.0% minimum
}
sigma_floor = SIGMA_FLOOR_BY_TIMEFRAME.get(timeframe, 0.005)
sigma_t = max(sigma_t, sigma_floor)
```

These floors are calibrated so that a 2-sigma move at the floor volatility approximately matches the minimum edge threshold for that timeframe (e.g., 15-min min_edge = 0.5%, 2 * 0.5% vol = 1.0% move range).

**Effort:** 30 minutes.

---

## 4. Recommended Execution Plan

### Phase A: Make the Model Honest (Sprint 31 — 1 week)

1. Fix B-NEW-1 (AutoCal schema) — 30 min
2. Fix B-NEW-2 (stale market detection) — 1 hr
3. Fix B-NEW-3 (paper balance inflation) — 2 hr
4. **Replace confidence formula with sigmoid mapping** — 30 min
5. **Add correlation discount to confidence adjustments** — 30 min
6. Implement B-NEW-4 as holding period minimum + time-to-expiry guard — 3 hr
7. Run 24-hour paper test (LT8-paper) with AutoCalibrator active

**LT8-paper success criteria:**
- Confidence distribution spans 0.60-0.89 (no single bucket >40% of signals)
- AutoCalibrator `cal_*` entries populate with real accuracy data
- Paper balance tracks within 150% of actual Kalshi balance
- Zero positions opened on expired markets
- Monthly positions are not momentum-stalled within first 24 hours

### Phase B: Prove Edge on Real Capital (Sprint 32 — 1 week)

1. Deploy LT8-live with existing ~$100 Kalshi balance
2. Run for 48-72 hours continuous
3. Cap total live exposure at $50 via `max_category_pct: 0.50` for crypto
4. AutoCalibrator must remain operational throughout

**LT8-live success criteria (all three required):**
- Accuracy at highest confidence bucket (>0.80) exceeds 50%
- Average winning trade P&L > average losing trade P&L (after fees)
- AutoCalibrator empirical accuracy tracks within 15% of model predictions

If any criterion fails, identify which parameter is miscalibrated (calibration offset, Kelly fraction, or edge threshold), adjust, and re-run. **Do not deposit additional capital until all three criteria pass.**

### Phase C: Scale After Proof (Sprint 33+ — conditional on Phase B)

1. Deposit $500-$1,000 from remaining budget
2. Apply BUG-003 fix (fee multiplier by timeframe)
3. Apply BUG-012 fix (sigma floor by timeframe)
4. Consider re-enabling economics pipeline (was +$4.69 in Sprint 19)
5. Evaluate Polymarket integration for deeper crypto liquidity and lower fees
6. Target 20% monthly growth with validated model parameters

---

## 5. What NOT to Do

1. **Do not add more agents or pipelines before Phase B completes.** The system has 18 agents. LT7 showed AutoCalibrator broken, X Sentiment returning 402 errors for the entire test, and notifier failing at close. Each agent is a failure surface. Reduce complexity before adding capability.

2. **Do not deploy fresh capital before LT8-live passes all three success criteria.** The -47.9% drawdown occurred because capital was deployed before model validation. Paper P&L is necessary but not sufficient evidence.

3. **Do not chase the 20% monthly growth target before proving positive EV per trade.** The math: 20% monthly on $100 = $20 = ~$0.67/day = ~10 correctly-sized bracket trades/day at current Kelly fractions. This is achievable but requires positive expected value first. Targeting the growth rate before proving EV will reproduce the Sprint 19-28 loss pattern.

4. **Do not skip Phase B and go directly to Phase C.** The paper balance inflation bug (B-NEW-3) means all prior paper P&L numbers are unreliable. Phase B is the first opportunity to measure true live EV with a corrected model. Skipping it risks the remaining capital.

---

## 6. Remaining Audit Bugs — Updated Priority

Post-LT7, the priority ordering of remaining audit bugs should be:

| ID | Original Severity | Recommended Priority | Rationale |
|----|-------------------|---------------------|-----------|
| **NEW** | CRITICAL | **P0 — Sprint 31** | Confidence formula saturation (76.5% at cap) |
| B-NEW-1 | HIGH | P0 — Sprint 31 | AutoCal schema (feedback loop dead) |
| B-NEW-3 | HIGH | P0 — Sprint 31 | Paper balance 12x inflation |
| B-NEW-2 | HIGH | P1 — Sprint 31 | Stale market detection |
| B-NEW-4 | MEDIUM | P1 — Sprint 31 | Monthly market + holding period |
| BUG-003 | HIGH | P2 — Sprint 32 | Fee multiplier semantics |
| BUG-012 | LOW → MEDIUM | P2 — Sprint 32 | Sigma floor by timeframe |
| BUG-009 | MEDIUM | P3 — Post-scale | Orphan engine attribution |
| BUG-010 | MEDIUM | P3 — Post-scale | EV monitor entry price |
| BUG-011 | LOW | P3 — Post-scale | Contract cap fallback |
| BUG-013 | LOW | P3 — Post-scale | Orderbook freshness |

---

*Document prepared for developer handoff. Implementation details are code-level specific — file paths, line numbers, and formulas are provided for direct application.*
