"""
Structured logging setup for Sibyl.

This module configures logging for the entire Sibyl system:

  1. CONSOLE OUTPUT: Human-readable, color-coded log messages for development.
  2. JSON FILE OUTPUT: Machine-parseable JSONL logs for monitoring tools
     (Grafana, Loki, etc.) saved to `logs/sibyl.jsonl`.
  3. AGENT CONTEXT: Each agent gets a logger with its name automatically
     attached to every log record, so you can filter logs by agent.

HOW TO USE:
    # At application startup (done in __main__.py):
    setup_logging("INFO")

    # In any module:
    import logging
    logger = logging.getLogger("sibyl.my_module")
    logger.info("Something happened")

    # In agents (handled by BaseAgent):
    self.logger.info("Agent-specific message")  # Automatically tagged

LOG LEVELS (from least to most severe):
    DEBUG    — Detailed diagnostic information (noisy, for debugging)
    INFO     — Normal operation confirmations
    WARNING  — Something unexpected but not broken
    ERROR    — Something failed, but the system continues
    CRITICAL — System-wide failure
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects.

    Output example:
        {"timestamp": "2026-03-17T23:00:00Z", "level": "INFO",
         "logger": "sibyl.kalshi_monitor", "message": "Refreshed 50 markets"}

    Used for the file handler so logs can be ingested by Grafana/Loki.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Include exception traceback if present
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Include agent context if attached (see get_agent_logger below)
        if hasattr(record, "agent"):
            log_entry["agent"] = record.agent
        if hasattr(record, "engine"):
            log_entry["engine"] = record.engine
        return json.dumps(log_entry)


class ConsoleFormatter(logging.Formatter):
    """Human-readable colored console formatter.

    Output example:
        23:00:00 INFO     [kalshi_monitor] Refreshed 50 markets

    Colors:
        DEBUG=Cyan, INFO=Green, WARNING=Yellow, ERROR=Red, CRITICAL=Magenta
    """

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        # Show agent name prefix if available (e.g., "[kalshi_monitor]")
        prefix = ""
        if hasattr(record, "agent"):
            prefix = f"[{record.agent}] "

        return (
            f"{color}{ts} {record.levelname:<8}{self.RESET} "
            f"{prefix}{record.getMessage()}"
        )


def setup_logging(
    level: str = "INFO",
    log_dir: str = "logs",
    json_file: bool = True,
) -> None:
    """Configure structured logging with console and optional JSON file output.

    Args:
        level:     Minimum log level ("DEBUG", "INFO", "WARNING", "ERROR").
        log_dir:   Directory to write JSON log files.
        json_file: If True, also write structured logs to logs/sibyl.jsonl.

    This function should be called ONCE at application startup.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Get (or create) the root "sibyl" logger — all child loggers inherit from this
    root_logger = logging.getLogger("sibyl")
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()  # Remove any existing handlers to avoid duplicates

    # Console handler — human-readable colored output to stdout
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(ConsoleFormatter())
    root_logger.addHandler(console)

    # JSON file handler — machine-parseable structured logs
    if json_file:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_path / "sibyl.jsonl", encoding="utf-8"
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(file_handler)

    # Silence noisy third-party loggers (they generate too much DEBUG output)
    for name in ("aiohttp", "websockets", "httpx", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_agent_logger(agent_name: str, engine: str | None = None) -> logging.Logger:
    """Create a logger with agent context automatically attached to all records.

    This is called by BaseAgent.__init__, so you don't need to call it directly.

    How it works:
        A custom logging.Filter is attached to the logger that adds
        `record.agent` and `record.engine` to every log record.
        The formatters above use these fields to show agent-specific context.

    Args:
        agent_name: Name of the agent (e.g., "kalshi_monitor").
        engine:     Optional engine tag (e.g., "SGE", "ACE").

    Returns:
        A configured logger instance.
    """
    logger = logging.getLogger(f"sibyl.{agent_name}")

    class AgentFilter(logging.Filter):
        """Attaches agent metadata to every log record."""
        def filter(self, record: logging.LogRecord) -> bool:
            record.agent = agent_name  # type: ignore[attr-defined]
            if engine:
                record.engine = engine  # type: ignore[attr-defined]
            return True  # Always pass the record through

    # Avoid adding duplicate filters if get_agent_logger is called multiple times
    if not any(isinstance(f, AgentFilter) for f in logger.filters):
        logger.addFilter(AgentFilter())
    return logger
