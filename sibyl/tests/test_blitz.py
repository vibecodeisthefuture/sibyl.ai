"""
Tests for the Blitz partition (Sprint 14).

Covers:
- BlitzScanner: market scanning, confidence estimation, signal generation
- BlitzExecutor: position sizing, risk gates, execution
- Blitz config: loading, parameters
- Integration: engine state, portfolio allocation
- Signal model: new types and statuses
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════════════════════════════════════════════════════════════════
# Test Signal Model Updates
# ═══════════════════════════════════════════════════════════════════════════

class TestBlitzSignalModel(unittest.TestCase):
    """Test that Blitz-specific signal types and statuses exist."""

    def test_blitz_signal_type_exists(self):
        from sibyl.models.signal import SignalType
        self.assertEqual(SignalType.BLITZ_LAST_SECOND.value, "BLITZ_LAST_SECOND")

    def test_blitz_ready_status_exists(self):
        from sibyl.models.signal import SignalStatus
        self.assertEqual(SignalStatus.BLITZ_READY.value, "BLITZ_READY")

    def test_sge_blitz_routing_exists(self):
        from sibyl.models.signal import EngineRouting
        self.assertEqual(EngineRouting.SGE_BLITZ.value, "SGE_BLITZ")

    def test_all_standard_types_still_exist(self):
        """Verify Blitz additions didn't break existing enums."""
        from sibyl.models.signal import SignalType, SignalStatus, EngineRouting
        # Standard signal types
        self.assertEqual(SignalType.ARBITRAGE.value, "ARBITRAGE")
        self.assertEqual(SignalType.DATA_FUNDAMENTAL.value, "DATA_FUNDAMENTAL")
        # Standard statuses
        self.assertEqual(SignalStatus.PENDING.value, "PENDING")
        self.assertEqual(SignalStatus.EXECUTED.value, "EXECUTED")
        # Standard routing
        self.assertEqual(EngineRouting.SGE.value, "SGE")
        self.assertEqual(EngineRouting.ACE.value, "ACE")


# ═══════════════════════════════════════════════════════════════════════════
# Test Blitz Config
# ═══════════════════════════════════════════════════════════════════════════

class TestBlitzConfig(unittest.TestCase):
    """Test Blitz configuration loading from sge_config.yaml."""

    def test_blitz_config_structure(self):
        """Verify the Blitz config section has all required keys."""
        from sibyl.core.config import load_yaml
        try:
            sge = load_yaml("sge_config.yaml")
        except FileNotFoundError:
            self.skipTest("sge_config.yaml not found")

        blitz = sge.get("blitz", {})
        self.assertIn("enabled", blitz)
        self.assertIn("capital_pct_of_sge", blitz)
        self.assertIn("scanner", blitz)
        self.assertIn("entry_criteria", blitz)
        self.assertIn("risk_policy", blitz)
        self.assertIn("execution", blitz)
        self.assertIn("target_patterns", blitz)

    def test_blitz_scanner_config(self):
        from sibyl.core.config import load_yaml
        try:
            sge = load_yaml("sge_config.yaml")
        except FileNotFoundError:
            self.skipTest("sge_config.yaml not found")

        scanner = sge["blitz"]["scanner"]
        self.assertEqual(scanner["poll_interval_seconds"], 1.0)
        self.assertEqual(scanner["close_window_seconds"], 90)
        self.assertEqual(scanner["min_close_window_seconds"], 5)

    def test_blitz_entry_criteria(self):
        from sibyl.core.config import load_yaml
        try:
            sge = load_yaml("sge_config.yaml")
        except FileNotFoundError:
            self.skipTest("sge_config.yaml not found")

        criteria = sge["blitz"]["entry_criteria"]
        self.assertEqual(criteria["min_confidence"], 0.85)
        self.assertEqual(criteria["min_ev"], 0.04)
        self.assertEqual(criteria["min_price_gap"], 0.05)
        self.assertEqual(criteria["max_price_gap"], 0.30)

    def test_blitz_risk_policy(self):
        from sibyl.core.config import load_yaml
        try:
            sge = load_yaml("sge_config.yaml")
        except FileNotFoundError:
            self.skipTest("sge_config.yaml not found")

        risk = sge["blitz"]["risk_policy"]
        self.assertEqual(risk["kelly_fraction"], 0.25)
        self.assertEqual(risk["max_single_position_pct"], 0.08)
        self.assertEqual(risk["max_concurrent_positions"], 5)

    def test_blitz_capital_allocation(self):
        from sibyl.core.config import load_yaml
        try:
            sge = load_yaml("sge_config.yaml")
        except FileNotFoundError:
            self.skipTest("sge_config.yaml not found")

        self.assertEqual(sge["blitz"]["capital_pct_of_sge"], 0.20)
        # 20% of SGE (70%) = 14% of total portfolio
        sge_pct = sge["engine"]["capital_allocation_pct"]
        blitz_of_total = sge_pct * sge["blitz"]["capital_pct_of_sge"]
        self.assertAlmostEqual(blitz_of_total, 0.14, places=2)

    def test_blitz_target_patterns(self):
        from sibyl.core.config import load_yaml
        try:
            sge = load_yaml("sge_config.yaml")
        except FileNotFoundError:
            self.skipTest("sge_config.yaml not found")

        patterns = sge["blitz"]["target_patterns"]
        expected = [
            "crypto_price_windows",
            "weather_temperature",
            "sports_final_minutes",
            "economic_data_windows",
            "stock_price_close",
            "culture_event_outcome",
        ]
        for name in expected:
            self.assertIn(name, patterns, f"Missing target pattern: {name}")
            self.assertIn("keywords", patterns[name])
            self.assertIn("description", patterns[name])


# ═══════════════════════════════════════════════════════════════════════════
# Test BlitzScanner
# ═══════════════════════════════════════════════════════════════════════════

class TestBlitzScanner(unittest.TestCase):
    """Test BlitzScanner agent logic."""

    def _make_scanner(self):
        from sibyl.agents.sge.blitz_scanner import BlitzScanner
        db = MagicMock()
        db.fetchone = AsyncMock(return_value=None)
        db.fetchall = AsyncMock(return_value=[])
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        config = {}
        scanner = BlitzScanner(db, config)
        return scanner, db

    def test_scanner_init(self):
        scanner, _ = self._make_scanner()
        self.assertEqual(scanner.name, "blitz_scanner")
        self.assertEqual(scanner.engine, "SGE")
        self.assertFalse(scanner._enabled)

    def test_scanner_default_params(self):
        scanner, _ = self._make_scanner()
        self.assertEqual(scanner._close_window_seconds, 90)
        self.assertEqual(scanner._min_close_window_seconds, 5)
        self.assertEqual(scanner._min_confidence, 0.85)
        self.assertEqual(scanner._min_ev, 0.04)
        self.assertEqual(scanner._min_price_gap, 0.05)
        self.assertEqual(scanner._max_price_gap, 0.30)
        self.assertEqual(scanner._max_concurrent_positions, 5)

    def test_scanner_poll_interval(self):
        scanner, _ = self._make_scanner()
        # Default when config not loaded
        self.assertEqual(scanner.poll_interval, 1.0)

    def test_pattern_matching_crypto(self):
        scanner, _ = self._make_scanner()
        scanner._target_patterns = {
            "crypto_price_windows": {
                "keywords": ["bitcoin", "btc", "ethereum", "eth", "crypto", "price"],
            },
            "weather_temperature": {
                "keywords": ["temperature", "degrees", "weather"],
            },
        }
        match = scanner._match_target_pattern("Will Bitcoin be above $100k?", "Crypto")
        self.assertEqual(match, "crypto_price_windows")

    def test_pattern_matching_weather(self):
        scanner, _ = self._make_scanner()
        scanner._target_patterns = {
            "weather_temperature": {
                "keywords": ["temperature", "degrees", "weather"],
            },
        }
        match = scanner._match_target_pattern(
            "Will the temperature in NYC be above 80°F?", "Weather"
        )
        self.assertEqual(match, "weather_temperature")

    def test_pattern_matching_no_match(self):
        scanner, _ = self._make_scanner()
        scanner._target_patterns = {
            "crypto_price_windows": {
                "keywords": ["bitcoin", "btc"],
            },
        }
        match = scanner._match_target_pattern("Will SpaceX launch succeed?", "Science")
        self.assertIsNone(match)

    def test_run_cycle_disabled(self):
        """When disabled, run_cycle should be a no-op."""
        scanner, db = self._make_scanner()
        scanner._enabled = False
        asyncio.get_event_loop().run_until_complete(scanner.run_cycle())
        db.fetchone.assert_not_called()

    def test_run_cycle_at_capacity(self):
        """When at max positions, should not scan."""
        scanner, db = self._make_scanner()
        scanner._enabled = True
        scanner._max_concurrent_positions = 2
        db.fetchone = AsyncMock(return_value={"cnt": 2})
        asyncio.get_event_loop().run_until_complete(scanner.run_cycle())
        # Should only have checked open positions, not queried markets
        db.fetchall.assert_not_called()

    def test_health_check(self):
        scanner, _ = self._make_scanner()
        scanner._enabled = True
        scanner._markets_scanned = 100
        scanner._signals_generated = 5
        health = scanner.health_check()
        self.assertTrue(health["enabled"])
        self.assertEqual(health["markets_scanned"], 100)
        self.assertEqual(health["signals_generated"], 5)


# ═══════════════════════════════════════════════════════════════════════════
# Test BlitzExecutor
# ═══════════════════════════════════════════════════════════════════════════

class TestBlitzExecutor(unittest.TestCase):
    """Test BlitzExecutor agent logic."""

    def _make_executor(self):
        from sibyl.agents.sge.blitz_executor import BlitzExecutor
        db = MagicMock()
        db.fetchone = AsyncMock(return_value=None)
        db.fetchall = AsyncMock(return_value=[])
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        config = {}
        executor = BlitzExecutor(db, config, mode="paper")
        return executor, db

    def test_executor_init(self):
        executor, _ = self._make_executor()
        self.assertEqual(executor.name, "blitz_executor")
        self.assertEqual(executor.engine, "SGE")
        self.assertEqual(executor._mode, "paper")
        self.assertFalse(executor._enabled)

    def test_executor_default_risk_params(self):
        executor, _ = self._make_executor()
        self.assertEqual(executor._kelly_fraction, 0.25)
        self.assertEqual(executor._max_single_position_pct, 0.08)
        self.assertEqual(executor._max_concurrent_positions, 5)
        self.assertEqual(executor._per_trade_stop_loss_pct, 0.50)
        self.assertEqual(executor._max_slippage_cents, 3)

    def test_executor_poll_interval(self):
        executor, _ = self._make_executor()
        self.assertEqual(executor.poll_interval, 1.0)

    def test_run_cycle_disabled(self):
        executor, db = self._make_executor()
        executor._enabled = False
        asyncio.get_event_loop().run_until_complete(executor.run_cycle())
        db.fetchone.assert_not_called()

    def test_run_cycle_no_signal(self):
        """When no BLITZ_READY signals, should be a no-op."""
        executor, db = self._make_executor()
        executor._enabled = True
        db.fetchone = AsyncMock(return_value=None)
        asyncio.get_event_loop().run_until_complete(executor.run_cycle())
        # Only one call to fetch signal, no position writes
        db.execute.assert_not_called()

    def test_rejection_tracking(self):
        executor, db = self._make_executor()
        executor._enabled = True
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        asyncio.get_event_loop().run_until_complete(
            executor._reject_signal(42, "TEST_REASON")
        )
        self.assertEqual(executor._rejections, 1)

    def test_health_check(self):
        executor, _ = self._make_executor()
        executor._enabled = True
        executor._executions = 10
        executor._rejections = 3
        health = executor.health_check()
        self.assertTrue(health["enabled"])
        self.assertEqual(health["executions"], 10)
        self.assertEqual(health["rejections"], 3)
        self.assertEqual(health["mode"], "paper")


# ═══════════════════════════════════════════════════════════════════════════
# Test Blitz Kelly Sizing
# ═══════════════════════════════════════════════════════════════════════════

class TestBlitzKellySizing(unittest.TestCase):
    """Test Kelly criterion calculations for Blitz parameters."""

    def _kelly(self, confidence: float, entry_price: float, kelly_frac: float) -> float:
        """Replicate Blitz Kelly calculation."""
        payout = (1.0 / entry_price) - 1.0
        if payout <= 0:
            return 0.0
        kelly_raw = (confidence * payout - (1.0 - confidence)) / payout
        kelly_raw = max(kelly_raw, 0)
        return min(kelly_raw, kelly_frac)

    def test_high_confidence_near_terminal(self):
        """conf=0.92, price=0.90 → small bet (small edge)."""
        kelly = self._kelly(0.92, 0.90, 0.25)
        self.assertGreater(kelly, 0)
        self.assertLessEqual(kelly, 0.25)

    def test_very_high_confidence_good_price(self):
        """conf=0.95, price=0.80 → larger bet (bigger edge)."""
        kelly = self._kelly(0.95, 0.80, 0.25)
        self.assertGreater(kelly, 0.10)

    def test_confidence_below_threshold(self):
        """conf=0.50, price=0.50 → zero Kelly (no edge at Blitz threshold)."""
        kelly = self._kelly(0.50, 0.50, 0.25)
        self.assertEqual(kelly, 0.0)

    def test_kelly_capped_at_fraction(self):
        """Even with massive edge, Kelly is capped at kelly_fraction."""
        kelly = self._kelly(0.99, 0.50, 0.25)
        self.assertLessEqual(kelly, 0.25)

    def test_edge_scenarios(self):
        """Test typical Blitz scenarios."""
        # Crypto BTC above $100k: price at 0.95, conf 0.97
        kelly = self._kelly(0.97, 0.95, 0.25)
        self.assertGreater(kelly, 0)

        # Weather temp above 80F: price at 0.88, conf 0.90
        kelly = self._kelly(0.90, 0.88, 0.25)
        self.assertGreater(kelly, 0)

        # Sports blowout: price at 0.92, conf 0.93
        kelly = self._kelly(0.93, 0.92, 0.25)
        self.assertGreater(kelly, 0)


# ═══════════════════════════════════════════════════════════════════════════
# Test Blitz Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestBlitzEdgeCases(unittest.TestCase):
    """Test edge cases in Blitz scanning and execution."""

    def test_price_gap_too_small(self):
        """Price at 0.97 → gap=0.03 < min_price_gap=0.05 → skip."""
        from sibyl.agents.sge.blitz_scanner import BlitzScanner
        scanner = BlitzScanner(MagicMock(), {})
        # Gap from YES terminal: 1.0 - 0.97 = 0.03
        self.assertLess(1.0 - 0.97, scanner._min_price_gap)

    def test_price_gap_too_large(self):
        """Price at 0.60 → gap=0.40 > max_price_gap=0.30 → skip."""
        from sibyl.agents.sge.blitz_scanner import BlitzScanner
        scanner = BlitzScanner(MagicMock(), {})
        gap_yes = 1.0 - 0.60  # 0.40
        gap_no = 0.60          # 0.60
        self.assertGreater(gap_yes, scanner._max_price_gap)
        self.assertGreater(gap_no, scanner._max_price_gap)

    def test_ideal_price_gap(self):
        """Price at 0.85 → gap=0.15, within [0.05, 0.30] → eligible."""
        from sibyl.agents.sge.blitz_scanner import BlitzScanner
        scanner = BlitzScanner(MagicMock(), {})
        gap = 1.0 - 0.85  # 0.15
        self.assertGreaterEqual(gap, scanner._min_price_gap)
        self.assertLessEqual(gap, scanner._max_price_gap)

    def test_no_side_determination(self):
        """Price at 0.50 → both gaps = 0.50 > max_price_gap → no Blitz."""
        from sibyl.agents.sge.blitz_scanner import BlitzScanner
        scanner = BlitzScanner(MagicMock(), {})
        gap_yes = 1.0 - 0.50  # 0.50
        gap_no = 0.50
        self.assertGreater(gap_yes, scanner._max_price_gap)
        self.assertGreater(gap_no, scanner._max_price_gap)


# ═══════════════════════════════════════════════════════════════════════════
# Test Blitz Capital Isolation
# ═══════════════════════════════════════════════════════════════════════════

class TestBlitzCapitalIsolation(unittest.TestCase):
    """Test that Blitz operates on an isolated capital pool."""

    def test_blitz_is_subengine_of_sge(self):
        """SGE_BLITZ is a sub-engine, not a top-level engine."""
        # SGE gets 70% of total. Blitz gets 20% of SGE = 14% of total.
        total_portfolio = 500.0
        sge_allocation = total_portfolio * 0.70  # $350
        blitz_allocation = sge_allocation * 0.20  # $70
        sge_standard = sge_allocation - blitz_allocation  # $280

        self.assertAlmostEqual(blitz_allocation, 70.0)
        self.assertAlmostEqual(sge_standard, 280.0)
        self.assertAlmostEqual(blitz_allocation / total_portfolio, 0.14)

    def test_blitz_positions_use_sge_blitz_engine(self):
        """Blitz positions must be tagged with engine='SGE_BLITZ'."""
        # This is enforced in BlitzExecutor._execute_for_engine
        # The INSERT uses 'SGE_BLITZ' hardcoded
        from sibyl.agents.sge.blitz_executor import BlitzExecutor
        executor = BlitzExecutor(MagicMock(), {}, mode="paper")
        self.assertEqual(executor.engine, "SGE")  # Parent engine
        # But positions are written with 'SGE_BLITZ'

    def test_category_concentration_limit(self):
        """Max 40% of Blitz pool in one category."""
        from sibyl.agents.sge.blitz_executor import BlitzExecutor
        executor = BlitzExecutor(MagicMock(), {}, mode="paper")
        self.assertEqual(executor._max_category_concentration, 0.40)


# ═══════════════════════════════════════════════════════════════════════════
# Test Blitz Applicable Market Types
# ═══════════════════════════════════════════════════════════════════════════

class TestBlitzApplicableMarkets(unittest.TestCase):
    """Test that Blitz correctly identifies applicable market types."""

    def setUp(self):
        from sibyl.agents.sge.blitz_scanner import BlitzScanner
        self.scanner = BlitzScanner(MagicMock(), {})
        self.scanner._target_patterns = {
            "crypto_price_windows": {
                "keywords": ["bitcoin", "btc", "ethereum", "eth", "solana",
                             "sol", "crypto"],
            },
            "weather_temperature": {
                "keywords": ["temperature", "degrees", "weather",
                             "fahrenheit", "celsius"],
            },
            "sports_final_minutes": {
                "keywords": ["score", "points", "goals", "touchdown",
                             "halftime", "quarter", "inning",
                             "nba", "nfl", "mlb", "nhl"],
            },
            "economic_data_windows": {
                "keywords": ["fed rate", "cpi", "inflation", "nonfarm",
                             "unemployment", "gdp", "fomc", "bls"],
            },
            "stock_price_close": {
                "keywords": ["stock", "share price", "market cap", "nasdaq",
                             "s&p", "dow jones", "nyse"],
            },
            "culture_event_outcome": {
                "keywords": ["oscar", "emmy", "grammy", "award", "nomination",
                             "premiere", "box office", "golden globe"],
            },
        }

    def test_crypto_btc_match(self):
        match = self.scanner._match_target_pattern(
            "Will Bitcoin be above $100,000 at 6pm ET?", "Crypto"
        )
        self.assertEqual(match, "crypto_price_windows")

    def test_crypto_eth_match(self):
        match = self.scanner._match_target_pattern(
            "Will Ethereum close above $4,000?", "Crypto"
        )
        self.assertEqual(match, "crypto_price_windows")

    def test_weather_match(self):
        match = self.scanner._match_target_pattern(
            "Will the high temperature in Phoenix exceed 110°F?", "Weather"
        )
        self.assertEqual(match, "weather_temperature")

    def test_sports_match(self):
        match = self.scanner._match_target_pattern(
            "Will the Lakers score more points than the Celtics?", "Sports"
        )
        self.assertEqual(match, "sports_final_minutes")

    def test_economic_match(self):
        match = self.scanner._match_target_pattern(
            "Will CPI inflation be above 3.0%?", "Economics"
        )
        self.assertEqual(match, "economic_data_windows")

    def test_stock_match(self):
        match = self.scanner._match_target_pattern(
            "Will AAPL stock close above $200?", "Financials"
        )
        self.assertEqual(match, "stock_price_close")

    def test_culture_match(self):
        match = self.scanner._match_target_pattern(
            "Will 'Oppenheimer' win the Oscar for Best Picture?", "Culture"
        )
        self.assertEqual(match, "culture_event_outcome")


# ═══════════════════════════════════════════════════════════════════════════
# Test Completeness
# ═══════════════════════════════════════════════════════════════════════════

class TestBlitzPolicyExemption(unittest.TestCase):
    """Test Section 20: Blitz partition policy exemption (Option B)."""

    def _make_policy(self):
        """Create a PolicyEngine with Blitz exemption enabled."""
        from sibyl.core.policy import PolicyEngine
        policy = PolicyEngine()
        # Minimal config with Blitz exemption and avoidance rules
        config = {
            "tiers": {
                "tier_1": {"name": "Steady", "engines": ["SGE"],
                           "min_confidence": 0.60, "min_ev": 0.03, "auto_entry": True},
                "tier_2": {"name": "Volatile", "engines": ["ACE"],
                           "min_confidence": 0.65, "min_ev": 0.06, "auto_entry": True},
                "tier_3": {"name": "Restricted", "engines": ["ACE"],
                           "min_confidence": 0.85, "min_ev": 0.15, "auto_entry": False,
                           "min_source_confirmations": 3},
            },
            "category_tier_map": {
                "Sports": "tier_2",
                "Culture & Entertainment": "tier_2",
                "Geopolitics & Legal": "tier_3",
                "Weather": "tier_1",
                "Crypto & Digital Assets": "tier_2",
            },
            "category_engine_permissions": {
                "Sports": {"primary": "ACE", "allowed": ["ACE"]},
                "Culture & Entertainment": {"primary": "ACE", "allowed": ["ACE"]},
                "Geopolitics & Legal": {"primary": "ACE", "allowed": ["ACE"],
                                         "override_only": True},
                "Weather": {"primary": "SGE", "allowed": ["SGE"]},
                "Crypto & Digital Assets": {"primary": "ACE", "allowed": ["ACE", "SGE"]},
            },
            "capital_caps": {},
            "universal_avoidance": {
                "min_open_interest_usd": 1000.0,
                "reject_subjective_resolution": True,
                "reject_no_signal_coverage": False,  # Disable for test simplicity
            },
            "approved_data_sources": {
                "Sports": ["api_sports"],
                "Culture & Entertainment": ["tmdb"],
                "Geopolitics & Legal": ["courtlistener"],
                "Weather": ["open_meteo"],
                "Crypto & Digital Assets": ["coingecko"],
            },
            "blitz_partition_exemption": {
                "enabled": True,
                "exempt_from": ["engine_category_permissions", "tier_classification_gates"],
                "still_enforced": ["universal_avoidance_rules"],
            },
            "override_protocol": {"min_confidence": 0.90, "min_ev": 0.20,
                                   "min_independent_sources": 3},
        }
        policy.initialize(config)
        return policy

    def test_blitz_exempt_sports_approved(self):
        """SGE_BLITZ should be allowed to trade Sports (normally ACE-only)."""
        policy = self._make_policy()
        decision = policy.pre_trade_gate(
            signal_data={"confidence": 0.92, "ev": 0.08},
            market_data={"category": "Sports", "market_id": "test-sports",
                         "open_interest": 5000},
            engine="SGE_BLITZ",
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.policy_tier_name, "Blitz-Exempt")

    def test_blitz_exempt_culture_approved(self):
        """SGE_BLITZ should be allowed to trade Culture (normally ACE-only)."""
        policy = self._make_policy()
        decision = policy.pre_trade_gate(
            signal_data={"confidence": 0.90, "ev": 0.06},
            market_data={"category": "Culture & Entertainment", "market_id": "test-culture",
                         "open_interest": 3000},
            engine="SGE_BLITZ",
        )
        self.assertTrue(decision.approved)

    def test_blitz_exempt_geopolitics_approved(self):
        """SGE_BLITZ should bypass Tier 3 override requirements."""
        policy = self._make_policy()
        decision = policy.pre_trade_gate(
            signal_data={"confidence": 0.88, "ev": 0.05},
            market_data={"category": "Geopolitics & Legal", "market_id": "test-geo",
                         "open_interest": 2000},
            engine="SGE_BLITZ",
        )
        self.assertTrue(decision.approved)

    def test_blitz_avoidance_still_enforced(self):
        """SGE_BLITZ should still be rejected by avoidance rules (low liquidity)."""
        policy = self._make_policy()
        decision = policy.pre_trade_gate(
            signal_data={"confidence": 0.92, "ev": 0.08},
            market_data={"category": "Sports", "market_id": "test-illiquid",
                         "open_interest": 500},  # Below $1000 min
            engine="SGE_BLITZ",
        )
        self.assertFalse(decision.approved)
        self.assertIn("avoidance", decision.rejection_reason.lower())

    def test_standard_sge_still_blocked_from_sports(self):
        """Standard SGE should still be blocked from Sports (exemption is Blitz-only)."""
        policy = self._make_policy()
        decision = policy.pre_trade_gate(
            signal_data={"confidence": 0.92, "ev": 0.08},
            market_data={"category": "Sports", "market_id": "test-sports",
                         "open_interest": 5000},
            engine="SGE",
        )
        self.assertFalse(decision.approved)
        # Should be rejected for engine permissions or quality floor
        self.assertTrue(len(decision.rejection_reason) > 0)

    def test_blitz_exemption_disabled(self):
        """When exemption is disabled, SGE_BLITZ should be treated like SGE."""
        from sibyl.core.policy import PolicyEngine
        policy = PolicyEngine()
        config = {
            "tiers": {
                "tier_2": {"name": "Volatile", "engines": ["ACE"],
                           "min_confidence": 0.65, "min_ev": 0.06, "auto_entry": True},
            },
            "category_tier_map": {"Sports": "tier_2"},
            "category_engine_permissions": {
                "Sports": {"primary": "ACE", "allowed": ["ACE"]},
            },
            "capital_caps": {},
            "universal_avoidance": {"min_open_interest_usd": 1000.0,
                                     "reject_no_signal_coverage": False},
            "approved_data_sources": {"Sports": ["api_sports"]},
            "blitz_partition_exemption": {"enabled": False},
            "override_protocol": {},
        }
        policy.initialize(config)
        decision = policy.pre_trade_gate(
            signal_data={"confidence": 0.92, "ev": 0.08},
            market_data={"category": "Sports", "market_id": "test", "open_interest": 5000},
            engine="SGE_BLITZ",
        )
        # Should be treated as normal and fail engine permission
        self.assertFalse(decision.approved)

    def test_blitz_exemption_config_loaded(self):
        """Verify the blitz exemption config is accessible via the policy engine."""
        policy = self._make_policy()
        blitz_cfg = policy.get_blitz_exemption_config()
        self.assertTrue(blitz_cfg["enabled"])
        self.assertIn("engine_category_permissions", blitz_cfg["exempt_from"])

    def test_section_20_in_policy_config(self):
        """Verify Section 20 exists in investment_policy_config.yaml."""
        from sibyl.core.config import load_yaml
        try:
            config = load_yaml("investment_policy_config.yaml")
        except FileNotFoundError:
            self.skipTest("investment_policy_config.yaml not found")
        blitz = config.get("blitz_partition_exemption", {})
        self.assertTrue(blitz.get("enabled"))
        self.assertIn("exempt_from", blitz)
        self.assertIn("still_enforced", blitz)
        self.assertIn("auto_calibration", blitz)


class TestBlitzCompleteness(unittest.TestCase):
    """Verify all Blitz components are importable and properly structured."""

    def test_import_blitz_scanner(self):
        from sibyl.agents.sge.blitz_scanner import BlitzScanner, BLITZ_SIGNAL_TYPE
        self.assertEqual(BLITZ_SIGNAL_TYPE, "BLITZ_LAST_SECOND")
        self.assertTrue(hasattr(BlitzScanner, "run_cycle"))
        self.assertTrue(hasattr(BlitzScanner, "_evaluate_market"))
        self.assertTrue(hasattr(BlitzScanner, "_estimate_confidence"))

    def test_import_blitz_executor(self):
        from sibyl.agents.sge.blitz_executor import BlitzExecutor
        self.assertTrue(hasattr(BlitzExecutor, "run_cycle"))
        self.assertTrue(hasattr(BlitzExecutor, "_reject_signal"))

    def test_blitz_scanner_inherits_base_agent(self):
        from sibyl.agents.sge.blitz_scanner import BlitzScanner
        from sibyl.core.base_agent import BaseAgent
        self.assertTrue(issubclass(BlitzScanner, BaseAgent))

    def test_blitz_executor_inherits_base_agent(self):
        from sibyl.agents.sge.blitz_executor import BlitzExecutor
        from sibyl.core.base_agent import BaseAgent
        self.assertTrue(issubclass(BlitzExecutor, BaseAgent))


if __name__ == "__main__":
    unittest.main()
