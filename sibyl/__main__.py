"""Sibyl entrypoint — python -m sibyl."""

import asyncio
import logging
import signal
import sys

from sibyl.core.config import load_system_config
from sibyl.core.database import DatabaseManager
from sibyl.core.logging import setup_logging


async def main() -> None:
    """Initialize Sibyl and run the agent orchestrator."""
    config = load_system_config()
    setup_logging(config.get("system", {}).get("log_level", "INFO"))

    logger = logging.getLogger("sibyl")
    logger.info(
        "╔══════════════════════════════════════════╗\n"
        "║          🔮  SIBYL v%s                ║\n"
        "║   Prediction Market Investing System     ║\n"
        "║   Mode: %-33s║\n"
        "╚══════════════════════════════════════════╝",
        "0.1.0",
        config.get("system", {}).get("mode", "paper"),
    )

    # Initialize database
    db = DatabaseManager(config.get("database", {}).get("path", "data/sibyl.db"))
    await db.initialize()
    logger.info("Database initialized: %s", db.db_path)

    # TODO: Sprint 1+ — start agent processes here
    logger.info("No agents configured yet. Exiting gracefully.")
    await db.close()


def _handle_signal(sig: signal.Signals, _frame: object) -> None:
    logging.getLogger("sibyl").info("Received %s — shutting down.", sig.name)
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    asyncio.run(main())
