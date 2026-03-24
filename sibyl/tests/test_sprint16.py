"""
Sprint 16 Tests — Live Validation, Dedup Tuning, and Calibration Framework.

Tests:
    1. Per-category dedup window configuration
    2. Pipeline validation report structure
    3. Blitz validation report structure
    4. Calibration framework computation
    5. Category classification for Kalshi markets
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


# ── Test Dedup Window Configuration ──────────────────────────────────────

class TestDedupWindows:
    """Verify per-category dedup windows are properly set."""

    def test_crypto_dedup_15min(self):
        from sibyl.pipelines.crypto_pipeline import CryptoPipeline
        assert CryptoPipeline.DEDUP_WINDOW_MINUTES == 15

    def test_sports_dedup_30min(self):
        from sibyl.pipelines.sports_pipeline import SportsPipeline
        assert SportsPipeline.DEDUP_WINDOW_MINUTES == 30

    def test_financial_dedup_60min(self):
        from sibyl.pipelines.financial_pipeline import FinancialPipeline
        assert FinancialPipeline.DEDUP_WINDOW_MINUTES == 60

    def test_weather_dedup_120min(self):
        from sibyl.pipelines.weather_pipeline import WeatherPipeline
        assert WeatherPipeline.DEDUP_WINDOW_MINUTES == 120

    def test_culture_dedup_120min(self):
        from sibyl.pipelines.culture_pipeline import CulturePipeline
        assert CulturePipeline.DEDUP_WINDOW_MINUTES == 120

    def test_geopolitics_dedup_120min(self):
        from sibyl.pipelines.geopolitics_pipeline import GeopoliticsPipeline
        assert GeopoliticsPipeline.DEDUP_WINDOW_MINUTES == 120

    def test_economics_dedup_240min(self):
        from sibyl.pipelines.economics_pipeline import EconomicsPipeline
        assert EconomicsPipeline.DEDUP_WINDOW_MINUTES == 240

    def test_science_dedup_360min(self):
        from sibyl.pipelines.science_pipeline import SciencePipeline
        assert SciencePipeline.DEDUP_WINDOW_MINUTES == 360

    def test_base_pipeline_default_60min(self):
        from sibyl.pipelines.base_pipeline import BasePipeline
        assert BasePipeline.DEDUP_WINDOW_MINUTES == 60


# ── Test Category Classification ─────────────────────────────────────────

class TestCategoryClassification:
    """Verify Kalshi market → Sibyl pipeline mapping."""

    def test_direct_category_match(self):
        from sibyl.tools.validate_pipelines import _classify_category
        assert _classify_category("Economics", "Fed Rate Decision") == "economics"
        assert _classify_category("Sports", "NBA Game") == "sports"
        assert _classify_category("Weather", "Temperature") == "weather"

    def test_keyword_fallback(self):
        from sibyl.tools.validate_pipelines import _classify_category
        assert _classify_category(None, "Bitcoin Price Above $100k") == "crypto"
        assert _classify_category(None, "NFL Super Bowl Winner") == "sports"
        assert _classify_category(None, "FDA Drug Approval") == "science"

    def test_uncategorized_fallback(self):
        from sibyl.tools.validate_pipelines import _classify_category
        assert _classify_category(None, "Random Unknown Market") == "uncategorized"
        assert _classify_category("NewCategory", "Something Weird") == "uncategorized"

    def test_case_insensitive(self):
        from sibyl.tools.validate_pipelines import _classify_category
        assert _classify_category("ECONOMICS", "test") == "economics"
        assert _classify_category("economics", "test") == "economics"

    def test_financial_category(self):
        from sibyl.tools.validate_pipelines import _classify_category
        assert _classify_category("Financial", "Stock Market") == "financial"
        assert _classify_category(None, "S&P 500 Close Above") == "financial"


# ── Test Validation Report Structure ─────────────────────────────────────

class TestValidationReport:
    """Test ValidationReport dataclass."""

    def test_report_creation(self):
        from sibyl.tools.validate_pipelines import ValidationReport
        report = ValidationReport(
            timestamp="2026-03-21 12:00:00 UTC",
            total_kalshi_events=50,
            total_kalshi_markets=200,
        )
        assert report.total_kalshi_events == 50
        assert report.total_kalshi_markets == 200
        assert report.total_signals == 0

    def test_report_summary(self):
        from sibyl.tools.validate_pipelines import ValidationReport
        report = ValidationReport(timestamp="test")
        summary = report.summary()
        assert "SIBYL LIVE PIPELINE VALIDATION REPORT" in summary
        assert "test" in summary

    def test_report_to_dict(self):
        from sibyl.tools.validate_pipelines import ValidationReport
        report = ValidationReport(
            timestamp="test",
            total_kalshi_events=10,
            total_signals=5,
        )
        d = report.to_dict()
        assert d["kalshi_events"] == 10
        assert d["total_signals"] == 5
        assert "pipelines" in d

    def test_pipeline_result_summary(self):
        from sibyl.tools.validate_pipelines import PipelineValidationResult
        pvr = PipelineValidationResult(
            pipeline_name="crypto",
            markets_available=20,
            signals_generated=5,
            avg_confidence=0.75,
            max_confidence=0.92,
            avg_ev=0.08,
            max_ev=0.15,
        )
        summary = pvr.summary()
        assert "CRYPTO" in summary
        assert "20" in summary
        assert "5" in summary


# ── Test Blitz Validation Report ─────────────────────────────────────────

class TestBlitzValidationReport:
    """Test BlitzValidationReport dataclass."""

    def test_report_creation(self):
        from sibyl.tools.validate_blitz import BlitzValidationReport
        report = BlitzValidationReport(timestamp="test", scan_window_hours=24)
        assert report.scan_window_hours == 24
        assert report.total_markets_scanned == 0

    def test_report_summary(self):
        from sibyl.tools.validate_blitz import BlitzValidationReport
        report = BlitzValidationReport(
            timestamp="test",
            total_markets_scanned=100,
            eligible_at_85pct=15,
        )
        summary = report.summary()
        assert "BLITZ VALIDATION REPORT" in summary
        assert "100" in summary

    def test_implied_confidence(self):
        from sibyl.tools.validate_blitz import _compute_implied_confidence
        # Market at 0.95 → 95% confidence (YES side strong)
        assert _compute_implied_confidence(0.95) == 0.95
        # Market at 0.05 → 95% confidence (NO side strong)
        assert _compute_implied_confidence(0.05) == 0.95
        # Market at 0.50 → 50% confidence (coin flip)
        assert _compute_implied_confidence(0.50) == 0.50
        # None → 50% default
        assert _compute_implied_confidence(None) == 0.50

    def test_parse_close_time(self):
        from sibyl.tools.validate_blitz import _parse_close_time
        # ISO format with Z
        dt = _parse_close_time("2026-03-21T15:00:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        # Empty string
        assert _parse_close_time("") is None
        # Invalid
        assert _parse_close_time("not-a-date") is None


# ── Test Calibration Framework ───────────────────────────────────────────

class TestCalibrationBucket:
    """Test CalibrationBucket computations."""

    def test_empty_bucket(self):
        from sibyl.tools.calibrate_confidence import CalibrationBucket
        b = CalibrationBucket(confidence_low=0.70, confidence_high=0.80)
        assert b.accuracy == 0.0
        assert b.expected_accuracy == 0.75
        assert b.calibration_error == 0.75  # Expected - 0 = 0.75

    def test_perfect_calibration(self):
        from sibyl.tools.calibrate_confidence import CalibrationBucket
        b = CalibrationBucket(
            confidence_low=0.70, confidence_high=0.80,
            count=100, correct=75,
        )
        assert b.accuracy == 0.75
        assert abs(b.calibration_error) < 0.001  # Perfect calibration

    def test_overconfident_bucket(self):
        from sibyl.tools.calibrate_confidence import CalibrationBucket
        b = CalibrationBucket(
            confidence_low=0.80, confidence_high=0.90,
            count=100, correct=60,  # 60% correct at 85% confidence
        )
        assert b.accuracy == 0.60
        assert b.calibration_error > 0  # Overconfident (positive error)

    def test_underconfident_bucket(self):
        from sibyl.tools.calibrate_confidence import CalibrationBucket
        b = CalibrationBucket(
            confidence_low=0.50, confidence_high=0.60,
            count=100, correct=70,  # 70% correct at 55% confidence
        )
        assert b.accuracy == 0.70
        assert b.calibration_error < 0  # Underconfident (negative error)


class TestCalibrationReport:
    """Test CalibrationReport."""

    def test_report_creation(self):
        from sibyl.tools.calibrate_confidence import CalibrationReport
        report = CalibrationReport(timestamp="test", analysis_window_days=30)
        assert report.analysis_window_days == 30
        assert report.total_signals == 0

    def test_report_summary(self):
        from sibyl.tools.calibrate_confidence import CalibrationReport
        report = CalibrationReport(timestamp="test")
        summary = report.summary()
        assert "CONFIDENCE CALIBRATION REPORT" in summary

    def test_report_to_dict(self):
        from sibyl.tools.calibrate_confidence import CalibrationReport
        report = CalibrationReport(
            timestamp="test",
            total_signals=100,
            total_resolved=80,
            overall_accuracy=0.75,
        )
        d = report.to_dict()
        assert d["total_signals"] == 100
        assert d["overall_accuracy"] == 0.75


class TestDetermineOutcome:
    """Test the outcome determination logic."""

    def test_yes_correct(self):
        from sibyl.tools.calibrate_confidence import _determine_outcome
        signal = {"detection_modes_triggered": "PIPELINE:crypto|DIR:YES"}
        market = {"status": "resolved", "resolution": "YES"}
        assert _determine_outcome(signal, market) is True

    def test_yes_incorrect(self):
        from sibyl.tools.calibrate_confidence import _determine_outcome
        signal = {"detection_modes_triggered": "PIPELINE:crypto|DIR:YES"}
        market = {"status": "resolved", "resolution": "NO"}
        assert _determine_outcome(signal, market) is False

    def test_no_correct(self):
        from sibyl.tools.calibrate_confidence import _determine_outcome
        signal = {"detection_modes_triggered": "PIPELINE:crypto|DIR:NO"}
        market = {"status": "resolved", "resolution": "NO"}
        assert _determine_outcome(signal, market) is True

    def test_unresolved_market(self):
        from sibyl.tools.calibrate_confidence import _determine_outcome
        signal = {"detection_modes_triggered": "PIPELINE:crypto|DIR:YES"}
        market = {"status": "active", "resolution": ""}
        assert _determine_outcome(signal, market) is None

    def test_no_market(self):
        from sibyl.tools.calibrate_confidence import _determine_outcome
        signal = {"detection_modes_triggered": "PIPELINE:crypto|DIR:YES"}
        assert _determine_outcome(signal, None) is None


# ── Test Confidence Bucket Helper ────────────────────────────────────────

class TestConfidenceBucket:
    """Test the confidence bucketing helper."""

    def test_high_confidence(self):
        from sibyl.tools.validate_pipelines import _confidence_bucket
        assert _confidence_bucket(0.95) == "0.90-1.00"

    def test_mid_confidence(self):
        from sibyl.tools.validate_pipelines import _confidence_bucket
        assert _confidence_bucket(0.75) == "0.70-0.79"

    def test_low_confidence(self):
        from sibyl.tools.validate_pipelines import _confidence_bucket
        assert _confidence_bucket(0.52) == "0.50-0.59"

    def test_boundary(self):
        from sibyl.tools.validate_pipelines import _confidence_bucket
        assert _confidence_bucket(0.80) == "0.80-0.89"
