"""Structured logging setup for Sibyl."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "agent"):
            log_entry["agent"] = record.agent
        if hasattr(record, "engine"):
            log_entry["engine"] = record.engine
        return json.dumps(log_entry)


class ConsoleFormatter(logging.Formatter):
    """Human-readable console formatter with color support."""

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
    """Configure structured logging with console and optional JSON file output."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Root sibyl logger
    root_logger = logging.getLogger("sibyl")
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # Console handler — human-readable
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(ConsoleFormatter())
    root_logger.addHandler(console)

    # JSON file handler — structured logs for Grafana/Loki
    if json_file:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_path / "sibyl.jsonl", encoding="utf-8"
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(file_handler)

    # Silence noisy third-party loggers
    for name in ("aiohttp", "websockets", "httpx", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_agent_logger(agent_name: str, engine: str | None = None) -> logging.Logger:
    """Create a logger with agent context attached to all records."""
    logger = logging.getLogger(f"sibyl.{agent_name}")

    class AgentFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.agent = agent_name  # type: ignore[attr-defined]
            if engine:
                record.engine = engine  # type: ignore[attr-defined]
            return True

    # Avoid duplicate filters
    if not any(isinstance(f, AgentFilter) for f in logger.filters):
        logger.addFilter(AgentFilter())
    return logger
