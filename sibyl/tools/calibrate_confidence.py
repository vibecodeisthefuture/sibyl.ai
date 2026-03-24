"""
Confidence Calibration Framework — retrospective calibration analysis.

PURPOSE:
    After running pipelines (in paper mode or against historical data),
    this tool:
    1. Reads all generated signals from the `signals` table
    2. Matches signals to resolved market outcomes
    3. Computes calibration curve: is 0.80 confidence actually correct 80%?
    4. Identifies per-pipeline confidence bias (over/under confident)
    5. Suggests per-pipeline confidence adjustments

    This is a RETROSPECTIVE analysis tool, not a real-time agent.

USAGE:
    python -m sibyl.tools.calibrate_confidence
    python -m sibyl.tools.calibrate_confidence --days 30
    python -m sibyl.tools.calibrate_confidence --pipeline crypto
    python -m sibyl.tools.calibrate_confidence --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from sibyl.core.database import DatabaseManager
from sibyl.core.config import SibylConfig

logger = logging.getLogger("sibyl.tools.calibrate_confidence")


# ── Calibration buckets ─────────────────────────────────────────────────
CALIBRATION_BUCKETS = [
    (0.50, 0.55),
    (0.55, 0.60),
    (0.60, 0.65),
    (0.65, 0.70),
    (0.70, 0.75),
    (0.75, 0.80),
    (0.80, 0.85),
    (0.85, 0.90),
    (0.90, 0.95),
    (0.95, 1.00),
]


@dataclass
class CalibrationBucket:
    """One bucket of the calibration curve."""
    confidence_low: float
    confidence_high: float
    count: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.count if self.count > 0 else 0.0

    @property
    def expected_accuracy(self) -> float:
        return (self.confidence_low + self.confidence_high) / 2.0

    @property
    def calibration_error(self) -> float:
        """Positive = overconfident, negative = underconfident."""
        return self.expected_accuracy - self.accuracy

    @property
    def label(self) -> str:
        return f"{self.confidence_low:.2f}-{self.confidence_high:.2f}"


@dataclass
class PipelineCalibration:
    """Calibration analysis for a single pipeline."""
    pipeline_name: str
    total_signals: int = 0
    resolved_signals: int = 0
    correct_predictions: int = 0
    buckets: list[CalibrationBucket] = field(default_factory=list)
    mean_confidence: float = 0.0
    actual_accuracy: float = 0.0
    brier_score: float = 0.0  # Lower is better (0 = perfect)
    suggested_adjustment: float = 0.0  # Multiply confidence by this factor

    def summary(self) -> str:
        lines = [
            f"  {self.pipeline_name.upper()} Pipeline Calibration",
            f"    Total signals: {self.total_signals}",
            f"    Resolved: {self.resolved_signals}",
            f"    Correct: {self.correct_predictions} ({self.actual_accuracy:.1%})",
            f"    Mean confidence: {self.mean_confidence:.3f}",
            f"    Brier score: {self.brier_score:.4f}",
        ]
        if abs(self.suggested_adjustment - 1.0) > 0.01:
            direction = "overconfident" if self.suggested_adjustment < 1.0 else "underconfident"
            lines.append(
                f"    Adjustment: ×{self.suggested_adjustment:.3f} ({direction})"
            )

        # Calibration curve
        active_buckets = [b for b in self.buckets if b.count > 0]
        if active_buckets:
            lines.append("    Calibration curve:")
            lines.append("      Conf Range  | N    | Accuracy | Expected | Error")
            lines.append("      " + "-" * 52)
            for b in active_buckets:
                error_str = f"{b.calibration_error:+.3f}"
                marker = " ⚠" if abs(b.calibration_error) > 0.10 else ""
                lines.append(
                    f"      {b.label:11s} | {b.count:4d} | {b.accuracy:7.1%}  | "
                    f"{b.expected_accuracy:7.1%}  | {error_str}{marker}"
                )

        return "\n".join(lines)


@dataclass
class CalibrationReport:
    """Full calibration report across all pipelines."""
    timestamp: str = ""
    analysis_window_days: int = 0
    total_signals: int = 0
    total_resolved: int = 0
    overall_accuracy: float = 0.0
    overall_brier_score: float = 0.0
    pipeline_calibrations: dict[str, PipelineCalibration] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            "=" * 70,
            "  SIBYL CONFIDENCE CALIBRATION REPORT",
            f"  {self.timestamp}",
            "=" * 70,
            "",
            f"  Analysis window: {self.analysis_window_days} days",
            f"  Total signals: {self.total_signals}",
            f"  Resolved signals: {self.total_resolved}",
            f"  Overall accuracy: {self.overall_accuracy:.1%}",
            f"  Overall Brier score: {self.overall_brier_score:.4f}",
            "",
            "-" * 70,
        ]

        for name in sorted(self.pipeline_calibrations.keys()):
            cal = self.pipeline_calibrations[name]
            lines.append(cal.summary())
            lines.append("")

        if self.recommendations:
            lines.append("-" * 70)
            lines.append("  RECOMMENDATIONS:")
            for rec in self.recommendations:
                lines.append(f"    • {rec}")
            lines.append("")

        lines.append(f"  Duration: {self.duration_seconds:.1f}s")
        lines.append("=" * 70)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "analysis_window_days": self.analysis_window_days,
            "total_signals": self.total_signals,
            "total_resolved": self.total_resolved,
            "overall_accuracy": self.overall_accuracy,
            "overall_brier_score": self.overall_brier_score,
            "pipelines": {
                name: {
                    "total_signals": cal.total_signals,
                    "resolved_signals": cal.resolved_signals,
                    "correct_predictions": cal.correct_predictions,
                    "mean_confidence": cal.mean_confidence,
                    "actual_accuracy": cal.actual_accuracy,
                    "brier_score": cal.brier_score,
                    "suggested_adjustment": cal.suggested_adjustment,
                    "calibration_curve": [
                        {
                            "range": b.label,
                            "count": b.count,
                            "accuracy": round(b.accuracy, 4),
                            "expected": round(b.expected_accuracy, 4),
                            "error": round(b.calibration_error, 4),
                        }
                        for b in cal.buckets
                        if b.count > 0
                    ],
                }
                for name, cal in sorted(self.pipeline_calibrations.items())
            },
            "recommendations": self.recommendations,
            "duration_seconds": self.duration_seconds,
        }


def _determine_outcome(signal_row: dict, market_row: dict | None) -> bool | None:
    """Determine if a signal prediction was correct.

    Returns True (correct), False (incorrect), or None (unresolvable).
    """
    if not market_row:
        return None

    market_status = (market_row.get("status") or "").lower()
    if market_status not in ("resolved", "closed", "settled"):
        return None

    # Extract signal direction from detection_modes_triggered
    detection = signal_row.get("detection_modes_triggered", "")
    direction = "YES"
    if "DIR:NO" in detection:
        direction = "NO"
    elif "DIR:YES" in detection:
        direction = "YES"

    # Check resolution
    resolution = market_row.get("resolution", market_row.get("result", ""))
    if not resolution:
        return None

    resolution_upper = str(resolution).upper()

    if direction == "YES":
        return resolution_upper in ("YES", "1", "TRUE", "WIN")
    else:
        return resolution_upper in ("NO", "0", "FALSE", "LOSS")


async def run_calibration(
    days: int = 30,
    pipeline_filter: str | None = None,
) -> CalibrationReport:
    """Run retrospective calibration analysis.

    Args:
        days: How many days of signal history to analyze.
        pipeline_filter: Optional single pipeline name to analyze.

    Returns:
        CalibrationReport with full calibration metrics.
    """
    report = CalibrationReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        analysis_window_days=days,
    )
    start_time = time.monotonic()

    config = SibylConfig()
    db = DatabaseManager(config.db_path)
    await db.initialize()

    # ── Fetch signals ───────────────────────────────────────────────────
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    query = """
        SELECT s.*, m.status as market_status, m.resolution,
               m.title as market_title
        FROM signals s
        LEFT JOIN markets m ON s.market_id = m.id
        WHERE s.timestamp >= ?
    """
    params: list[Any] = [cutoff_str]

    if pipeline_filter:
        query += " AND s.detection_modes_triggered LIKE ?"
        params.append(f"%PIPELINE:{pipeline_filter}%")

    query += " ORDER BY s.timestamp DESC"

    try:
        rows = await db.fetchall(query, tuple(params))
    except Exception as e:
        logger.error("Failed to fetch signals: %s", e)
        rows = []

    # ── Analyze per pipeline ────────────────────────────────────────────
    pipeline_signals: dict[str, list[dict]] = defaultdict(list)

    for row in rows:
        row_dict = dict(row)
        report.total_signals += 1

        # Extract pipeline name from detection_modes_triggered
        detection = row_dict.get("detection_modes_triggered", "")
        pname = "unknown"
        if "PIPELINE:" in detection:
            parts = detection.split("PIPELINE:")
            if len(parts) > 1:
                pname = parts[1].split("|")[0].strip()

        pipeline_signals[pname].append(row_dict)

    # ── Compute calibration per pipeline ────────────────────────────────
    all_brier_scores = []

    for pname, signals in sorted(pipeline_signals.items()):
        cal = PipelineCalibration(
            pipeline_name=pname,
            total_signals=len(signals),
            buckets=[
                CalibrationBucket(low, high) for low, high in CALIBRATION_BUCKETS
            ],
        )

        confidences = []
        brier_terms = []

        for sig in signals:
            conf = sig.get("confidence", 0.5)
            confidences.append(conf)

            # Try to resolve
            market_row = {
                "status": sig.get("market_status"),
                "resolution": sig.get("resolution"),
            }
            outcome = _determine_outcome(sig, market_row)

            if outcome is not None:
                cal.resolved_signals += 1
                report.total_resolved += 1

                if outcome:
                    cal.correct_predictions += 1

                # Brier score component
                actual = 1.0 if outcome else 0.0
                brier_terms.append((conf - actual) ** 2)

                # Bucket it
                for bucket in cal.buckets:
                    if bucket.confidence_low <= conf < bucket.confidence_high:
                        bucket.count += 1
                        if outcome:
                            bucket.correct += 1
                        break

        # Compute aggregates
        if confidences:
            cal.mean_confidence = sum(confidences) / len(confidences)
        if cal.resolved_signals > 0:
            cal.actual_accuracy = cal.correct_predictions / cal.resolved_signals
        if brier_terms:
            cal.brier_score = sum(brier_terms) / len(brier_terms)
            all_brier_scores.extend(brier_terms)

        # Suggest calibration adjustment
        if cal.resolved_signals >= 10 and cal.mean_confidence > 0:
            cal.suggested_adjustment = round(
                cal.actual_accuracy / cal.mean_confidence, 3
            )
        else:
            cal.suggested_adjustment = 1.0

        report.pipeline_calibrations[pname] = cal

    # ── Overall metrics ─────────────────────────────────────────────────
    if report.total_resolved > 0:
        total_correct = sum(c.correct_predictions for c in report.pipeline_calibrations.values())
        report.overall_accuracy = total_correct / report.total_resolved
    if all_brier_scores:
        report.overall_brier_score = sum(all_brier_scores) / len(all_brier_scores)

    # ── Recommendations ─────────────────────────────────────────────────
    for pname, cal in report.pipeline_calibrations.items():
        if cal.resolved_signals < 10:
            report.recommendations.append(
                f"{pname}: Insufficient data ({cal.resolved_signals} resolved). "
                f"Need ≥10 resolved signals for reliable calibration."
            )
        elif cal.suggested_adjustment < 0.85:
            report.recommendations.append(
                f"{pname}: Significantly OVERCONFIDENT (adj={cal.suggested_adjustment:.3f}). "
                f"Reduce confidence scores by {(1 - cal.suggested_adjustment) * 100:.0f}%."
            )
        elif cal.suggested_adjustment > 1.15:
            report.recommendations.append(
                f"{pname}: Significantly UNDERCONFIDENT (adj={cal.suggested_adjustment:.3f}). "
                f"Consider increasing confidence or lowering threshold."
            )
        elif cal.brier_score > 0.25:
            report.recommendations.append(
                f"{pname}: Poor Brier score ({cal.brier_score:.3f}). "
                f"Review signal generation logic for accuracy issues."
            )

    if report.total_signals == 0:
        report.recommendations.append(
            "No signals found. Run pipelines in paper mode first, then re-run calibration."
        )
    elif report.total_resolved == 0:
        report.recommendations.append(
            "No resolved markets found. Wait for markets to settle, then re-run."
        )

    await db.close()
    report.duration_seconds = time.monotonic() - start_time
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sibyl Confidence Calibration — retrospective signal accuracy analysis"
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Days of signal history to analyze (default: 30).",
    )
    parser.add_argument(
        "--pipeline", type=str, default=None,
        help="Single pipeline to analyze (e.g., 'crypto').",
    )
    parser.add_argument("--json", dest="json_output", action="store_true", default=False)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    report = await run_calibration(
        days=args.days,
        pipeline_filter=args.pipeline,
    )

    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())

    output_path = args.output or "data/calibration_report.json"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2)
    logger.info("Report written to %s", output_path)


if __name__ == "__main__":
    asyncio.run(main())
