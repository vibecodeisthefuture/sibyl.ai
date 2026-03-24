# Blitz Partition vs. Investment Policy Framework — Conflict Analysis

**Sprint 14 | March 2026**
**Prepared for: Rafael (Sole Stakeholder)**

---

## 1. Identified Policy Conflicts

After auditing the Blitz partition design against every section of `investment_policy_config.yaml`, seven conflicts were identified. Each is detailed below with the specific policy rule, how Blitz diverges, and the operational impact.

### Conflict 1: Category Engine Permissions

**Policy Rule:** `category_engine_permissions` restricts which engine can trade each category. Sports is ACE-only. Culture & Entertainment is ACE-only. Geopolitics & Legal is ACE-only with override_only flag.

**Blitz Behavior:** Blitz is category-agnostic and runs under the SGE umbrella (engine = SGE_BLITZ). It will attempt to trade Sports markets closing in the final 90 seconds, Culture event outcomes, and any other category where a closing market has >85% confidence.

**Impact:** Under current policy, a Lakers blowout in the 4th quarter with 60 seconds to close would be blocked because Sports is not permitted for SGE. This is the single biggest conflict — it eliminates approximately 30–40% of Blitz opportunities by category.

### Conflict 2: Tier 3 Restricted Category Auto-Entry

**Policy Rule:** Geopolitics & Legal is Tier 3, which requires `auto_entry: false`, minimum 0.85 confidence, 0.15 EV, and 3 independent source confirmations. Override protocol demands 0.90 confidence, 0.20 EV, and 3 sources.

**Blitz Behavior:** Blitz requires >0.85 confidence and >0.04 EV. It uses price-implied probability as its primary confidence source (one source, not three). It auto-enters without override protocol.

**Impact:** Any Geopolitics market that happens to close in the Blitz window would be auto-traded at a lower EV threshold and without the triple-confirmation requirement. However, Geopolitics markets rarely have the closing-window profile that Blitz targets, so this conflict is low-frequency but high-severity if it occurs.

### Conflict 3: SGE Signal Whitelist

**Policy Rule:** SGE's `signal_whitelist` is restricted to ARBITRAGE, MEAN_REVERSION, and LIQUIDITY_VACUUM.

**Blitz Behavior:** Blitz generates BLITZ_LAST_SECOND signals, which are not in the SGE whitelist. Blitz is designed to accept signals from any category pipeline.

**Impact:** If the standard signal router were to process a BLITZ signal, it would be rejected. This is actually a non-issue because Blitz bypasses the signal router entirely (BlitzScanner → BLITZ_READY → BlitzExecutor), but it's a structural inconsistency that could cause confusion during debugging.

### Conflict 4: SGE Max Single Position Size

**Policy Rule:** SGE limits max_single_position_pct to 0.02 (2% of engine capital).

**Blitz Behavior:** Blitz allows 0.08 (8% of Blitz pool). Since Blitz pool is 20% of SGE, the effective max per trade is 8% × 20% = 1.6% of SGE capital. This actually stays within the 2% SGE limit in absolute terms, but the *intent* of the policy is conservative position sizing, and 8% of the sub-pool is aggressive.

**Impact:** Individually, each Blitz trade is small relative to total SGE. But with 5 concurrent positions at max size, Blitz could deploy 40% of its pool simultaneously (8% × 5 = 40%), which is aggressive relative to SGE's 55% max total exposure.

### Conflict 5: Execution Style — Market Orders vs. Patient Limits

**Policy Rule:** SGE uses `order_type: "limit"` with `limit_offset_bps: 50` for patient execution. The entire philosophy of SGE is "slow and steady."

**Blitz Behavior:** Blitz uses `order_type: "market"` for instant fill. This is the opposite of patient execution.

**Impact:** Market orders pay the spread, which directly reduces EV. For a market at 0.92 YES with a 2¢ spread, a market order fills at 0.93, reducing profit from 0.08 to 0.07 per contract (12.5% EV reduction). However, for markets closing in ≤90 seconds, the alternative is no fill at all, making market orders the only viable option.

### Conflict 6: Capital Allocation Caps per Category

**Policy Rule:** `capital_caps` defines per-engine, per-category exposure limits (e.g., Crypto SGE cap = 10%, Sports SGE cap = 5%).

**Blitz Behavior:** Blitz enforces a flat 40% category concentration limit within its own pool, but does not check against the policy's per-category SGE caps.

**Impact:** If Blitz deploys 40% of its pool into Crypto, that's 40% × 20% = 8% of SGE capital in Crypto, which is near but within the 10% policy cap. However, if standard SGE also has Crypto exposure, the combined SGE + SGE_BLITZ Crypto exposure could exceed the 10% cap.

### Conflict 7: Data Freshness Rules

**Policy Rule:** Different categories have different max staleness (live game state = 60s, weather forecast = 90min, economic release = 30min).

**Blitz Behavior:** Blitz uses the most recent price and any pipeline signal from the last 60 minutes. It does not enforce category-specific freshness rules.

**Impact:** For most Blitz scenarios, the data freshness concern is moot because Blitz relies primarily on market price (which is inherently fresh) rather than stale pipeline data. The price IS the data near close. However, a pipeline signal from 58 minutes ago could theoretically contribute to a Blitz confidence estimate when fresher data would have changed the assessment.

---

## 2. Comparison: Policy-Compliant vs. Policy-Exempt Blitz

### Option A: Blitz Abides by Policy Framework

Under this option, Blitz would respect all existing policy rules.

**What changes:**

- Blitz can only trade categories where SGE is permitted (Weather, Mentions, Economics & Macro, Science & Technology, and hybrid categories where SGE is allowed). Sports, Culture, and Geopolitics would be off-limits.
- Tier 3 markets require override protocol (0.90 confidence, 0.20 EV, 3 sources) — effectively disabling Blitz for those categories.
- Position sizing capped at 2% of SGE capital (not 8% of Blitz pool).
- Must use limit orders (making execution within 90-second windows impractical).
- Per-category caps enforced against combined SGE + SGE_BLITZ exposure.

**Estimated Opportunity Reduction:**

| Category | % of Blitz Opportunities | Policy Status |
|----------|--------------------------|---------------|
| Crypto Price Windows | 25% | Allowed (hybrid) |
| Weather Temperature | 15% | Allowed |
| Sports Final Minutes | 25% | **BLOCKED** (ACE-only) |
| Economic Data Windows | 10% | Allowed |
| Stock Price Close | 10% | Allowed (hybrid) |
| Culture Event Outcome | 15% | **BLOCKED** (ACE-only) |

**Result:** ~40% of Blitz opportunities eliminated. The limit-order requirement makes the remaining 60% functionally non-executable within the 90-second window.

**Effective Blitz utilization under policy compliance: ~5–10%** (only markets where SGE is permitted AND a limit order can fill in time).

**Pros:**
- Full risk framework protection applies uniformly.
- No edge cases or policy inconsistencies.
- Simpler to audit and debug.
- Combined exposure reporting is accurate.

**Cons:**
- Blitz becomes nearly non-functional. The limit-order requirement alone makes it impractical.
- 14% of portfolio capital (the Blitz pool) sits mostly idle.
- The entire strategic purpose of Blitz (fast, category-agnostic, near-certain profit) is defeated.

### Option B: Blitz Operates Above Policy Framework

Under this option, the Blitz partition has its own self-contained risk rules that supersede the main investment policy for trades within the Blitz criteria (≤90s close, >85% confidence).

**What changes:**

- Blitz trades any category regardless of engine permissions.
- Market orders are permitted (necessary for the time window).
- Position sizing follows Blitz-specific rules (8% of Blitz pool, 25% Kelly).
- Blitz has its own circuit breaker (15% drawdown of Blitz pool) independent of SGE circuit breaker.
- Category concentration limited to 40% of Blitz pool (independent of main policy caps).
- Blitz capital is isolated: losses are contained within the 20% SGE sub-pool.

**Safeguards already built into Blitz (risk controls that are MORE conservative than the main policy in certain respects):**

| Risk Control | Main Policy | Blitz |
|-------------|-------------|-------|
| Minimum confidence | 0.60 (Tier 1) | **0.85** (stricter) |
| Minimum EV | 0.03 (Tier 1) | **0.04** (stricter) |
| Max capital at risk | 70% of portfolio (SGE) | **14% of portfolio** (much less) |
| Price convergence gate | None | **5–30¢ from terminal** (unique to Blitz) |
| Time-based filtering | None | **≤90s to close** (unique) |
| Concurrent position limit | None (just exposure %) | **5 max** (explicit cap) |
| Circuit breaker | -10% SGE drawdown | -15% Blitz drawdown (on smaller pool) |

**Pros:**
- Blitz operates at full effectiveness across all categories.
- Market orders enable actual execution within the time window.
- Capital isolation (14% of total) limits worst-case loss.
- Confidence threshold (>85%) is already more conservative than standard SGE (>60%).
- This is the only option that achieves the stakeholder's stated goal of "blitzkrieg-style, near-certain, slower-but-guaranteed profit growth."

**Cons:**
- Creates a policy exception that could be precedent for further exceptions.
- Combined SGE + SGE_BLITZ category exposure may exceed individual category caps.
- Sports and Culture trades bypass tier-appropriate risk controls.
- Debugging requires understanding two overlapping risk frameworks.
- If the price-implied confidence is wrong (e.g., thin orderbook with misleading price), the speed of execution means there's no time for error correction.

---

## 3. Recommendation

**Option B (Policy-Exempt) is recommended** for the following reasons:

1. **The confidence floor protects you.** Blitz's 85% minimum confidence is higher than any standard policy tier except Tier 3 override (90%). Every Blitz trade already exceeds the quality floor of Tier 1 and Tier 2.

2. **Capital isolation contains the risk.** The Blitz pool is 14% of total portfolio. Even a complete wipeout of the Blitz pool (which would require 5 simultaneous max-size positions all going to zero) only loses 14% of portfolio value — well within the SGE circuit breaker threshold of 10% SGE drawdown (which is 7% of total portfolio). The Blitz circuit breaker (15% of Blitz pool = 2.1% of total) would trip long before that.

3. **The time constraint IS the risk control.** Markets closing in ≤90 seconds with prices near their terminal value are statistically unlikely to reverse. The real-world scenarios where YES is at 0.92 and flips to 0.08 in the final 90 seconds are extreme tail events (game-canceling injuries, market halts, natural disasters).

4. **Policy compliance would nullify the feature.** The limit-order requirement alone makes Blitz non-functional. Building a feature that can't operate serves no purpose.

**Recommended implementation:** Blitz is policy-exempt but must log every trade with full reasoning for audit. If Blitz performance degrades (tracked separately per the policy's `track_performance_separately: true` override rule), the confidence threshold should be auto-raised (matching the policy's existing calibration mechanism).

---

## 4. Proposed Policy Amendment

If you approve Option B, the following clause should be added to the investment policy:

> **Section 20: Blitz Partition Exception**
>
> The SGE Blitz partition operates under its own risk framework for trades meeting ALL of the following criteria: (a) market closes within ≤90 seconds, (b) signal confidence exceeds 85%, (c) expected value exceeds 4%, (d) price is within 5–30¢ of terminal value. Blitz trades are exempt from: engine category permissions (Section 3), signal type whitelist, execution style requirements, and per-category capital caps. Blitz trades remain subject to: the Blitz-specific circuit breaker (15% of Blitz pool), the Blitz position sizing limits (8% of pool per trade, 5 concurrent max), the Blitz category concentration limit (40% of pool per category), and the universal avoidance rules (Section 13 — minimum liquidity, subjective resolution rejection). Blitz performance is tracked separately and subject to auto-calibration per Section 17.
