# Phase 3.5: Pipeline Integration & Live Calibration — Implementation Plan

**Target: Sprint 15–17**
**Prerequisite: Sprint 14 (Blitz Partition) — COMPLETE**

---

## Overview

Phase 3.5 bridges the gap between Sprint 13's offline pipeline code and a fully operational trading system. The 8 category signal pipelines, the cross-category correlation engine, and the Blitz partition are all built and tested in isolation. This phase wires them into the live system, validates them against real Kalshi data, and adds the infrastructure needed for continuous autonomous operation.

Phase 3.5 is broken into three sprints ordered by dependency chain and risk level.

---

## Sprint 15: CLI Wiring & Pipeline Scheduling

**Goal:** Make pipelines and Blitz executable from the command line and run on a configurable schedule.

### Task 15.1: Wire PipelineManager into `__main__.py`

**Files:** `sibyl/__main__.py`

Add a new `--agents pipeline` mode that:
- Imports and initializes PipelineManager
- Calls `pipeline_manager.initialize()` to set up all 8 category pipelines + correlation engine
- Runs `pipeline_manager.run_all()` on a configurable interval (default: 15 minutes)
- Registers PipelineManager as a schedulable agent via `BaseAgent` pattern (wrap in a `PipelineAgent` adapter that calls `run_all()` in its `run_cycle()`)

Acceptance criteria:
- `python -m sibyl --agents pipeline` starts all 8 pipelines
- `python -m sibyl --agents pipeline --categories crypto,weather` runs only specified pipelines
- Pipelines produce signals in the `signals` table visible to downstream agents
- Graceful shutdown stops all pipeline clients

### Task 15.2: Wire BlitzScanner + BlitzExecutor into `__main__.py`

**Files:** `sibyl/__main__.py`

Add a new `--agents blitz` mode that:
- Imports BlitzScanner and BlitzExecutor
- Initializes both with shared DatabaseManager and config
- Schedules both as concurrent BaseAgent tasks (1-second polling)
- Ensures SGE_BLITZ engine_state row exists

Also update `--agents all` to include both pipelines and Blitz.

Acceptance criteria:
- `python -m sibyl --agents blitz` starts scanner + executor
- `python -m sibyl --agents all` includes pipeline + blitz alongside monitors, intelligence, execution
- BlitzScanner logs market scans every second
- BlitzExecutor picks up BLITZ_READY signals

### Task 15.3: Pipeline Scheduling Agent

**Files:** `sibyl/agents/intelligence/pipeline_agent.py` (new)

Create a `PipelineAgent` that wraps PipelineManager in the BaseAgent lifecycle:
- `poll_interval`: Configurable via `system_config.yaml` (default 900 seconds = 15 minutes)
- `run_cycle()`: Calls `pipeline_manager.run_all()` and logs the PipelineRunResult summary
- Tracks: pipelines run, signals generated, errors per pipeline
- Health check returns pipeline status summary

Acceptance criteria:
- PipelineAgent runs on schedule and produces signals
- Each pipeline's last-run timestamp and signal count is trackable
- Errors in one pipeline don't crash others (PipelineManager already handles this)

### Task 15.4: Config Updates

**Files:** `config/system_config.yaml`

Add:
```yaml
pipeline:
  enabled: true
  run_interval_seconds: 900      # 15 minutes
  categories: "all"               # or comma-separated list
  correlation_engine: true
  max_signals_per_run: 100        # Safety cap

blitz:
  enabled: true                   # References sge_config.yaml blitz section
```

### Task 15.5: Tests

- 8–10 tests for PipelineAgent (init, scheduling, run_cycle, health check)
- 4–6 tests for CLI integration (argparse, agent creation)
- Integration test: pipeline run → signals table → signal router picks up

---

## Sprint 16: Live Validation & Signal Calibration

**Goal:** Run pipelines against real Kalshi market data and validate signal quality. Calibrate confidence thresholds per category.

### Task 16.1: Live Pipeline Validation Script

**Files:** `sibyl/tools/validate_pipelines.py` (new)

A standalone validation tool that:
1. Connects to the real Kalshi API (read-only) using KalshiClient
2. Fetches active markets across all categories
3. Runs each pipeline against the real market set
4. Compares generated signals against actual market prices
5. Reports: signals generated per category, confidence distribution, EV distribution, any errors

This is NOT paper trading — it's a one-shot validation run that checks whether the pipelines produce reasonable signals for current market conditions.

Acceptance criteria:
- Runs against live Kalshi data without placing any orders
- Reports signal count, average confidence, average EV per pipeline
- Flags any pipeline that produces 0 signals (possible data issue)
- Flags any pipeline that produces >50 signals (possible false positive flood)

### Task 16.2: Live Blitz Validation

**Files:** `sibyl/tools/validate_blitz.py` (new)

A Blitz-specific validation tool that:
1. Fetches markets closing within the next 24 hours from Kalshi
2. Simulates BlitzScanner evaluation at various time-to-close windows
3. Reports: how many markets would have been eligible at ≤90s, ≤60s, ≤30s
4. Estimates daily Blitz opportunity count by category

Acceptance criteria:
- Produces an estimate of daily Blitz trade volume
- Identifies which categories have the most Blitz-eligible markets
- No orders placed — pure analysis

### Task 16.3: Signal Deduplication Tuning

**Files:** `sibyl/pipelines/base_pipeline.py`

The current 60-minute dedup window is a one-size-fits-all default. Tune per category:
- Weather: 120 minutes (forecasts change slowly)
- Sports: 30 minutes (game state changes fast)
- Crypto: 15 minutes (price moves quickly)
- Economics: 240 minutes (data releases are sparse)
- Culture: 120 minutes
- Science: 360 minutes (FDA calendars change rarely)
- Geopolitics: 120 minutes
- Financial: 60 minutes (default)

Implementation: Add `dedup_window_minutes` to each pipeline class as a class attribute, override in `base_pipeline._write_signals()`.

### Task 16.4: Confidence Calibration Framework

**Files:** `sibyl/tools/calibrate_confidence.py` (new)

After running pipelines for 1+ weeks in paper mode, this tool:
1. Reads all generated signals from the `signals` table
2. Compares predicted confidence vs. actual market outcomes (from resolved markets)
3. Computes calibration curve: is 0.80 confidence actually correct 80% of the time?
4. Suggests per-pipeline confidence adjustments

This is a retrospective analysis tool, not a real-time agent.

### Task 16.5: Tests

- Validation script unit tests (mocked API responses)
- Dedup window tests per category
- Calibration calculation tests (synthetic data)

---

## Sprint 17: Performance Tracking & Dashboard Integration

**Goal:** Add observability for pipeline and Blitz performance. Integrate into the existing dashboard.

### Task 17.1: Pipeline Performance Table

**Files:** `sibyl/core/database.py` (schema migration)

Add a `pipeline_performance` table:
```sql
CREATE TABLE pipeline_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_name TEXT NOT NULL,
    run_timestamp TEXT NOT NULL,
    signals_generated INTEGER DEFAULT 0,
    markets_scanned INTEGER DEFAULT 0,
    avg_confidence REAL,
    avg_ev REAL,
    errors INTEGER DEFAULT 0,
    duration_seconds REAL,
    category TEXT
);
```

PipelineAgent writes a row after each pipeline run.

### Task 17.2: Blitz Performance Table

**Files:** `sibyl/core/database.py` (schema migration)

Add a `blitz_performance` table:
```sql
CREATE TABLE blitz_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    trades_executed INTEGER DEFAULT 0,
    trades_rejected INTEGER DEFAULT 0,
    markets_scanned INTEGER DEFAULT 0,
    avg_confidence REAL,
    avg_slippage_cents REAL,
    win_count INTEGER DEFAULT 0,
    loss_count INTEGER DEFAULT 0,
    total_pnl REAL DEFAULT 0,
    category_breakdown TEXT  -- JSON: {"Crypto": 3, "Sports": 2}
);
```

BlitzExecutor writes daily summary rows. Auto-calibration reads this data.

### Task 17.3: Blitz Auto-Calibration Agent

**Files:** `sibyl/agents/sge/blitz_calibrator.py` (new)

Implements the auto-calibration rule from Section 20:
- Runs weekly (every 7 days)
- Reads `blitz_performance` for the past 90 days
- If Blitz win rate < 60% over 50+ trades: raise `min_confidence` by 0.02
- If Blitz win rate > 80%: lower `min_confidence` by 0.01 (floor at 0.85)
- Writes updated threshold to `system_state` (BlitzScanner reads it on each cycle)

### Task 17.4: Dashboard Pipeline Tab

**Files:** `sibyl/dashboard/api.py`, `sibyl/dashboard/frontend.py`

Add a "Pipelines" tab to the dashboard showing:
- Per-pipeline last run time, signal count, error count
- Signal confidence distribution (histogram)
- Category heatmap: which categories are producing the most signals
- Blitz section: trades today, win rate, P&L, avg slippage

### Task 17.5: Docker Compose Updates

**Files:** `docker-compose.yaml`

Add pipeline and Blitz services:
```yaml
services:
  sibyl-pipelines:
    build: .
    command: python -m sibyl --agents pipeline
    depends_on: [sibyl-db]

  sibyl-blitz:
    build: .
    command: python -m sibyl --agents blitz
    depends_on: [sibyl-db, sibyl-pipelines]
```

### Task 17.6: Tests

- Pipeline performance table read/write tests
- Blitz performance table read/write tests
- Auto-calibration logic tests (synthetic win/loss data)
- Dashboard API endpoint tests for new pipeline/blitz routes

---

## Dependency Graph

```
Sprint 15 (CLI & Scheduling)
    │
    ├── Task 15.1: PipelineManager CLI wiring
    ├── Task 15.2: Blitz CLI wiring
    ├── Task 15.3: PipelineAgent (scheduling)
    ├── Task 15.4: Config updates
    └── Task 15.5: Tests
         │
         ▼
Sprint 16 (Live Validation & Calibration)
    │
    ├── Task 16.1: Live pipeline validation (requires 15.1)
    ├── Task 16.2: Live Blitz validation (requires 15.2)
    ├── Task 16.3: Dedup tuning (independent)
    ├── Task 16.4: Confidence calibration framework (requires 16.1 running for 1+ weeks)
    └── Task 16.5: Tests
         │
         ▼
Sprint 17 (Performance & Dashboard)
    │
    ├── Task 17.1: Pipeline performance table (requires 15.3)
    ├── Task 17.2: Blitz performance table (requires 15.2)
    ├── Task 17.3: Blitz auto-calibrator (requires 17.2)
    ├── Task 17.4: Dashboard pipeline tab (requires 17.1, 17.2)
    ├── Task 17.5: Docker Compose (requires 15.1, 15.2)
    └── Task 17.6: Tests
```

---

## Estimated Effort

| Sprint | Tasks | New Files | Modified Files | Est. Tests | Est. Time |
|--------|-------|-----------|----------------|------------|-----------|
| 15 | 5 | 2 | 3 | 15–20 | 1 session |
| 16 | 5 | 3 | 1 | 12–15 | 1 session |
| 17 | 6 | 3 | 4 | 15–20 | 1–2 sessions |
| **Total** | **16** | **8** | **8** | **42–55** | **3–4 sessions** |

---

## Success Criteria

Phase 3.5 is complete when:

1. `python -m sibyl --agents all` starts the full system including pipelines and Blitz
2. Pipelines run on a 15-minute schedule and produce signals for all 8 categories
3. BlitzScanner identifies and scores ≤90s closing markets at 1-second frequency
4. BlitzExecutor converts high-confidence opportunities into paper positions
5. Live validation confirms pipelines produce reasonable signals for real Kalshi markets
6. Per-category dedup windows are calibrated
7. Pipeline and Blitz performance data is persisted and visible on the dashboard
8. Blitz auto-calibration adjusts confidence thresholds based on historical performance
9. Docker Compose can run pipelines and Blitz as separate containers
10. All new code has test coverage (target: 500+ total tests)
