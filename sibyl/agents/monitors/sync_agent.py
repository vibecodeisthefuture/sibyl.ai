"""
Cross-Platform Sync Agent — unifies data and detects arbitrage opportunities.

PURPOSE:
    This agent finds the SAME real-world event on both Polymarket and Kalshi,
    then compares their prices.  If prices diverge significantly, it flags
    an arbitrage opportunity.

WHY THIS MATTERS:
    If Polymarket says "Fed rate cut" is 70% likely, but Kalshi says 58%,
    there's a 12% spread.  Since we can only trade on Kalshi (US restriction),
    we can use this signal to inform our trading confidence.

HOW MARKET MATCHING WORKS:
    1. Load all active markets from both platforms (from SQLite).
    2. For each Polymarket market, find the best-matching Kalshi market using:
       - Title similarity (60% weight) — fuzzy string matching via difflib
       - Category match (20% weight) — exact match on category tag
       - Close date proximity (20% weight) — same expiration date
    3. If the combined score exceeds the threshold (default: 0.55), it's a match.

WHAT HAPPENS AFTER MATCHING:
    - Price divergence check: compares latest prices and writes alerts to
      the `system_state` table when the spread exceeds 5% (configurable).
    - Event ID tagging: assigns a shared `event_id` to both markets so
      downstream agents know they're tracking the same real-world event.

CONFIGURATION (from system_config.yaml → cross_platform section):
    similarity_threshold:       0.55   (minimum score to consider a match)
    price_divergence_alert_pct: 0.05   (5% spread triggers an alert)
"""

from __future__ import annotations

import difflib
import logging
from datetime import datetime
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.sync")


class CrossPlatformSyncAgent(BaseAgent):
    """Matches markets across platforms and detects price divergences.

    This agent runs on a 5-minute cycle.  It:
      1. Queries active markets on both platforms
      2. Matches pairs using fuzzy title matching + category + close_date
      3. Compares prices and flags divergences above threshold
      4. Auto-assigns event_id tags for matched market pairs
    """

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="cross_platform_sync", db=db, config=config)
        self._sync_config = config.get("cross_platform", {})
        self._matched_pairs: list[dict] = []

    @property
    def poll_interval(self) -> float:
        """Run every 30 seconds for near-real-time divergence detection."""
        return float(
            self.config.get("polling", {}).get("sync_interval_seconds", 30)
        )

    async def start(self) -> None:
        self.logger.info("Cross-Platform Sync Agent started")

    async def run_cycle(self) -> None:
        # ── Step 1: Load active markets from both platforms ───────────
        poly_markets = await self.db.fetchall(
            """SELECT id, title, category, close_date, event_id
               FROM markets WHERE platform = 'polymarket' AND status = 'active'"""
        )
        kalshi_markets = await self.db.fetchall(
            """SELECT id, title, category, close_date, event_id
               FROM markets WHERE platform = 'kalshi' AND status = 'active'"""
        )

        if not poly_markets or not kalshi_markets:
            self.logger.debug(
                "Insufficient markets for sync (poly=%d, kalshi=%d)",
                len(poly_markets), len(kalshi_markets),
            )
            return

        # ── Step 2: Match markets across platforms ────────────────────
        similarity_threshold = float(self._sync_config.get("similarity_threshold", 0.55))
        self._matched_pairs = []

        for pm in poly_markets:
            best_match = None
            best_score = 0.0

            for km in kalshi_markets:
                score = self._compute_similarity(pm, km)
                if score > best_score:
                    best_score = score
                    best_match = km

            if best_match and best_score >= similarity_threshold:
                self._matched_pairs.append({
                    "polymarket_id": pm["id"],
                    "kalshi_id": best_match["id"],
                    "similarity": best_score,
                    "poly_title": pm["title"],
                    "kalshi_title": best_match["title"],
                })

        self.logger.info(
            "Found %d cross-platform market matches (threshold=%.2f)",
            len(self._matched_pairs), similarity_threshold,
        )

        # ── Step 3: Detect price divergences ──────────────────────────
        divergence_threshold = float(self._sync_config.get("price_divergence_alert_pct", 0.05))
        await self._check_divergences(divergence_threshold)

        # ── Step 4: Auto-tag event_id on matched pairs ────────────────
        await self._tag_event_ids()

    async def stop(self) -> None:
        self.logger.info("Cross-Platform Sync Agent stopped")

    # ── Internal methods ──────────────────────────────────────────────

    def _compute_similarity(self, pm_row: Any, km_row: Any) -> float:
        """Compute match score between a Polymarket and Kalshi market.

        Combines:
          - Title similarity (SequenceMatcher): 60% weight
          - Same category: 20% weight
          - Close date proximity: 20% weight
        """
        # Title similarity
        pm_title = (pm_row["title"] or "").lower().strip()
        km_title = (km_row["title"] or "").lower().strip()
        title_score = difflib.SequenceMatcher(None, pm_title, km_title).ratio()

        # Category match
        cat_score = 1.0 if pm_row["category"] == km_row["category"] else 0.0

        # Close date proximity (simple check — same date string)
        date_score = 0.0
        if pm_row["close_date"] and km_row["close_date"]:
            try:
                pm_date = pm_row["close_date"][:10]  # YYYY-MM-DD
                km_date = km_row["close_date"][:10]
                date_score = 1.0 if pm_date == km_date else 0.3
            except (TypeError, IndexError):
                date_score = 0.0

        return title_score * 0.60 + cat_score * 0.20 + date_score * 0.20

    async def _check_divergences(self, threshold: float) -> None:
        """Compare latest prices between matched market pairs."""
        for pair in self._matched_pairs:
            try:
                pm_price_row = await self.db.fetchone(
                    "SELECT yes_price FROM prices WHERE market_id = ? ORDER BY timestamp DESC LIMIT 1",
                    (pair["polymarket_id"],),
                )
                km_price_row = await self.db.fetchone(
                    "SELECT yes_price FROM prices WHERE market_id = ? ORDER BY timestamp DESC LIMIT 1",
                    (pair["kalshi_id"],),
                )

                if not pm_price_row or not km_price_row:
                    continue

                pm_price = float(pm_price_row["yes_price"])
                km_price = float(km_price_row["yes_price"])
                divergence = abs(pm_price - km_price)

                if divergence >= threshold:
                    # Write divergence alert to system_state
                    alert_key = f"arb_divergence_{pair['polymarket_id']}_{pair['kalshi_id']}"
                    alert_value = (
                        f"Polymarket={pm_price:.3f} Kalshi={km_price:.3f} "
                        f"spread={divergence:.3f} sim={pair['similarity']:.2f}"
                    )
                    await self.db.execute(
                        """INSERT INTO system_state (key, value, updated_at)
                           VALUES (?, ?, datetime('now'))
                           ON CONFLICT(key) DO UPDATE SET
                             value = excluded.value,
                             updated_at = datetime('now')
                        """,
                        (alert_key, alert_value),
                    )
                    self.logger.info(
                        "DIVERGENCE DETECTED: %s <-> %s (spread=%.3f)",
                        pair["poly_title"][:40], pair["kalshi_title"][:40], divergence,
                    )
            except Exception:
                self.logger.debug("Divergence check failed for pair: %s", pair)
                continue
        await self.db.commit()

    async def _tag_event_ids(self) -> None:
        """Auto-assign event_id to matched market pairs."""
        for pair in self._matched_pairs:
            # Generate event_id from the Kalshi market title (more structured)
            title = pair["kalshi_title"][:50].upper().replace(" ", "_")
            event_id = f"CROSS_{title}"
            confidence = min(pair["similarity"], 0.80)  # Auto-tag capped at 0.80

            for market_id in (pair["polymarket_id"], pair["kalshi_id"]):
                await self.db.execute(
                    """UPDATE markets SET
                         event_id = COALESCE(event_id, ?),
                         event_id_confidence = CASE
                           WHEN event_id IS NULL THEN ?
                           WHEN event_id_confidence < ? THEN ?
                           ELSE event_id_confidence
                         END,
                         updated_at = datetime('now')
                       WHERE id = ?
                    """,
                    (event_id, confidence, confidence, confidence, market_id),
                )
        await self.db.commit()
