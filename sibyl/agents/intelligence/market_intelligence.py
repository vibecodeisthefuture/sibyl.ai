"""
Market Intelligence Agent — real-time surveillance of prediction market activity.

PURPOSE:
    This agent continuously analyzes incoming market data (from Sprint 1 monitors)
    to detect unusual activity that may signal a trading opportunity.  It runs
    THREE independent surveillance modes in parallel on each polling cycle.

THE THREE SURVEILLANCE MODES:

    MODE A — WHALE WATCHING:
        Detects abnormally large trades on individual markets.
        How: Computes a rolling average trade size per market, then flags any
        trade that exceeds `threshold_multiplier × rolling_avg`.
        Output: Writes to `whale_events` table + emits a WHALE detection event.
        Why it matters: Large trades often precede significant price moves.

    MODE B — VOLUME ANOMALY:
        Detects sudden spikes in 24h trading volume.
        How: Computes a Z-score against a 30-day rolling window of daily volumes.
        If Z-score > 2.5, it's flagged as a volume anomaly.
        Output: Emits a VOLUME_SURGE detection event.
        Why it matters: Volume surges often indicate new information entering the market.

    MODE C — ORDER BOOK DEPTH:
        Detects structural changes in the order book.
        Sub-detections:
          - SPREAD_EXPANSION: The bid-ask spread widens beyond 4% (liquidity drying up).
          - LIQUIDITY_VACUUM: Total depth drops below $500 (thin market, easy to move).
          - WALL_APPEARED: A single price level has 8× the average depth (someone defending a price).
          - WALL_DISAPPEARED: A previously detected wall vanishes (support/resistance removed).
        Output: Emits corresponding detection events.
        Why it matters: Order book shape changes often precede price moves.

DETECTION EVENTS:
    Each mode produces "detection events" — dicts containing:
        {market_id, mode, timestamp, details}
    These are collected in `self._detection_queue` and consumed by the
    Signal Generator agent (see signal_generator.py).

CONFIGURATION:
    All thresholds are read from `config/market_intelligence_config.yaml`.
    See that file for detailed parameter descriptions.
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.market_intelligence")


class MarketIntelligenceAgent(BaseAgent):
    """Runs three surveillance modes to detect unusual market activity.

    Detection events are stored in `self._detection_queue` — a list of dicts
    that the Signal Generator reads and clears on each of its cycles.

    Usage:
        agent = MarketIntelligenceAgent(db=db, config=system_config)
        agent.schedule()  # Runs in background
        # Signal Generator reads agent._detection_queue
    """

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        """Initialize with database and system config.

        Args:
            db:     Shared DatabaseManager instance.
            config: System config dict (from system_config.yaml).
        """
        super().__init__(name="market_intelligence", db=db, config=config)

        # Load Market Intelligence-specific configuration
        # This comes from config/market_intelligence_config.yaml
        self._mi_config: dict[str, Any] = {}  # Loaded in start()
        self._surv: dict[str, Any] = {}        # surveillance section
        self._composite: dict[str, Any] = {}   # composite section

        # Detection queue — Signal Generator consumes and clears this
        self._detection_queue: list[dict[str, Any]] = []

        # State tracking for Mode C (order book wall detection)
        # Maps market_id → set of price levels where walls were last seen
        self._previous_walls: dict[str, set[float]] = defaultdict(set)

    @property
    def poll_interval(self) -> float:
        """Run every 30 seconds (same as price snapshots)."""
        return float(self.config.get("polling", {}).get(
            "price_snapshot_interval_seconds", 30
        ))

    async def start(self) -> None:
        """Load Market Intelligence configuration on startup."""
        from sibyl.core.config import load_yaml
        try:
            self._mi_config = load_yaml("market_intelligence_config.yaml")
        except FileNotFoundError:
            self.logger.warning("market_intelligence_config.yaml not found, using defaults")
            self._mi_config = {}

        self._surv = self._mi_config.get("surveillance", {})
        self._composite = self._mi_config.get("composite", {})
        self.logger.info("Market Intelligence Agent started (3 surveillance modes active)")

    async def run_cycle(self) -> None:
        """Execute all three surveillance modes on each cycle."""
        # Get list of active markets to analyze
        markets = await self.db.fetchall(
            "SELECT id, platform, category FROM markets WHERE status = 'active'"
        )
        if not markets:
            return

        # Run all three modes
        await self._mode_a_whale_watching(markets)
        await self._mode_b_volume_anomaly(markets)
        await self._mode_c_orderbook_depth(markets)

        if self._detection_queue:
            self.logger.info(
                "Cycle complete: %d new detection events queued",
                len(self._detection_queue),
            )

    async def stop(self) -> None:
        """Cleanup on shutdown."""
        self.logger.info(
            "Market Intelligence Agent stopped (%d unprocessed detections)",
            len(self._detection_queue),
        )

    # ── MODE A: Whale Watching ────────────────────────────────────────

    async def _mode_a_whale_watching(self, markets: list) -> None:
        """Detect abnormally large trades.

        For each market:
            1. Fetch trades from the last 5 minutes (since last cycle).
            2. Compute the rolling average trade size (last 100 trades).
            3. If any trade exceeds `threshold × avg_size`, it's a whale.
            4. Write to whale_events table and add to detection queue.

        The threshold multiplier varies by category (e.g., crypto=6×, politics=5×).
        """
        # Category-specific thresholds (from config)
        default_mult = float(self._surv.get("whale_threshold_multiplier_default", 4.0))
        cat_thresholds = self._surv.get("whale_threshold_by_category", {})

        for market in markets:
            market_id = market["id"]
            category = market["category"] or "other"
            platform = market["platform"]
            threshold_mult = float(cat_thresholds.get(category, default_mult))

            try:
                # Get the rolling average trade size (last 100 trades)
                avg_row = await self.db.fetchone(
                    """SELECT AVG(size) as avg_size, COUNT(*) as cnt
                       FROM (SELECT size FROM trades_log
                             WHERE market_id = ?
                             ORDER BY timestamp DESC LIMIT 100)""",
                    (market_id,),
                )

                if not avg_row or avg_row["cnt"] < 5:
                    continue  # Not enough trade data yet

                avg_size = float(avg_row["avg_size"])
                if avg_size <= 0:
                    continue

                whale_threshold = avg_size * threshold_mult

                # Check recent trades (last 2 minutes) for whales
                recent_trades = await self.db.fetchall(
                    """SELECT side, size, price, timestamp FROM trades_log
                       WHERE market_id = ?
                         AND timestamp > datetime('now', '-2 minutes')
                       ORDER BY timestamp DESC""",
                    (market_id,),
                )

                for trade in recent_trades:
                    trade_size = float(trade["size"])
                    if trade_size >= whale_threshold:
                        # Record the whale event in the database
                        await self.db.execute(
                            """INSERT INTO whale_events
                               (market_id, platform, side, size, price, threshold)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                market_id, platform, trade["side"],
                                trade_size, float(trade["price"]), whale_threshold,
                            ),
                        )

                        # Add to detection queue for Signal Generator
                        self._detection_queue.append({
                            "market_id": market_id,
                            "mode": "WHALE",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "details": {
                                "side": trade["side"],
                                "size": trade_size,
                                "avg_size": avg_size,
                                "multiplier": trade_size / avg_size,
                                "threshold": whale_threshold,
                                "platform": platform,
                            },
                        })
                        self.logger.info(
                            "WHALE detected on %s: size=%.1f (%.1f× avg)",
                            market_id, trade_size, trade_size / avg_size,
                        )

            except Exception:
                self.logger.debug("Whale check failed for %s", market_id)
                continue

        await self.db.commit()

    # ── MODE B: Volume Anomaly ────────────────────────────────────────

    async def _mode_b_volume_anomaly(self, markets: list) -> None:
        """Detect sudden spikes in 24h trading volume.

        For each market:
            1. Get the latest volume_24h from the prices table.
            2. Get the rolling 30-day history of daily volumes.
            3. Compute Z-score = (current - mean) / stdev.
            4. If Z-score > 2.5, flag as VOLUME_SURGE.

        Z-score interpretation:
            2.5 = volume is 2.5 standard deviations above the 30-day mean.
            This happens naturally about 0.6% of the time — so it's unusual.
        """
        zscore_threshold = float(self._surv.get("volume_zscore_threshold", 2.5))

        for market in markets:
            market_id = market["id"]

            try:
                # Get the current 24h volume (latest snapshot)
                latest = await self.db.fetchone(
                    """SELECT volume_24h FROM prices
                       WHERE market_id = ? AND volume_24h IS NOT NULL
                       ORDER BY timestamp DESC LIMIT 1""",
                    (market_id,),
                )
                if not latest or latest["volume_24h"] is None:
                    continue

                current_volume = float(latest["volume_24h"])
                if current_volume <= 0:
                    continue

                # Get historical daily volumes (one sample per day, last 30 days)
                # We take the MAX volume per day as the daily snapshot
                history = await self.db.fetchall(
                    """SELECT MAX(volume_24h) as daily_vol
                       FROM prices
                       WHERE market_id = ?
                         AND volume_24h IS NOT NULL
                         AND timestamp > datetime('now', '-30 days')
                       GROUP BY DATE(timestamp)
                       ORDER BY DATE(timestamp) DESC
                       LIMIT 30""",
                    (market_id,),
                )

                if len(history) < 5:
                    continue  # Need at least 5 days of data

                volumes = [float(row["daily_vol"]) for row in history if row["daily_vol"]]
                if len(volumes) < 5:
                    continue

                mean_vol = statistics.mean(volumes)
                stdev_vol = statistics.stdev(volumes)

                if stdev_vol <= 0:
                    continue  # Can't compute Z-score with zero variance

                zscore = (current_volume - mean_vol) / stdev_vol

                if zscore >= zscore_threshold:
                    self._detection_queue.append({
                        "market_id": market_id,
                        "mode": "VOLUME_SURGE",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "details": {
                            "current_volume": current_volume,
                            "mean_volume": mean_vol,
                            "stdev": stdev_vol,
                            "zscore": zscore,
                        },
                    })
                    self.logger.info(
                        "VOLUME SURGE on %s: z=%.2f (vol=%.0f, mean=%.0f)",
                        market_id, zscore, current_volume, mean_vol,
                    )

            except Exception:
                self.logger.debug("Volume check failed for %s", market_id)
                continue

    # ── MODE C: Order Book Depth ──────────────────────────────────────

    async def _mode_c_orderbook_depth(self, markets: list) -> None:
        """Detect structural changes in the order book.

        For each market:
            1. Fetch the latest orderbook snapshot.
            2. Compute bid-ask spread, total depth, and identify walls.
            3. Flag anomalies: spread expansion, thin markets, wall changes.

        Key concepts:
            - "Spread" = best_ask - best_bid (how expensive it is to trade)
            - "Depth"  = total quantity across all price levels (market liquidity)
            - "Wall"   = a price level with 8× the average size (large resting order)
        """
        spread_threshold = float(self._surv.get("spread_expansion_threshold", 0.04))
        thin_market_threshold = float(self._surv.get("thin_market_depth_threshold", 500))
        wall_multiplier = float(self._surv.get("wall_size_multiplier", 8.0))

        for market in markets:
            market_id = market["id"]

            try:
                # Get the latest orderbook snapshot
                row = await self.db.fetchone(
                    """SELECT bids, asks FROM orderbook
                       WHERE market_id = ?
                       ORDER BY timestamp DESC LIMIT 1""",
                    (market_id,),
                )
                if not row:
                    continue

                bids = json.loads(row["bids"]) if row["bids"] else []
                asks = json.loads(row["asks"]) if row["asks"] else []

                if not bids or not asks:
                    continue

                # Compute best bid, best ask, and spread
                best_bid = max(b["price"] for b in bids)
                best_ask = min(a["price"] for a in asks)
                midpoint = (best_bid + best_ask) / 2.0

                if midpoint <= 0:
                    continue

                spread = best_ask - best_bid
                normalized_spread = spread / midpoint  # As a fraction of price

                # Check 1: Spread expansion
                if normalized_spread > spread_threshold:
                    self._detection_queue.append({
                        "market_id": market_id,
                        "mode": "SPREAD_EXPANSION",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "details": {
                            "spread": spread,
                            "normalized_spread": normalized_spread,
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                        },
                    })

                # Check 2: Thin market (liquidity vacuum)
                total_bid_depth = sum(b["size"] for b in bids)
                total_ask_depth = sum(a["size"] for a in asks)
                total_depth = total_bid_depth + total_ask_depth

                if total_depth < thin_market_threshold:
                    self._detection_queue.append({
                        "market_id": market_id,
                        "mode": "LIQUIDITY_VACUUM",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "details": {
                            "total_depth": total_depth,
                            "bid_depth": total_bid_depth,
                            "ask_depth": total_ask_depth,
                            "threshold": thin_market_threshold,
                        },
                    })

                # Check 3: Wall detection (large resting orders)
                all_levels = bids + asks
                if len(all_levels) >= 3:
                    avg_size = statistics.mean(l["size"] for l in all_levels)
                    wall_threshold = avg_size * wall_multiplier

                    current_walls: set[float] = set()
                    for level in all_levels:
                        if level["size"] >= wall_threshold:
                            current_walls.add(level["price"])

                    # Compare with previous walls to detect changes
                    previous = self._previous_walls.get(market_id, set())

                    # New walls that just appeared
                    new_walls = current_walls - previous
                    for price in new_walls:
                        self._detection_queue.append({
                            "market_id": market_id,
                            "mode": "WALL_APPEARED",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "details": {
                                "price": price,
                                "wall_threshold": wall_threshold,
                                "avg_level_size": avg_size,
                            },
                        })

                    # Old walls that just disappeared
                    removed_walls = previous - current_walls
                    for price in removed_walls:
                        self._detection_queue.append({
                            "market_id": market_id,
                            "mode": "WALL_DISAPPEARED",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "details": {
                                "price": price,
                                "wall_threshold": wall_threshold,
                            },
                        })

                    self._previous_walls[market_id] = current_walls

            except Exception:
                self.logger.debug("Orderbook analysis failed for %s", market_id)
                continue

    # ── Public API ────────────────────────────────────────────────────

    def get_and_clear_detections(self) -> list[dict[str, Any]]:
        """Return all queued detection events and clear the queue.

        Called by the Signal Generator to consume detection events.
        Thread-safe within asyncio (single event loop).

        Returns:
            List of detection event dicts, each containing:
              - market_id: Which market triggered
              - mode: Detection type (WHALE, VOLUME_SURGE, etc.)
              - timestamp: When the detection occurred
              - details: Mode-specific data (sizes, thresholds, etc.)
        """
        events = list(self._detection_queue)
        self._detection_queue.clear()
        return events
