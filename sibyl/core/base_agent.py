"""
Abstract base class for all Sibyl agents.

Every agent in Sibyl (monitors, intelligence, signal router, etc.) inherits
from BaseAgent.  This class provides:

  1. A standard lifecycle: start() → run_cycle() (repeats) → stop()
  2. Automatic error recovery with exponential backoff.
  3. Health check reporting for monitoring.
  4. Graceful shutdown via asyncio task cancellation.

HOW TO CREATE A NEW AGENT:
    class MyAgent(BaseAgent):
        def __init__(self, db, config):
            super().__init__(name="my_agent", db=db, config=config)

        @property
        def poll_interval(self) -> float:
            return 30.0  # Run every 30 seconds

        async def start(self):
            # One-time setup (connect to APIs, etc.)
            pass

        async def run_cycle(self):
            # This runs on every tick. Do your work here.
            data = await fetch_something()
            await self.db.execute("INSERT ...", (data,))
            await self.db.commit()

        async def stop(self):
            # Cleanup (close connections, etc.)
            pass

HOW THE RUN LOOP WORKS:
    1. schedule() creates an asyncio background task.
    2. run() calls start() once, then enters a while loop.
    3. Each iteration calls run_cycle(), then sleeps for poll_interval.
    4. If run_cycle() throws an exception, the error is logged and the
       agent keeps running. After 5+ consecutive errors, it backs off.
    5. shutdown() sets _running = False and cancels the asyncio task.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from sibyl.core.database import DatabaseManager
from sibyl.core.logging import get_agent_logger


class BaseAgent(ABC):
    """Base class providing lifecycle management for all Sibyl agents.

    Each agent implements:
      - start()      — called once at initialization
      - run_cycle()  — called repeatedly on the agent's polling schedule
      - stop()       — called once for graceful shutdown

    The base class handles the run loop, error recovery, and health reporting.
    """

    def __init__(
        self,
        name: str,
        db: DatabaseManager,
        config: dict[str, Any],
        engine: str | None = None,
    ) -> None:
        """Initialize the base agent.

        Args:
            name:   Unique agent name (used in logs and health checks).
            db:     Shared DatabaseManager instance for reading/writing data.
            config: System configuration dict (from system_config.yaml).
            engine: Optional engine tag ('SGE' or 'ACE') for engine-specific agents.
        """
        self.name = name
        self.db = db
        self.config = config
        self.engine = engine
        self.logger = get_agent_logger(name, engine)

        # Internal state — do not modify directly from subclasses
        self._running = False       # Set to True when run loop is active
        self._cycle_count = 0       # Total number of completed polling cycles
        self._error_count = 0       # Total number of errors (for backoff logic)
        self._task: asyncio.Task | None = None  # Reference to the background task

    # ── Abstract methods (subclasses MUST implement these) ────────────

    @abstractmethod
    async def start(self) -> None:
        """One-time initialization (e.g., connect to APIs, warm caches)."""
        ...

    @abstractmethod
    async def run_cycle(self) -> None:
        """Execute one polling cycle of this agent's work.

        This is where the agent does its actual job.  It's called repeatedly
        with poll_interval seconds of sleep between calls.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown (e.g., close connections, flush state)."""
        ...

    # ── Overridable properties ────────────────────────────────────────

    @property
    def poll_interval(self) -> float:
        """Seconds between run_cycle() invocations.  Override in subclass.

        Example: return 30.0 to run every 30 seconds.
        """
        return 60.0

    # ── Run loop ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main run loop — repeatedly calls run_cycle() with error recovery.

        This method runs indefinitely until shutdown() is called.
        You should NOT call this directly — use schedule() instead.
        """
        self._running = True
        self.logger.info("Agent starting: %s", self.name)

        # Step 1: Call start() once.  If it fails, the agent won't run.
        try:
            await self.start()
        except Exception:
            self.logger.exception("Failed to start agent: %s", self.name)
            return

        # Step 2: Enter the polling loop.
        while self._running:
            try:
                await self.run_cycle()
                self._cycle_count += 1
            except asyncio.CancelledError:
                # Cancellation is a normal shutdown path — not an error
                self.logger.info("Agent cancelled: %s", self.name)
                break
            except Exception:
                # Log the error but keep running.  The agent is designed to
                # survive transient failures (network timeouts, API errors).
                self._error_count += 1
                self.logger.exception(
                    "Error in cycle %d of %s (total errors: %d)",
                    self._cycle_count,
                    self.name,
                    self._error_count,
                )
                # Exponential backoff after 5+ consecutive errors.
                # Caps at 5 minutes (300 seconds) to avoid infinite waits.
                if self._error_count > 5:
                    backoff = min(self._error_count * 10, 300)
                    self.logger.warning("Backing off %ds due to repeated errors", backoff)
                    await asyncio.sleep(backoff)

            # Sleep until the next cycle
            await asyncio.sleep(self.poll_interval)

        # Step 3: Call stop() for cleanup.
        try:
            await self.stop()
        except Exception:
            self.logger.exception("Error during shutdown of %s", self.name)

        self.logger.info(
            "Agent stopped: %s (cycles=%d, errors=%d)",
            self.name,
            self._cycle_count,
            self._error_count,
        )

    # ── Task management ───────────────────────────────────────────────

    def schedule(self) -> asyncio.Task:
        """Schedule this agent as a background asyncio task.

        Usage:
            task = agent.schedule()  # Agent starts running in background
            # ... later ...
            await agent.shutdown()   # Gracefully stop the agent
        """
        self._task = asyncio.create_task(self.run(), name=f"agent-{self.name}")
        return self._task

    async def shutdown(self) -> None:
        """Request graceful shutdown.

        Sets the _running flag to False so the run loop exits after the
        current cycle completes, then cancels the asyncio task.
        """
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # Expected — cancellation is how we stop the task

    # ── Health monitoring ─────────────────────────────────────────────

    def health_check(self) -> dict[str, Any]:
        """Return agent health status for monitoring and diagnostics.

        Returns a dict like:
            {"agent": "kalshi_monitor", "engine": None, "running": True,
             "cycles": 42, "errors": 0}
        """
        return {
            "agent": self.name,
            "engine": self.engine,
            "running": self._running,
            "cycles": self._cycle_count,
            "errors": self._error_count,
        }
