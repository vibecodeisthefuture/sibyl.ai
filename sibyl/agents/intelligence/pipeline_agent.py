"""
Pipeline Agent — wraps PipelineManager in the BaseAgent lifecycle for Sibyl.ai.

PURPOSE:
    The Pipeline Agent manages all category-based signal pipelines (economics,
    weather, sports, crypto, etc.). It initializes the PipelineManager on
    startup, runs all pipelines on a configurable schedule, logs results,
    and writes pipeline statistics to the system_state for monitoring.

LIFECYCLE:
    1. start()  — Initialize PipelineManager, verify readiness
    2. run_cycle() — Execute all pipelines, log results, update system state
    3. stop()   — Close PipelineManager and clean up resources

CONFIGURATION:
    The agent reads poll_interval from config['pipeline.run_interval_seconds']
    (default 900 seconds = 15 minutes).

    Optional categories filter (from config['pipeline.categories']):
      - 'all' or omitted: Run all pipelines
      - Single category string: Run only that pipeline (e.g., 'economics')
      - List of categories: Run only those pipelines (e.g., ['economics', 'weather'])

USAGE:
    from sibyl.agents.intelligence.pipeline_agent import PipelineAgent

    agent = PipelineAgent(db=db, config=system_config)
    await agent.schedule()  # Runs in background
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager
from sibyl.pipelines.pipeline_manager import PipelineManager, PipelineRunResult

logger = logging.getLogger("sibyl.agents.pipeline_agent")


class PipelineAgent(BaseAgent):
    """Wraps PipelineManager in the BaseAgent lifecycle.

    This agent:
      1. Initializes the PipelineManager on startup
      2. Runs all pipelines on each cycle
      3. Logs pipeline results and writes statistics to system state
      4. Supports category filtering for selective pipeline execution
      5. Provides pipeline health status in health_check()

    The agent respects the BaseAgent lifecycle with automatic error recovery,
    exponential backoff on repeated failures, and graceful shutdown.
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: dict[str, Any],
        categories: str | list[str] | None = None,
    ) -> None:
        """Initialize the Pipeline Agent.

        Args:
            db:         Shared DatabaseManager for reading/writing data.
            config:     System configuration dict (from system_config.yaml).
            categories: Optional filter for which pipelines to run.
                        'all' (default) runs all pipelines.
                        Single string: Run only that pipeline (e.g., 'economics').
                        List: Run only those pipelines (e.g., ['economics', 'weather']).
        """
        super().__init__(name="pipeline_agent", db=db, config=config)

        # Pipeline Manager — initialized in start()
        self._pipeline_manager: PipelineManager | None = None

        # Category filtering — normalize input
        self._categories: set[str] = set()
        if categories is not None:
            if isinstance(categories, str):
                if categories.lower() != "all":
                    self._categories.add(categories.lower())
            elif isinstance(categories, list):
                self._categories.update(c.lower() for c in categories)

        # Track last run statistics for health reporting
        self._last_run_result: PipelineRunResult | None = None
        self._last_error: str | None = None

    @property
    def poll_interval(self) -> float:
        """Return seconds between pipeline runs.

        Reads from config['pipeline.run_interval_seconds'] if present,
        defaults to 900 seconds (15 minutes).
        """
        interval = self.config.get("pipeline", {}).get("run_interval_seconds", 900)
        return float(interval)

    async def start(self) -> None:
        """Initialize the PipelineManager and verify readiness.

        This is called once when the agent starts. If initialization fails,
        the agent will not proceed to the run loop.
        """
        self.logger.info(
            "Starting PipelineAgent with poll_interval=%.0f seconds",
            self.poll_interval,
        )

        # Sprint 20: Pass category filter to PipelineManager
        # so only requested pipelines are initialized (saves API calls + init time)
        cat_filter = self._categories if self._categories else None
        self._pipeline_manager = PipelineManager(self.db, categories=cat_filter)

        try:
            ready_count = await self._pipeline_manager.initialize()
            if ready_count > 0:
                self.logger.info(
                    "PipelineManager initialized: %d pipelines ready",
                    ready_count,
                )
            else:
                self.logger.warning("PipelineManager: No pipelines initialized")

            # Log category filter if active
            if self._categories:
                self.logger.info(
                    "Category filter active: %s", ", ".join(sorted(self._categories))
                )
            else:
                self.logger.info("Running all pipelines (no category filter)")

        except Exception as e:
            self.logger.exception("Failed to initialize PipelineManager: %s", e)
            raise

    async def run_cycle(self) -> None:
        """Execute one pipeline run cycle.

        This method:
          1. Calls pipeline_manager.run_all() to execute all pipelines
          2. Logs the result summary
          3. Stores statistics in system_state for monitoring
          4. Tracks the result for health reporting
        """
        if self._pipeline_manager is None:
            self.logger.error("Pipeline manager not initialized")
            return

        try:
            # Run all pipelines
            result = await self._pipeline_manager.run_all()
            self._last_run_result = result
            self._last_error = None

            # Log the summary
            self.logger.info(result.summary())

            # Write statistics to system state for monitoring dashboards
            await self._write_run_stats(result)

        except Exception as e:
            self._last_error = str(e)
            self.logger.exception("Error during pipeline run cycle: %s", e)
            raise

    async def stop(self) -> None:
        """Gracefully shut down the PipelineManager.

        Closes all pipelines and their data clients, then logs completion.
        """
        if self._pipeline_manager is not None:
            try:
                await self._pipeline_manager.close()
                self.logger.info("PipelineManager closed")
            except Exception as e:
                self.logger.exception("Error closing PipelineManager: %s", e)

        self._pipeline_manager = None

    def health_check(self) -> dict[str, Any]:
        """Return agent health status including pipeline information.

        Extends the base health check with pipeline-specific metrics:
          - pipeline_status: Initialization status of all pipelines
          - last_run: Duration and statistics from the last run
          - errors: Count of signals that failed to process
          - last_error: Description of the last error (if any)

        Returns:
            Dict with agent status and pipeline metrics.
        """
        # Start with base health check
        health = super().health_check()

        # Add pipeline-specific metrics
        if self._pipeline_manager:
            health["pipeline_status"] = self._pipeline_manager.get_pipeline_status()

        if self._last_run_result:
            health["last_run"] = {
                "duration_seconds": self._last_run_result.duration_seconds,
                "pipelines_run": self._last_run_result.pipelines_run,
                "pipelines_failed": self._last_run_result.pipelines_failed,
                "total_signals": self._last_run_result.total_signals,
            }

        if self._last_error:
            health["last_error"] = self._last_error

        return health

    # ── Helper methods ────────────────────────────────────────────────────

    async def _write_run_stats(self, result: PipelineRunResult) -> None:
        """Write pipeline run statistics to system_state for monitoring.

        This allows dashboards and alerts to track pipeline performance over time.

        Args:
            result: PipelineRunResult from the run cycle.
        """
        try:
            # Prepare statistics record
            stats = {
                "pipeline_run_duration": result.duration_seconds,
                "pipeline_run_signals_total": result.total_signals,
                "pipeline_run_pipelines_run": result.pipelines_run,
                "pipeline_run_pipelines_failed": result.pipelines_failed,
            }

            # Add per-pipeline signal counts
            for pipeline_name, signals in result.signals_by_pipeline.items():
                stats[f"pipeline_run_signals_{pipeline_name}"] = len(signals)

            # Add correlation stats if available
            if result.correlation_result:
                stats["pipeline_run_correlation_composites"] = len(
                    result.correlation_result.composite_signals
                )
                stats["pipeline_run_correlation_boosted"] = len(
                    result.correlation_result.boosted_signals
                )
                stats["pipeline_run_correlation_warnings"] = len(
                    result.correlation_result.correlation_warnings
                )

            # Write each stat to system_state (key-value pairs for dashboard)
            for key, value in stats.items():
                await self.db.execute(
                    """INSERT INTO system_state (key, value, updated_at)
                       VALUES (?, ?, datetime('now'))
                       ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')""",
                    (key, str(value), str(value)),
                )
            await self.db.commit()
            self.logger.debug("Pipeline run stats written: %d keys", len(stats))

        except Exception as e:
            # Log but don't fail the cycle if stats write fails
            self.logger.warning("Failed to write pipeline stats: %s", e)
