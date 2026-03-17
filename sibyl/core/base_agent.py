"""Abstract base class for all Sibyl agents."""

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
        self.name = name
        self.db = db
        self.config = config
        self.engine = engine
        self.logger = get_agent_logger(name, engine)
        self._running = False
        self._cycle_count = 0
        self._error_count = 0
        self._task: asyncio.Task | None = None

    @abstractmethod
    async def start(self) -> None:
        """One-time initialization (e.g., connect to APIs, warm caches)."""
        ...

    @abstractmethod
    async def run_cycle(self) -> None:
        """Execute one polling cycle of this agent's work."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown (e.g., close connections, flush state)."""
        ...

    @property
    def poll_interval(self) -> float:
        """Seconds between run_cycle() invocations. Override in subclass."""
        return 60.0

    async def run(self) -> None:
        """Main run loop — repeatedly calls run_cycle() with error recovery."""
        self._running = True
        self.logger.info("Agent starting: %s", self.name)

        try:
            await self.start()
        except Exception:
            self.logger.exception("Failed to start agent: %s", self.name)
            return

        while self._running:
            try:
                await self.run_cycle()
                self._cycle_count += 1
            except asyncio.CancelledError:
                self.logger.info("Agent cancelled: %s", self.name)
                break
            except Exception:
                self._error_count += 1
                self.logger.exception(
                    "Error in cycle %d of %s (total errors: %d)",
                    self._cycle_count,
                    self.name,
                    self._error_count,
                )
                # Back off on repeated errors
                if self._error_count > 5:
                    backoff = min(self._error_count * 10, 300)
                    self.logger.warning("Backing off %ds due to repeated errors", backoff)
                    await asyncio.sleep(backoff)

            await asyncio.sleep(self.poll_interval)

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

    def schedule(self) -> asyncio.Task:
        """Schedule this agent as a background asyncio task."""
        self._task = asyncio.create_task(self.run(), name=f"agent-{self.name}")
        return self._task

    async def shutdown(self) -> None:
        """Request graceful shutdown."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def health_check(self) -> dict[str, Any]:
        """Return agent health status for monitoring."""
        return {
            "agent": self.name,
            "engine": self.engine,
            "running": self._running,
            "cycles": self._cycle_count,
            "errors": self._error_count,
        }
