"""
Dashboard Server — starts uvicorn alongside the agent event loop.

HOW IT WORKS:
    The dashboard runs as an asyncio task inside the same event loop as the
    agents.  This is achieved by using uvicorn's async serve() API instead
    of its blocking run() method.

    This means:
        - One process, one event loop, one container.
        - The API shares the same DatabaseManager as the agents (read-only).
        - No extra ports for inter-process communication.
        - Shutdown is coordinated with the agent shutdown flow.

USAGE (called from __main__.py):
    from sibyl.dashboard.server import start_dashboard

    server_task = await start_dashboard(db, host="0.0.0.0", port=8088)
    # ... later, on shutdown ...
    server_task.cancel()

PORT:
    Default: 8088 (configurable via --dashboard-port CLI arg or env var).
    This avoids conflicts with common dev ports (3000, 5173, 8080).
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from sibyl.core.database import DatabaseManager
from sibyl.dashboard.api import create_app
from sibyl.dashboard.frontend import DASHBOARD_HTML

logger = logging.getLogger("sibyl.dashboard.server")


async def start_dashboard(
    db: DatabaseManager,
    host: str = "0.0.0.0",
    port: int = 8088,
) -> asyncio.Task:
    """Start the dashboard as a background asyncio task.

    Creates the FastAPI app, mounts the frontend, and runs uvicorn
    in async mode (non-blocking).

    Args:
        db:   Shared DatabaseManager instance.
        host: Bind address (default: all interfaces).
        port: HTTP port (default: 8088).

    Returns:
        The asyncio.Task running the uvicorn server (cancel it to stop).
    """
    from fastapi.responses import HTMLResponse

    app = create_app(db)

    # ── Mount the React SPA at the root ──────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def serve_frontend():
        """Serve the single-page React dashboard."""
        return HTMLResponse(content=DASHBOARD_HTML, status_code=200)

    # ── Configure uvicorn for async mode ─────────────────────────────
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",  # Reduce uvicorn noise (our logger handles the rest)
        access_log=False,     # Disable per-request access logs
    )
    server = uvicorn.Server(config)

    # Run uvicorn as a background task (non-blocking)
    task = asyncio.create_task(server.serve(), name="dashboard-server")

    logger.info("Dashboard started at http://%s:%d", host, port)
    return task
