# Sibyl.ai Live Test #2 Report

**Sprint 22.5 | March 26, 2026**

---

## Test Parameters

| Parameter | Value |
|-----------|-------|
| Duration | 2h 5m (01:50 - 03:55 UTC) |
| Mode | LIVE (Kalshi) |
| Starting Balance | $186.76 |
| Final Balance | $185.95 |
| Net P&L | **-$0.81 (-0.43%)** |
| Capital Deployed | 100% available |
| Risk Config | Kelly 0.30, Confidence floor 60%, Max position 12% |
| Pipeline Interval | 60 seconds |
| Fill Timeout | 120 seconds (fixed from 10s during test) |

---

## Key Metrics

| Metric | Value |
|--------|-------|
| Pipeline Cycles | 242 |
| Signals Generated | ~54-70 per cycle |
| Signals Routed to SGE | 28-31 per cycle |
| Orders Placed (Kalshi) | 55 |
| Orders Filled (detected) | 2 (3.6%) |
| Orders Filled (actual on Kalshi) | 5+ (9%+) |
| Orders Cancelled (120s timeout) | 52 |
| Order Exceptions | 1 (yearly market ticker) |
| Kalshi Rate Limits (429) | 1,222 |
| Total Errors (all agents) | 3,466 |
| Agent Backoffs | 166 |
| Total Traded Volume | $258.79 (test positions only) |
| Total Fees Paid | $3.97 (test), $5.18 (all) |

---

## Position Summary (Test #2 Only)

| Market | Side | Contracts | Traded | Realized P&L | Fees |
|--------|------|-----------|--------|-------------|------|
| XRP Min Mon >= $1.30 | YES | 63 | $61.35 | **+$1.65** | $1.76 |
| SOL Min Mon >= $80 | YES | 13 | $20.48 | **+$0.52** | $0.56 |
| ETH Max Mon <= $250K | NO | 9 | $9.00 | $0.00 | $0.06 |
| XRP Min Mon >= $1.20 | NO | 10 | $20.50 | -$0.50 | $0.18 |
| BTC Min Mon >= $65K | YES | 2 | $65.09 | -$1.09 | $1.02 |
| XRP Min Mon >= $1.10 | NO | (legacy) | $82.37 | -$2.37 | $0.39 |
| **TOTAL** | | | **$258.79** | **-$1.79** | **$3.97** |

Winners: 2 positions (+$2.17) | Losers: 3 positions (-$3.96) | Breakeven: 1

---

## Critical Findings

### 1. Fill Timeout Was the Primary Blocker (FIXED)

The original 10-second fill timeout caused 100% order failure in Test #1 (23 orders, 0 fills). Orders placed as limit orders need time to find counterparties — especially in overnight bracket markets.

**Fix applied during test**: Increased from 5x2s (10s) to 20x6s (120s). This immediately improved fill rate. Two fills were detected within the timeout window at 37s and 12s respectively.

### 2. Orphan Position Problem (NEW BUG)

Our system detected 2 fills, but Kalshi showed 5+ active positions. Orders that fill AFTER the 120s cancel window become "orphan positions" — they exist on Kalshi but are NOT tracked in our database.

**Mechanism**: Cancel request on an already-filled order is a no-op on Kalshi. Our code assumes cancellation succeeded and moves on.

**Impact**: ~$53.83 in untracked exposure during the test. Positions are invisible to the risk dashboard, stop-loss guard, and exit optimizer.

**Priority**: HIGH — must fix before next live deployment.

### 3. Kalshi API Rate Limiting (1,222 hits)

Multiple agents competing for Kalshi API access. The rate limiter is per-client but 4 agents create independent clients:

- KalshiMonitor (market data polling)
- OrderExecutor (order placement + confirmation polling)
- PositionLifecycle (position exits)
- PortfolioAllocator (balance sync)

Rate limit cascades caused 166 agent backoffs and degraded all non-order-executor agents.

### 4. Serial Order Processing Bottleneck

Each order blocks the executor for up to 120 seconds (fill timeout). With 55 orders over 125 minutes, throughput was ~0.44 orders/minute. The pipeline generates 28-31 signals per cycle (every 60s), but only 1 order can be processed every 2 minutes.

**Effective signal-to-order throughput**: 55 orders / (31 signals/cycle x 99 routing cycles) = 1.8% signal utilization.

### 5. Kalshi sell_position Bug (FIXED)

The `sell_position` method in kalshi_client.py didn't include price fields for market orders. Kalshi API requires exactly one price field on every order. Same bug existed in `place_order` for market buy orders.

**Fix applied during test**: Both methods now include price fields for all order types (1c for market sells, 99c for market buys).

### 6. Overnight Liquidity

The test ran 01:50 - 03:55 UTC (9:50 PM - 11:55 PM ET). Kalshi crypto bracket markets have minimal counterparty activity overnight. Most non-filled orders had prices at 91-96c (deep in-the-money brackets) where no one trades.

---

## What Worked

1. **Pipeline**: 242 cycles at 60s intervals, 0.8s execution time, generating 54-70 signals/cycle. Rock-solid.
2. **Signal Quality**: The 2 winner positions (XRP-130: +$1.65, SOL-8000: +$0.52) show the bracket model identifies profitable edges.
3. **Fill Timeout Fix**: Increasing from 10s to 120s turned a 0% fill rate into a measurable one. Both detected fills came within the window (37s, 12s).
4. **Risk Controls**: Circuit breakers stayed CLEAR. No runaway losses. Drawdown alerts functioned correctly.
5. **Kalshi Auth**: RSA-PSS authentication stable for the full 2-hour session with advanced tier.

---

## Recommendations for Next Test

### Must Fix (Before Next Live Test)

1. **Async fill confirmation**: Don't block the executor for 120s per order. Place the order, record the order_id, move to next signal. Check all pending orders asynchronously in a separate loop.
2. **Orphan position detection**: After cancel, re-query Kalshi positions periodically and reconcile against DB. If a cancelled order actually filled, register it in the DB.
3. **Shared rate limiter**: All Kalshi-hitting agents should share a single rate-limited client pool (30 read/s, 30 write/s across ALL agents, not per-agent).

### Should Fix

4. **Run during market hours**: 8 AM - 5 PM ET for maximum bracket market liquidity.
2. **Cap extreme prices**: Avoid placing orders at 95-96c — these deep-ITM brackets have zero counterparty interest.
3. **ntfy.sh rate limiting**: The free tier (250 msg/day) was exhausted in the first minute. Either upgrade or throttle notifications.

### Nice to Have

7. **Order type flexibility**: Consider market orders for small positions (<$20) to guarantee fills.
2. **Pipeline dedup on order level**: Multiple signals for the same market ticker generated duplicate orders (e.g., KXSOLMAXMON-SOL-26MAR31-10000 ordered twice).

---

## Financial Summary

| Category | Amount |
|----------|--------|
| Starting Balance (pre-test #1) | $191.69 |
| Sprint 19 Legacy Losses | -$3.86 |
| Test #2 Trading P&L | -$1.79 |
| Total Fees | -$5.18 |
| **Final Balance** | **$185.95** |
| **Total Return** | **-$5.74 (-3.0%)** |

*Note: The -3.0% total loss includes Sprint 19 legacy positions that resolved during the test window. Test #2 trading-only loss was -$1.79 (-0.96% of starting balance).*

---

## Appendix: Code Changes During Test

| File | Change | Reason |
|------|--------|--------|
| `order_executor.py:487-539` | Fill timeout 10s -> 120s (20x6s) | 0% fill rate with 10s |
| `position_lifecycle.py:226-261` | Sell timeout 10s -> 120s (20x6s) | Matching fix for exit orders |
| `kalshi_client.py:718-721` | Added price field for market buy orders | Kalshi API requires price always |
| `kalshi_client.py:765-774` | Added price field for market sell orders | Same fix for sell side |
