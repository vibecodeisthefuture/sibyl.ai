"""
Sibyl.ai — Autonomous Prediction Market Investing System.

This is the APPLICATION ENTRY POINT.  Run with:
    python -m sibyl                              # Start all agents (default)
    python -m sibyl --agents monitor             # Start monitor agents only
    python -m sibyl --agents intelligence        # Start intelligence agents only
    python -m sibyl --agents pipeline            # Start category signal pipelines
    python -m sibyl --agents blitz               # Start Blitz last-second scanner
    python -m sibyl --mode paper                 # Paper trading mode (default)
    python -m sibyl --mode live                  # Live trading (real money!)
    python -m sibyl --backtest                   # Run backtest on all history
    python -m sibyl --backtest --from 2026-01-01 --to 2026-03-19  # Date range

WHAT HAPPENS WHEN YOU START SIBYL:
    1. Loads all YAML configuration from config/ directory.
    2. Loads .env file with API credentials.
    3. Initializes the SQLite database (creates tables if needed).
    4. Creates and starts the requested agents as asyncio background tasks.
    5. Runs indefinitely until you press Ctrl+C or send SIGTERM.
    6. On shutdown, gracefully stops all agents and closes the database.

AGENTS STARTED:
    --agents monitor (or all):
        - PolymarketMonitorAgent: Ingests Polymarket data (read-only)
        - KalshiMonitorAgent:     Ingests Kalshi data (primary platform)
        - CrossPlatformSyncAgent: Matches markets + detects price divergences
    --agents intelligence (or all):
        - MarketIntelligenceAgent: 3 surveillance modes (Whale, Volume, OrderBook)
        - SignalGenerator:         Composite scoring, EV estimation
        - SignalRouter:            Routes signals to SGE/ACE engines
    --agents pipeline (or all):
        - PipelineAgent:  8 category signal pipelines on 15-min schedule
                          (Economics, Weather, Sports, Crypto, Culture,
                           Science, Geopolitics, Financial)
    --agents blitz (or all):
        - BlitzScanner:   1-second polling for ≤90s closing markets (>85% conf)
        - BlitzExecutor:  Fast-path market order execution (SGE_BLITZ sub-engine)
    --agents execution (or all):
        - OrderExecutor:             Signal → position via Kelly-sized orders
        - PositionLifecycleManager:  5 sub-routines managing open positions
        - EngineStateManager:        Capital allocation + circuit breaker tracking
    --agents portfolio (or all):
        - PortfolioAllocator:  Balance sync, SGE/ACE capital splits, rebalancing
        - RiskDashboard:       Aggregate risk metrics, drawdown tracking, daily P&L
        - Notifier:            Push notifications via ntfy.sh
    --agents advanced (or all):
        - BreakoutScout:     Multi-source sentiment aggregation + Perplexity research
        - Narrator:          LLM-powered portfolio health digests + alert escalation
        - XSentimentAgent:   X/Twitter 6-stage sentiment pipeline (Basic tier)

DASHBOARD:
    When --dashboard is passed, a FastAPI web server starts alongside the agents.
    Default port: 8088 (override with --dashboard-port).
    Access at: http://localhost:8088

SIGNAL HANDLING:
    - SIGINT (Ctrl+C): Graceful shutdown
    - SIGTERM (Docker): Graceful shutdown
    - Windows: Uses KeyboardInterrupt fallback (add_signal_handler not supported)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from sibyl.core.config import SibylConfig
from sibyl.core.database import DatabaseManager
from sibyl.core.logging import setup_logging

logger = logging.getLogger("sibyl")


async def main(args: argparse.Namespace) -> None:
    """Initialize database, create agents, and run until shutdown.

    This is the main async entry point.  It:
        1. Loads config from YAML + .env
        2. Creates and initializes the SQLite database
        3. Instantiates the requested agents
        4. Runs them as background asyncio tasks
        5. Waits for shutdown signal (Ctrl+C or SIGTERM)
        6. Gracefully stops all agents and closes the database
    """
    # ── Step 1: Load all configuration ────────────────────────────────
    config = SibylConfig()
    setup_logging(config.system.get("system", {}).get("log_level", "INFO"))
    logger.info(
        "Sibyl.ai v%s starting (mode=%s)",
        config.system.get("system", {}).get("version", "0.1.0"),
        config.mode,
    )

    # ── Step 2: Initialize database ───────────────────────────────────
    db = DatabaseManager(config.db_path)
    await db.initialize()
    logger.info("Database ready at %s (WAL=%s)", config.db_path, await db.get_wal_mode())

    # ── Step 3: Create agents ─────────────────────────────────────────
    agents = []
    agent_scope = args.agents if hasattr(args, "agents") else "all"

    if agent_scope in ("monitor", "all"):
        # Import agents here (not at top of file) to avoid circular imports
        # and to only load what's needed based on --agents flag.
        from sibyl.agents.monitors.polymarket_monitor import PolymarketMonitorAgent
        from sibyl.agents.monitors.kalshi_monitor import KalshiMonitorAgent
        from sibyl.agents.monitors.sync_agent import CrossPlatformSyncAgent

        agents.append(PolymarketMonitorAgent(db=db, config=config.system))
        agents.append(KalshiMonitorAgent(db=db, config=config.system))
        agents.append(CrossPlatformSyncAgent(db=db, config=config.system))

    # Intelligence layer agents (Sprint 2)
    intel_agent = None  # Shared reference so SignalGenerator can read detections
    if agent_scope in ("intelligence", "all"):
        from sibyl.agents.intelligence.market_intelligence import MarketIntelligenceAgent
        from sibyl.agents.intelligence.signal_generator import SignalGenerator
        from sibyl.agents.intelligence.signal_router import SignalRouter

        intel_agent = MarketIntelligenceAgent(db=db, config=config.system)
        agents.append(intel_agent)
        agents.append(SignalGenerator(db=db, config=config.system, intel_agent=intel_agent))
        agents.append(SignalRouter(db=db, config=config.system))

    # Category Signal Pipelines (Sprint 13 + 15)
    # Runs 8 data-driven pipelines on a 15-minute schedule, producing signals
    # from external data sources (FRED, ESPN, CoinGecko, etc.)
    if agent_scope in ("pipeline", "all"):
        from sibyl.agents.intelligence.pipeline_agent import PipelineAgent

        # CLI --categories overrides config; config is fallback
        cli_categories = getattr(args, "categories", None)
        if cli_categories:
            categories = [c.strip() for c in cli_categories.split(",")]
        else:
            pipeline_cfg = config.system.get("pipeline", {})
            categories = pipeline_cfg.get("categories", "all")
            if isinstance(categories, str) and categories != "all":
                categories = [c.strip() for c in categories.split(",")]
        agents.append(PipelineAgent(db=db, config=config.system, categories=categories))

    # Blitz Partition (Sprint 14 + 15)
    # 1-second polling for last-second high-confidence market opportunities.
    # BlitzScanner finds eligible markets; BlitzExecutor places market orders.
    if agent_scope in ("blitz", "all"):
        from sibyl.agents.sge.blitz_scanner import BlitzScanner
        from sibyl.agents.sge.blitz_executor import BlitzExecutor

        trade_mode = args.mode if hasattr(args, "mode") else "paper"
        agents.append(BlitzScanner(db=db, config=config.system))
        agents.append(BlitzExecutor(db=db, config=config.system, mode=trade_mode))

    # Execution layer agents (Sprint 3)
    if agent_scope in ("execution", "all"):
        from sibyl.agents.execution.order_executor import OrderExecutor
        from sibyl.agents.execution.position_lifecycle import PositionLifecycleManager
        from sibyl.agents.execution.engine_state_manager import EngineStateManager

        trade_mode = args.mode if hasattr(args, "mode") else "paper"
        agents.append(OrderExecutor(db=db, config=config.system, mode=trade_mode))
        agents.append(PositionLifecycleManager(db=db, config=config.system))
        agents.append(EngineStateManager(db=db, config=config.system))

    # Portfolio & Risk Management layer agents (Sprint 4)
    if agent_scope in ("portfolio", "all"):
        from sibyl.agents.allocator.portfolio_allocator import PortfolioAllocator
        from sibyl.agents.analytics.risk_dashboard import RiskDashboard
        from sibyl.agents.notifications.notifier import Notifier

        trade_mode = args.mode if hasattr(args, "mode") else "paper"
        agents.append(PortfolioAllocator(db=db, config=config.system, mode=trade_mode))
        agents.append(RiskDashboard(db=db, config=config.system))
        agents.append(Notifier(db=db, config=config.system))

    # Advanced Intelligence layer agents (Sprint 7+8)
    if agent_scope in ("advanced", "all"):
        from sibyl.agents.scout.breakout_scout import BreakoutScout
        from sibyl.agents.narrator.narrator import Narrator
        from sibyl.agents.sentiment.x_sentiment_agent import XSentimentAgent

        agents.append(BreakoutScout(db=db, config=config.system))
        agents.append(Narrator(db=db, config=config.system))
        agents.append(XSentimentAgent(db=db, config=config.system))

    if not agents:
        logger.error("No agents to run. Use --agents monitor|intelligence|execution|all")
        await db.close()
        return

    logger.info("Starting %d agent(s): %s", len(agents), [a.name for a in agents])

    # ── Step 4: Schedule agents as background tasks ───────────────────
    # Each agent.schedule() creates an asyncio.Task that runs the agent's
    # polling loop (see base_agent.py → run() method).
    tasks = [agent.schedule() for agent in agents]

    # ── Step 4b: Start dashboard server (if requested) ───────────────
    dashboard_task = None
    if getattr(args, "dashboard", False):
        from sibyl.dashboard.server import start_dashboard

        dash_port = getattr(args, "dashboard_port", 8088) or 8088
        dashboard_task = await start_dashboard(db, host="0.0.0.0", port=dash_port)
        logger.info("Dashboard available at http://0.0.0.0:%d", dash_port)

    # ── Step 5: Wait for shutdown signal ──────────────────────────────
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        """Called when SIGINT or SIGTERM is received."""
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for SIGTERM.
            # We rely on KeyboardInterrupt (Ctrl+C) instead.
            pass

    try:
        await shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

    # ── Step 6: Graceful shutdown ─────────────────────────────────────
    logger.info("Shutting down agents...")

    # Stop dashboard server first (fast shutdown)
    if dashboard_task and not dashboard_task.done():
        dashboard_task.cancel()
        try:
            await asyncio.wait_for(dashboard_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        logger.info("Dashboard stopped")

    for agent in agents:
        await agent.shutdown()  # Sets _running=False and cancels the task

    # Wait for agent tasks to finish (with a 10-second timeout each)
    for task in tasks:
        try:
            await asyncio.wait_for(task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass  # Agent didn't stop in time — move on

    await db.close()
    logger.info("Sibyl.ai shutdown complete")


def cli() -> None:
    """Parse command-line arguments and start the async event loop.

    This is the synchronous wrapper around the async main() function.
    It's called when you run `python -m sibyl`.
    """
    parser = argparse.ArgumentParser(
        description="Sibyl.ai — Prediction Market Investing System"
    )
    parser.add_argument(
        "--mode", choices=["paper", "live"], default="paper",
        help="Trading mode: 'paper' (simulated) or 'live' (real money). Default: paper."
    )
    parser.add_argument(
        "--agents",
        choices=["monitor", "intelligence", "pipeline", "blitz", "execution", "portfolio", "advanced", "all"],
        default="all",
        help=(
            "Which agents to run: 'monitor', 'intelligence', 'pipeline', 'blitz', "
            "'execution', 'portfolio', 'advanced', or 'all'. Default: all."
        ),
    )
    parser.add_argument(
        "--categories",
        type=str, default=None,
        help="Comma-separated pipeline categories to run (e.g., 'crypto,weather'). Default: all.",
    )
    parser.add_argument(
        "--dashboard", action="store_true", default=False,
        help="Start the web dashboard alongside agents (default: disabled).",
    )
    parser.add_argument(
        "--dashboard-port", type=int, default=8088,
        help="Port for the web dashboard (default: 8088).",
    )
    # ── Backtesting CLI (Sprint 10) ──────────────────────────────────
    parser.add_argument(
        "--backtest", action="store_true", default=False,
        help="Run historical backtest instead of live/paper trading.",
    )
    parser.add_argument(
        "--from", dest="backtest_from", type=str, default=None,
        help="Backtest start date (YYYY-MM-DD). Default: all history.",
    )
    parser.add_argument(
        "--to", dest="backtest_to", type=str, default=None,
        help="Backtest end date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--balance", type=float, default=500.0,
        help="Virtual starting balance for backtest (default: $500).",
    )
    args = parser.parse_args()

    if args.backtest:
        asyncio.run(run_backtest(args))
    else:
        try:
            asyncio.run(main(args))
        except KeyboardInterrupt:
            print("\nSibyl.ai interrupted.")
            sys.exit(0)


async def run_backtest(args: argparse.Namespace) -> None:
    """Run historical backtesting and print results.

    This is the entry point for `python -m sibyl --backtest`.
    It initializes the database (read-only), loads strategy configs,
    replays signals through the full pipeline, and prints results.
    """
    import json as json_mod
    from sibyl.core.config import SibylConfig
    from sibyl.backtesting.engine import BacktestEngine
    from sibyl.backtesting.category_tracker import CategoryPerformanceTracker

    config = SibylConfig()
    setup_logging("INFO")

    db = DatabaseManager(config.db_path)
    await db.initialize()

    logger.info("Starting backtest...")

    engine = BacktestEngine(
        db=db,
        starting_balance=args.balance,
    )
    await engine.initialize()

    result = await engine.run(
        start_date=args.backtest_from,
        end_date=args.backtest_to,
    )

    # Print human-readable summary
    print("\n" + result.summary() + "\n")

    # Also compute and persist category performance
    tracker = CategoryPerformanceTracker(db=db)
    stats = await tracker.compute()
    await tracker.persist(stats)

    # Write JSON results to data/backtest_results.json
    results_path = "data/backtest_results.json"
    with open(results_path, "w") as f:
        json_mod.dump(result.to_dict(), f, indent=2)
    logger.info("Full results written to %s", results_path)

    await db.close()


# This block runs when you execute: python -m sibyl
if __name__ == "__main__":
    cli()
