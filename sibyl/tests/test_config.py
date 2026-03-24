"""Tests for configuration loading."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from sibyl.core.config import load_yaml, load_system_config, load_engine_config, SibylConfig


@pytest.fixture
def config_dir(tmp_path):
    """Create a temporary config directory with minimal configs."""
    config = tmp_path / "config"
    config.mkdir()

    # system_config.yaml
    system = {
        "system": {"name": "sibyl", "mode": "paper", "log_level": "DEBUG"},
        "database": {"path": "data/test.db"},
    }
    (config / "system_config.yaml").write_text(yaml.dump(system))

    # sge_config.yaml
    sge = {
        "engine": {"name": "SGE", "capital_allocation_pct": 0.70},
        "risk_policy": {"kelly_fraction": 0.15, "min_ev_threshold": 0.03},
    }
    (config / "sge_config.yaml").write_text(yaml.dump(sge))

    # ace_config.yaml
    ace = {
        "engine": {"name": "ACE", "capital_allocation_pct": 0.30},
        "risk_policy": {"kelly_fraction": 0.35, "min_ev_threshold": 0.06},
    }
    (config / "ace_config.yaml").write_text(yaml.dump(ace))

    # Agent configs
    for name in [
        "market_intelligence_config.yaml",
        "position_lifecycle_config.yaml",
        "narrator_config.yaml",
        "breakout_scout_config.yaml",
        "portfolio_allocator_config.yaml",
        "risk_dashboard_config.yaml",
    ]:
        (config / name).write_text(yaml.dump({"test": True}))

    return tmp_path


def test_load_yaml_success(config_dir):
    """Test loading a valid YAML config."""
    with patch("sibyl.core.config._find_config_dir", return_value=config_dir / "config"):
        data = load_yaml("system_config.yaml")
        assert data["system"]["name"] == "sibyl"
        assert data["system"]["mode"] == "paper"


def test_load_yaml_missing_file(config_dir):
    """Test that loading a nonexistent config raises FileNotFoundError."""
    with patch("sibyl.core.config._find_config_dir", return_value=config_dir / "config"):
        with pytest.raises(FileNotFoundError):
            load_yaml("nonexistent_config.yaml")


def test_load_engine_config_sge(config_dir):
    """Test loading SGE engine config."""
    with patch("sibyl.core.config._find_config_dir", return_value=config_dir / "config"):
        sge = load_engine_config("sge")
        assert sge["engine"]["capital_allocation_pct"] == 0.70
        assert sge["risk_policy"]["kelly_fraction"] == 0.15


def test_load_engine_config_ace(config_dir):
    """Test loading ACE engine config."""
    with patch("sibyl.core.config._find_config_dir", return_value=config_dir / "config"):
        ace = load_engine_config("ace")
        assert ace["engine"]["capital_allocation_pct"] == 0.30
        assert ace["risk_policy"]["kelly_fraction"] == 0.35


def test_load_engine_config_invalid():
    """Test that an invalid engine name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown engine"):
        load_engine_config("invalid")


def test_sibyl_config_aggregation(config_dir):
    """Test SibylConfig loads and aggregates all configs."""
    with (
        patch("sibyl.core.config._find_config_dir", return_value=config_dir / "config"),
        patch("sibyl.core.config._PROJECT_ROOT", config_dir),
    ):
        cfg = SibylConfig()
        assert cfg.system["system"]["name"] == "sibyl"
        assert cfg.sge["engine"]["name"] == "SGE"
        assert cfg.ace["engine"]["name"] == "ACE"
        assert cfg.mode == "paper"
        assert cfg.is_live is False
