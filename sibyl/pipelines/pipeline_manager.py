"""
Pipeline Manager — orchestrates all category signal pipelines.

Provides a single entry point to initialize, run, and close all pipelines.

CONCURRENCY MODEL (Sprint 16 optimization):
    All 8 category pipelines run CONCURRENTLY via asyncio.gather().
    Each pipeline is independent — they read from separate data sources,
    query the same DB (SQLite WAL mode supports concurrent reads), and
    write signals to separate market_id rows.

    Only the correlation engine runs AFTER all pipelines complete, because
    it needs the full cross-category signal set as input.

    Before (sequential): ~120s for 8 pipelines (15s each avg)
    After (concurrent):  ~20s  for 8 pipelines (bounded by slowest)

    This is the difference between missing a market move and catching it.

USAGE:
    from sibyl.pipelines.pipeline_manager import PipelineManager

    manager = PipelineManager(db)
    await manager.initialize()
    result = await manager.run_all()
    print(result.summary())
    await manager.close()
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from sibyl.pipelines.base_pipeline import BasePipeline, PipelineSignal
from sibyl.pipelines.economics_pipeline import EconomicsPipeline
from sibyl.pipelines.weather_pipeline import WeatherPipeline
from sibyl.pipelines.sports_pipeline import SportsPipeline
from sibyl.pipelines.crypto_pipeline import CryptoPipeline
from sibyl.pipelines.culture_pipeline import CulturePipeline
from sibyl.pipelines.science_pipeline import SciencePipeline
from sibyl.pipelines.geopolitics_pipeline import GeopoliticsPipeline
from sibyl.pipelines.financial_pipeline import FinancialPipeline
from sibyl.pipelines.correlation_engine import (
    CrossCategoryCorrelationEngine,
    CorrelationResult,
)

if TYPE_CHECKING:
    from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.pipelines.manager")


# ── Per-pipeline timeout (seconds) ──────────────────────────────────────
# No single pipeline should block the entire run.  If a data source is
# down or slow, we timeout and move on.
PIPELINE_RUN_TIMEOUT = 600.0  # 600s max per pipeline run — API calls can be slow
PIPELINE_INIT_TIMEOUT = 15.0  # 15s max per pipeline init


@dataclass
class PipelineRunResult:
    """Aggregated results from running all pipelines."""
    signals_by_pipeline: dict[str, list[PipelineSignal]] = field(default_factory=dict)
    correlation_result: CorrelationResult | None = None
    errors: dict[str, str] = field(default_factory=dict)
    duration_seconds: float = 0.0
    pipelines_run: int = 0
    pipelines_failed: int = 0
    per_pipeline_timing: dict[str, float] = field(default_factory=dict)

    @property
    def total_signals(self) -> int:
        total = sum(len(sigs) for sigs in self.signals_by_pipeline.values())
        if self.correlation_result:
            total += len(self.correlation_result.composite_signals)
        return total

    def summary(self) -> str:
        """Human-readable summary of the pipeline run."""
        lines = [
            f"Pipeline Run Summary ({self.duration_seconds:.1f}s)",
            f"  Pipelines: {self.pipelines_run} run, {self.pipelines_failed} failed",
            f"  Total signals: {self.total_signals}",
        ]
        for name, sigs in sorted(self.signals_by_pipeline.items()):
            timing = self.per_pipeline_timing.get(name, 0.0)
            lines.append(f"    {name}: {len(sigs)} signals ({timing:.1f}s)")
        if self.correlation_result:
            cr = self.correlation_result
            lines.append(
                f"  Correlation: {len(cr.composite_signals)} composites, "
                f"{len(cr.boosted_signals)} boosted, "
                f"{len(cr.correlation_warnings)} warnings"
            )
        if self.errors:
            lines.append("  Errors:")
            for name, err in self.errors.items():
                lines.append(f"    {name}: {err}")
        return "\n".join(lines)


class PipelineManager:
    """Orchestrates all category signal pipelines with concurrent execution.

    All 8 pipelines initialize and run concurrently via asyncio.gather().
    Each pipeline is error-isolated — one failure doesn't affect others.
    Per-pipeline timeouts prevent any single slow source from blocking.
    The correlation engine runs after all pipelines complete.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        self._pipelines: list[BasePipeline] = []
        self._correlation_engine: CrossCategoryCorrelationEngine | None = None
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def pipeline_count(self) -> int:
        return len(self._pipelines)

    # ── Initialization ──────────────────────────────────────────────────

    async def _init_one(self, pipeline: BasePipeline) -> bool:
        """Initialize a single pipeline with timeout and error isolation."""
        try:
            ok = await asyncio.wait_for(
                pipeline.initialize(),
                timeout=PIPELINE_INIT_TIMEOUT,
            )
            return bool(ok)
        except asyncio.TimeoutError:
            logger.error(
                "%s pipeline init TIMED OUT (>%.0fs)",
                pipeline.PIPELINE_NAME, PIPELINE_INIT_TIMEOUT,
            )
            return False
        except Exception as e:
            logger.error(
                "Failed to initialize %s pipeline: %s",
                pipeline.PIPELINE_NAME, e,
            )
            return False

    async def initialize(self) -> int:
        """Initialize all pipelines CONCURRENTLY and the correlation engine.

        Returns:
            Number of successfully initialized pipelines.
        """
        self._pipelines = [
            EconomicsPipeline(self._db),
            WeatherPipeline(self._db),
            SportsPipeline(self._db),
            CryptoPipeline(self._db),
            CulturePipeline(self._db),
            SciencePipeline(self._db),
            GeopoliticsPipeline(self._db),
            FinancialPipeline(self._db),
        ]

        # Fire all 8 init tasks concurrently
        init_start = time.monotonic()
        results = await asyncio.gather(
            *(self._init_one(p) for p in self._pipelines),
            return_exceptions=False,
        )
        init_duration = time.monotonic() - init_start

        ready_count = sum(1 for r in results if r)

        self._correlation_engine = CrossCategoryCorrelationEngine(self._db)
        self._initialized = ready_count > 0

        logger.info(
            "PipelineManager initialized: %d/%d pipelines ready (%.1fs concurrent)",
            ready_count, len(self._pipelines), init_duration,
        )
        return ready_count

    # ── Execution ───────────────────────────────────────────────────────

    async def _run_one(
        self, pipeline: BasePipeline
    ) -> tuple[str, list[PipelineSignal] | None, str | None, float]:
        """Run a single pipeline with timeout and error isolation.

        Returns:
            (pipeline_name, signals_or_None, error_or_None, duration_seconds)
        """
        name = pipeline.PIPELINE_NAME
        start = time.monotonic()
        try:
            signals = await asyncio.wait_for(
                pipeline.run(),
                timeout=PIPELINE_RUN_TIMEOUT,
            )
            duration = time.monotonic() - start
            logger.info(
                "%s pipeline: %d signals (%.1fs)",
                name, len(signals), duration,
            )
            return name, signals, None, duration
        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            err = f"TIMED OUT (>{PIPELINE_RUN_TIMEOUT:.0f}s)"
            logger.error("%s pipeline %s", name, err)
            return name, None, err, duration
        except Exception as e:
            duration = time.monotonic() - start
            logger.error("%s pipeline failed: %s", name, e)
            return name, None, str(e), duration

    async def run_all(self) -> PipelineRunResult:
        """Run all pipelines CONCURRENTLY, then run correlation engine.

        All 8 pipelines execute simultaneously via asyncio.gather().
        Total wall-clock time = max(individual pipeline times) instead
        of sum(individual pipeline times).

        Returns:
            PipelineRunResult with signals, errors, and per-pipeline timing.
        """
        result = PipelineRunResult()
        start = time.monotonic()

        if not self._initialized:
            logger.warning("PipelineManager not initialized")
            return result

        # Filter to initialized pipelines only
        active_pipelines = [p for p in self._pipelines if p.initialized]
        if not active_pipelines:
            logger.warning("No initialized pipelines to run")
            return result

        # Fire all pipeline runs concurrently
        outcomes = await asyncio.gather(
            *(self._run_one(p) for p in active_pipelines),
            return_exceptions=False,
        )

        # Collect results
        all_signals: list[PipelineSignal] = []
        for name, signals, error, duration in outcomes:
            result.pipelines_run += 1
            result.per_pipeline_timing[name] = round(duration, 2)

            if error:
                result.pipelines_failed += 1
                result.errors[name] = error
            elif signals is not None:
                result.signals_by_pipeline[name] = signals
                all_signals.extend(signals)

        # Run correlation engine on combined signals (must be sequential —
        # needs the full cross-category signal set)
        if self._correlation_engine and len(all_signals) >= 2:
            try:
                corr_result = await self._correlation_engine.analyze(all_signals)
                result.correlation_result = corr_result
            except Exception as e:
                result.errors["correlation"] = str(e)
                logger.error("Correlation engine failed: %s", e)

        result.duration_seconds = time.monotonic() - start
        logger.info(result.summary())
        return result

    async def run_single(self, pipeline_name: str) -> list[PipelineSignal]:
        """Run a single pipeline by name.

        Args:
            pipeline_name: Pipeline name (e.g., "economics", "weather").

        Returns:
            List of generated signals.
        """
        for pipeline in self._pipelines:
            if pipeline.PIPELINE_NAME == pipeline_name:
                if not pipeline.initialized:
                    logger.warning("%s pipeline not initialized", pipeline_name)
                    return []
                return await pipeline.run()

        logger.error("Pipeline not found: %s", pipeline_name)
        return []

    async def close(self) -> None:
        """Close all pipelines concurrently."""
        async def _close_one(p: BasePipeline) -> None:
            try:
                await p.close()
            except Exception:
                pass

        await asyncio.gather(*(
            _close_one(p) for p in self._pipelines
        ))
        self._initialized = False
        logger.info("PipelineManager closed")

    def get_pipeline_status(self) -> dict[str, dict[str, Any]]:
        """Get initialization status of all pipelines."""
        return {
            p.PIPELINE_NAME: {
                "category": p.CATEGORY,
                "initialized": p.initialized,
                "client_count": len(p._clients),
                "dedup_window_min": p.DEDUP_WINDOW_MINUTES,
            }
            for p in self._pipelines
        }
