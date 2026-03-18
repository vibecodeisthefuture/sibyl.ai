"""
Sibyl.ai — Autonomous Prediction Market Investing System.

This is the APPLICATION ENTRY POINT.  Run with:
    python -m sibyl                              # Start all agents (default)
    python -m sibyl --agents monitor             # Start monitor agents only
    python -m sibyl --agents intelligence        # Start intelligence agents only
    python -m sibyl --mode paper                 # Paper trading mode (default)
    python -m sibyl --mode live                  # Live trading (real money!)

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
    --agents execution (or all):
        - OrderExecutor:             Signal → position via Kelly-sized orders
        - PositionLifecycleManager:  5 sub-routines managing open positions
        - EngineStateManager:        Capital allocation + circuit breaker tracking

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

    # Execution layer agents (Sprint 3)
    if agent_scope in ("execution", "all"):
        from sibyl.agents.execution.order_executor import OrderExecutor
        from sibyl.agents.execution.position_lifecycle import PositionLifecycleManager
        from sibyl.agents.execution.engine_state_manager import EngineStateManager

        trade_mode = args.mode if hasattr(args, "mode") else "paper"
        agents.append(OrderExecutor(db=db, config=config.system, mode=trade_mode))
        agents.append(PositionLifecycleManager(db=db, config=config.system))
        agents.append(EngineStateManager(db=db, config=config.system))

    if not agents:
        logger.error("No agents to run. Use --agents monitor|intelligence|execution|all")
        await db.close()
        return

    logger.info("Starting %d agent(s): %s", len(agents), [a.name for a in agents])

    # ── Step 4: Schedule agents as background tasks ───────────────────
    # Each agent.schedule() creates an asyncio.Task that runs the agent's
    # polling loop (see base_agent.py → run() method).
    tasks = [agent.schedule() for agent in agents]

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
        "--agents", choices=["monitor", "intelligence", "execution", "all"], default="all",
        help="Which agents to run: 'monitor', 'intelligence', 'execution', or 'all'. Default: all."
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nSibyl.ai interrupted.")
        sys.exit(0)


# This block runs when you execute: python -m sibyl
if __name__ == "__main__":
    cli()
