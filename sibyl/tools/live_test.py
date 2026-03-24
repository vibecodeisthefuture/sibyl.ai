"""
Sibyl Live 1-Hour Test Orchestrator
====================================
Runs the full signal pipeline loop every N minutes for a configurable duration.

WHAT IT DOES:
    1. Fetches ALL active Kalshi markets (all 8 categories)
    2. Seeds markets + prices into the validation DB
    3. Runs all 8 pipelines concurrently via PipelineManager
    4. Collects signals, computes EV rankings
    5. Logs everything to stdout + JSON report
    6. Sleeps, then repeats until the test window closes

NO ORDERS ARE PLACED — read-only analysis only.

USAGE:
    # Default: 1-hour test, 5-minute intervals
    python -m sibyl.tools.live_test

    # Custom: 30-minute test, 3-minute intervals, verbose
    python -m sibyl.tools.live_test --duration 30 --interval 3 --verbose

    # Quick smoke test: single cycle
    python -m sibyl.tools.live_test --cycles 1 --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from sibyl.clients.kalshi_client import KalshiClient
from sibyl.core.database import DatabaseManager
from sibyl.pipelines.pipeline_manager import PipelineManager
from sibyl.pipelines.base_pipeline import PipelineSignal

logger = logging.getLogger("sibyl.live_test")


# ── Data classes for structured reporting ────────────────────────────────

@dataclass
class CycleResult:
    """Result of a single pipeline cycle."""
    cycle_number: int
    timestamp: str
    markets_fetched: int = 0
    markets_seeded: int = 0
    markets_with_price: int = 0
    signals_total: int = 0
    signals_by_pipeline: dict[str, int] = field(default_factory=dict)
    top_signals: list[dict] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    duration_seconds: float = 0.0
    category_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass
class LiveTestReport:
    """Aggregated report across all cycles."""
    start_time: str = ""
    end_time: str = ""
    total_cycles: int = 0
    total_signals_generated: int = 0
    unique_markets_with_signals: int = 0
    signals_per_pipeline_total: dict[str, int] = field(default_factory=dict)
    best_signals_ever: list[dict] = field(default_factory=list)
    cycle_results: list[dict] = field(default_factory=list)
    pipeline_health: dict[str, dict] = field(default_factory=dict)
    avg_cycle_duration: float = 0.0
    warnings: list[str] = field(default_factory=list)


# ── Import the shared fetch/seed/classify logic ─────────────────────────
from sibyl.tools.validate_pipelines import (
    fetch_all_kalshi_markets,
    seed_markets_to_db,
    _classify_category,
)


async def run_single_cycle(
    cycle_num: int,
    kalshi: KalshiClient,
    db: DatabaseManager,
    manager: PipelineManager,
    verbose: bool = False,
) -> CycleResult:
    """Execute one full pipeline cycle: fetch → seed → analyze → report.

    Args:
        cycle_num: Current cycle number (1-based).
        kalshi: Authenticated Kalshi client.
        db: Initialized database manager.
        manager: Initialized pipeline manager.
        verbose: Include sample signal details.

    Returns:
        CycleResult with metrics for this cycle.
    """
    result = CycleResult(
        cycle_number=cycle_num,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )
    cycle_start = time.monotonic()

    # ── Step 1: Fetch all active Kalshi markets ─────────────────────
    # Gap-fill (discovering events beyond the pagination window) is
    # expensive (~600s) so we only run it on cycle 1. Subsequent cycles
    # use the fast path (~7s) since markets don't change intra-hour.
    do_gap_fill = cycle_num == 1
    logger.info(
        "Cycle %d: Fetching Kalshi markets%s...",
        cycle_num, " (with gap-fill)" if do_gap_fill else "",
    )
    try:
        events, markets = await fetch_all_kalshi_markets(
            kalshi, max_pages=15, gap_fill=do_gap_fill,
        )
        result.markets_fetched = len(markets)
    except Exception as e:
        logger.error("Cycle %d: Failed to fetch markets: %s", cycle_num, e)
        result.errors["fetch"] = str(e)
        result.duration_seconds = time.monotonic() - cycle_start
        return result

    # Category breakdown
    for m in markets:
        cat = m.get("_sibyl_category", "uncategorized")
        result.category_breakdown[cat] = result.category_breakdown.get(cat, 0) + 1

    # Count markets with price data
    for m in markets:
        for key in ("yes_ask_dollars", "yes_bid_dollars", "last_price_dollars"):
            val = m.get(key)
            if val is not None:
                try:
                    if float(val) > 0:
                        result.markets_with_price += 1
                        break
                except (ValueError, TypeError):
                    pass

    logger.info(
        "Cycle %d: %d markets (%d with price) across %d events",
        cycle_num, len(markets), result.markets_with_price, len(events),
    )

    # ── Step 2: Seed to DB ──────────────────────────────────────────
    try:
        seeded = await seed_markets_to_db(db, markets)
        result.markets_seeded = seeded
        logger.info("Cycle %d: Seeded %d markets to DB", cycle_num, seeded)
    except Exception as e:
        logger.error("Cycle %d: DB seed failed: %s", cycle_num, e)
        result.errors["seed"] = str(e)

    # ── Step 3: Run all pipelines ───────────────────────────────────
    logger.info("Cycle %d: Running all pipelines...", cycle_num)
    try:
        run_result = await manager.run_all()
    except Exception as e:
        logger.error("Cycle %d: Pipeline run failed: %s", cycle_num, e)
        result.errors["pipelines"] = str(e)
        result.duration_seconds = time.monotonic() - cycle_start
        return result

    # ── Step 4: Collect and rank signals ────────────────────────────
    all_signals: list[PipelineSignal] = []
    for pname, sigs in run_result.signals_by_pipeline.items():
        result.signals_by_pipeline[pname] = len(sigs)
        all_signals.extend(sigs)

    # Add correlation composites
    if run_result.correlation_result:
        cr = run_result.correlation_result
        all_signals.extend(cr.composite_signals)
        result.signals_by_pipeline["correlation"] = len(cr.composite_signals)

    result.signals_total = len(all_signals)
    result.errors.update(run_result.errors)

    # Rank by confidence * ev_estimate (sort by expected profitability)
    def signal_score(sig: PipelineSignal) -> float:
        ev = abs(sig.ev_estimate) if sig.ev_estimate else 0.0
        return sig.confidence * max(ev, 0.01)  # floor EV at 1% for ranking

    ranked = sorted(all_signals, key=signal_score, reverse=True)

    # Top 10 signals for the report
    for sig in ranked[:10]:
        result.top_signals.append({
            "market_id": sig.market_id,
            "signal_type": sig.signal_type,
            "confidence": round(sig.confidence, 4),
            "ev_estimate": round(sig.ev_estimate, 4),
            "direction": sig.direction,
            "pipeline": sig.source_pipeline or sig.category,
            "reasoning": sig.reasoning[:150] if sig.reasoning else "",
        })

    result.duration_seconds = time.monotonic() - cycle_start

    # ── Print cycle summary ─────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  CYCLE {cycle_num} COMPLETE — {result.timestamp}")
    print(f"{'='*70}")
    print(f"  Markets: {result.markets_fetched} fetched, {result.markets_seeded} seeded, {result.markets_with_price} with price")
    print(f"  Signals: {result.signals_total} total ({result.duration_seconds:.1f}s)")
    print(f"  By pipeline:")
    for pname, count in sorted(result.signals_by_pipeline.items()):
        status = "✓" if count > 0 else "✗"
        print(f"    {status} {pname}: {count}")
    if result.errors:
        print(f"  Errors: {len(result.errors)}")
        for ename, emsg in result.errors.items():
            print(f"    ✗ {ename}: {emsg[:80]}")
    if result.top_signals:
        print(f"\n  Top signals:")
        for i, sig in enumerate(result.top_signals[:5], 1):
            print(
                f"    {i}. [{sig['direction']}] {sig['market_id'][:30]} "
                f"conf={sig['confidence']:.2f} ev={sig['ev_estimate']:.3f} "
                f"({sig['pipeline']})"
            )
            if verbose and sig.get("reasoning"):
                print(f"       → {sig['reasoning'][:100]}")
    print(f"{'='*70}\n")

    return result


async def run_live_test(
    duration_minutes: int = 60,
    interval_minutes: int = 5,
    max_cycles: int | None = None,
    verbose: bool = False,
) -> LiveTestReport:
    """Run the full live test loop.

    Args:
        duration_minutes: Total test duration in minutes.
        interval_minutes: Minutes between cycles.
        max_cycles: If set, stop after this many cycles regardless of duration.
        verbose: Include detailed signal output.

    Returns:
        LiveTestReport with all metrics.
    """
    report = LiveTestReport(
        start_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )

    # ── Initialize infrastructure ───────────────────────────────────
    key_id = os.environ.get("KALSHI_KEY_ID")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")

    if key_path and not os.path.exists(key_path):
        logger.warning("Kalshi key file not found at %s — public-only mode", key_path)
        key_id = None
        key_path = None

    kalshi = KalshiClient(key_id=key_id, private_key_path=key_path)
    logger.info("Kalshi client ready (authenticated=%s)", kalshi.is_authenticated)

    # Use /tmp for the validation DB to avoid WAL issues on mounted filesystems
    db_path = "/tmp/sibyl_live_test.db"
    db = DatabaseManager(db_path)
    await db.initialize()
    logger.info("Database initialized at %s", db_path)

    manager = PipelineManager(db)
    ready_count = await manager.initialize()
    logger.info("PipelineManager initialized: %d pipelines ready", ready_count)

    # ── Calculate cycle count ───────────────────────────────────────
    total_cycles = max_cycles or (duration_minutes // interval_minutes)
    if total_cycles < 1:
        total_cycles = 1

    print("\n" + "=" * 70)
    print("  SIBYL LIVE TEST — STARTING")
    print(f"  Duration: {duration_minutes} min | Interval: {interval_minutes} min | Cycles: {total_cycles}")
    print(f"  Pipelines ready: {ready_count}/8")
    print(f"  Auth: {'YES' if kalshi.is_authenticated else 'PUBLIC-ONLY'}")
    print("=" * 70 + "\n")

    # ── Main loop ───────────────────────────────────────────────────
    all_market_ids_with_signals: set[str] = set()
    test_start = time.monotonic()

    for cycle in range(1, total_cycles + 1):
        # Check time limit
        elapsed = (time.monotonic() - test_start) / 60.0
        if elapsed >= duration_minutes and max_cycles is None:
            logger.info("Time limit reached (%.1f min). Stopping.", elapsed)
            break

        cycle_result = await run_single_cycle(
            cycle_num=cycle,
            kalshi=kalshi,
            db=db,
            manager=manager,
            verbose=verbose,
        )

        # Accumulate into report
        report.total_cycles += 1
        report.total_signals_generated += cycle_result.signals_total
        for pname, count in cycle_result.signals_by_pipeline.items():
            report.signals_per_pipeline_total[pname] = (
                report.signals_per_pipeline_total.get(pname, 0) + count
            )
        for sig in cycle_result.top_signals:
            all_market_ids_with_signals.add(sig["market_id"])
        report.cycle_results.append(asdict(cycle_result))

        # Track best signals across all cycles
        for sig in cycle_result.top_signals[:3]:
            report.best_signals_ever.append({
                **sig,
                "cycle": cycle,
            })

        # Sleep between cycles (skip for last cycle)
        if cycle < total_cycles:
            remaining = duration_minutes - elapsed
            if remaining > 0 and max_cycles is None:
                sleep_time = min(interval_minutes * 60, remaining * 60)
            else:
                sleep_time = interval_minutes * 60
            logger.info("Sleeping %.0f seconds until next cycle...", sleep_time)
            await asyncio.sleep(sleep_time)

    # ── Finalize report ─────────────────────────────────────────────
    report.end_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    report.unique_markets_with_signals = len(all_market_ids_with_signals)

    # Sort best signals by score
    report.best_signals_ever.sort(
        key=lambda s: s["confidence"] * max(abs(s.get("ev_estimate", 0)), 0.01),
        reverse=True,
    )
    report.best_signals_ever = report.best_signals_ever[:20]  # Top 20 overall

    # Pipeline health summary
    for pname in ["economics", "weather", "sports", "crypto", "culture",
                   "science", "geopolitics", "financial"]:
        total = report.signals_per_pipeline_total.get(pname, 0)
        cycles_active = sum(
            1 for cr in report.cycle_results
            if cr.get("signals_by_pipeline", {}).get(pname, 0) > 0
        )
        report.pipeline_health[pname] = {
            "total_signals": total,
            "cycles_active": cycles_active,
            "avg_signals_per_cycle": total / max(report.total_cycles, 1),
            "health": "HEALTHY" if cycles_active > 0 else "INACTIVE",
        }

    if report.total_cycles > 0:
        total_dur = sum(cr.get("duration_seconds", 0) for cr in report.cycle_results)
        report.avg_cycle_duration = total_dur / report.total_cycles

    # ── Print final report ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  SIBYL LIVE TEST — FINAL REPORT")
    print(f"  {report.start_time} → {report.end_time}")
    print("=" * 70)
    print(f"  Cycles completed:        {report.total_cycles}")
    print(f"  Total signals:           {report.total_signals_generated}")
    print(f"  Unique markets signaled: {report.unique_markets_with_signals}")
    print(f"  Avg cycle duration:      {report.avg_cycle_duration:.1f}s")
    print()
    print("  Pipeline Health:")
    for pname, health in sorted(report.pipeline_health.items()):
        status = "✓" if health["health"] == "HEALTHY" else "✗"
        print(
            f"    {status} {pname:15s}: {health['total_signals']:4d} signals, "
            f"{health['cycles_active']}/{report.total_cycles} cycles active, "
            f"{health['avg_signals_per_cycle']:.1f}/cycle"
        )
    print()
    if report.best_signals_ever:
        print("  Top 10 Signals (all cycles):")
        for i, sig in enumerate(report.best_signals_ever[:10], 1):
            score = sig["confidence"] * max(abs(sig.get("ev_estimate", 0)), 0.01)
            print(
                f"    {i:2d}. [{sig['direction']:3s}] {sig['market_id'][:35]:35s} "
                f"conf={sig['confidence']:.2f} ev={sig.get('ev_estimate', 0):.3f} "
                f"score={score:.4f} (cycle {sig['cycle']}, {sig['pipeline']})"
            )
    if report.warnings:
        print()
        for w in report.warnings:
            print(f"  ⚠ {w}")
    print("=" * 70)

    # ── Cleanup ─────────────────────────────────────────────────────
    await manager.close()
    await kalshi.close()
    await db.close()

    # Save JSON report
    report_path = os.path.join(project_root, "data", "live_test_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(asdict(report), f, indent=2, default=str)
    logger.info("Full report saved to %s", report_path)

    return report


async def main():
    parser = argparse.ArgumentParser(
        description="Sibyl Live Test — 1-hour pipeline validation loop"
    )
    parser.add_argument(
        "--duration", type=int, default=60,
        help="Test duration in minutes (default: 60)",
    )
    parser.add_argument(
        "--interval", type=int, default=5,
        help="Minutes between cycles (default: 5)",
    )
    parser.add_argument(
        "--cycles", type=int, default=None,
        help="Run exactly N cycles (overrides duration)",
    )
    parser.add_argument(
        "--verbose", action="store_true", default=False,
        help="Include detailed signal reasoning in output",
    )
    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    await run_live_test(
        duration_minutes=args.duration,
        interval_minutes=args.interval,
        max_cycles=args.cycles,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    asyncio.run(main())
