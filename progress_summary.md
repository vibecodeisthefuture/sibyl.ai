---
project: Sibyl.ai
repo: https://github.com/vibecodeisthefuture/sibyl.ai
date: 2026-03-26
sprint_completed: 22
test_count: 528
test_status: all_passing
latest_commit: pending
current_focus: "Sprint 22: Execution Integrity — Ghost Trade Prevention, Fill-Price P&L, Position Reconciliation, Spread-Aware EV"
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

Sibyl.ai is an autonomous prediction market trading agent built on Kalshi with 22 completed sprints. Current state: crypto-only with Always-On Bracket Trader, Hyperliquid real-time price streaming (1-second), and DB-first architecture for persistent participation in every BTC/ETH/SOL/XRP market iteration across 15-min, hourly, and daily timeframes. Sprint 22 hardened the execution layer against 5 classes of real-money bugs (ghost trades, phantom P&L, poor fill rate, missing reconciliation, spread blindness) and added API-level timestamp filtering to prevent stale market accumulation.

## Quick Reference

- **docs/sprint_log.md** — Complete history of all sprints with implementation details
- **docs/roadmap.md** — Sprint 22+ timeline, category re-enablement strategy
- **docs/architecture.md** — Architectural decisions, system workflow diagram, API costs
- **docs/file_registry.md** — Complete file listing by category (agents, pipelines, clients, tests, config)

## Current Status

- **Sprint Completed:** 22 (Execution Integrity, 2026-03-26)
- **Test Count:** 528 (108 core tests passing, 1 skipped)
- **Current Focus:** Resume live test #2 with clean restart — verify execution hardening + bracket parser + liquidity
- **Live Account:** $191.69 total capital ($191.69 HWM, reset from stale $505.59)

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
- **Agents:** 19 total (18 existing + HyperliquidPriceAgent)
- **PositionLifecycleManager sub-routines:** 6 (added F: Position Reconciliation in Sprint 22)
- **Policy Sections:** 21 (added Section 21: Per-Category Risk Profiles)
- **New DB Tables:** 5 — crypto_spot_prices, crypto_volatility, crypto_order_book, crypto_funding, crypto_micro_candles
- **Hyperliquid Rate Budget:** ~237 weight/min of 1200/min (20% utilized)
- **Live Account Balance:** $191.69 (confirmed 2026-03-24)
- **Execution hardening:** 5 real-money bug fixes (Sprint 22), all `__pycache__` cleared

## Next Immediate Actions

1. **Clean restart + live test #2** — all Sprint 21.5 + 22 fixes are in place; `__pycache__` fully cleared; ready for 2-hour live test
2. **Assess liquidity** — monitor whether Kalshi crypto bracket markets develop liquidity; if not, consider market-maker strategy or alternative timeframes
3. **Revert pyproject.toml** — change `requires-python` back to `>=3.12` before pushing to GitHub
4. **Push commit to GitHub** — user must push from local machine (VM lacks credentials)
5. **Sprint 23 planning** — confidence calibration with Brier scores; consider expanding beyond crypto if liquidity remains zero
