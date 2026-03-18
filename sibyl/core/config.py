"""
YAML configuration loader with .env credential merging.

This module handles loading all configuration for the Sibyl system:

  1. YAML CONFIG FILES: Located in the `config/` directory at project root.
     Each agent has its own YAML file (e.g., `sge_config.yaml`).
     The `system_config.yaml` file contains global settings.

  2. ENVIRONMENT VARIABLES: Secrets (API keys, passwords) are stored in
     a `.env` file at the project root.  python-dotenv reads this file
     and sets the values as environment variables.

  3. SibylConfig CLASS: A convenience container that loads ALL config files
     in one shot.  Used by __main__.py to pass settings to agents.

DIRECTORY STRUCTURE:
    sibyl.ai/
    ├── .env                          ← Secrets (git-ignored)
    ├── .env.example                  ← Template showing required keys
    └── config/
        ├── system_config.yaml        ← Global settings (polling, platforms)
        ├── sge_config.yaml           ← Stable Growth Engine settings
        ├── ace_config.yaml           ← Alpha Capture Engine settings
        ├── market_intelligence_config.yaml
        ├── position_lifecycle_config.yaml
        ├── narrator_config.yaml
        ├── breakout_scout_config.yaml
        └── markets_watchlist.yaml    ← Manually curated markets
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger("sibyl.config")

# Compute paths relative to this file's location.
# This file is at: sibyl/core/config.py
# So parent.parent.parent gets us to the project root.
_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_config_dir() -> Path:
    """Locate the config directory.

    Looks in two places:
      1. Relative to this file (works when installed as a package)
      2. Relative to the current working directory (works during dev)
    """
    if _CONFIG_DIR.is_dir():
        return _CONFIG_DIR
    # Fallback: look from CWD (useful when running from project root)
    cwd_config = Path.cwd() / "config"
    if cwd_config.is_dir():
        return cwd_config
    raise FileNotFoundError(
        f"Config directory not found at {_CONFIG_DIR} or {cwd_config}"
    )


def load_yaml(filename: str) -> dict[str, Any]:
    """Load a single YAML config file from the config directory.

    Args:
        filename: Name of the YAML file (e.g., "system_config.yaml").

    Returns:
        Parsed YAML as a Python dict.

    Raises:
        FileNotFoundError: If the file doesn't exist.
    """
    config_dir = _find_config_dir()
    filepath = config_dir / filename
    if not filepath.exists():
        raise FileNotFoundError(f"Config file not found: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}  # safe_load returns None for empty files
    logger.debug("Loaded config: %s", filename)
    return data


def load_env() -> None:
    """Load the .env file from the project root into os.environ.

    This makes secrets available via os.environ.get("KEY_NAME").
    If no .env file exists, a warning is logged but execution continues.
    """
    env_path = _PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.debug("Loaded .env from %s", env_path)
    else:
        logger.warning("No .env file found at %s", env_path)


def get_credential(key: str, required: bool = True) -> str:
    """Get a credential from environment variables.

    Args:
        key:      Environment variable name (e.g., "KALSHI_KEY_ID").
        required: If True, raises EnvironmentError when the key is missing.

    Returns:
        The credential value, or empty string if not required and missing.
    """
    value = os.environ.get(key, "")
    if required and not value:
        raise EnvironmentError(
            f"Required credential '{key}' not set. "
            f"Add it to your .env file or set it as an environment variable."
        )
    return value


def load_system_config() -> dict[str, Any]:
    """Load the main system configuration (system_config.yaml).

    Also loads .env to ensure environment variables are available.
    """
    load_env()
    return load_yaml("system_config.yaml")


def load_engine_config(engine: str) -> dict[str, Any]:
    """Load SGE or ACE engine configuration.

    Args:
        engine: Either 'sge' or 'ace' (case-insensitive).

    Returns:
        Parsed config dict from sge_config.yaml or ace_config.yaml.
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

    Returns:
        Parsed config dict for that agent.
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
    """Aggregated configuration container for the full system.

    Loads ALL configuration files in one shot.  Pass this to agents so they
    can access any config they need.

    Usage:
        config = SibylConfig()
        print(config.db_path)       # "data/sibyl.db"
        print(config.mode)          # "paper"
        print(config.sge["kelly_fraction"])  # Access SGE-specific settings

    Properties:
        db_path: Path to the SQLite database file.
        mode:    "paper" or "live" trading mode.
        is_live: True if mode is "live".
    """

    def __init__(self) -> None:
        """Load all config files and .env credentials."""
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
        """Path to the SQLite database file (default: 'data/sibyl.db')."""
        return self.system.get("database", {}).get("path", "data/sibyl.db")

    @property
    def mode(self) -> str:
        """Trading mode: 'paper' (simulated) or 'live' (real money)."""
        return self.system.get("system", {}).get("mode", "paper")

    @property
    def is_live(self) -> bool:
        """True if the system is running in live trading mode."""
        return self.mode == "live"
