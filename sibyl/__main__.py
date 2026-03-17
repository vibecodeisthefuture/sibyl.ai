"""Sibyl.ai — Autonomous Prediction Market Investing System.

Entry point for running the full agent suite.
Usage: python -m sibyl [--mode paper|live] [--agents monitor|all]
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
    """Initialize database and run agents."""
    config = SibylConfig()
    setup_logging(config.system.get("system", {}).get("log_level", "INFO"))
    logger.info("Sibyl.ai v%s starting (mode=%s)", config.system.get("system", {}).get("version", "0.1.0"), config.mode)

    # ── Database ──────────────────────────────────────────────────────
    db = DatabaseManager(config.db_path)
    await db.initialize()
    logger.info("Database ready at %s (WAL=%s)", config.db_path, await db.get_wal_mode())

    # ── Agents ────────────────────────────────────────────────────────
    agents = []
    agent_scope = args.agents if hasattr(args, "agents") else "all"

    if agent_scope in ("monitor", "all"):
        from sibyl.agents.monitors.polymarket_monitor import PolymarketMonitorAgent
        from sibyl.agents.monitors.kalshi_monitor import KalshiMonitorAgent
        from sibyl.agents.monitors.sync_agent import CrossPlatformSyncAgent

        agents.append(PolymarketMonitorAgent(db=db, config=config.system))
        agents.append(KalshiMonitorAgent(db=db, config=config.system))
        agents.append(CrossPlatformSyncAgent(db=db, config=config.system))

    if not agents:
        logger.error("No agents to run. Use --agents monitor|all")
        await db.close()
        return

    logger.info("Starting %d agent(s): %s", len(agents), [a.name for a in agents])

    # Schedule all agents
    tasks = [agent.schedule() for agent in agents]

    # ── Graceful shutdown ─────────────────────────────────────────────
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for SIGTERM
            pass

    # Wait for shutdown
    try:
        await shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

    # Stop all agents
    logger.info("Shutting down agents...")
    for agent in agents:
        await agent.shutdown()

    # Wait for tasks to complete
    for task in tasks:
        try:
            await asyncio.wait_for(task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    await db.close()
    logger.info("Sibyl.ai shutdown complete")


def cli() -> None:
    parser = argparse.ArgumentParser(description="Sibyl.ai — Prediction Market Investing System")
    parser.add_argument(
        "--mode", choices=["paper", "live"], default="paper",
        help="Trading mode (default: paper)"
    )
    parser.add_argument(
        "--agents", choices=["monitor", "all"], default="all",
        help="Which agents to run (default: all)"
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nSibyl.ai interrupted.")
        sys.exit(0)


if __name__ == "__main__":
    cli()
