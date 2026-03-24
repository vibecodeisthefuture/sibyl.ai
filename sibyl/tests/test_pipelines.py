"""
Comprehensive tests for Sprint 13 — Category Signal Pipelines.

Tests cover:
1. BasePipeline infrastructure (signal validation, edge computation, DB writing)
2. Each of the 8 category pipelines (initialization, market matching, analysis)
3. Cross-category correlation engine
4. PipelineManager orchestration
5. End-to-end data-to-signal-to-router workflow

All tests use mocked data clients to avoid real API calls.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sibyl.pipelines.base_pipeline import BasePipeline, PipelineSignal
from sibyl.pipelines.correlation_engine import (
    CrossCategoryCorrelationEngine,
    CorrelationResult,
    CATEGORY_CORRELATIONS,
)
from sibyl.pipelines.pipeline_manager import PipelineManager, PipelineRunResult


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_db():
    """Create a mock DatabaseManager with common query responses."""
    db = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.fetchone = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def sample_markets():
    """Sample Kalshi markets for testing."""
    return [
        {"id": "CPI-24-JAN-UP", "title": "Will CPI exceed 3.5% in January 2024?",
         "category": "Economics", "close_date": "2024-02-15", "status": "active"},
        {"id": "FED-RATE-DEC", "title": "Will the Fed raise rates in December?",
         "category": "Economics", "close_date": "2024-12-20", "status": "active"},
        {"id": "BTC-100K", "title": "Will Bitcoin exceed $100,000 by end of 2024?",
         "category": "Crypto", "close_date": "2024-12-31", "status": "active"},
        {"id": "TEMP-NYC-90", "title": "Will NYC temperature exceed 90°F this week?",
         "category": "Weather", "close_date": "2024-07-15", "status": "active"},
        {"id": "NBA-LAL-WIN", "title": "Will the Lakers win tonight's NBA game?",
         "category": "Sports", "close_date": "2024-03-15", "status": "active"},
        {"id": "OSCAR-BEST-PIC", "title": "Will Oppenheimer win Best Picture at the Oscars?",
         "category": "Culture", "close_date": "2024-03-10", "status": "active"},
        {"id": "FDA-DRUG-X", "title": "Will the FDA approve Drug X by Q2 2024?",
         "category": "Tech & Science", "close_date": "2024-06-30", "status": "active"},
        {"id": "SCOTUS-CASE", "title": "Will the Supreme Court rule in favor in Case Y?",
         "category": "Geopolitics & Legal", "close_date": "2024-06-30", "status": "active"},
        {"id": "AAPL-EARNINGS", "title": "Will Apple beat earnings estimates?",
         "category": "Companies", "close_date": "2024-04-25", "status": "active"},
        {"id": "GDP-RECESSION", "title": "Will GDP growth be negative (recession)?",
         "category": "Economics", "close_date": "2024-07-30", "status": "active"},
    ]


# ═══════════════════════════════════════════════════════════════════════
# BasePipeline Tests
# ═══════════════════════════════════════════════════════════════════════

class TestBasePipeline:
    """Tests for BasePipeline infrastructure methods."""

    def test_compute_edge_yes_underpriced(self):
        """Data says 0.70 probability, market priced at 0.55 → buy YES."""
        edge, direction, ev = BasePipeline._compute_edge(0.70, 0.55)
        assert direction == "YES"
        assert edge == pytest.approx(0.15, abs=0.01)
        assert ev > 0

    def test_compute_edge_yes_overpriced(self):
        """Data says 0.30 probability, market priced at 0.55 → buy NO."""
        edge, direction, ev = BasePipeline._compute_edge(0.30, 0.55)
        assert direction == "NO"
        assert edge == pytest.approx(0.25, abs=0.01)
        assert ev > 0

    def test_compute_edge_no_edge(self):
        """Data agrees with market → no edge."""
        edge, direction, ev = BasePipeline._compute_edge(0.50, 0.50)
        assert edge == pytest.approx(0.0, abs=0.001)
        assert ev == pytest.approx(0.0, abs=0.001)

    def test_edge_to_confidence_minimum(self):
        """Zero edge → base confidence."""
        conf = BasePipeline._edge_to_confidence(0.0)
        assert conf == pytest.approx(0.55)

    def test_edge_to_confidence_scaling(self):
        """Edge of 0.15 should produce meaningful confidence boost."""
        conf = BasePipeline._edge_to_confidence(0.15)
        assert conf > 0.55
        assert conf <= 0.99

    def test_edge_to_confidence_cap(self):
        """Very large edge still capped at 0.99."""
        conf = BasePipeline._edge_to_confidence(0.50)
        assert conf == 0.99

    def test_pipeline_signal_creation(self):
        """PipelineSignal can be created with all fields."""
        sig = PipelineSignal(
            market_id="TEST-001",
            signal_type="DATA_FUNDAMENTAL",
            confidence=0.75,
            ev_estimate=0.12,
            direction="YES",
            reasoning="Test signal",
            source_pipeline="test",
            category="Economics",
        )
        assert sig.market_id == "TEST-001"
        assert sig.confidence == 0.75
        assert sig.direction == "YES"

    def test_validate_signals_filters_low_confidence(self, mock_db):
        """Signals with confidence < 0.50 should be filtered out."""

        class TestPipeline(BasePipeline):
            CATEGORY = "Test"
            PIPELINE_NAME = "test"
            def _create_clients(self): return []
            async def _analyze(self, markets): return []

        pipeline = TestPipeline(mock_db)
        signals = [
            PipelineSignal(market_id="A", signal_type="T", confidence=0.49, ev_estimate=0.1),
            PipelineSignal(market_id="B", signal_type="T", confidence=0.75, ev_estimate=0.1),
            PipelineSignal(market_id="", signal_type="T", confidence=0.80, ev_estimate=0.1),
        ]
        valid = pipeline._validate_signals(signals)
        assert len(valid) == 1
        assert valid[0].market_id == "B"

    def test_validate_signals_caps_confidence(self, mock_db):
        """Confidence should be capped at 0.99."""

        class TestPipeline(BasePipeline):
            CATEGORY = "Test"
            PIPELINE_NAME = "test"
            def _create_clients(self): return []
            async def _analyze(self, markets): return []

        pipeline = TestPipeline(mock_db)
        signals = [
            PipelineSignal(market_id="A", signal_type="T", confidence=1.5, ev_estimate=0.1),
        ]
        valid = pipeline._validate_signals(signals)
        assert valid[0].confidence == 0.99


# ═══════════════════════════════════════════════════════════════════════
# Economics Pipeline Tests
# ═══════════════════════════════════════════════════════════════════════

class TestEconomicsPipeline:
    """Tests for the Economics signal pipeline."""

    def test_initialization(self, mock_db):
        from sibyl.pipelines.economics_pipeline import EconomicsPipeline
        pipeline = EconomicsPipeline(mock_db)
        assert pipeline.CATEGORY == "Economics"
        assert pipeline.PIPELINE_NAME == "economics"

    def test_create_clients(self, mock_db):
        from sibyl.pipelines.economics_pipeline import EconomicsPipeline
        pipeline = EconomicsPipeline(mock_db)
        clients = pipeline._create_clients()
        assert len(clients) == 3  # FRED, BLS, BEA
        client_names = {c.name for c in clients}
        assert "FRED" in client_names
        assert "BLS" in client_names
        assert "BEA" in client_names

    def test_category_variants(self, mock_db):
        from sibyl.pipelines.economics_pipeline import EconomicsPipeline
        pipeline = EconomicsPipeline(mock_db)
        variants = pipeline._category_variants()
        assert "Economics" in variants
        assert "economics" in variants

    @pytest.mark.asyncio
    async def test_find_matching_markets(self, mock_db, sample_markets):
        from sibyl.pipelines.economics_pipeline import EconomicsPipeline
        pipeline = EconomicsPipeline(mock_db)
        matches = pipeline._find_matching_markets(
            sample_markets, ["CPI", "inflation"]
        )
        assert len(matches) >= 1
        assert any("CPI" in m["title"] for m in matches)


# ═══════════════════════════════════════════════════════════════════════
# Weather Pipeline Tests
# ═══════════════════════════════════════════════════════════════════════

class TestWeatherPipeline:
    def test_initialization(self, mock_db):
        from sibyl.pipelines.weather_pipeline import WeatherPipeline
        pipeline = WeatherPipeline(mock_db)
        assert pipeline.CATEGORY == "Weather"
        assert pipeline.PIPELINE_NAME == "weather"

    def test_create_clients(self, mock_db):
        from sibyl.pipelines.weather_pipeline import WeatherPipeline
        pipeline = WeatherPipeline(mock_db)
        clients = pipeline._create_clients()
        assert len(clients) == 2  # OpenMeteo, NOAA
        client_names = {c.name for c in clients}
        assert "OpenMeteo" in client_names
        assert "NOAA" in client_names

    def test_category_variants(self, mock_db):
        from sibyl.pipelines.weather_pipeline import WeatherPipeline
        pipeline = WeatherPipeline(mock_db)
        variants = pipeline._category_variants()
        assert "Weather" in variants
        assert "Climate" in variants


# ═══════════════════════════════════════════════════════════════════════
# Sports Pipeline Tests
# ═══════════════════════════════════════════════════════════════════════

class TestSportsPipeline:
    def test_initialization(self, mock_db):
        from sibyl.pipelines.sports_pipeline import SportsPipeline
        pipeline = SportsPipeline(mock_db)
        assert pipeline.CATEGORY == "Sports"
        assert pipeline.PIPELINE_NAME == "sports"

    def test_create_clients(self, mock_db):
        from sibyl.pipelines.sports_pipeline import SportsPipeline
        pipeline = SportsPipeline(mock_db)
        clients = pipeline._create_clients()
        assert len(clients) == 4  # ESPN, API-Sports, BallDontLie, TheSportsDB


# ═══════════════════════════════════════════════════════════════════════
# Crypto Pipeline Tests
# ═══════════════════════════════════════════════════════════════════════

class TestCryptoPipeline:
    def test_initialization(self, mock_db):
        from sibyl.pipelines.crypto_pipeline import CryptoPipeline
        pipeline = CryptoPipeline(mock_db)
        assert pipeline.CATEGORY == "Crypto"
        assert pipeline.PIPELINE_NAME == "crypto"

    def test_create_clients(self, mock_db):
        from sibyl.pipelines.crypto_pipeline import CryptoPipeline
        pipeline = CryptoPipeline(mock_db)
        clients = pipeline._create_clients()
        assert len(clients) == 2  # CoinGecko, FearGreed

    def test_extract_price_threshold(self, mock_db):
        from sibyl.pipelines.crypto_pipeline import CryptoPipeline
        pipeline = CryptoPipeline(mock_db)
        # Test $100,000 format
        threshold = pipeline._extract_price_threshold(
            "Will Bitcoin exceed $100,000 by end of 2024?"
        )
        assert threshold == pytest.approx(100000, abs=1)

    def test_extract_coin_from_title(self, mock_db):
        from sibyl.pipelines.crypto_pipeline import CryptoPipeline
        pipeline = CryptoPipeline(mock_db)
        coin = pipeline._extract_coin_from_title("Will Bitcoin exceed $100k?")
        assert coin.lower() == "bitcoin"

    def test_extract_coin_ethereum(self, mock_db):
        from sibyl.pipelines.crypto_pipeline import CryptoPipeline
        pipeline = CryptoPipeline(mock_db)
        coin = pipeline._extract_coin_from_title("Will ETH price reach $5,000?")
        assert coin.lower() == "ethereum"


# ═══════════════════════════════════════════════════════════════════════
# Culture Pipeline Tests
# ═══════════════════════════════════════════════════════════════════════

class TestCulturePipeline:
    def test_initialization(self, mock_db):
        from sibyl.pipelines.culture_pipeline import CulturePipeline
        pipeline = CulturePipeline(mock_db)
        assert pipeline.CATEGORY == "Culture"
        assert pipeline.PIPELINE_NAME == "culture"

    def test_create_clients(self, mock_db):
        from sibyl.pipelines.culture_pipeline import CulturePipeline
        pipeline = CulturePipeline(mock_db)
        clients = pipeline._create_clients()
        assert len(clients) == 2  # TMDb, Wikipedia


# ═══════════════════════════════════════════════════════════════════════
# Science Pipeline Tests
# ═══════════════════════════════════════════════════════════════════════

class TestSciencePipeline:
    def test_initialization(self, mock_db):
        from sibyl.pipelines.science_pipeline import SciencePipeline
        pipeline = SciencePipeline(mock_db)
        assert pipeline.CATEGORY == "Tech & Science"
        assert pipeline.PIPELINE_NAME == "science"

    def test_create_clients(self, mock_db):
        from sibyl.pipelines.science_pipeline import SciencePipeline
        pipeline = SciencePipeline(mock_db)
        clients = pipeline._create_clients()
        assert len(clients) == 2  # OpenFDA, ClinicalTrials


# ═══════════════════════════════════════════════════════════════════════
# Geopolitics Pipeline Tests
# ═══════════════════════════════════════════════════════════════════════

class TestGeopoliticsPipeline:
    def test_initialization(self, mock_db):
        from sibyl.pipelines.geopolitics_pipeline import GeopoliticsPipeline
        pipeline = GeopoliticsPipeline(mock_db)
        assert pipeline.CATEGORY == "Geopolitics & Legal"
        assert pipeline.PIPELINE_NAME == "geopolitics"

    def test_create_clients(self, mock_db):
        from sibyl.pipelines.geopolitics_pipeline import GeopoliticsPipeline
        pipeline = GeopoliticsPipeline(mock_db)
        clients = pipeline._create_clients()
        assert len(clients) == 3  # CourtListener, GDELT, Congress

    def test_category_variants_includes_politics(self, mock_db):
        from sibyl.pipelines.geopolitics_pipeline import GeopoliticsPipeline
        pipeline = GeopoliticsPipeline(mock_db)
        variants = pipeline._category_variants()
        assert "Geopolitics & Legal" in variants
        assert "Politics" in variants or "politics" in variants


# ═══════════════════════════════════════════════════════════════════════
# Financial Pipeline Tests
# ═══════════════════════════════════════════════════════════════════════

class TestFinancialPipeline:
    def test_initialization(self, mock_db):
        from sibyl.pipelines.financial_pipeline import FinancialPipeline
        pipeline = FinancialPipeline(mock_db)
        assert pipeline.CATEGORY == "Financials"
        assert pipeline.PIPELINE_NAME == "financial"

    def test_create_clients(self, mock_db):
        from sibyl.pipelines.financial_pipeline import FinancialPipeline
        pipeline = FinancialPipeline(mock_db)
        clients = pipeline._create_clients()
        assert len(clients) == 1  # FMP


# ═══════════════════════════════════════════════════════════════════════
# Correlation Engine Tests
# ═══════════════════════════════════════════════════════════════════════

class TestCorrelationEngine:
    """Tests for the cross-category correlation engine."""

    def test_category_correlations_exist(self):
        """Verify correlation rules are defined."""
        assert len(CATEGORY_CORRELATIONS) >= 5
        assert ("Economics", "Financials") in CATEGORY_CORRELATIONS
        assert CATEGORY_CORRELATIONS[("Economics", "Financials")] > 0.5

    @pytest.mark.asyncio
    async def test_no_signals_returns_empty(self, mock_db):
        engine = CrossCategoryCorrelationEngine(mock_db)
        result = await engine.analyze([])
        assert len(result.boosted_signals) == 0
        assert len(result.composite_signals) == 0

    @pytest.mark.asyncio
    async def test_single_signal_no_correlation(self, mock_db):
        engine = CrossCategoryCorrelationEngine(mock_db)
        signals = [
            PipelineSignal(
                market_id="A", signal_type="T", confidence=0.7,
                ev_estimate=0.1, direction="YES", category="Economics",
                source_pipeline="economics",
            ),
        ]
        result = await engine.analyze(signals)
        assert len(result.composite_signals) == 0

    @pytest.mark.asyncio
    async def test_reinforcing_signals_boost(self, mock_db):
        """Two signals on same market from correlated categories get boosted."""
        engine = CrossCategoryCorrelationEngine(mock_db)
        sig_a = PipelineSignal(
            market_id="MKT-1", signal_type="T", confidence=0.70,
            ev_estimate=0.1, direction="YES", category="Economics",
            source_pipeline="economics",
        )
        sig_b = PipelineSignal(
            market_id="MKT-1", signal_type="T", confidence=0.65,
            ev_estimate=0.08, direction="YES", category="Financials",
            source_pipeline="financial",
        )
        result = await engine.analyze([sig_a, sig_b])
        # Should have at least one boosted signal
        assert len(result.boosted_signals) >= 1
        # The stronger signal should have been boosted
        boosted = result.boosted_signals[0]
        assert boosted.confidence > 0.70  # Was boosted from 0.70

    @pytest.mark.asyncio
    async def test_composite_signal_generation(self, mock_db):
        """Multiple agreeing signals on same market → composite."""
        engine = CrossCategoryCorrelationEngine(mock_db)
        signals = [
            PipelineSignal(
                market_id="MKT-1", signal_type="T", confidence=0.72,
                ev_estimate=0.10, direction="YES", category="Economics",
                source_pipeline="economics",
            ),
            PipelineSignal(
                market_id="MKT-1", signal_type="T", confidence=0.68,
                ev_estimate=0.08, direction="YES", category="Financials",
                source_pipeline="financial",
            ),
        ]
        result = await engine.analyze(signals)
        assert len(result.composite_signals) >= 1
        composite = result.composite_signals[0]
        assert composite.signal_type == "COMPOSITE_HIGH_CONVICTION"
        assert composite.confidence >= 0.70  # Average + consensus bonus

    @pytest.mark.asyncio
    async def test_conflicting_signals_detected(self, mock_db):
        """Conflicting signals on same market produce warning."""
        engine = CrossCategoryCorrelationEngine(mock_db)
        signals = [
            PipelineSignal(
                market_id="MKT-1", signal_type="T", confidence=0.70,
                ev_estimate=0.1, direction="YES", category="Economics",
                source_pipeline="economics",
            ),
            PipelineSignal(
                market_id="MKT-1", signal_type="T", confidence=0.65,
                ev_estimate=0.08, direction="NO", category="Financials",
                source_pipeline="financial",
            ),
        ]
        result = await engine.analyze(signals)
        assert any("CONFLICT" in w for w in result.correlation_warnings)

    @pytest.mark.asyncio
    async def test_crowded_trade_warning(self, mock_db):
        """Many signals pointing same direction → crowded trade warning."""
        engine = CrossCategoryCorrelationEngine(mock_db)
        signals = [
            PipelineSignal(
                market_id=f"MKT-{i}", signal_type="T", confidence=0.70,
                ev_estimate=0.1, direction="YES", category="Economics",
                source_pipeline="economics",
            )
            for i in range(6)
        ]
        result = await engine.analyze(signals)
        assert any("CROWDED" in w for w in result.correlation_warnings)


# ═══════════════════════════════════════════════════════════════════════
# PipelineManager Tests
# ═══════════════════════════════════════════════════════════════════════

class TestPipelineManager:
    """Tests for the PipelineManager orchestrator."""

    @pytest.mark.asyncio
    async def test_initialization(self, mock_db):
        """PipelineManager creates all 8 pipelines."""
        with patch.dict(os.environ, {
            "FRED_API_KEY": "test",
            "BLS_API_KEY": "test",
            "BEA_API_KEY": "test",
            "NOAA_API_KEY": "test",
            "API_SPORTS_KEY": "test",
            "BALLDONTLIE_API_KEY": "test",
            "THESPORTSDB_API_KEY": "test",
            "TMDB_API_KEY": "test",
            "COINGECKO_API_KEY": "test",
            "OPENFDA_API_KEY": "test",
            "COURTLISTENER_API_KEY": "test",
            "CONGRESS_API_KEY": "test",
            "FMP_API_KEY": "test",
        }):
            manager = PipelineManager(mock_db)
            count = await manager.initialize()
            assert count == 8  # All 8 pipelines
            assert manager.pipeline_count == 8

    @pytest.mark.asyncio
    async def test_get_pipeline_status(self, mock_db):
        """Pipeline status returns info for all pipelines."""
        with patch.dict(os.environ, {
            "FRED_API_KEY": "test", "BLS_API_KEY": "test",
            "BEA_API_KEY": "test", "NOAA_TOKEN": "test",
            "API_SPORTS_KEY": "test", "BALLDONTLIE_API_KEY": "test",
            "THESPORTSDB_API_KEY": "test", "TMDB_BEARER_TOKEN": "test",
            "COINGECKO_API_KEY": "test", "OPENFDA_API_KEY": "test",
            "COURTLISTENER_API_KEY": "test", "CONGRESS_API_KEY": "test",
            "FMP_API_KEY": "test",
        }):
            manager = PipelineManager(mock_db)
            await manager.initialize()
            status = manager.get_pipeline_status()
            assert "economics" in status
            assert "weather" in status
            assert "sports" in status
            assert "crypto" in status
            assert "culture" in status
            assert "science" in status
            assert "geopolitics" in status
            assert "financial" in status

    @pytest.mark.asyncio
    async def test_run_all_empty_markets(self, mock_db):
        """Running with no markets produces no signals (but doesn't crash)."""
        with patch.dict(os.environ, {
            "FRED_API_KEY": "test", "BLS_API_KEY": "test",
            "BEA_API_KEY": "test", "NOAA_TOKEN": "test",
            "API_SPORTS_KEY": "test", "BALLDONTLIE_API_KEY": "test",
            "THESPORTSDB_API_KEY": "test", "TMDB_BEARER_TOKEN": "test",
            "COINGECKO_API_KEY": "test", "OPENFDA_API_KEY": "test",
            "COURTLISTENER_API_KEY": "test", "CONGRESS_API_KEY": "test",
            "FMP_API_KEY": "test",
        }):
            manager = PipelineManager(mock_db)
            await manager.initialize()
            result = await manager.run_all()
            assert isinstance(result, PipelineRunResult)
            assert result.pipelines_run >= 1
            assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_run_result_summary(self, mock_db):
        """PipelineRunResult.summary() returns formatted string."""
        result = PipelineRunResult(
            signals_by_pipeline={"economics": [], "crypto": []},
            pipelines_run=2,
            pipelines_failed=0,
            duration_seconds=1.5,
        )
        summary = result.summary()
        assert "Pipeline Run Summary" in summary
        assert "2 run" in summary
        assert "economics" in summary


# ═══════════════════════════════════════════════════════════════════════
# Signal Type Model Tests
# ═══════════════════════════════════════════════════════════════════════

class TestSignalTypes:
    """Verify new pipeline signal types are in the model."""

    def test_data_fundamental_exists(self):
        from sibyl.models.signal import SignalType
        assert SignalType.DATA_FUNDAMENTAL.value == "DATA_FUNDAMENTAL"

    def test_data_sentiment_exists(self):
        from sibyl.models.signal import SignalType
        assert SignalType.DATA_SENTIMENT.value == "DATA_SENTIMENT"

    def test_data_momentum_exists(self):
        from sibyl.models.signal import SignalType
        assert SignalType.DATA_MOMENTUM.value == "DATA_MOMENTUM"

    def test_data_divergence_exists(self):
        from sibyl.models.signal import SignalType
        assert SignalType.DATA_DIVERGENCE.value == "DATA_DIVERGENCE"

    def test_data_catalyst_exists(self):
        from sibyl.models.signal import SignalType
        assert SignalType.DATA_CATALYST.value == "DATA_CATALYST"


# ═══════════════════════════════════════════════════════════════════════
# End-to-End Integration Tests
# ═══════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    """End-to-end tests: pipeline → signal → DB → routing readiness."""

    @pytest.mark.asyncio
    async def test_signal_writes_to_db_correctly(self, mock_db):
        """Verify signals are written with correct schema."""
        from sibyl.pipelines.base_pipeline import BasePipeline, PipelineSignal

        class DummyPipeline(BasePipeline):
            CATEGORY = "Test"
            PIPELINE_NAME = "test"
            def _create_clients(self): return []
            async def _analyze(self, markets):
                return [PipelineSignal(
                    market_id="TEST-001",
                    signal_type="DATA_FUNDAMENTAL",
                    confidence=0.75,
                    ev_estimate=0.12,
                    direction="YES",
                    reasoning="Test fundamental signal",
                    source_pipeline="test",
                    category="Test",
                )]

        pipeline = DummyPipeline(mock_db)
        pipeline._initialized = True
        signals = await pipeline.run()

        assert len(signals) == 1
        # Verify DB was called with correct INSERT
        mock_db.execute.assert_called()
        call_args = mock_db.execute.call_args_list
        # Find the INSERT call
        insert_call = None
        for call in call_args:
            if "INSERT INTO signals" in str(call):
                insert_call = call
                break
        assert insert_call is not None

    @pytest.mark.asyncio
    async def test_signal_duplicate_prevention(self, mock_db):
        """Duplicate signals within 60 minutes should be prevented."""

        class DummyPipeline(BasePipeline):
            CATEGORY = "Test"
            PIPELINE_NAME = "test"
            def _create_clients(self): return []
            async def _analyze(self, markets):
                return [PipelineSignal(
                    market_id="TEST-001",
                    signal_type="DATA_FUNDAMENTAL",
                    confidence=0.75,
                    ev_estimate=0.12,
                )]

        # Simulate existing recent signal
        mock_db.fetchone = AsyncMock(
            side_effect=lambda sql, params=None: (
                {"id": 1} if "SELECT id FROM signals" in sql else None
            )
        )

        pipeline = DummyPipeline(mock_db)
        pipeline._initialized = True
        signals = await pipeline.run()

        assert len(signals) == 1  # Signal generated
        # But should NOT have been written to DB (duplicate check)
        insert_calls = [
            c for c in mock_db.execute.call_args_list
            if "INSERT INTO signals" in str(c)
        ]
        assert len(insert_calls) == 0

    @pytest.mark.asyncio
    async def test_pipeline_signal_compatible_with_router(self):
        """Verify pipeline signals have all fields the SignalRouter expects."""
        sig = PipelineSignal(
            market_id="MKT-001",
            signal_type="DATA_FUNDAMENTAL",
            confidence=0.75,
            ev_estimate=0.12,
            direction="YES",
            reasoning="Test",
            source_pipeline="economics",
            category="Economics",
        )
        # Router expects these fields in the signals table:
        assert sig.market_id  # Non-empty
        assert sig.signal_type  # Non-empty
        assert 0.0 <= sig.confidence <= 1.0
        assert isinstance(sig.ev_estimate, float)
        assert sig.reasoning  # Non-empty

    @pytest.mark.asyncio
    async def test_full_pipeline_to_correlation_flow(self, mock_db):
        """Simulate full flow: multiple pipelines → correlation engine."""
        econ_signals = [
            PipelineSignal(
                market_id="GDP-MKT", signal_type="DATA_FUNDAMENTAL",
                confidence=0.72, ev_estimate=0.10, direction="YES",
                category="Economics", source_pipeline="economics",
            ),
        ]
        fin_signals = [
            PipelineSignal(
                market_id="GDP-MKT", signal_type="DATA_MOMENTUM",
                confidence=0.68, ev_estimate=0.08, direction="YES",
                category="Financials", source_pipeline="financial",
            ),
        ]

        all_signals = econ_signals + fin_signals
        engine = CrossCategoryCorrelationEngine(mock_db)
        result = await engine.analyze(all_signals)

        # Should produce composite signal for GDP-MKT
        assert len(result.composite_signals) >= 1
        # Should boost the stronger signal
        assert len(result.boosted_signals) >= 1


# ═══════════════════════════════════════════════════════════════════════
# Completeness Tests
# ═══════════════════════════════════════════════════════════════════════

class TestCompleteness:
    """Verify all pipeline components exist and are properly connected."""

    def test_all_pipeline_files_exist(self):
        """All 8 category pipeline files exist."""
        import sibyl.pipelines.economics_pipeline
        import sibyl.pipelines.weather_pipeline
        import sibyl.pipelines.sports_pipeline
        import sibyl.pipelines.crypto_pipeline
        import sibyl.pipelines.culture_pipeline
        import sibyl.pipelines.science_pipeline
        import sibyl.pipelines.geopolitics_pipeline
        import sibyl.pipelines.financial_pipeline
        import sibyl.pipelines.correlation_engine
        import sibyl.pipelines.pipeline_manager

    def test_all_pipelines_inherit_base(self):
        """All pipelines inherit from BasePipeline."""
        from sibyl.pipelines.economics_pipeline import EconomicsPipeline
        from sibyl.pipelines.weather_pipeline import WeatherPipeline
        from sibyl.pipelines.sports_pipeline import SportsPipeline
        from sibyl.pipelines.crypto_pipeline import CryptoPipeline
        from sibyl.pipelines.culture_pipeline import CulturePipeline
        from sibyl.pipelines.science_pipeline import SciencePipeline
        from sibyl.pipelines.geopolitics_pipeline import GeopoliticsPipeline
        from sibyl.pipelines.financial_pipeline import FinancialPipeline

        for cls in [
            EconomicsPipeline, WeatherPipeline, SportsPipeline, CryptoPipeline,
            CulturePipeline, SciencePipeline, GeopoliticsPipeline, FinancialPipeline,
        ]:
            assert issubclass(cls, BasePipeline), f"{cls.__name__} must inherit BasePipeline"

    def test_all_pipelines_have_category(self):
        """Each pipeline has CATEGORY and PIPELINE_NAME set."""
        from sibyl.pipelines.economics_pipeline import EconomicsPipeline
        from sibyl.pipelines.weather_pipeline import WeatherPipeline
        from sibyl.pipelines.sports_pipeline import SportsPipeline
        from sibyl.pipelines.crypto_pipeline import CryptoPipeline
        from sibyl.pipelines.culture_pipeline import CulturePipeline
        from sibyl.pipelines.science_pipeline import SciencePipeline
        from sibyl.pipelines.geopolitics_pipeline import GeopoliticsPipeline
        from sibyl.pipelines.financial_pipeline import FinancialPipeline

        pipelines = [
            EconomicsPipeline, WeatherPipeline, SportsPipeline, CryptoPipeline,
            CulturePipeline, SciencePipeline, GeopoliticsPipeline, FinancialPipeline,
        ]
        categories = set()
        names = set()
        for cls in pipelines:
            assert cls.CATEGORY, f"{cls.__name__} missing CATEGORY"
            assert cls.PIPELINE_NAME, f"{cls.__name__} missing PIPELINE_NAME"
            categories.add(cls.CATEGORY)
            names.add(cls.PIPELINE_NAME)

        assert len(categories) == 8  # All unique categories
        assert len(names) == 8  # All unique pipeline names

    def test_pipeline_manager_includes_all_pipelines(self, mock_db):
        """PipelineManager creates all 8 pipelines."""
        manager = PipelineManager(mock_db)
        # Access _pipelines indirectly via initialize
        # Count pipeline classes in the source
        from sibyl.pipelines.pipeline_manager import PipelineManager as PM
        import inspect
        source = inspect.getsource(PM.initialize)
        assert "EconomicsPipeline" in source
        assert "WeatherPipeline" in source
        assert "SportsPipeline" in source
        assert "CryptoPipeline" in source
        assert "CulturePipeline" in source
        assert "SciencePipeline" in source
        assert "GeopoliticsPipeline" in source
        assert "FinancialPipeline" in source
