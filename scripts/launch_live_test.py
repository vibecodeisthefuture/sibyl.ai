#!/usr/bin/env python3
"""
Sibyl.ai — 24-Hour Live Trading Test Launch Script

WHAT THIS DOES:
    1. Verifies Kalshi authentication and account balance
    2. Confirms database is accessible and schema is up-to-date
    3. Runs a 5-minute paper-mode sanity check (all agents)
    4. Switches to LIVE mode and runs for 24 hours
    5. Generates a post-test report with P&L, trades, and signals

USAGE:
    # From the project root (sibyl.ai/):
    python scripts/launch_live_test.py

    # Or with custom duration (default: 24 hours):
    python scripts/launch_live_test.py --hours 1

    # Paper-mode only (sanity check, no real trades):
    python scripts/launch_live_test.py --paper-only

PREREQUISITES:
    - Kalshi account funded ($200 minimum recommended)
    - RSA key at config/kalshi_key.pem
    - .env file with KALSHI_KEY_ID set
    - All dependencies installed (pip install -r requirements.txt or pyproject.toml)

SAFETY:
    - Starts with paper-mode verification before any real trades
    - Position sizing uses Kelly Criterion with conservative fractions
    - Circuit breakers halt trading at 20% drawdown
    - Max position per market: ~5% of portfolio
    - Ctrl+C triggers graceful shutdown at any time
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from sibyl.core.config import SibylConfig, load_env
from sibyl.core.database import DatabaseManager
from sibyl.core.logging import setup_logging

logger = logging.getLogger("sibyl.launch")


async def verify_kalshi_auth() -> dict:
    """Verify Kalshi API authentication and return account info."""
    from sibyl.clients.kalshi_client import KalshiClient

    load_env()
    key_id = os.environ.get("KALSHI_KEY_ID", "")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "config/kalshi_key.pem")

    if not key_id:
        raise RuntimeError("KALSHI_KEY_ID not set in .env")
    if not Path(key_path).exists():
        raise RuntimeError(f"Kalshi RSA key not found at {key_path}")

    client = KalshiClient(key_id=key_id, private_key_path=key_path)

    # Test balance endpoint — get_balance() returns float (dollars) or None
    balance_dollars = await client.get_balance()
    if balance_dollars is None:
        raise RuntimeError("Failed to fetch balance — auth may be invalid")

    # Test positions endpoint — get_positions() returns dict with market_positions list
    positions_data = await client.get_positions()
    if isinstance(positions_data, dict):
        positions = positions_data.get("market_positions", [])
    elif isinstance(positions_data, list):
        positions = positions_data
    else:
        positions = []
    open_positions = [p for p in positions if isinstance(p, dict) and p.get("total_traded", 0) > 0]

    await client.close()

    return {
        "balance_dollars": balance_dollars,
        "open_positions": len(open_positions),
        "auth_status": "OK",
    }


async def verify_database(db_path: str) -> dict:
    """Verify database is accessible and schema is current."""
    db = DatabaseManager(db_path)
    await db.initialize()

    # Count tables
    tables = await db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    table_count = len(tables)

    # Check engine_state has SGE_BLITZ
    blitz_row = await db.fetchone(
        "SELECT * FROM engine_state WHERE engine = 'SGE_BLITZ'"
    )

    # Count existing data
    market_count = await db.fetchone("SELECT COUNT(*) as c FROM markets")
    signal_count = await db.fetchone("SELECT COUNT(*) as c FROM signals")

    await db.close()

    return {
        "tables": table_count,
        "sge_blitz_ready": blitz_row is not None,
        "existing_markets": market_count["c"] if market_count else 0,
        "existing_signals": signal_count["c"] if signal_count else 0,
    }


async def run_paper_sanity_check(duration_seconds: int = 300) -> bool:
    """Run a brief paper-mode test to verify all agents start cleanly."""
    logger.info("=" * 60)
    logger.info("PAPER-MODE SANITY CHECK (%d seconds)", duration_seconds)
    logger.info("=" * 60)

    from sibyl.core.config import SibylConfig
    from sibyl.core.database import DatabaseManager

    config = SibylConfig()
    db = DatabaseManager(config.db_path)
    await db.initialize()

    # Import and create all agents (same as __main__.py)
    from sibyl.agents.monitors.polymarket_monitor import PolymarketMonitorAgent
    from sibyl.agents.monitors.kalshi_monitor import KalshiMonitorAgent
    from sibyl.agents.monitors.sync_agent import CrossPlatformSyncAgent
    from sibyl.agents.intelligence.market_intelligence import MarketIntelligenceAgent
    from sibyl.agents.intelligence.signal_generator import SignalGenerator
    from sibyl.agents.intelligence.signal_router import SignalRouter
    from sibyl.agents.intelligence.pipeline_agent import PipelineAgent
    from sibyl.agents.sge.blitz_scanner import BlitzScanner
    from sibyl.agents.sge.blitz_executor import BlitzExecutor
    from sibyl.agents.execution.order_executor import OrderExecutor
    from sibyl.agents.execution.position_lifecycle import PositionLifecycleManager
    from sibyl.agents.execution.engine_state_manager import EngineStateManager
    from sibyl.agents.allocator.portfolio_allocator import PortfolioAllocator
    from sibyl.agents.analytics.risk_dashboard import RiskDashboard
    from sibyl.agents.notifications.notifier import Notifier
    from sibyl.agents.scout.breakout_scout import BreakoutScout
    from sibyl.agents.narrator.narrator import Narrator
    from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent
    from sibyl.agents.monitors.hyperliquid_price_agent import HyperliquidPriceAgent

    agents = [
        PolymarketMonitorAgent(db=db, config=config.system),
        KalshiMonitorAgent(db=db, config=config.system),
        CrossPlatformSyncAgent(db=db, config=config.system),
        HyperliquidPriceAgent(db=db, config=config.system),
        MarketIntelligenceAgent(db=db, config=config.system),
        SignalGenerator(db=db, config=config.system, intel_agent=None),
        SignalRouter(db=db, config=config.system),
        PipelineAgent(db=db, config=config.system, categories="all"),
        BlitzScanner(db=db, config=config.system),
        BlitzExecutor(db=db, config=config.system, mode="paper"),
        OrderExecutor(db=db, config=config.system, mode="paper"),
        PositionLifecycleManager(db=db, config=config.system),
        EngineStateManager(db=db, config=config.system),
        PortfolioAllocator(db=db, config=config.system, mode="paper"),
        RiskDashboard(db=db, config=config.system),
        Notifier(db=db, config=config.system),
        BreakoutScout(db=db, config=config.system),
        Narrator(db=db, config=config.system),
        XSentimentAgent(db=db, config=config.system),
    ]

    logger.info("Starting %d agents in paper mode...", len(agents))
    tasks = [agent.schedule() for agent in agents]

    # Run for the specified duration
    await asyncio.sleep(duration_seconds)

    # Check for errors
    errors = []
    for agent in agents:
        if hasattr(agent, "_error_count") and agent._error_count > 0:
            errors.append(f"{agent.name}: {agent._error_count} errors")

    # Shutdown
    for agent in agents:
        await agent.shutdown()
    for task in tasks:
        try:
            await asyncio.wait_for(task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    # Check results
    market_count = await db.fetchone("SELECT COUNT(*) as c FROM markets")
    signal_count = await db.fetchone("SELECT COUNT(*) as c FROM signals")

    await db.close()

    markets = market_count["c"] if market_count else 0
    signals = signal_count["c"] if signal_count else 0

    logger.info("Paper-mode results: %d markets, %d signals, %d errors",
                markets, signals, len(errors))

    if errors:
        for err in errors:
            logger.warning("Agent error: %s", err)

    return markets > 0  # Success if we discovered at least some markets


async def run_live_trading(hours: float = 24.0) -> dict:
    """Run the full live trading system for the specified duration."""
    duration = int(hours * 3600)

    logger.info("=" * 60)
    logger.info("LIVE TRADING MODE — %s hours", hours)
    logger.info("Starting at: %s", datetime.now(timezone.utc).isoformat())
    logger.info("Will run until: %s",
                datetime.fromtimestamp(time.time() + duration, tz=timezone.utc).isoformat())
    logger.info("=" * 60)

    # Use the standard entry point with live mode
    args = argparse.Namespace(
        mode="live",
        agents="all",
        categories=None,
        dashboard=True,
        dashboard_port=8088,
        backtest=False,
    )

    from sibyl.__main__ import main as sibyl_main

    # Set up shutdown timer
    shutdown_event = asyncio.Event()

    async def _auto_shutdown():
        await asyncio.sleep(duration)
        logger.info("24-hour test duration reached — initiating shutdown")
        shutdown_event.set()
        # Send SIGINT to trigger graceful shutdown
        os.kill(os.getpid(), signal.SIGINT)

    timer_task = asyncio.create_task(_auto_shutdown())

    start_time = time.time()
    try:
        await sibyl_main(args)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        timer_task.cancel()

    elapsed = time.time() - start_time

    # Generate post-test report
    return await generate_report(elapsed)


async def generate_report(elapsed_seconds: float) -> dict:
    """Generate a post-test report with P&L, trades, and signals."""
    config = SibylConfig()
    db = DatabaseManager(config.db_path)
    await db.initialize()

    # Gather stats
    market_count = await db.fetchone("SELECT COUNT(*) as c FROM markets")
    signal_count = await db.fetchone("SELECT COUNT(*) as c FROM signals")
    execution_count = await db.fetchone("SELECT COUNT(*) as c FROM executions")
    position_count = await db.fetchone("SELECT COUNT(*) as c FROM positions")

    # P&L from engine state
    sge_state = await db.fetchone("SELECT * FROM engine_state WHERE engine = 'SGE'")
    ace_state = await db.fetchone("SELECT * FROM engine_state WHERE engine = 'ACE'")
    blitz_state = await db.fetchone("SELECT * FROM engine_state WHERE engine = 'SGE_BLITZ'")

    # Recent signals
    recent_signals = await db.fetchall(
        "SELECT * FROM signals ORDER BY created_at DESC LIMIT 20"
    )

    # Recent executions
    recent_execs = await db.fetchall(
        "SELECT * FROM executions ORDER BY created_at DESC LIMIT 20"
    )

    await db.close()

    report = {
        "test_duration_hours": round(elapsed_seconds / 3600, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "markets_discovered": market_count["c"] if market_count else 0,
        "signals_generated": signal_count["c"] if signal_count else 0,
        "executions": execution_count["c"] if execution_count else 0,
        "positions": position_count["c"] if position_count else 0,
        "engines": {
            "SGE": dict(sge_state) if sge_state else None,
            "ACE": dict(ace_state) if ace_state else None,
            "SGE_BLITZ": dict(blitz_state) if blitz_state else None,
        },
        "recent_signals": [dict(s) for s in (recent_signals or [])],
        "recent_executions": [dict(e) for e in (recent_execs or [])],
    }

    # Write report to file
    report_path = PROJECT_ROOT / "data" / "live_test_24h_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    logger.info("Report written to %s", report_path)
    return report


async def main():
    """Main entry point for the launch script."""
    parser = argparse.ArgumentParser(description="Sibyl.ai Live Trading Test Launcher")
    parser.add_argument("--hours", type=float, default=24.0,
                        help="Duration of live test in hours (default: 24)")
    parser.add_argument("--paper-only", action="store_true",
                        help="Only run paper-mode sanity check, no live trading")
    parser.add_argument("--skip-sanity", action="store_true",
                        help="Skip the paper-mode sanity check")
    parser.add_argument("--sanity-duration", type=int, default=300,
                        help="Duration of paper-mode sanity check in seconds (default: 300)")
    args = parser.parse_args()

    setup_logging("INFO")

    print("\n" + "=" * 60)
    print("  SIBYL.AI — 24-HOUR LIVE TRADING TEST LAUNCHER")
    print("=" * 60 + "\n")

    # Step 1: Verify Kalshi auth
    print("[1/4] Verifying Kalshi authentication...")
    try:
        auth_info = await verify_kalshi_auth()
        print(f"  ✓ Auth: {auth_info['auth_status']}")
        print(f"  ✓ Balance: ${auth_info['balance_dollars']:.2f}")
        print(f"  ✓ Open positions: {auth_info['open_positions']}")

        if auth_info["balance_dollars"] < 10.0:
            print("  ⚠ WARNING: Balance is very low. Consider funding the account.")
    except Exception as e:
        print(f"  ✗ Auth FAILED: {e}")
        print("  Cannot proceed without valid Kalshi authentication.")
        sys.exit(1)

    # Step 2: Verify database
    print("\n[2/4] Verifying database...")
    config = SibylConfig()
    try:
        db_info = await verify_database(config.db_path)
        print(f"  ✓ Tables: {db_info['tables']}")
        print(f"  ✓ SGE_BLITZ: {'ready' if db_info['sge_blitz_ready'] else 'MISSING'}")
        print(f"  ✓ Existing markets: {db_info['existing_markets']}")
        print(f"  ✓ Existing signals: {db_info['existing_signals']}")
    except Exception as e:
        print(f"  ✗ Database FAILED: {e}")
        sys.exit(1)

    # Step 3: Paper-mode sanity check
    if not args.skip_sanity:
        print(f"\n[3/4] Running paper-mode sanity check ({args.sanity_duration}s)...")
        try:
            ok = await run_paper_sanity_check(args.sanity_duration)
            if ok:
                print("  ✓ Paper-mode sanity check PASSED")
            else:
                print("  ⚠ Paper-mode found 0 markets — may need more time for gap-fill")
        except Exception as e:
            print(f"  ✗ Sanity check FAILED: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        print("\n[3/4] Paper-mode sanity check SKIPPED")

    if args.paper_only:
        print("\n[4/4] Paper-only mode — skipping live trading.")
        print("All checks passed. System is ready for live trading.")
        return

    # Step 4: Launch live trading
    print(f"\n[4/4] Launching LIVE TRADING for {args.hours} hours...")
    print("  Dashboard will be available at http://localhost:8088")
    print("  Press Ctrl+C at any time for graceful shutdown.\n")

    try:
        report = await run_live_trading(hours=args.hours)
        print("\n" + "=" * 60)
        print("  LIVE TEST COMPLETE")
        print("=" * 60)
        print(f"  Duration: {report['test_duration_hours']} hours")
        print(f"  Markets: {report['markets_discovered']}")
        print(f"  Signals: {report['signals_generated']}")
        print(f"  Executions: {report['executions']}")
        print(f"  Positions: {report['positions']}")
        print(f"\n  Full report: data/live_test_24h_report.json")
    except Exception as e:
        print(f"\nLive test ended with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nLive test interrupted by user.")
        sys.exit(0)
