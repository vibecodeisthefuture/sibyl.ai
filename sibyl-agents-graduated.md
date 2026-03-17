---
project: Sibyl — Graduated Agent Specifications
status: active
started: 2026-03-11
area: personal
type: spec
tags:
  - spec
  - sibyl
  - agents
  - architecture
  - prediction-markets
related:
  - "[[Projects/sibyl-overview]]"
  - "[[Projects/sibyl-agent-brainstorm]]"
commands_used:
  - /graduate
  - /draft
source_candidates: 10
consolidated_to: 4
phase2_planned: 3
total_functions_preserved: 44
---

# Sibyl — Graduated Agent Specifications

> Derived from `[[Projects/sibyl-agent-brainstorm]]` via consolidation pass, with one net-new agent and iterative integration rounds.
> **10 brainstorm candidates absorbed into 4 production agents + 3 agents planned for Phase 2.** All original functions are preserved — agent boundaries were redrawn to eliminate redundant polling loops, inter-agent message overhead, and duplicated database reads. Phase 2 agents are documented with build prerequisites and integration contracts. Suitable for direct integration into `[[Projects/sibyl-overview]]`.

---

## Consolidation Map

```
BRAINSTORM (10 agents)                  GRADUATED (4 Phase 1 agents)
─────────────────────────────────────────────────────────────────────────────

🐋 Whale Watching Agent ─────────────┐
🔔 Volume Anomaly Detector ───────────┤──► 🔭 Market Intelligence Agent
📊 Order Book Depth Agent ────────────┘      (Surveillance Loop: Modes A, B, C)

♻️  EV Recalculator Agent ────────────┐
🚨  Stop-Loss Patrol Agent ───────────┤
⏱️  Exit Timing Agent ────────────────┤──► 🛡️  Position Lifecycle Manager
🕐  Resolution Radar Agent ───────────┤      (Sub-routines A–E)
🔗  Correlation Scanner Agent ────────┘

🌡️  Portfolio Health Narrator ────────────► 🌡️  Portfolio Health Narrator
                                                   (unchanged)

[Net-new capability] ────────────────────► 🔍  Breakout Scout Agent


PHASE 2 ROADMAP (3 agents — post Phase 1 completion)
─────────────────────────────────────────────────────────────────────────────

📋 Opportunity Queue Manager ────────────► Phase 2 — Execution Layer
📐 Signal Quality Calibrator ────────────► Phase 2 — Learning & Calibration
🔬 Post-Mortem Agent ────────────────────► Phase 2 — Learning & Calibration
```

### Why Each Boundary Was Drawn

**Whale Watching + Volume Anomaly + Order Book Depth → Market Intelligence Agent (Surveillance Loop):**
All three are market microstructure surveillance functions — they detect anomalies in trade flow, volume statistics, and book structure. None require LLM calls for their detection logic; all three produce detection events that flow to the same Analysis Queue Worker for LLM reasoning. Keeping them as separate agents would mean three independent polling loops reading the same `trades_log`, `prices`, and `orderbook` tables with three separate database connections. Merging them into three Detection Modes in one Surveillance Loop eliminates that overhead while allowing each mode to run on its own optimal cadence.

**Correlation Scanner → Position Lifecycle Manager (Sub-routine E):**
The Correlation Scanner watches `positions` and `markets` tables — the same tables the PLM's other sub-routines already read — and produces risk management outputs (blocking signals to the Portfolio Allocator). It is structurally identical to the other PLM sub-routines: it reads the shared position cache, runs on a moderate polling cycle, and writes protective signals. Adding it as Sub-routine E is a natural fit. The key new infrastructure it requires is `markets.event_id` tagging — all markets must be tagged with their underlying event so correlation grouping works.

**Portfolio Health Narrator kept standalone:**
Different rhythm (6-hour reporting) and purpose (human oversight, not trading decisions). Merging would couple operational monitoring with reporting in ways that complicate both.

**Breakout Scout as net-new:**
No existing agent looks outside the currently-tracked market universe. The Scout's external research pipeline (multi-source, multi-API) and discovery loop have no functional overlap with any brainstorm agent. Net-new capability.

**Phase 2 agents deferred — not merged:**
Opportunity Queue Manager, Signal Quality Calibrator, and Post-Mortem Agent all require Phase 1 to generate sufficient operational data before they become useful (deferred signal history, resolved position records with outcomes). They are not merged into Phase 1 agents because their triggering conditions don't exist until Phase 1 is live.

---

## Agent 1 — 🔭 Market Intelligence Agent

**Absorbs:** Whale Watching Agent (Mode A) + Volume Anomaly Detector (Mode B) + Order Book Depth Agent (Mode C) + Reasoning Agent
**Engine Affinity:** ACE (primary — Modes A and B feed momentum signals); SGE (Mode C feeds Liquidity Vacuum signals); Meta (routing authority for all enriched signals)
**Phase:** 1

### Purpose

A multi-mode surveillance agent that closes the full market microstructure monitoring → reasoning loop into a single process. Three Detection Modes run in the Surveillance Loop to continuously watch trade flow, volume patterns, and orderbook structure across all tracked markets. Each mode pushes detection events to a shared Analysis Queue Worker that applies LLM reasoning to assess actionability, adjust confidence, generate counter-theses, and route enriched signals to the appropriate engine. The three modes are complementary — a whale bet (Mode A) coinciding with a volume spike (Mode B) and thinning orderbook depth (Mode C) on the same market is a high-conviction composite signal that the Analysis Queue Worker recognizes and escalates.

### Internal Architecture

```
Market Intelligence Agent
│
├── [Thread 1] Surveillance Loop  — always-on, CPU-only, no LLM
│   │
│   ├── ─── Detection Mode A: Whale Watching ─── (30s polling)
│   │   ├── Poll real-time trade feed (Polymarket + Kalshi Monitor Agents)
│   │   ├── For each trade: compare size vs. dynamic threshold per market
│   │   │       threshold = mean_trade_size[market_id] × N[category]
│   │   ├── On breach: write `whale_events` record; push WHALE event to Analysis Queue
│   │   └── Track repeat wallet IDs (Polymarket CLOB only) → smart money model
│   │
│   ├── ─── Detection Mode B: Volume Anomaly ─── (5min polling)
│   │   ├── For each tracked market:
│   │   │       rolling_mean = 30-day avg daily volume
│   │   │       rolling_std  = 30-day std daily volume
│   │   │       z_score = (current_24h_volume − rolling_mean) / rolling_std
│   │   ├── If z_score > 2.5: compute severity
│   │   │       LOW:    2.5 ≤ z < 3.5
│   │   │       MEDIUM: 3.5 ≤ z < 5.0
│   │   │       HIGH:   z ≥ 5.0
│   │   ├── Organic vs. spike distinction:
│   │   │       spike  = burst within single polling window → signal-worthy
│   │   │       organic = gradual multi-period growth → informational only, not pushed
│   │   ├── Resolution proximity boost:
│   │   │       if hours_to_resolution < 72: elevate severity one level
│   │   └── On spike detection: push VOLUME_SURGE event to Analysis Queue
│   │           {market_id, z_score, severity, resolution_proximity, organic_flag}
│   │
│   └── ─── Detection Mode C: Order Book Depth ─── (variable cadence per market)
│       │   ACE-primary markets: 1s | SGE-primary markets: 10s
│       ├── Read latest `orderbook` snapshot (written by Monitor Agents)
│       ├── Compute per snapshot:
│       │       normalized_spread  = (ask_best − bid_best) / mid_price
│       │       total_depth        = Σ(bid_qty + ask_qty) within 3% of mid_price
│       │       side_imbalance     = bid_volume_pct near mid (0.0=all asks, 1.0=all bids)
│       ├── Compare vs. prior snapshot (held in Surveillance Loop state):
│       │       SPREAD_EXPANSION:    normalized_spread > threshold AND prior 3 snapshots below
│       │       WALL_APPEARED:       any level with qty > N× avg_level_qty absent in prior snapshot
│       │       WALL_DISAPPEARED:    large resting order from prior snapshot now absent
│       ├── Composite detection:
│       │       LIQUIDITY_VACUUM: SPREAD_EXPANSION + total_depth < thin_market_threshold
│       │           → Push to Analysis Queue with SGE routing preference tag
│       └── All detections → push to Analysis Queue
│               {market_id, detection_type, normalized_spread, total_depth, side_imbalance}
│
└── [Thread 2] Analysis Queue Worker  — async, LLM-powered, event-driven
    ├── Dequeues events from all three Detection Modes
    ├── Composite signal detection:
    │       if WHALE + VOLUME_SURGE on same market_id within 15-min window:
    │           escalate to COMPOSITE_HIGH_CONVICTION; force Claude Sonnet
    ├── Fetches context per event:
    │       - Market metadata, 48h price history, open positions in market
    │       - market_research record (from Breakout Scout) if available and fresh
    │             → Scout sentiment, synthesis, key_yes/no_args included as context
    ├── Model selection:
    │       COMPOSITE_HIGH_CONVICTION or whale_size > sonnet_threshold → Claude Sonnet
    │       All other events → Claude Haiku
    ├── LLM analysis prompt produces:
    │       - Signal assessment: actionable? confidence modifier (+/−)
    │       - Engine routing recommendation (override or confirm Signal Router default)
    │       - Counter-thesis: top reason NOT to act (1–2 sentences)
    │       - Reasoning narrative (audit trail)
    │       - Scout consensus alignment (if Scout data present):
    │             "Activity ALIGNS WITH / CONTRADICTS Scout consensus (BULLISH/BEARISH)"
    │       - Mode attribution: which detection mode(s) triggered this analysis
    ├── Writes enriched `signals` record:
    │       signal_type, confidence_adjusted, routing_override, reasoning,
    │       counter_thesis, scout_consensus_alignment, detection_modes_triggered
    ├── Timeout fallback (8s): pass to router with default routing, flag UNANALYZED
    └── Rate limiter: max concurrent LLM calls configurable; excess events queue
```

### Detection Mode Summary

| Mode | What It Detects | Polling | Primary Engine Output |
|:---|:---|:---|:---|
| **A — Whale Watching** | Single trades >> market average | 30s | ACE momentum signal |
| **B — Volume Anomaly** | z-score spike on rolling 30-day volume baseline | 5min | ACE volume surge signal |
| **C — Order Book Depth** | Spread expansion, liquidity walls, LIQUIDITY_VACUUM composite | 1s / 10s | SGE liquidity vacuum; both for spread/wall events |

### Composite Signal Escalation

When two or more Detection Modes trigger on the same market within a 15-minute window, the Analysis Queue Worker automatically escalates to `COMPOSITE_HIGH_CONVICTION` status:

| Combination | Interpretation | Routing |
|:---|:---|:---|
| Whale (A) + Volume Surge (B) | Informed money entering with broad market awareness | ACE — high-conviction entry |
| Volume Surge (B) + Liquidity Vacuum (C) | Market heating up while liquidity is thin — price move likely | ACE (entry) + SGE (if spread makes arb viable) |
| Whale (A) + Liquidity Vacuum (C) | Large player entering an illiquid market — outsized impact expected | ACE — elevated position sizing |
| All three (A+B+C) | Maximum composite signal — rare, treat as highest conviction | ACE — escalate to Sonnet, override to maximum Kelly fraction |

### Inputs

| Source | Data | Mode |
|:---|:---|:---|
| Polymarket Monitor Agent | Real-time trade feed | Mode A |
| Kalshi Monitor Agent | Real-time trade feed | Mode A |
| `trades_log` table | Historical trade size distribution (threshold calibration) | Mode A |
| `prices` table | 30-day daily volume history per market | Mode B |
| `markets` table | Resolution dates (proximity boost), category tags | Mode B |
| `orderbook` table | L2 orderbook snapshots (written by Monitor Agents) | Mode C |
| `positions` table | Open positions in flagged market (Analysis context) | Analysis Queue |
| `market_research` table | Scout pre-research packet (if available and fresh) | Analysis Queue |

### Outputs

| Output | Destination | Trigger |
|:---|:---|:---|
| `whale_events` record | SQLite `whale_events` table | Mode A detection |
| `WHALE` event | Analysis Queue (internal) | Mode A detection |
| `VOLUME_SURGE` event | Analysis Queue (internal) | Mode B spike detection |
| `SPREAD_EXPANSION` / `WALL_APPEARED` / `WALL_DISAPPEARED` / `LIQUIDITY_VACUUM` event | Analysis Queue (internal) | Mode C detection |
| Enriched `signals` record | SQLite `signals` table | After each Analysis Queue pass |
| Engine routing override | Signal Router | LLM recommends non-default routing |
| Confidence modifier | `signals.confidence_adjusted` | Always |
| Counter-thesis | `signals.counter_thesis` | Always |
| Scout alignment note | `signals.scout_consensus_alignment` | When `market_research` record exists |
| `UNANALYZED` flag | `signals` record | LLM timeout only |

### Key Configuration (`config/market_intelligence_config.yaml`)

```yaml
surveillance:
  # Mode A — Whale Watching
  whale_poll_interval_seconds: 30
  whale_threshold_multiplier_default: 4.0
  whale_threshold_by_category:
    politics: 5.0
    sports:   3.5
    crypto:   6.0
    economics: 4.0
    other:    4.0
  wallet_tracking_enabled: true         # Polymarket only
  wallet_track_min_events: 3

  # Mode B — Volume Anomaly
  volume_poll_interval_minutes: 5
  volume_rolling_window_days: 30
  volume_zscore_threshold: 2.5          # minimum z-score for detection
  volume_spike_hourly_rate_threshold: 0.25  # fraction of daily vol in single hour = spike
  volume_resolution_proximity_boost_hours: 72

  # Mode C — Order Book Depth
  orderbook_poll_interval_ace_seconds: 1
  orderbook_poll_interval_sge_seconds: 10
  orderbook_depth_window_pct: 0.03      # 3% of mid_price for depth calculation
  spread_expansion_threshold: 0.04     # normalized_spread > 4% triggers SPREAD_EXPANSION
  thin_market_depth_threshold: 500     # total_depth < $500 triggers LIQUIDITY_VACUUM
  wall_size_multiplier: 8.0            # qty > 8× avg_level_qty = WALL

composite:
  window_minutes: 15                   # events within this window are treated as composite
  high_conviction_modes_required: 2    # how many modes must fire to trigger composite

analysis:
  llm_haiku_model: "claude-haiku-4-5-20251001"
  llm_sonnet_model: "claude-sonnet-4-6"
  sonnet_threshold_usd: 500            # whale size above this → Sonnet
  max_concurrent_llm_calls: 3
  llm_timeout_seconds: 8
  queue_max_depth: 50
  scout_context_min_freshness: 0.30
```

### Notes

- Mode A and Mode B can co-trigger on the same event and are explicitly designed to reinforce each other — a whale bet without volume context is moderate conviction; whale + volume spike is high conviction
- Mode C's LIQUIDITY_VACUUM signal is the primary driver of SGE Liquidity Vacuum entries; the Analysis Queue Worker tags these with SGE routing preference and the LLM confirms or overrides
- Wall events (APPEARED/DISAPPEARED) are particularly valuable for ACE — a disappearing large resting order at a key price level often precedes a sharp move
- Wallet tracking is Polymarket-specific; Kalshi accounts are anonymized — this asymmetry is noted in `PLATFORM_NOTES.md`

---

## Agent 2 — 🛡️ Position Lifecycle Manager

**Absorbs:** EV Recalculator Agent (Sub-routine B) + Stop-Loss Patrol Agent (Sub-routine A) + Exit Timing Agent (Sub-routine C) + Resolution Radar Agent (Sub-routine D) + Correlation Scanner Agent (Sub-routine E)
**Engine Affinity:** Both (per-engine thresholds applied internally); Meta (Stop Guard and Correlation Scanner are engine-agnostic)
**Phase:** 1

### Purpose

A single agent that owns both the full lifecycle of every open position (entry to close) and the real-time correlation risk profile of the entire portfolio. Five named sub-routines share one position state cache and run on independent schedules within a single process. The **Stop Guard** runs on the tightest loop, isolated from all other sub-routines so it cannot be blocked by errors elsewhere. The **Correlation Scanner** adds a portfolio-level risk lens: it ensures that neither engine accumulates concentrated exposure to a single underlying real-world event, and blocks new entries that would push correlation beyond caps.

### Sub-routine Overview

| Sub-routine | Function | Polling | Priority |
|:---|:---|:---|:---|
| **A — Stop Guard** | Hard stop monitoring; emergency exits; circuit breaker escalation | 5–10s | Highest — isolated thread |
| **B — EV Monitor** | Live EV recalculation on all open positions; EV inversion detection | 90s (ACE) / 5min (SGE) | High |
| **C — Exit Optimizer** | EV capture exits; momentum stall exits; routes EXIT signals | 2min | High |
| **D — Resolution Tracker** | Resolution countdown; convergence alerts; late-stage ACE opportunities | 5min | Medium |
| **E — Correlation Scanner** | Cross-position event correlation; pre-entry checks; TradeBot cross-check | 10min | Medium |

### Internal Architecture

```
Position Lifecycle Manager
│
├── [Shared State]  Position Cache  — refreshed from `positions` table every 30s
│                                    Sub-routines B, C, D, E read from cache
│                                    Sub-routine A reads live price feed directly
│
├── [Sub-routine A]  Stop Guard  — 5–10s polling  ← HIGHEST PRIORITY, ISOLATED THREAD
│   ├── For each open position: check current_price vs. stop_loss_threshold
│   ├── Reads raw price feed directly (NOT the 30s cache — avoids staleness on safety checks)
│   ├── On breach: emit emergency EXIT signal to appropriate engine Executor
│   ├── On ≥ 3 breaches within 15-min rolling window (either engine):
│   │       emit CIRCUIT_BREAKER_ALERT to Portfolio Allocator
│   └── Wrapped in isolated try/except — exceptions elsewhere CANNOT affect Stop Guard
│
├── [Sub-routine B]  EV Monitor  — 90s (ACE) / 5min (SGE)
│   ├── For each open position:
│   │       EV = (confidence × (1 − current_price)) − ((1 − confidence) × current_price)
│   ├── Write updated EV to `positions` table
│   ├── If EV sign has reversed since entry: emit EV_INVERTED → triggers Exit Optimizer
│   └── If |ΔEV| > 0.05 since last cycle: flag SIGNIFICANT_EV_SHIFT for Narrator
│
├── [Sub-routine C]  Exit Optimizer  — 2min (also triggered by EV_INVERTED events)
│   ├── Three exit conditions evaluated per position:
│   │
│   │   1. EV Capture Exit:
│   │       (current_price − entry_price) / (target_price − entry_price) ≥ 0.80
│   │       → OPTIONAL_EXIT: SGE holds (policy-dependent); ACE exits and redeploys
│   │
│   │   2. EV Inversion Exit (from Sub-routine B):
│   │       → RECOMMENDED_EXIT: both engines
│   │
│   │   3. Momentum Stall Exit (ACE only):
│   │       price movement < threshold for N consecutive cycles
│   │       → RECOMMENDED_EXIT: ACE only; SGE holds
│   │
│   └── EXIT signals tagged: position_id, engine, reason
│               (EV_CAPTURE | EV_INVERTED | MOMENTUM_STALL)
│
├── [Sub-routine D]  Resolution Tracker  — 5min
│   ├── For all tracked markets: compute hours_to_resolution = close_date − now
│   ├── Convergence alert: yes_price > 0.85 or < 0.15 AND hours_to_resolution < 72
│   │       → write CONVERGENCE_ALERT to `signals` table
│   │       → if open position involved: internally notify Exit Optimizer
│   ├── Late-stage ACE opportunity: yes_price 0.60–0.80 AND hours_to_resolution < 48
│   │       → write RESOLUTION_OPPORTUNITY signal → ACE Signal Filter
│   └── Adverse alert: open position + price moving against + < 48h to resolution
│           → internally escalate to Stop Guard for immediate threshold check
│
└── [Sub-routine E]  Correlation Scanner  — 10min
    │
    ├── REQUIRES: `markets.event_id` tagging (see Notes — tagging schema must be designed)
    │
    ├── Step 1: Open position correlation check
    │   ├── Group all open positions (both engines) by event_id
    │   ├── For each event_id with combined_exposure > 3% of total portfolio:
    │   │       write CORRELATION_ALERT to `system_state`
    │   │           {event_id, combined_exposure_pct, engines_affected, markets_list}
    │   └── If exposure spans both SGE AND ACE on the same event_id:
    │           emit CROSS_ENGINE_BLOCK to Portfolio Allocator
    │               (blocks further entries on this event_id across both engines until exposure drops)
    │
    ├── Step 2: Pre-entry correlation check
    │   ├── For each PENDING signal in `signals` table:
    │   │       determine event_id of candidate market
    │   │       check if entry would cause event_id total exposure > cap
    │   └── Write PRE_ENTRY_CORRELATION result to signal record:
    │           CLEAR: entry is safe from a correlation perspective
    │           FLAGGED: entry would elevate event_id exposure — Signal Filter must decide
    │           BLOCKED: entry would breach event_id cap — automatically rejected
    │
    └── Step 3: TradeBot cross-check (crypto markets only)
        ├── If market.category = 'crypto':
        │       query TradeBot exposure tracking (shared table or API)
        │       compute combined Sibyl + TradeBot exposure on correlated event
        └── If combined cross-system exposure > threshold:
                emit CROSS_SYSTEM_CORRELATION_ALERT to Portfolio Allocator
```

### Inputs

| Source | Data | Sub-routine |
|:---|:---|:---|
| `positions` table | All open positions (engine, entry_price, stop_loss, target_price, event_id) | All |
| Live price feed (Monitor Agents) | Current market prices | Stop Guard (direct); others via cache |
| `signals` table | Original signal confidence; PENDING signals (pre-entry check) | EV Monitor, Correlation Scanner |
| `markets` table | Resolution dates, market status, **event_id tags**, category | Resolution Tracker, Correlation Scanner |
| Engine policy files | Stop-loss thresholds, exit behavior, correlation caps | All |
| `system_state` table | Current Allocator state, active blocks | Correlation Scanner |
| TradeBot exposure API / shared table | Crypto position exposure (cross-system check) | Correlation Scanner (crypto only) |

### Outputs

| Output | Destination | Sub-routine |
|:---|:---|:---|
| Emergency `EXIT` signal | SGE or ACE Executor | Stop Guard |
| `CIRCUIT_BREAKER_ALERT` | Portfolio Allocator | Stop Guard |
| Updated EV on position | `positions` table | EV Monitor |
| `EV_INVERTED` internal event | Exit Optimizer | EV Monitor |
| `SIGNIFICANT_EV_SHIFT` flag | Position record | EV Monitor |
| `RECOMMENDED_EXIT` signal | SGE or ACE Executor | Exit Optimizer |
| `OPTIONAL_EXIT` signal | SGE or ACE Executor | Exit Optimizer |
| `CONVERGENCE_ALERT` | `signals` table | Resolution Tracker |
| `RESOLUTION_OPPORTUNITY` signal | `signals` table → ACE Signal Filter | Resolution Tracker |
| `CORRELATION_ALERT` | `system_state` table | Correlation Scanner |
| `CROSS_ENGINE_BLOCK` | Portfolio Allocator | Correlation Scanner |
| `PRE_ENTRY_CORRELATION` flag | `signals` record (CLEAR / FLAGGED / BLOCKED) | Correlation Scanner |
| `CROSS_SYSTEM_CORRELATION_ALERT` | Portfolio Allocator | Correlation Scanner (crypto) |

### New Database Requirements

```sql
-- Required: event_id tagging on markets table
ALTER TABLE markets ADD COLUMN event_id TEXT;
  -- Format: "{CATEGORY}_{EVENT_NAME}_{YEAR}" e.g., "POLITICS_US_FED_MARCH_2026"
  -- Set by: Monitor Agents on market ingestion; Breakout Scout for discovered markets
  -- Null allowed: markets without a clear underlying event (e.g., novelty/misc markets)

ALTER TABLE markets ADD COLUMN event_id_confidence REAL;
  -- 0.0–1.0: how confident the tagging is (auto-tagged = lower; manually confirmed = 1.0)
```

> **Event ID Tagging Strategy:** Initially, event IDs are assigned by the Monitor Agents using keyword matching on market titles (e.g., "Fed" + "March" + "2026" → `ECON_FED_MARCH_2026`). Confidence is low (0.5–0.7) for auto-tags. A periodic manual review step should confirm and upgrade high-stakes event IDs (elections, major Fed decisions) to confidence 1.0 before Correlation Scanner acts on them. Add this to the weekly operational review.

### Key Configuration (`config/position_lifecycle_config.yaml`)

```yaml
stop_guard:
  poll_interval_seconds: 7
  circuit_breaker_window_minutes: 15
  circuit_breaker_stop_count: 3

ev_monitor:
  poll_interval_ace_seconds: 90
  poll_interval_sge_seconds: 300
  significant_ev_shift_threshold: 0.05

exit_optimizer:
  poll_interval_seconds: 120
  ev_capture_threshold: 0.80
  momentum_stall_cycles: 4
  momentum_stall_threshold: 0.005

resolution_tracker:
  poll_interval_seconds: 300
  convergence_yes_threshold: 0.85
  convergence_no_threshold: 0.15
  convergence_hours_window: 72
  ace_opportunity_hours_window: 48
  ace_opportunity_price_min: 0.60
  ace_opportunity_price_max: 0.80

correlation_scanner:
  poll_interval_minutes: 10
  event_exposure_alert_threshold_pct: 0.03    # alert at 3% combined portfolio exposure per event
  event_exposure_block_threshold_pct: 0.07    # block new entries at 7%
  cross_engine_block_enabled: true
  tradebot_cross_check_enabled: true
  tradebot_cross_system_threshold_pct: 0.10   # combined Sibyl + TradeBot > 10% = alert
  min_event_id_confidence: 0.50               # only scan events with confidence above this
```

### Notes

- Stop Guard isolation is non-negotiable — wrap all other sub-routines in try/except blocks; exceptions must never propagate to the Stop Guard thread
- Sub-routine E requires `markets.event_id` — without it, the Correlation Scanner cannot group positions. Build the keyword-matching auto-tagger in the Monitor Agents *before* Phase 1 goes live, even if initial confidence is low
- The `PRE_ENTRY_CORRELATION` check is advisory for FLAGGED signals (engine decides) and hard-blocking for BLOCKED signals — the Signal Filter must check this flag before executing any entry
- TradeBot cross-check is particularly important during crypto market cycles where both systems may accumulate correlated exposure without realizing it

---

## Agent 3 — 🌡️ Portfolio Health Narrator

**Absorbs:** Portfolio Health Narrator (unchanged — no consolidation applied)
**Engine Affinity:** Meta
**Phase:** 1

### Purpose

Every 6 hours, reads the full state of both engines, the Portfolio Allocator, the Breakout Scout pipeline, and any active Correlation Scanner blocks — then generates a concise, plain-English health summary. The primary human oversight mechanism for an otherwise fully autonomous system.

### Output Schema (per digest)

```
[SIBYL HEALTH — {timestamp}]

ALLOCATOR STATUS
  Capital split: SGE {x}% / ACE {y}% (target: 70/30)
  Rebalance queue: {n} pending | Circuit breakers: {all clear | SGE tripped | ACE tripped}
  Cross-engine correlation blocks: {none | {n} active on events: {event_ids}}

SGE — Stable Growth Engine ({capital} deployed)
  Open positions: {n} | Unrealized P&L: {+/−x.x%}
  Risk envelope: {within normal | approaching cap | at cap}
  Notable: {1–2 sentence observation or "Nothing notable."}

ACE — Alpha Capture Engine ({capital} deployed)
  Open positions: {n} | Unrealized P&L: {+/−x.x%}
  Risk envelope: {within normal | approaching cap | at cap}
  Notable: {1–2 sentence observation or "Nothing notable."}

SCOUT PIPELINE
  New markets discovered (last 6h): {n}
  High-conviction research packets: {n} (BULLISH: {x} | BEARISH: {y} | CONTESTED: {z})
  Notable: {top discovery or "No notable discoveries."}

CORRELATION RISK
  Active event blocks: {none | list of event_ids with combined exposure %s}
  Cross-system alerts: {none | TradeBot overlap details}

SYSTEM ALERTS
  {List active alerts, or "No active alerts."}

VERDICT
  {One sentence overall system health assessment.}
```

### Inputs

| Source | Data |
|:---|:---|
| `engine_state` table | Per-engine capital, exposure, circuit breaker status, drawdown |
| `positions` table | All open positions per engine |
| `signals` table | Signals generated in the last 6 hours |
| `system_state` table | Allocator status, rebalance queue, cross-engine blocks, correlation alerts |
| `whale_events` table | Any whale events in the last 6 hours |
| `market_research` table | Scout discoveries and research packets from the last 6 hours |

### Outputs

| Output | Destination |
|:---|:---|
| Full narrative digest | Appended to `sibyl-log.md` under ## Log |
| Condensed summary (2–3 lines) | Notification channel (ntfy.sh, Slack, or Grafana annotation) |

### Key Configuration (`config/narrator_config.yaml`)

```yaml
schedule_cron: "0 */6 * * *"
llm_model: "claude-haiku-4-5-20251001"
notification_channel: "ntfy"
ntfy_topic: "sibyl-health"
max_digest_tokens: 550
alert_escalation_threshold: 2
```

---

## Agent 4 — 🔍 Breakout Scout Agent

**Absorbs:** Net-new capability (no brainstorm equivalent)
**Engine Affinity:** ACE (primary consumer); SGE (receives BULLISH arb signals)
**Phase:** 1

### Purpose

The only agent that looks *outside* the currently-tracked market universe. Continuously scans both platforms for newly listed or rapidly-emerging markets, scores them against quantitative breakout criteria, and runs a multi-source deep research pipeline (authoritative news, Reddit, X/Twitter, Perplexity) for qualifying markets. Outputs structured public consensus packets stored in `market_research` and injected into the Market Intelligence Agent's Analysis Queue as pre-researched context.

### Internal Architecture

```
Breakout Scout Agent
│
├── [Thread 1] Discovery Loop  — 15-min polling
│   ├── Query both platforms for markets listed/updated in last 24h
│   ├── Compute Breakout Score per market (0–100):
│   │       volume_growth_rate × 0.35
│   │     + odds_velocity       × 0.30
│   │     + listing_recency     × 0.20
│   │     + category_heat       × 0.15
│   ├── Score > threshold AND not freshly cached → push to Research Queue
│   ├── New market not in `markets` table:
│   │       → emit NEW_MARKET_ALERT to Monitor Agents (begin streaming)
│   │       → write stub to `markets` with discovery_source = BREAKOUT_SCOUT
│   └── Write breakout_score to `markets` table for all evaluated markets
│
└── [Thread 2] Research Queue Worker  — async, multi-source
    ├── Phase 1: Parallel Source Collection (4 concurrent, 30s timeout each)
    │   ├── [Tier 1] NewsAPI / GDELT — authoritative news (last 7 days)
    │   ├── [Tier 2] Perplexity sonar-pro — structured consensus synthesis + prediction market meta
    │   ├── [Tier 3] X/Twitter v2 Bearer — verified accounts (min 100 likes) + general (min 10 likes)
    │   └── [Tier 4] Reddit PRAW — subreddits by category (r/PredictionMarkets always; others conditional)
    │
    ├── Phase 2: LLM Synthesis (Claude Sonnet)
    │   ├── Confirmation bias guard: system prompt requires seeking contrary evidence first
    │   ├── Source tiering: Tier 1 > Tier 2 > Tier 3 > Tier 4 in consensus weighting
    │   ├── CONTESTED ≠ NEUTRAL: split community vs. low-volume ambiguity flagged separately
    │   └── Outputs strict JSON: sentiment_score (−1.0→+1.0), sentiment_label, confidence,
    │           source_breakdown, key_yes_args, key_no_args, notable_dissent, synthesis
    │
    ├── Phase 3: Storage & Routing
    │   ├── Write full packet to `market_research` table
    │   ├── BULLISH/BEARISH (confidence ≥ 0.55) → push SCOUT_HIGH_CONVICTION to Market Intelligence
    │   ├── BULLISH/BEARISH (confidence 0.35–0.54) → push SCOUT_MODERATE
    │   ├── CONTESTED → push SCOUT_CONTESTED (lower priority, ACE only)
    │   └── NEUTRAL → DB only; no routing (re-evaluated next Discovery Loop cycle)
    │
    └── Phase 4: Freshness Maintenance (every 2h)
        ├── Decay: freshness_score − 0.15 per cycle (~13h to full stale)
        └── Re-research trigger: freshness < 0.30 AND market active AND has open position
```

### Source Tier Weighting

| Tier | Sources | Weight | Rationale |
|:---|:---|:---|:---|
| **1 — Authoritative** | Reuters, AP, Bloomberg, FT, WSJ + category-specific | Highest | Fact-checked, editorial standards |
| **2 — Prediction Meta** | Perplexity synthesized search, Metaculus, Manifold, Good Judgment | High | Forecasters with skin in the game |
| **3 — High-Signal Social** | X/Twitter verified accounts ≥100 likes | Medium | Engaged informed commentators |
| **4 — Community** | Reddit threads, general X/Twitter | Lower | High volume; directional signal only |

### Key Configuration (`config/breakout_scout_config.yaml`)

```yaml
discovery:
  poll_interval_minutes: 15
  lookback_hours: 24
  breakout_score_threshold: 52
  freshness_skip_threshold: 0.40
  score_weights:
    volume_growth_rate: 0.35
    odds_velocity: 0.30
    listing_recency: 0.20
    category_heat: 0.15
  category_heat_multipliers:
    active_election_cycle: 1.5
    major_sporting_event: 1.4
    active_fed_cycle: 1.3
    standard: 1.0

research:
  phase1_timeout_seconds: 30
  phase1_max_concurrent: 4
  llm_synthesis_model: "claude-sonnet-4-6"
  reddit_subreddits_always: [PredictionMarkets]
  reddit_subreddits_by_category:
    politics:  [politics, worldnews, PoliticalDiscussion]
    sports:    [sports, nfl, nba, soccer]
    crypto:    [CryptoCurrency, Bitcoin, ethereum]
    economics: [Economics, finance, investing]
    other:     [news]
  twitter_high_signal_min_likes: 100
  news_lookback_days: 7
  high_conviction_confidence_threshold: 0.55
  moderate_confidence_threshold: 0.35

freshness:
  decay_interval_hours: 2
  decay_amount_per_cycle: 0.15
  active_position_reresearch_threshold: 0.30
```

---

## Phase 2 Roadmap

> The following three agents are planned for Phase 2 development, beginning after Phase 1 is fully live and has accumulated sufficient operational data. Each requires a Phase 1 prerequisite before it can be built usefully. Phase 2 agents extend Phase 1 capabilities rather than replacing them.

---

### Phase 2 — Agent A: 📋 Opportunity Queue Manager

**What it does:** Manages the queue of deferred signals — signals that were generated but could not execute because an engine was at capacity, circuit-broken, or blocked by a correlation check. Ranks deferred signals by EV decay rate and re-prioritizes them for execution when capacity becomes available. Marks signals VOID when their EV has decayed below the receiving engine's minimum threshold.

**Phase 1 prerequisite:** Requires a populated `signals` table with `DEFERRED` and `VOID` status values and real operational data on how frequently signals are deferred (expected from Phase 1 production experience). Also requires `engine_state` capacity tracking to be reliable.

**Where it sits:** Between the Signal Router and the engine Signal Filters — it intercepts DEFERRED signals before they would be dropped, holds them in a ranked queue, and re-dispatches them when the engine reports capacity.

**Integration with Phase 1 agents:**
- Reads `signals` table (DEFERRED records) and `engine_state` (capacity status)
- Calls EV Monitor sub-routine logic (lightweight version, no LLM) to recalculate EV before re-dispatch
- Dispatches valid signals back to Signal Router with updated EV estimate
- Marks expired signals VOID in `signals` table

**Key design decisions to resolve:**
- Should re-dispatch trigger a full re-analysis by Market Intelligence Agent, or is lightweight EV recalculation sufficient? Recommend: lightweight recalculation for SGE signals; full re-analysis for ACE signals (ACE entries are more time-sensitive and context-dependent)
- What is the maximum hold time before a signal is VOID regardless of EV? Recommend configurable per signal type: arbitrage signals expire fastest (30min); political/long-duration signals can hold longer (4h+)

**Build trigger:** Implement after Phase 1 reveals how often signals are being deferred in production. If deferral rate is < 5% of signals, this agent adds minimal value and can be deprioritized further.

---

### Phase 2 — Agent B: 📐 Signal Quality Calibrator

**What it does:** After each position resolves, compares the signal's original estimated EV and confidence against the actual outcome (WIN/LOSS/PUSH). Over time, computes rolling win rates per signal type, per engine, and per market category — and generates threshold recalibration recommendations when estimated and actual performance diverge significantly. Does NOT auto-apply changes; outputs human-reviewable recommendation records.

**Phase 1 prerequisite:** Requires a minimum of ~60 resolved positions across both engines for meaningful statistics (30 per engine). At typical Phase 1 activity levels, this means approximately 4–8 weeks of production operation before the Calibrator produces actionable recommendations.

**Integration with Phase 1 agents:**
- Triggered by: position status changes to CLOSED in `positions` table
- Reads: `performance` table (outcome, realized P&L), `signals` table (original confidence, EV estimate, signal_type, engine routing)
- Outputs: `calibration_recommendations` table records — never directly modifies `sge_config.yaml` or `ace_config.yaml`
- Analytics Agent (when built) surfaces Calibrator recommendations in weekly reports
- Post-Mortem Agent (Phase 2, below) provides richer input to the Calibrator via `positions.post_mortem` field

**Output format per recommendation:**
```
signal_type, engine, market_category,
  estimated_win_rate, actual_win_rate_30d, actual_win_rate_90d,
  estimated_avg_ev, actual_avg_ev_30d, actual_avg_ev_90d,
  recommended_confidence_adjustment,
  recommendation_text,
  status: PENDING_REVIEW | APPROVED | REJECTED
```

**Key insight this agent targets:** Are certain signal types (e.g., MOMENTUM) profitable for SGE but unprofitable for ACE — or vice versa? If yes, the Signal Router's routing logic should be adapted. The Calibrator surfaces this pattern; a human reviews it before any routing change is made.

---

### Phase 2 — Agent C: 🔬 Post-Mortem Agent

**What it does:** After each position closes, automatically generates a structured post-mortem — documenting the original thesis, actual market outcome, key inflection point during the position's lifetime, what the system's signals got right or wrong, and a tagged lesson. Writes results to `SIGNAL_REGISTRY.md` and the `performance` table. Over time, post-mortems become the richest training data for the Signal Quality Calibrator.

**Phase 1 prerequisite:** Requires closed position history. Maximally useful if Market Intelligence Agent has been storing Scout research packets in `market_research` (which it does from Phase 1 Day 1) — because the Post-Mortem Agent cross-references Scout sentiment vs. actual outcome, which is one of the highest-value analyses the system can do. Also requires `positions.thesis` field to be populated (the post-mortem needs a "what did the system think going in?" baseline).

**Integration with Phase 1 agents:**
- Triggered by: position status changes to CLOSED
- Reads: `positions` + `executions` (trade records), `signals` (original signal + confidence + counter_thesis + reasoning), `market_research` (Scout sentiment at time of entry), `prices` (full price history during position lifetime), `whale_events` (any whale activity during position)
- Writes: post-mortem narrative to `performance.post_mortem`; WIN/LOSS/PUSH tag to performance record; summary entry to `SIGNAL_REGISTRY.md`
- Feeds: Signal Quality Calibrator (Agent B above) with enriched context per resolved position

**Output format per post-mortem (stored in `performance.post_mortem`):**
```
thesis:           {original entry reasoning from signals.reasoning}
scout_context:    {Scout sentiment label + synthesis at time of entry, if available}
outcome:          WIN | LOSS | PUSH
realized_ev:      {actual P&L vs. estimated EV at entry}
key_inflection:   {1–2 sentences: what moment/event was decisive?}
signal_accuracy:  {were the detection modes that fired correct? e.g., "Whale signal correct; Volume Surge was noise"}
counter_thesis_review: {was the stored counter_thesis valid in hindsight?}
lesson:           {1 sentence actionable takeaway}
signal_type_tag:  {PRIMARY_CORRECT | PRIMARY_INCORRECT | COMPOSITE_MIXED | etc.}
```

**ACE focus:** ACE positions (up to 8% of engine capital per position) warrant the most thorough post-mortems. Configure a `depth` flag: ACE positions get full post-mortem (all fields above); SGE positions below 1% capital get a condensed version (outcome, realized_ev, lesson only) to reduce LLM cost.

---

## Lossless Function Audit

All functions from the 10 integrated brainstorm candidates are accounted for below. Phase 2 agents are noted separately as planned functions. Agent 4 (Breakout Scout) introduces net-new functions with no brainstorm equivalent.

| Original Function | Original Agent | Preserved In | Notes |
|:---|:---|:---|:---|
| Dynamic whale threshold detection | Whale Watching | Market Intelligence — Mode A | Threshold formula unchanged |
| `whale_events` table writes | Whale Watching | Market Intelligence — Mode A | Schema unchanged |
| Repeat wallet tracking (Polymarket) | Whale Watching | Market Intelligence — Mode A | Config flag |
| Trigger for downstream reasoning | Whale Watching | Market Intelligence — internal | Internal queue push |
| LLM signal assessment (Haiku/Sonnet tiered) | Reasoning Agent | Market Intelligence — Analysis Queue | Model selection preserved |
| Engine routing override | Reasoning Agent | Market Intelligence — Analysis Queue | `signals.routing_override` |
| Confidence adjustment | Reasoning Agent | Market Intelligence — Analysis Queue | `signals.confidence_adjusted` |
| Counter-thesis generation | Reasoning Agent | Market Intelligence — Analysis Queue | Embedded in analysis prompt |
| Async queue + rate limiting + timeout fallback | Reasoning Agent | Market Intelligence — Analysis Queue | All config params preserved |
| Signal enrichment / audit narrative | Reasoning Agent | Market Intelligence — Analysis Queue | `signals.reasoning` |
| Scout consensus alignment | *NEW via Agent 4* | Market Intelligence — Analysis Queue | `signals.scout_consensus_alignment` |
| Z-score volume anomaly detection (2.5σ threshold) | Volume Anomaly Detector | Market Intelligence — Mode B | Threshold config preserved |
| Organic vs. spike volume distinction | Volume Anomaly Detector | Market Intelligence — Mode B | `volume_spike_hourly_rate_threshold` |
| Severity scoring (LOW/MEDIUM/HIGH) | Volume Anomaly Detector | Market Intelligence — Mode B | z-score bands preserved |
| Resolution proximity weighting | Volume Anomaly Detector | Market Intelligence — Mode B | 72h boost window preserved |
| VOLUME_SURGE signal generation | Volume Anomaly Detector | Market Intelligence — Mode B → Analysis Queue | Routes to ACE via Analysis Queue |
| Normalized spread computation | Order Book Depth | Market Intelligence — Mode C | `(ask − bid) / mid` formula preserved |
| LIQUIDITY_VACUUM composite detection | Order Book Depth | Market Intelligence — Mode C | SPREAD_EXPANSION + thin depth |
| WALL_APPEARED / WALL_DISAPPEARED detection | Order Book Depth | Market Intelligence — Mode C | `wall_size_multiplier` config preserved |
| Variable polling cadence per engine affinity | Order Book Depth | Market Intelligence — Mode C | 1s ACE / 10s SGE config preserved |
| Feeds SGE Liquidity Vacuum signal | Order Book Depth | Market Intelligence — Mode C → Analysis Queue | SGE routing preference tag on LIQUIDITY_VACUUM |
| EV recalculation (90s ACE / 5min SGE) | EV Recalculator | PLM — Sub-routine B | Cadence config preserved |
| Updated EV written to `positions` | EV Recalculator | PLM — Sub-routine B | Column unchanged |
| EV_INVERTED alert → exit trigger | EV Recalculator | PLM — Sub-routine B → C | Internal event |
| Hard stop monitoring (5–10s) | Stop-Loss Patrol | PLM — Sub-routine A | Cadence preserved |
| Emergency EXIT signals | Stop-Loss Patrol | PLM — Sub-routine A | Format unchanged |
| CIRCUIT_BREAKER_ALERT (≥3 stops / 15min) | Stop-Loss Patrol | PLM — Sub-routine A | Window + count preserved |
| Independence from engine trackers | Stop-Loss Patrol | PLM — Sub-routine A | Isolated thread |
| EV capture exit trigger (≥80%) | Exit Timing | PLM — Sub-routine C | Config preserved |
| Engine-differentiated exits (SGE patient, ACE fast) | Exit Timing | PLM — Sub-routine C | Applied per-engine |
| Momentum stall exit (ACE) | Exit Timing | PLM — Sub-routine C | Config preserved |
| EXIT signals tagged with reason | Exit Timing | PLM — Sub-routine C + A | Reason field on exit signal |
| Resolution countdown monitoring | Resolution Radar | PLM — Sub-routine D | Cadence unchanged |
| Convergence alerts (>0.85 / <0.15 within 72h) | Resolution Radar | PLM — Sub-routine D | Thresholds preserved |
| Late-stage ACE opportunity detection | Resolution Radar | PLM — Sub-routine D | RESOLUTION_OPPORTUNITY signal |
| Adverse resolution → Stop Guard escalation | Resolution Radar | PLM — Sub-routine D → A | Internal escalation |
| Open position event correlation grouping | Correlation Scanner | PLM — Sub-routine E | `markets.event_id` required |
| CORRELATION_ALERT to system_state | Correlation Scanner | PLM — Sub-routine E | Alert format preserved |
| CROSS_ENGINE_BLOCK to Portfolio Allocator | Correlation Scanner | PLM — Sub-routine E | Cap config preserved |
| Pre-entry correlation check on pending signals | Correlation Scanner | PLM — Sub-routine E | PRE_ENTRY_CORRELATION flag — new capability enhancement |
| TradeBot crypto cross-check | Correlation Scanner | PLM — Sub-routine E | `tradebot_cross_check_enabled` config |
| 6-hour narrative digest | Portfolio Health Narrator | Portfolio Health Narrator | Schema updated; correlation section added |
| Engine health reporting | Portfolio Health Narrator | Portfolio Health Narrator | Unchanged |
| Push to notification channel | Portfolio Health Narrator | Portfolio Health Narrator | Config-driven |
| Alert escalation on ≥2 active alerts | Portfolio Health Narrator | Portfolio Health Narrator | Preserved |

**Audit result: 44 / 44 functions preserved across 10 integrated brainstorm candidates. ✅**

**Phase 2 planned functions (not yet built):**
- Deferred signal queue management + EV decay ranking (Opportunity Queue Manager)
- Signal type win rate tracking + threshold recalibration recommendations (Signal Quality Calibrator)
- Structured post-mortem generation per closed position (Post-Mortem Agent)

---

## System Architecture Diagram

```
┌───────────────────────────────────────────────────────────────────────────┐
│  🔍 BREAKOUT SCOUT AGENT                                                   │
│                                                                             │
│  [Discovery] 15min ─► breakout_score all markets                           │
│       │               NEW_MARKET_ALERT → Monitor Agents                    │
│       ▼                                                                     │
│  [Research Queue] ──► Tier 1–4 parallel APIs → Sonnet synthesis           │
│       │               → market_research table                              │
│  [Freshness] 2h ──► decay; re-research active-position markets            │
│                                                                             │
│  Routes BULLISH/BEARISH/CONTESTED → Market Intelligence Analysis Queue     │
└──────────────────────────────┬────────────────────────────────────────────┘
                               │ research packets
                               ▼
MARKET DATA (shared infrastructure)
  Polymarket Monitor Agent ◄── NEW_MARKET_ALERT
  Kalshi Monitor Agent     ◄── NEW_MARKET_ALERT
  Cross-Platform Sync Agent
         │
         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  🔭 MARKET INTELLIGENCE AGENT                                              │
│                                                                             │
│  [Surveillance Loop — Thread 1]                                            │
│    Mode A: Whale Watching     30s ──► whale_events table                   │
│    Mode B: Volume Anomaly     5min ─► z-score spike detection              │
│    Mode C: Order Book Depth   1s/10s ► spread / wall / vacuum detection   │
│         │                                                                   │
│         │ (all modes → internal Analysis Queue)                             │
│         ▼                                                                   │
│  [Analysis Queue Worker — Thread 2]                                        │
│    Composite signal detection (A+B, B+C, A+C, A+B+C)                      │
│    Scout context lookup (market_research)                                  │
│    LLM reasoning (Haiku / Sonnet tiered)                                   │
│    → enriched signals table                                                │
│        + routing_override, confidence_adjusted, counter_thesis             │
│        + scout_consensus_alignment, detection_modes_triggered              │
└─────────────────────────────┬─────────────────────────────────────────────┘
                              │
                              ▼
                  Signal Generator + Signal Router
                  ┌────────────┴────────────┐
                  ▼                          ▼
                SGE (70%)                 ACE (30%)
                Executor                  Executor
                  │                          │
                  └──────────────┬───────────┘
                                 │ (open positions, pending signals)
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  🛡️  POSITION LIFECYCLE MANAGER                                            │
│                                                                             │
│  [Sub-routine A] Stop Guard        5–10s ─► emergency EXIT + CB alerts    │
│  [Sub-routine B] EV Monitor       90s/5m ─► EV updates + EV_INVERTED      │
│  [Sub-routine C] Exit Optimizer      2min ─► EV_CAPTURE / STALL exits     │
│  [Sub-routine D] Resolution Tracker  5min ─► convergence + ACE opps       │
│  [Sub-routine E] Correlation Scanner 10min ► CORRELATION_ALERT            │
│                                             CROSS_ENGINE_BLOCK             │
│                                             PRE_ENTRY_CORRELATION flags    │
│                                             CROSS_SYSTEM alerts            │
│                                                                             │
│  Stop Guard: isolated thread | Position cache: shared by B, C, D, E       │
└────────────────────────────────────────────────┬──────────────────────────┘
                                                 │
                          (state reads: engine_state, positions,
                           signals, system_state, market_research)
                                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  🌡️  PORTFOLIO HEALTH NARRATOR                                             │
│                                                                             │
│  Every 6h: SGE + ACE health | Scout pipeline | Correlation blocks         │
│  → sibyl-log.md + notification push                                   │
│  Immediate push if ≥ 2 active alerts                                       │
└───────────────────────────────────────────────────────────────────────────┘


PHASE 2 (post Phase 1 completion — build when prerequisites are met)
─────────────────────────────────────────────────────────────────────────────
  📋 Opportunity Queue Manager ─── deferred signal queue between Router and Filters
  📐 Signal Quality Calibrator ─── post-resolution threshold recalibration (human review)
  🔬 Post-Mortem Agent ─────────── structured close analysis → SIGNAL_REGISTRY.md
```

---

## Next Actions

**Phase 1 — Build**
- [ ] **Graduate all 4 agents to main overview** — update Agent Hierarchy, database schema, and directory structure in `[[Projects/sibyl-overview]]`
- [ ] **`config/market_intelligence_config.yaml`** — create with all Mode A/B/C and composite detection defaults
- [ ] **`config/position_lifecycle_config.yaml`** — create with all sub-routine A–E defaults
- [ ] **`config/narrator_config.yaml`** — create; set `max_digest_tokens: 550`; configure ntfy.sh
- [ ] **`config/breakout_scout_config.yaml`** — create with discovery + research defaults
- [ ] **`agents/market_intelligence/`** — stub: `surveillance_loop.py` (Modes A, B, C), `analysis_queue.py`
- [ ] **`agents/position_lifecycle/`** — stub: `stop_guard.py`, `ev_monitor.py`, `exit_optimizer.py`, `resolution_tracker.py`, `correlation_scanner.py`
- [ ] **`agents/narrator/`** — stub: `narrator.py`
- [ ] **`agents/breakout_scout/`** — stub: `discovery.py`, `research_queue.py`, `freshness.py`
- [ ] **Provision API credentials** — Reddit PRAW app, X/Twitter v2 Bearer token, Perplexity API key, NewsAPI key → store in `.env`
- [ ] **Run database migrations:**
  - `CREATE TABLE market_research` (Scout research packets)
  - `CREATE TABLE whale_events` (Market Intelligence Mode A)
  - `ALTER TABLE markets ADD COLUMN event_id TEXT` (Correlation Scanner dependency)
  - `ALTER TABLE markets ADD COLUMN event_id_confidence REAL`
  - `ALTER TABLE markets ADD COLUMN discovery_source TEXT`
  - `ALTER TABLE markets ADD COLUMN breakout_score REAL`
  - `ALTER TABLE signals ADD COLUMN routing_override TEXT`
  - `ALTER TABLE signals ADD COLUMN confidence_adjusted REAL`
  - `ALTER TABLE signals ADD COLUMN counter_thesis TEXT`
  - `ALTER TABLE signals ADD COLUMN reasoning TEXT`
  - `ALTER TABLE signals ADD COLUMN scout_consensus_alignment TEXT`
  - `ALTER TABLE signals ADD COLUMN detection_modes_triggered TEXT`
  - `ALTER TABLE signals ADD COLUMN pre_entry_correlation TEXT`
- [ ] **Build event_id auto-tagger** — keyword matching in Monitor Agents assigns `event_id` + low confidence score on market ingestion; manually confirm high-stakes events before PLM Correlation Scanner acts on them
- [ ] **Resolve open question**: UNANALYZED signals (LLM timeout) — ACE hold 60s or forward immediately?
- [ ] **Resolve open question**: NEUTRAL markets with rising breakout score — auto-escalate to research with `forced_research` flag?

**Phase 2 — Preparation (do during Phase 1 operation)**
- [ ] **Monitor deferred signal rate** — if > 5% of generated signals are DEFERRED in Phase 1, prioritize Opportunity Queue Manager build for Phase 2
- [ ] **Add `positions.thesis` column** — needed by Post-Mortem Agent; populate during Phase 1 via Market Intelligence reasoning narrative so Phase 2 has clean data
- [ ] **Add `performance.post_mortem` column** — stub for Phase 2 Post-Mortem Agent writes
- [ ] **Add `calibration_recommendations` table** — stub for Phase 2 Signal Quality Calibrator outputs
- [ ] **Confirm Phase 2 build trigger**: begin Phase 2 after ≥ 60 resolved positions across both engines and ≥ 4 weeks of production data

---

## Log

**2026-03-11**: Note created. 7 graduate candidates consolidated into 3 production agent specs. 32/32 original functions preserved.

**2026-03-11**: Agent 4 (🔍 Breakout Scout) added as net-new capability with multi-source research pipeline, tiered source weighting, CONTESTED/NEUTRAL distinction, quantitative breakout scoring, freshness decay, and bidirectional integration with Market Intelligence Agent.

**2026-03-11**: Integration round 2. Volume Anomaly Detector added as Market Intelligence Detection Mode B. Order Book Depth Agent added as Market Intelligence Detection Mode C. Composite signal escalation logic added to Analysis Queue Worker (A+B, B+C, A+C, A+B+C patterns). Correlation Scanner added as Position Lifecycle Manager Sub-routine E; pre-entry correlation check and TradeBot cross-check added as capability enhancements beyond original spec; `markets.event_id` tagging schema requirement documented. Phase 2 roadmap section added for Opportunity Queue Manager, Signal Quality Calibrator, and Post-Mortem Agent with build prerequisites, integration contracts, and trigger conditions. Total source candidates 7→10. Total functions preserved: 44/44. Architecture diagram and all Next Actions updated.
