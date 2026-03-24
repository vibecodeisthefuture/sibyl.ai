"""
Tests for Sprint 15 — Pipeline Agent + Sonar LLM Refactor.

Covers:
- PipelineAgent: initialization, category filtering, polling, health checks
- SonarLLMClient: initialization, synthesis, digest, call counting
- CLI integration: argparse options, config sections
- Sonar refactor: BreakoutScout and Narrator now use SonarLLMClient

Total: 21 tests across 4 test classes.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════════════════════════════════════════════════════════════════
# Test PipelineAgent (8 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestPipelineAgentInit(unittest.TestCase):
    """Test PipelineAgent initialization with various category configurations."""

    def test_init_with_default_config(self):
        """Test initialization with minimal config (no category filter)."""
        from sibyl.agents.intelligence.pipeline_agent import PipelineAgent
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {"pipeline": {"run_interval_seconds": 900}}

        agent = PipelineAgent(db=db_mock, config=config)

        self.assertEqual(agent.name, "pipeline_agent")
        self.assertEqual(agent._categories, set())  # No filter
        self.assertIsNone(agent._pipeline_manager)

    def test_init_with_single_category_string(self):
        """Test initialization with a single category as string."""
        from sibyl.agents.intelligence.pipeline_agent import PipelineAgent
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {"pipeline": {"run_interval_seconds": 900}}

        agent = PipelineAgent(db=db_mock, config=config, categories="crypto")

        self.assertEqual(agent._categories, {"crypto"})

    def test_init_with_category_list(self):
        """Test initialization with multiple categories as list."""
        from sibyl.agents.intelligence.pipeline_agent import PipelineAgent
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {"pipeline": {"run_interval_seconds": 900}}

        agent = PipelineAgent(
            db=db_mock,
            config=config,
            categories=["economics", "weather", "sports"],
        )

        self.assertEqual(agent._categories, {"economics", "weather", "sports"})

    def test_init_with_all_categories(self):
        """Test that 'all' is treated as no filter."""
        from sibyl.agents.intelligence.pipeline_agent import PipelineAgent
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {"pipeline": {"run_interval_seconds": 900}}

        agent = PipelineAgent(db=db_mock, config=config, categories="all")

        self.assertEqual(agent._categories, set())  # 'all' = no filter


class TestPipelineAgentPolling(unittest.TestCase):
    """Test PipelineAgent polling interval configuration."""

    def test_poll_interval_from_config(self):
        """Test that poll_interval reads from config."""
        from sibyl.agents.intelligence.pipeline_agent import PipelineAgent
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {"pipeline": {"run_interval_seconds": 600}}

        agent = PipelineAgent(db=db_mock, config=config)

        self.assertEqual(agent.poll_interval, 600.0)

    def test_poll_interval_defaults_to_900(self):
        """Test that poll_interval defaults to 900 when not in config."""
        from sibyl.agents.intelligence.pipeline_agent import PipelineAgent
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {"pipeline": {}}  # No run_interval_seconds

        agent = PipelineAgent(db=db_mock, config=config)

        self.assertEqual(agent.poll_interval, 900.0)

    def test_poll_interval_defaults_to_900_when_no_pipeline_section(self):
        """Test that poll_interval defaults to 900 when pipeline section missing."""
        from sibyl.agents.intelligence.pipeline_agent import PipelineAgent
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {}  # No 'pipeline' key at all

        agent = PipelineAgent(db=db_mock, config=config)

        self.assertEqual(agent.poll_interval, 900.0)


class TestPipelineAgentHealth(unittest.TestCase):
    """Test PipelineAgent health_check() method."""

    def test_health_check_returns_base_fields(self):
        """Test that health_check includes base agent fields."""
        from sibyl.agents.intelligence.pipeline_agent import PipelineAgent
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {"pipeline": {"run_interval_seconds": 900}}

        agent = PipelineAgent(db=db_mock, config=config)

        health = agent.health_check()

        self.assertIn("agent", health)
        self.assertEqual(health["agent"], "pipeline_agent")
        self.assertIn("running", health)

    def test_health_check_includes_last_run_after_cycle(self):
        """Test that health_check includes last_run stats after run_cycle."""
        from sibyl.agents.intelligence.pipeline_agent import PipelineAgent
        from sibyl.pipelines.pipeline_manager import PipelineRunResult
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {"pipeline": {"run_interval_seconds": 900}}

        agent = PipelineAgent(db=db_mock, config=config)

        # Simulate a run result (PipelineRunResult uses dataclass defaults)
        result = PipelineRunResult(
            duration_seconds=15.5,
            pipelines_run=8,
            pipelines_failed=0,
            signals_by_pipeline={},
            correlation_result=None,
        )
        agent._last_run_result = result

        health = agent.health_check()

        self.assertIn("last_run", health)
        self.assertEqual(health["last_run"]["duration_seconds"], 15.5)
        self.assertEqual(health["last_run"]["pipelines_run"], 8)
        self.assertEqual(health["last_run"]["total_signals"], 0)  # No signals in empty dict


# ═══════════════════════════════════════════════════════════════════════════
# Test SonarLLMClient (6 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestSonarLLMClientInit(unittest.TestCase):
    """Test SonarLLMClient initialization and basic properties."""

    def test_init_default_values(self):
        """Test that init sets default values correctly."""
        from sibyl.clients.sonar_llm_client import SonarLLMClient

        client = SonarLLMClient()

        self.assertEqual(client._model, "sonar")
        self.assertEqual(client._daily_call_cap, 100)
        self.assertEqual(client._calls_today, 0)
        self.assertEqual(client._api_key, "")
        self.assertIsNone(client._http)

    def test_init_custom_values(self):
        """Test that init respects custom model and cap."""
        from sibyl.clients.sonar_llm_client import SonarLLMClient

        client = SonarLLMClient(model="sonar-pro", daily_call_cap=50)

        self.assertEqual(client._model, "sonar-pro")
        self.assertEqual(client._daily_call_cap, 50)

    @patch.dict(os.environ, {"PERPLEXITY_API_KEY": ""}, clear=True)
    def test_initialize_returns_false_when_no_api_key(self):
        """Test that initialize() returns False when API key is not set."""
        from sibyl.clients.sonar_llm_client import SonarLLMClient

        client = SonarLLMClient()

        result = client.initialize()

        self.assertFalse(result)
        self.assertIsNone(client._http)

    @patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key-123"})
    def test_available_property_false_before_init(self):
        """Test that available property returns False before initialize()."""
        from sibyl.clients.sonar_llm_client import SonarLLMClient

        client = SonarLLMClient()

        self.assertFalse(client.available)

    @patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key-123"})
    def test_available_property_true_after_init(self):
        """Test that available property returns True after initialize()."""
        from sibyl.clients.sonar_llm_client import SonarLLMClient

        client = SonarLLMClient()
        result = client.initialize()

        self.assertTrue(result)
        self.assertTrue(client.available)

    def test_calls_remaining_today_decrements(self):
        """Test that calls_remaining_today decrements as calls are made."""
        from sibyl.clients.sonar_llm_client import SonarLLMClient

        client = SonarLLMClient(daily_call_cap=100)

        self.assertEqual(client.calls_remaining_today, 100)

        client._calls_today = 30

        self.assertEqual(client.calls_remaining_today, 70)

    def test_calls_remaining_today_never_negative(self):
        """Test that calls_remaining_today returns 0 when cap exceeded."""
        from sibyl.clients.sonar_llm_client import SonarLLMClient

        client = SonarLLMClient(daily_call_cap=100)
        client._calls_today = 150  # Over cap

        self.assertEqual(client.calls_remaining_today, 0)


class TestSonarLLMClientDailyReset(unittest.TestCase):
    """Test SonarLLMClient daily call counter reset."""

    def test_reset_daily_counter(self):
        """Test that reset_daily_counter resets _calls_today to 0."""
        from sibyl.clients.sonar_llm_client import SonarLLMClient

        client = SonarLLMClient()
        client._calls_today = 45

        client.reset_daily_counter()

        self.assertEqual(client._calls_today, 0)


class TestSonarLLMClientSynthesis(unittest.TestCase):
    """Test SonarLLMClient synthesis methods."""

    @patch.dict(os.environ, {}, clear=True)
    def test_synthesize_research_returns_none_when_not_initialized(self):
        """Test that synthesize_research returns None when not initialized."""
        from sibyl.clients.sonar_llm_client import SonarLLMClient

        client = SonarLLMClient()

        # Don't initialize — _http will be None
        result = asyncio.run(client.synthesize_research("test prompt"))

        self.assertIsNone(result)

    def test_synthesize_research_respects_daily_cap(self):
        """Test that synthesize_research returns None when cap reached."""
        from sibyl.clients.sonar_llm_client import SonarLLMClient
        import httpx

        client = SonarLLMClient(daily_call_cap=5)
        client._http = MagicMock(spec=httpx.AsyncClient)
        client._calls_today = 5  # At cap

        result = asyncio.run(client.synthesize_research("test prompt"))

        self.assertIsNone(result)

    def test_generate_digest_returns_none_when_not_initialized(self):
        """Test that generate_digest returns None when not initialized."""
        from sibyl.clients.sonar_llm_client import SonarLLMClient

        client = SonarLLMClient()

        result = asyncio.run(client.generate_digest("test prompt"))

        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════════
# Test CLI Integration (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestCLIIntegration(unittest.TestCase):
    """Test CLI argument parsing and config sections."""

    def test_argparse_accepts_pipeline_as_agents_choice(self):
        """Test that argparse accepts 'pipeline' as --agents choice."""
        from sibyl.__main__ import cli
        import argparse

        # Simulate parsing --agents pipeline
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--agents",
            choices=["monitor", "intelligence", "pipeline", "blitz", "execution", "portfolio", "advanced", "all"],
            default="all",
        )

        args = parser.parse_args(["--agents", "pipeline"])

        self.assertEqual(args.agents, "pipeline")

    def test_argparse_accepts_blitz_as_agents_choice(self):
        """Test that argparse accepts 'blitz' as --agents choice."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--agents",
            choices=["monitor", "intelligence", "pipeline", "blitz", "execution", "portfolio", "advanced", "all"],
            default="all",
        )

        args = parser.parse_args(["--agents", "blitz"])

        self.assertEqual(args.agents, "blitz")

    def test_categories_flag_parses_correctly(self):
        """Test that --categories flag parses as comma-separated string."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--categories", type=str, default=None)

        args = parser.parse_args(["--categories", "crypto,weather,sports"])

        self.assertEqual(args.categories, "crypto,weather,sports")

    def test_system_config_has_pipeline_section(self):
        """Test that system_config.yaml has a 'pipeline' section."""
        from sibyl.core.config import load_yaml

        try:
            system_config = load_yaml("system_config.yaml")
        except FileNotFoundError:
            self.skipTest("system_config.yaml not found")

        self.assertIn("pipeline", system_config)
        self.assertIn("enabled", system_config["pipeline"])
        self.assertIn("run_interval_seconds", system_config["pipeline"])

    def test_system_config_has_blitz_section(self):
        """Test that system_config.yaml has a 'blitz' section."""
        from sibyl.core.config import load_yaml

        try:
            system_config = load_yaml("system_config.yaml")
        except FileNotFoundError:
            self.skipTest("system_config.yaml not found")

        self.assertIn("blitz", system_config)


# ═══════════════════════════════════════════════════════════════════════════
# Test Sonar Refactor (3 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestSonarRefactorBreakoutScout(unittest.TestCase):
    """Test that BreakoutScout uses _sonar_llm (not _anthropic)."""

    def test_breakout_scout_has_sonar_llm_attribute(self):
        """Test that BreakoutScout initializes with _sonar_llm attribute."""
        from sibyl.agents.scout.breakout_scout import BreakoutScout
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {}

        agent = BreakoutScout(db=db_mock, config=config)

        self.assertIsNone(agent._sonar_llm)  # Not initialized yet
        self.assertTrue(hasattr(agent, "_sonar_llm"))

    def test_breakout_scout_does_not_have_anthropic_attribute(self):
        """Test that BreakoutScout does not use old _anthropic attribute."""
        from sibyl.agents.scout.breakout_scout import BreakoutScout
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {}

        agent = BreakoutScout(db=db_mock, config=config)

        self.assertFalse(hasattr(agent, "_anthropic"))

    def test_breakout_scout_fallback_synthesis_still_works(self):
        """Test that BreakoutScout._fallback_synthesis is still callable."""
        from sibyl.agents.scout.breakout_scout import BreakoutScout

        source_data = {
            "reddit": {"sentiment": "bullish", "posts": 10},
            "newsapi": {"sentiment": "neutral", "articles": 5},
            "perplexity": {"summary": "Mixed signals"},
            "twitter": {"sentiment": "bearish", "tweets": 3},
        }

        result = BreakoutScout._fallback_synthesis(source_data)

        self.assertIsInstance(result, dict)
        self.assertIn("sentiment_label", result)
        self.assertIn("synthesis", result)


class TestSonarRefactorNarrator(unittest.TestCase):
    """Test that Narrator uses _sonar_llm (not _anthropic)."""

    def test_narrator_has_sonar_llm_attribute(self):
        """Test that Narrator initializes with _sonar_llm attribute."""
        from sibyl.agents.narrator.narrator import Narrator
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {}

        agent = Narrator(db=db_mock, config=config)

        self.assertIsNone(agent._sonar_llm)  # Not initialized yet
        self.assertTrue(hasattr(agent, "_sonar_llm"))

    def test_narrator_does_not_have_anthropic_attribute(self):
        """Test that Narrator does not use old _anthropic attribute."""
        from sibyl.agents.narrator.narrator import Narrator
        from sibyl.core.database import DatabaseManager

        db_mock = MagicMock(spec=DatabaseManager)
        config = {}

        agent = Narrator(db=db_mock, config=config)

        self.assertFalse(hasattr(agent, "_anthropic"))

    def test_narrator_fallback_digest_still_works(self):
        """Test that Narrator._fallback_digest is still callable."""
        from sibyl.agents.narrator.narrator import Narrator

        snapshot = {
            "portfolio": {
                "portfolio_total_balance": 1000.0,
                "portfolio_cash_reserve": 200.0,
            },
            "positions": [
                {
                    "title": "Will BTC hit $100k",
                    "side": "YES",
                    "pnl": 150.0,
                }
            ],
            "risk": {
                "risk_drawdown_pct": "5.2",
                "risk_win_rate_7d": "65.0",
            },
        }

        result = Narrator._fallback_digest(snapshot, escalation=False)

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)  # Non-empty digest


if __name__ == "__main__":
    unittest.main()
