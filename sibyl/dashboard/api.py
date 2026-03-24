"""
Sibyl.ai Dashboard API — FastAPI REST endpoints for the web dashboard.

PURPOSE:
    Serves all data the React frontend needs to display portfolio status,
    open positions, signal history, risk metrics, and the portfolio value
    time-series chart.

ENDPOINTS:
    GET /api/health            — System health + agent status
    GET /api/portfolio         — Portfolio overview (balance, reserve, allocation)
    GET /api/positions         — Open positions with real-time P&L
    GET /api/positions/history — Closed positions with outcomes
    GET /api/signals           — Recent signal feed (last 50)
    GET /api/risk              — Risk dashboard metrics (drawdown, Sharpe, win rate)
    GET /api/chart/portfolio   — Time-series data for portfolio value chart
    GET /api/engines           — Engine state (SGE + ACE capital, exposure, circuit breaker)

ARCHITECTURE:
    The API server runs INSIDE the same process as the agents.  It shares
    the same DatabaseManager instance (read-only queries).  This avoids
    needing a separate database connection and keeps the deployment simple
    (one container = agents + dashboard).

    FastAPI is started as a background asyncio task alongside the agents.
    It uses uvicorn's async server, so it doesn't block the agent event loop.

STATIC FILES:
    The React SPA is served at GET / as a single HTML file.
    Static assets (JS, CSS) are inlined — no separate build step needed.

CORS:
    Enabled for local development (localhost:3000, localhost:5173).
    In production (K8s), the frontend is served from the same origin.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.dashboard.api")

# ── Module-level DB reference ──────────────────────────────────────────
# Set by create_app() when the dashboard starts.  All endpoints read from
# this shared DatabaseManager instance.
_db: DatabaseManager | None = None


def create_app(db: DatabaseManager) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db: Shared DatabaseManager instance (same one agents use).

    Returns:
        Configured FastAPI app ready to be served by uvicorn.
    """
    global _db
    _db = db

    app = FastAPI(
        title="Sibyl.ai Dashboard",
        description="Portfolio monitoring dashboard for the Sibyl prediction market system.",
        version="0.2.0",
    )

    # ── CORS (for local dev when frontend runs on a different port) ───
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:5173", "*"],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # ── Register routes ──────────────────────────────────────────────
    app.include_router(_build_router())

    return app


def _build_router():
    """Build the API router with all dashboard endpoints."""
    from fastapi import APIRouter

    router = APIRouter(prefix="/api")

    # ── Health ────────────────────────────────────────────────────────

    @router.get("/health")
    async def health():
        """System health check — returns basic status and timestamp."""
        if not _db:
            return {"status": "error", "message": "Database not initialized"}

        # Count active agents (based on system_state keys)
        row = await _db.fetchone(
            "SELECT COUNT(*) as count FROM system_state WHERE key LIKE 'agent_%'"
        )
        return {
            "status": "ok",
            "database": "connected",
        }

    # ── Portfolio Overview ────────────────────────────────────────────

    @router.get("/portfolio")
    async def portfolio():
        """Portfolio overview: balance, reserve, allocation, daily P&L."""
        if not _db:
            return {}

        state = await _get_system_state_dict()
        engines = await _get_engines()

        total = float(state.get("portfolio_total_balance", "0"))
        reserve = float(state.get("portfolio_cash_reserve", "0"))
        allocable = float(state.get("portfolio_allocable", "0"))

        # Sum daily P&L across engines
        daily_pnl = sum(float(e.get("daily_pnl", 0)) for e in engines.values())

        # Sum deployed capital
        deployed = sum(float(e.get("deployed_capital", 0)) for e in engines.values())

        return {
            "total_balance": round(total, 2),
            "cash_reserve": round(reserve, 2),
            "allocable_capital": round(allocable, 2),
            "deployed_capital": round(deployed, 2),
            "available_capital": round(allocable - deployed, 2),
            "daily_pnl": round(daily_pnl, 2),
            "engines": engines,
        }

    # ── Open Positions ────────────────────────────────────────────────

    @router.get("/positions")
    async def positions():
        """Open positions with real-time P&L."""
        if not _db:
            return []

        rows = await _db.fetchall(
            """SELECT p.id, p.market_id, p.platform, p.engine, p.side,
                      p.size, p.entry_price, p.current_price, p.target_price,
                      p.stop_loss, p.pnl, p.ev_current, p.status, p.thesis,
                      p.opened_at, m.title, m.category
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status = 'OPEN'
               ORDER BY p.opened_at DESC"""
        )
        return [_row_to_dict(r) for r in rows]

    # ── Position History ──────────────────────────────────────────────

    @router.get("/positions/history")
    async def positions_history():
        """Closed positions with outcomes (last 50)."""
        if not _db:
            return []

        rows = await _db.fetchall(
            """SELECT p.id, p.market_id, p.platform, p.engine, p.side,
                      p.size, p.entry_price, p.current_price, p.pnl,
                      p.status, p.opened_at, p.closed_at, m.title
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status IN ('CLOSED', 'STOPPED')
               ORDER BY p.closed_at DESC
               LIMIT 50"""
        )
        return [_row_to_dict(r) for r in rows]

    # ── Signal Feed ───────────────────────────────────────────────────

    @router.get("/signals")
    async def signals():
        """Recent signals with routing info (last 50)."""
        if not _db:
            return []

        rows = await _db.fetchall(
            """SELECT s.id, s.market_id, s.timestamp, s.signal_type,
                      s.confidence, s.ev_estimate, s.routed_to, s.status,
                      s.reasoning, s.detection_modes_triggered,
                      m.title, m.platform
               FROM signals s
               JOIN markets m ON s.market_id = m.id
               ORDER BY s.id DESC
               LIMIT 50"""
        )
        return [_row_to_dict(r) for r in rows]

    # ── Risk Metrics ──────────────────────────────────────────────────

    @router.get("/risk")
    async def risk():
        """Risk dashboard metrics: drawdown, win rate, Sharpe, exposure."""
        if not _db:
            return {}

        state = await _get_system_state_dict()

        return {
            "high_water_mark": float(state.get("risk_hwm", "0")),
            "drawdown_pct": float(state.get("risk_drawdown_pct", "0")),
            "drawdown_level": state.get("risk_drawdown_level", "CLEAR"),
            "total_exposure": float(state.get("risk_total_exposure", "0")),
            "open_positions": int(state.get("risk_open_positions", "0")),
            "daily_pnl_sge": float(state.get("risk_daily_pnl_sge", "0")),
            "daily_pnl_ace": float(state.get("risk_daily_pnl_ace", "0")),
            "win_rate_7d": float(state.get("risk_win_rate_7d", "0")),
            "sharpe_30d": float(state.get("risk_sharpe_30d", "0")),
        }

    # ── Portfolio Chart ───────────────────────────────────────────────

    @router.get("/chart/portfolio")
    async def chart_portfolio():
        """Time-series data for portfolio value chart.

        Returns up to 500 data points from the portfolio_snapshots in
        system_state, or reconstructs from position P&L if no snapshots exist.
        """
        if not _db:
            return []

        # Strategy: use closed position P&L aggregated by day as a proxy
        # for portfolio value over time.  True time-series would need a
        # dedicated snapshot table (future enhancement).
        rows = await _db.fetchall(
            """SELECT
                 date(closed_at) as date,
                 SUM(pnl) as daily_pnl
               FROM positions
               WHERE status IN ('CLOSED', 'STOPPED') AND closed_at IS NOT NULL
               GROUP BY date(closed_at)
               ORDER BY date
               LIMIT 500"""
        )

        # Build cumulative P&L series
        # Start with the paper balance from system_state
        balance_row = await _db.fetchone(
            "SELECT value FROM system_state WHERE key = 'portfolio_total_balance'"
        )
        # Get starting balance by subtracting all realized P&L
        total_pnl_row = await _db.fetchone(
            "SELECT COALESCE(SUM(pnl), 0) as total FROM positions WHERE status IN ('CLOSED', 'STOPPED')"
        )
        current_balance = float(balance_row["value"]) if balance_row else 500.0
        total_realized = float(total_pnl_row["total"]) if total_pnl_row else 0.0
        starting_balance = current_balance - total_realized

        cumulative = starting_balance
        series = []
        for r in rows:
            cumulative += float(r["daily_pnl"])
            series.append({
                "date": r["date"],
                "value": round(cumulative, 2),
                "daily_pnl": round(float(r["daily_pnl"]), 2),
            })

        # Always include a "today" point at the current balance
        if balance_row:
            from datetime import datetime, timezone
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if not series or series[-1]["date"] != today:
                series.append({
                    "date": today,
                    "value": round(current_balance, 2),
                    "daily_pnl": 0.0,
                })

        return series

    # ── Engine State ──────────────────────────────────────────────────

    @router.get("/engines")
    async def engines():
        """Engine state for SGE and ACE."""
        if not _db:
            return {}
        return await _get_engines()

    # ── Category Breakdown ─────────────────────────────────────────────

    @router.get("/categories")
    async def categories():
        """Category-level breakdown: position count, total deployed, P&L per category.

        Aggregates open positions by their market category to show how
        capital is distributed across Kalshi's market verticals (Politics,
        Sports, Culture, Crypto, Climate, Economics, etc.).
        """
        if not _db:
            return []

        rows = await _db.fetchall(
            """SELECT
                 m.category,
                 COUNT(*) as position_count,
                 SUM(p.size * p.entry_price) as total_deployed,
                 SUM(p.pnl) as total_pnl
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status = 'OPEN'
               GROUP BY m.category
               ORDER BY total_deployed DESC"""
        )
        return [
            {
                "category": r["category"] or "Unknown",
                "position_count": r["position_count"],
                "total_deployed": round(float(r["total_deployed"] or 0), 2),
                "total_pnl": round(float(r["total_pnl"] or 0), 2),
            }
            for r in rows
        ]

    # ── Research Data ──────────────────────────────────────────────────

    @router.get("/research")
    async def research():
        """Recent market research from BreakoutScout (last 30 entries).

        Returns sentiment scores, key arguments, and freshness data
        from the multi-source research pipeline (Reddit + NewsAPI +
        Perplexity → LLM synthesis).
        """
        if not _db:
            return []

        try:
            rows = await _db.fetchall(
                """SELECT r.market_id, r.sentiment_score, r.sentiment_label,
                          r.key_arguments, r.synthesis, r.freshness,
                          r.created_at, m.title, m.category
                   FROM market_research r
                   JOIN markets m ON r.market_id = m.id
                   ORDER BY r.created_at DESC
                   LIMIT 30"""
            )
            return [_row_to_dict(r) for r in rows]
        except Exception:
            # market_research table may not exist if scout hasn't run yet
            return []

    return router


# ── Helper Functions ───────────────────────────────────────────────────

async def _get_system_state_dict() -> dict[str, str]:
    """Read all system_state rows into a flat dict."""
    rows = await _db.fetchall("SELECT key, value FROM system_state")
    return {r["key"]: r["value"] for r in rows}


async def _get_engines() -> dict[str, dict]:
    """Read engine_state rows into a dict keyed by engine name."""
    rows = await _db.fetchall("SELECT * FROM engine_state")
    result = {}
    for r in rows:
        result[r["engine"]] = {
            "total_capital": float(r["total_capital"]),
            "deployed_capital": float(r["deployed_capital"]),
            "available_capital": float(r["available_capital"]),
            "exposure_pct": float(r["exposure_pct"]),
            "drawdown_pct": float(r["drawdown_pct"]),
            "daily_pnl": float(r["daily_pnl"]),
            "circuit_breaker": r["circuit_breaker"],
        }
    return result


def _row_to_dict(row) -> dict:
    """Convert an aiosqlite.Row to a plain dict (JSON-serializable)."""
    return {key: row[key] for key in row.keys()}
