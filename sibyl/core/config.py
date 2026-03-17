"""YAML configuration loader with .env credential merging."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger("sibyl.config")

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_config_dir() -> Path:
    """Locate the config directory relative to project root."""
    if _CONFIG_DIR.is_dir():
        return _CONFIG_DIR
    # Fallback: look from CWD
    cwd_config = Path.cwd() / "config"
    if cwd_config.is_dir():
        return cwd_config
    raise FileNotFoundError(
        f"Config directory not found at {_CONFIG_DIR} or {cwd_config}"
    )


def load_yaml(filename: str) -> dict[str, Any]:
    """Load a single YAML config file from the config directory."""
    config_dir = _find_config_dir()
    filepath = config_dir / filename
    if not filepath.exists():
        raise FileNotFoundError(f"Config file not found: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    logger.debug("Loaded config: %s", filename)
    return data


def load_env() -> None:
    """Load .env file from project root."""
    env_path = _PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.debug("Loaded .env from %s", env_path)
    else:
        logger.warning("No .env file found at %s", env_path)


def get_credential(key: str, required: bool = True) -> str:
    """Get a credential from environment variables."""
    value = os.environ.get(key, "")
    if required and not value:
        raise EnvironmentError(
            f"Required credential '{key}' not set. "
            f"Add it to your .env file or set it as an environment variable."
        )
    return value


def load_system_config() -> dict[str, Any]:
    """Load the main system configuration."""
    load_env()
    return load_yaml("system_config.yaml")


def load_engine_config(engine: str) -> dict[str, Any]:
    """Load SGE or ACE engine configuration.

    Args:
        engine: Either 'sge' or 'ace' (case-insensitive).
    """
    name = engine.lower()
    if name not in ("sge", "ace"):
        raise ValueError(f"Unknown engine: {engine}. Must be 'sge' or 'ace'.")
    return load_yaml(f"{name}_config.yaml")


def load_agent_config(agent_name: str) -> dict[str, Any]:
    """Load an agent-specific configuration file.

    Args:
        agent_name: One of 'market_intelligence', 'position_lifecycle',
                    'narrator', 'breakout_scout'.
    """
    valid = {
        "market_intelligence",
        "position_lifecycle",
        "narrator",
        "breakout_scout",
    }
    if agent_name not in valid:
        raise ValueError(f"Unknown agent config: {agent_name}. Valid: {valid}")
    return load_yaml(f"{agent_name}_config.yaml")


class SibylConfig:
    """Aggregated configuration container for the full system."""

    def __init__(self) -> None:
        load_env()
        self.system = load_yaml("system_config.yaml")
        self.sge = load_yaml("sge_config.yaml")
        self.ace = load_yaml("ace_config.yaml")
        self.market_intelligence = load_yaml("market_intelligence_config.yaml")
        self.position_lifecycle = load_yaml("position_lifecycle_config.yaml")
        self.narrator = load_yaml("narrator_config.yaml")
        self.breakout_scout = load_yaml("breakout_scout_config.yaml")
        logger.info("All configuration files loaded successfully.")

    @property
    def db_path(self) -> str:
        return self.system.get("database", {}).get("path", "data/sibyl.db")

    @property
    def mode(self) -> str:
        return self.system.get("system", {}).get("mode", "paper")

    @property
    def is_live(self) -> bool:
        return self.mode == "live"
