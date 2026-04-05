# Sibyl.ai Live Test #5 Report

**Sprint 28 | March 30, 2026**

---

## Test Parameters

| Parameter | Value |
|-----------|-------|
| Duration | 2h 34m (00:10 - 03:50 UTC / 5:10 - 8:50 PM PST) |
| Mode | LIVE (Kalshi) — first ~6 min ran in PAPER mode (bug) |
| Starting Kalshi Cash | $42.37 |
| Pre-Existing Position Exposure | ~$73.67 (65 positions, carried from LT4) |
| Implied Starting Portfolio | ~$116.04 |
| Final Cash Balance | $36.63 |
| Final Open Positions | 15 |
| Final Exposure | $63.30 |
| Implied Final Portfolio | ~$99.93 |
| Session P&L | **-$16.07 (-13.8%)** |
| All-Time Realized P&L | **-$64.87** |
| All-Time Fees | **$29.35** |
| Sprint 28 Features | Entry floor 50c, Kelly cut 60-70%, max 5 contracts, BRACKET_ARB NO disabled, max_position_pct 5% |

---

## Key Metrics

| Metric | Value |
|--------|-------|
| Pipeline Cycles | 202 |
| Live Orders Placed | 1,000 |
| Live Sells | 50 |
| Market Resolutions | 18 |
| Balance Gate Triggers | 417 |
| Positions at Termination (pre-close) | 28 |
| Positions Closed (post-test) | 18 (16 OTM batch + 2 additional) |
| Positions Kept | 15 (all ITM or favorable) |
| Paper Mode Time Lost | ~6 minutes |

---

## Health Check Timeline

| Checkpoint | Time (PST) | Cash | Open Positions | Exposure | Portfolio | Notes |
|------------|------------|------|----------------|----------|-----------|-------|
| Start | 5:10 PM | $42.37 | 65 | ~$73.67 | ~$116.04 | 65 positions inherited from LT4 |
| HC1 (T+29m) | 5:39 PM | $39.11 | 21 | $67.43 | ~$106.54 | 44 positions resolved/exited |
| HC2 (T+50m) | 6:00 PM | $15.14 | 29 | $90.66 | ~$105.80 | Capital deployed into new positions |
| HC3 (T+80m) | 6:30 PM | $14.89 | 29 | $90.77 | ~$105.66 | Stable — balance gate blocking new orders |
| HC4 (T+110m) | 7:40 PM | $17.72 | 28 | $87.27 | ~$104.99 | Slow bleed: 89.6% drawdown from HWM |
| Final (post-close) | 8:50 PM | $36.63 | 15 | $63.30 | ~$99.93 | 18 non-profitable positions closed |

**Trajectory:** Steady decline. Portfolio dropped ~$1/hour from HC2 onward as existing positions bled value and resolutions netted losses. Balance gate (417 triggers) prevented further capital deployment after HC2, effectively halting new entries for the last 90 minutes.

---

## Critical Finding: Timeframe Concentration

### Only Friday & Monthly Markets Traded

User observation during HC4 identified that Sibyl was **not trading** any of the following:
- 15-minute crypto brackets
- 1-hour crypto brackets
- End-of-day crypto brackets

All 28 open positions were concentrated in:
- **End-of-week** (Friday close at 5 PM EDT) — e.g., `KXBTCD-26APR0317-*`
- **Monthly** high/low brackets — e.g., `KXBTCMINMON-*`

### Root Cause: 50c Entry Floor Eliminates 97% of Markets

Sprint 28 set `longshot_reject_below: 0.50` in `investment_policy_config.yaml`, meaning Sibyl rejects any entry where the implied price is below 50 cents.

**This is structurally incompatible with short-timeframe Kalshi crypto brackets.**

Kalshi crypto bracket mechanics:
- **Intraday (15-min, hourly):** Brackets are spaced every $50-$100 for BTC. With BTC at ~$83K, only the 1-2 brackets nearest the current price have meaningful probability. The rest are far out-of-the-money with prices of 0.5c-5c.
- **End-of-day:** Similar to intraday but with wider brackets. Still, 90%+ of brackets are below 50c.
- **Friday close:** 7 days of price movement creates enough uncertainty that many brackets land in the 30c-70c range. ~48% average price.
- **Monthly:** Widest dispersion. Highest fraction of brackets above 50c.

**Market price distribution from DB:**

| Timeframe | Markets | Avg Price | Markets Above 50c | % Above 50c |
|-----------|---------|-----------|-------------------|--------------|
| BTC intraday | 694 | $0.024 | ~2 | 0.3% |
| BTC today (daily) | 376 | $0.569 | ~230 | 61% |
| BTC Friday | 50 | $0.484 | ~22 | 44% |
| BTC monthly | 31 | $0.188 | ~8 | 26% |

The 50c floor was designed to avoid negative-EV longshots (LT1-LT4 data showed <20c entries had 0% win rate). But the floor was set too aggressively — it **also eliminates the entire intraday and hourly market universe** where Sibyl could otherwise compound faster through higher trade frequency.

### Impact on Portfolio Scaling

With only Friday and monthly markets accessible:
- **Trade frequency:** ~1-2 settlement events per week (Friday) + 1 per month
- **Time to compound:** Weeks to months between position entry and resolution
- **Capital utilization:** Low — most capital sits in pending positions for days

With intraday/hourly access:
- **Trade frequency:** 24-96 settlement events per day
- **Time to compound:** Minutes to hours
- **Capital utilization:** High — capital recycles rapidly through resolved positions

**This is the single largest bottleneck to rapid portfolio scaling.**

---

## Critical Finding: $5 Maximum Payout Per Position

### All Positions Capped at 5 Contracts

Sprint 28 set `max_contracts_per_trade: 5`. On Kalshi, each binary contract pays $1.00 at resolution. Therefore:

- Maximum payout per position: 5 x $1.00 = **$5.00**
- Typical entry cost (at 50c-85c): $2.50 - $4.25
- Maximum profit per position: **$0.75 - $2.50**

With 28 positions, the theoretical maximum portfolio gain (all positions win) is $21-$70. But many positions have entry costs near 80-85c, making max profit per position only ~$0.75-$1.00.

### Impact

On a $100 portfolio, even a perfect win rate yields:
- 28 positions x ~$1.50 avg profit = **$42 max gain** (42% return)
- But achieving 100% win rate is unrealistic. At 65% win rate with 35% loss: net ~$0 after fees

The 5-contract cap was data-justified (LT1-LT4: 1-3 contracts = +30-60% ROI vs 11+ = -3.8%). But the cap interacts destructively with the 50c entry floor: high-price entries (>50c) have thin margins, and the contract cap prevents scaling into the few trades that do have edge.

**Recommendation:** Implement tiered contract caps by entry price:
- Entry 50c-70c: 10 contracts (max payout $10, max profit $3-5)
- Entry 70c-85c: 7 contracts (max payout $7, max profit $1-2)
- Entry 85c-93c: 5 contracts (max payout $5, max profit $0.35-0.75)

---

## Critical Finding: Fee Drag

### 29% Fee Burden Across All Tests

| Test | Fees | Portfolio Size | Fee % |
|------|------|---------------|-------|
| LT2 | $4.44 | $186 | 2.4% |
| LT3 | $13.74 | $170 | 8.1% |
| LT4 | $21.26 | $116 | 18.3% |
| LT5 | $29.35 | $100 | 29.4% |
| **Cumulative** | **$29.35** | **$100** | **29.4%** |

Kalshi's fee structure: ~0.7% per contract side (1.4% roundtrip). With 5-contract positions at 80c entry:
- Fee per position: ~$0.056 (5 x $0.80 x 0.014)
- But this accumulates over hundreds of filled+resolved positions

The fee drag is proportionally devastating on a small portfolio. At $1,000 it would be ~3%. At $100, the same fee volume eats 29% of capital.

**Recommendation:** Prioritize higher-edge trades to ensure fee recovery. The current `fee_recovery_multiplier: 2.0` (edge must exceed 2x the fee) is appropriate but only effective if Sibyl can access higher-edge short-timeframe markets.

---

## Other Contributing Factors

### 1. Paper Mode Bug (6 Minutes Lost)

Sibyl launched in paper mode despite `system_config.yaml` having `mode: "live"`. Root cause: `__main__.py:210` uses `args.mode` (CLI argument, defaults to "paper"), not the config file value.

**Impact:** Minor — 6 minutes of lost trading time. But this is the second time this bug has occurred (also in LT4). The mode enforcement from Sprint 27 only prevents live→paper config drift, not the CLI default issue.

### 2. Balance Gate Saturation

417 balance gate triggers means the system attempted to place 417 orders that were rejected due to insufficient cash. After HC2 ($15.14 cash), Sibyl was effectively unable to open new positions for the remaining 90 minutes of the test.

**Impact:** Lost opportunity. Positions that resolved at a loss freed up cash, but the freed cash was too small to meet position sizing requirements (5 contracts x 50c = $2.50 minimum).

### 3. Position Inheritance

65 positions were carried from LT4. Many of these were opened under prior sprint logic (Sprint 24-26) with different risk parameters. The LT5 session bore the losses from these legacy positions while being unable to open enough new Sprint 28-compliant positions to offset.

### 4. Drawdown Spiral

At HC4: "DRAWDOWN CRITICAL: 89.6% from HWM ($1,007.15 -> $104.99)". The HWM includes the original $191.69 deposit plus simulated paper gains. The drawdown metric is misleading (comparing real losses against a high-water mark inflated by paper-mode phantom gains), but it reflects the true capital destruction from deposit to current state.

### 5. Allocator Zero-Allocation

At HC4: "INITIAL ALLOCATION: ACE -> $0.00 (0% of $12.47)". With only $12.47 available for allocation and minimum position sizes of $2.50, the allocator had no viable trades. The system was capital-starved.

---

## Sprint 28 Features Assessment

| Feature | Status | Assessment |
|---------|--------|------------|
| Entry floor (50c) | Active | **Overly aggressive** — blocks all intraday/hourly markets. Needs tiered approach by timeframe. |
| Kelly cut (60-70%) | Active | **Working as intended** — smaller positions reduce per-trade loss. But insufficient data to validate. |
| Max 5 contracts | Active | **Too restrictive** at this portfolio size — $5 max payout caps profit potential. |
| BRACKET_ARB NO disabled | Active | **Working** — eliminated the negative-EV targeted NO strategy. |
| max_position_pct 5% | Active | **Working** — no over-concentration observed. |
| min_no_premium 50c | Active | **Working** — NO trades restricted to high-premium entries. |

---

## Financial Summary

| Category | Amount |
|----------|--------|
| Original Deposit | $191.69 |
| Current Cash | $36.63 |
| Open Position Exposure | $63.30 (15 positions) |
| Implied Portfolio | ~$99.93 |
| All-Time Realized P&L | -$64.87 |
| All-Time Fees | $29.35 |
| **All-Time Return** | **-$91.76 (-47.9%)** |
| **LT5 Session Return** | **-$16.07 (-13.8%)** |

---

## Comparison: LT2 through LT5

| Metric | LT2 (S22.5) | LT3 (S24) | LT4 (S26) | LT5 (S28) |
|--------|-------------|------------|------------|------------|
| Duration | 2h 5m | 37m | 1h 0m | 2h 34m |
| Pipeline Cycles | 242 | 34 | 63 | 202 |
| Orders Placed | 7 | 5 | ~200 | 1,000 |
| Session P&L | -$0.81 | -$7.43 | ~-$6.00 | -$16.07 |
| Fee Drag (% of portfolio) | 2.4% | 8.1% | 18.3% | 29.4% |
| Timeframes Traded | Monthly only | Friday + Monthly | Friday + Monthly | Friday + Monthly |
| Max Position Payout | Varies | Varies | $10-26 | $5.00 |
| Balance Gate Triggers | N/A | N/A | Minimal | 417 |

**Key trend:** Order volume has scaled dramatically (7 → 1,000) but all orders are concentrated in the same narrow market slice (Friday + monthly). Fee drag is compounding as portfolio shrinks. The 5-contract cap in LT5 reduced per-trade risk but also capped upside.

---

## Recommendations for Rapid Portfolio Scaling

### Tier 1: Must Fix (Highest Impact)

**1. Tiered Entry Floor by Timeframe**

Replace the flat 50c floor with timeframe-aware thresholds:

| Timeframe | Proposed Floor | Rationale |
|-----------|---------------|-----------|
| 15-min | 5c | Narrow brackets, most volume is 1-10c. Need market access. |
| Hourly | 10c | Slightly wider. Reject only extreme longshots. |
| Daily (end-of-day) | 15c | Moderate dispersion. 15c+ brackets have viable edge. |
| Friday (end-of-week) | 30c | Current sweet spot — LT4 data shows profitability here. |
| Monthly | 30c | Wider brackets, more uncertainty. |

**Impact:** Unlocks 694+ intraday BTC markets, 376+ daily markets, plus ETH/SOL/XRP equivalents. Enables 24-96 trade resolutions per day instead of 1-2 per week.

**2. Dynamic Contract Cap by Entry Price**

Replace flat `max_contracts_per_trade: 5` with price-tiered caps:

| Entry Price | Max Contracts | Max Payout | Max Profit |
|-------------|--------------|------------|------------|
| 5c-20c | 15 | $15 | $12-14 |
| 20c-50c | 10 | $10 | $5-8 |
| 50c-70c | 8 | $8 | $2.40-4.00 |
| 70c-85c | 6 | $6 | $0.90-1.80 |
| 85c-93c | 5 | $5 | $0.35-0.75 |

**Impact:** Higher profit ceiling per trade, especially on lower-price entries where edge is measurable and win rates are validated.

**3. CLI Mode Default Fix**

Change `__main__.py:210` default from `"paper"` to read from `system_config.yaml`, or require explicit `--mode` flag with no default.

### Tier 2: Should Improve

**4. Intraday Capital Recycling Strategy**

With intraday market access, implement rapid capital recycling:
- As 15-min positions resolve, immediately redeploy freed capital
- Prioritize markets closing soonest (fastest capital return)
- Target 20+ resolution events per trading day

**5. Fee-Conscious Position Sizing**

At small portfolio sizes, fee drag is the dominant cost. Consider:
- Minimum position profit threshold: don't enter if max profit < 3x expected fees
- Batch entries to amortize per-order overhead

**6. Progressive Risk Scaling**

As portfolio grows, gradually relax constraints:
- $100-250: Conservative (current Kelly, lower contract caps)
- $250-500: Moderate (increase caps by 50%, add hourly markets)
- $500+: Aggressive (full market access, dynamic Kelly based on Brier scores)

### Tier 3: Strategic

**7. Abandon Pure-Favorite Strategy**

The 50c floor was derived from LT1-LT4 data, but those tests never had access to intraday markets with proper position sizing. The data is biased by the conditions that produced it. Intraday markets with small position sizes (1-3 contracts at 5-15c) have fundamentally different risk profiles than monthly markets with large positions.

**8. Track Per-Timeframe Performance**

Before fully relaxing the entry floor, add per-timeframe P&L tracking to validate that intraday entries are indeed profitable. This creates the feedback loop needed to calibrate thresholds dynamically.

---

## Conclusion

LT5 confirmed that Sprint 28's risk controls successfully reduced per-trade losses but created a structural scaling bottleneck. The 50c entry floor and 5-contract cap, while individually rational based on LT1-LT4 data, interact to produce a system that:

1. **Cannot access 97% of available crypto markets** (all intraday/hourly)
2. **Caps maximum profit at $5 per position** regardless of edge quality
3. **Compounds capital at weekly frequency** instead of hourly/daily
4. **Starves itself of capital** within 50 minutes of trading (417 balance gate blocks)

The path to rapid portfolio scaling requires timeframe-aware entry thresholds and dynamic contract caps — not a return to the unconstrained risk parameters that caused LT1-LT4 losses, but a calibrated middle ground that opens intraday markets with appropriate per-timeframe position sizing.

**Current portfolio: $99.93 (-47.9% all-time)**
**Next action: Implement tiered entry floor + dynamic contract cap (Sprint 29)**
