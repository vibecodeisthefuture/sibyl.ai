"""
Category Performance Tracker — aggregates win rate, ROI, and strategy effectiveness.

PURPOSE:
    Periodically computes performance metrics per market category from the
    positions table and writes them to `category_performance` in system_state.
    These metrics are used by:
      1. The dashboard (to display per-category performance)
      2. The auto-tuning system (future: adjust category modifiers based on results)
      3. The correlation penalty scaler (dynamic sizing based on category track record)

HOW IT WORKS:
    1. Queries all CLOSED/STOPPED positions grouped by market category.
    2. Computes per-category: win rate, total P&L, ROI, position count, avg hold time.
    3. Writes results as JSON to system_state under the key `category_performance`.
    4. Also writes a `category_performance_updated_at` timestamp.

USAGE:
    tracker = CategoryPerformanceTracker(db=db)
    stats = await tracker.compute()
    await tracker.persist(stats)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from typing import Any

from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.backtesting.category_tracker")


@dataclass
class CategoryStats:
    """Aggregated performance for a single category."""
    category: str
    total_positions: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_deployed: float = 0.0
    avg_hold_hours: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    @property
    def roi(self) -> float:
        return self.total_pnl / self.total_deployed if self.total_deployed > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["win_rate"] = round(self.win_rate, 4)
        d["roi"] = round(self.roi, 4)
        return d


class CategoryPerformanceTracker:
    """Computes and persists category-level performance metrics.

    Usage:
        tracker = CategoryPerformanceTracker(db=db)
        stats = await tracker.compute()
        await tracker.persist(stats)
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def compute(self) -> dict[str, CategoryStats]:
        """Query the DB and compute per-category performance.

        Returns:
            Dict of category name → CategoryStats.
        """
        rows = await self._db.fetchall(
            """SELECT
                 m.category,
                 COUNT(*) as total,
                 SUM(CASE WHEN p.pnl > 0 THEN 1 ELSE 0 END) as wins,
                 SUM(CASE WHEN p.pnl <= 0 THEN 1 ELSE 0 END) as losses,
                 COALESCE(SUM(p.pnl), 0) as total_pnl,
                 COALESCE(SUM(p.size * p.entry_price), 0) as total_deployed,
                 MAX(p.pnl) as best_trade,
                 MIN(p.pnl) as worst_trade,
                 AVG(
                    CASE WHEN p.closed_at IS NOT NULL AND p.opened_at IS NOT NULL
                    THEN (julianday(p.closed_at) - julianday(p.opened_at)) * 24
                    ELSE NULL END
                 ) as avg_hold_hours
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status IN ('CLOSED', 'STOPPED')
               GROUP BY m.category
               ORDER BY total_pnl DESC"""
        )

        result: dict[str, CategoryStats] = {}
        for r in rows:
            cat = r["category"] or "Unknown"
            result[cat] = CategoryStats(
                category=cat,
                total_positions=r["total"],
                wins=r["wins"],
                losses=r["losses"],
                total_pnl=round(float(r["total_pnl"]), 4),
                total_deployed=round(float(r["total_deployed"]), 4),
                avg_hold_hours=round(float(r["avg_hold_hours"] or 0), 2),
                best_trade=round(float(r["best_trade"] or 0), 4),
                worst_trade=round(float(r["worst_trade"] or 0), 4),
            )

        logger.info(
            "Category performance computed: %d categories, total P&L=$%.2f",
            len(result), sum(s.total_pnl for s in result.values()),
        )

        return result

    async def persist(self, stats: dict[str, CategoryStats]) -> None:
        """Write category performance to system_state.

        Stored as JSON under key `category_performance`.
        """
        payload = {cat: s.to_dict() for cat, s in stats.items()}
        json_str = json.dumps(payload)

        await self._db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES ('category_performance', ?, datetime('now'))""",
            (json_str,),
        )
        await self._db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES ('category_performance_updated_at', datetime('now'), datetime('now'))"""
        )
        await self._db.commit()

        logger.info("Category performance persisted to system_state")

    async def get_category_win_rate(self, category: str) -> float:
        """Get the historical win rate for a specific category.

        Returns 0.5 (neutral) if no data exists for the category.
        Used by the dynamic correlation penalty scaler.
        """
        row = await self._db.fetchone(
            """SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN p.pnl > 0 THEN 1 ELSE 0 END) as wins
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status IN ('CLOSED', 'STOPPED')
                 AND m.category = ?""",
            (category,),
        )
        if not row or row["total"] == 0:
            return 0.5  # Neutral default
        return row["wins"] / row["total"]

    async def get_category_roi(self, category: str) -> float:
        """Get the historical ROI for a specific category.

        Returns 0.0 (neutral) if no data exists.
        """
        row = await self._db.fetchone(
            """SELECT
                 COALESCE(SUM(p.pnl), 0) as total_pnl,
                 COALESCE(SUM(p.size * p.entry_price), 0) as total_deployed
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status IN ('CLOSED', 'STOPPED')
                 AND m.category = ?""",
            (category,),
        )
        if not row or float(row["total_deployed"]) == 0:
            return 0.0
        return float(row["total_pnl"]) / float(row["total_deployed"])
