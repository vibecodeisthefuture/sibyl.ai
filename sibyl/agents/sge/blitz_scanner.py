"""
Blitz Scanner — high-frequency scanner for last-second market opportunities.

PURPOSE:
    The Blitz Scanner is the eyes of the Blitz partition. It monitors ALL
    active Kalshi markets and identifies those closing within ≤90 seconds
    where Sibyl has ≥85% confidence in the outcome. When a qualifying
    opportunity is found, it generates a BLITZ signal that bypasses the
    standard signal routing pipeline and goes directly to the Blitz Executor.

WHY BLITZ IS DIFFERENT FROM STANDARD SGE:
    Standard SGE uses patient limit orders on a 3-second polling cycle.
    Blitz uses market orders on a 1-second polling cycle because:
    - Markets closing in ≤90 seconds don't have time for limit order fills
    - At >85% confidence, the edge justifies paying the spread
    - Speed of execution is the primary competitive advantage

SCANNING LOGIC:
    Every 1 second, the scanner:
    1. Queries the `markets` table for markets closing within the configured
       time window (default ≤90 seconds, minimum ≥5 seconds).
    2. For each candidate market, checks the latest price from `prices` table.
    3. Computes implied probability from the price and compares against
       confidence from the most recent pipeline signal or model output.
    4. If confidence > 85% AND price gap exists (market hasn't converged),
       generates a BLITZ signal with status='BLITZ_READY'.
    5. The Blitz Executor picks up BLITZ_READY signals on its own fast cycle.

APPLICABLE MARKET TYPES:
    - Crypto price windows (15min, 1hr): BTC/ETH price above/below threshold
    - Weather temperature closes: Temperature above/below X°F at close
    - Sports final minutes: Outcome near-certain with large score differential
    - Economic data release: Post-release markets not yet priced in
    - Stock price closes: Intraday price near close makes outcome clear
    - Culture event outcomes: Live results/leaks before price convergence

POLLING: Every 1 second (configurable).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.sge.blitz_scanner")


# ── Blitz Signal Type ─────────────────────────────────────────────────────

BLITZ_SIGNAL_TYPE = "BLITZ_LAST_SECOND"


class BlitzScanner(BaseAgent):
    """Scans for last-second high-confidence trading opportunities.

    Part of the SGE Blitz partition (Sprint 14). Monitors all active markets
    and generates BLITZ signals when a market is closing within ≤90 seconds
    and confidence exceeds 85%.

    Lifecycle:
        scanner = BlitzScanner(db, config)
        await scanner.start()    # Load Blitz config
        await scanner.run()      # Enters 1-second scan loop
        await scanner.shutdown() # Graceful stop
    """

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="blitz_scanner", db=db, config=config, engine="SGE")

        # Blitz config (loaded in start())
        self._blitz_config: dict[str, Any] = {}
        self._enabled = False

        # Scanning parameters (overridden from config in start())
        self._close_window_seconds = 90
        self._min_close_window_seconds = 5
        self._min_confidence = 0.85
        self._min_ev = 0.04
        self._min_price_gap = 0.05
        self._max_price_gap = 0.30
        self._max_concurrent_positions = 5

        # Target pattern keywords for signal enhancement
        self._target_patterns: dict[str, dict] = {}

        # Stats tracking
        self._markets_scanned = 0
        self._signals_generated = 0

    @property
    def poll_interval(self) -> float:
        """Run every 1 second — speed is the competitive advantage."""
        return float(self._blitz_config.get("scanner", {}).get(
            "poll_interval_seconds", 1.0
        ))

    async def start(self) -> None:
        """Load Blitz configuration from sge_config.yaml."""
        from sibyl.core.config import load_yaml

        try:
            sge = load_yaml("sge_config.yaml")
            self._blitz_config = sge.get("blitz", {})
        except FileNotFoundError:
            self.logger.warning("sge_config.yaml not found — Blitz disabled")
            return

        self._enabled = self._blitz_config.get("enabled", False)
        if not self._enabled:
            self.logger.info("Blitz partition is DISABLED in config")
            return

        # Load scanning parameters
        scanner = self._blitz_config.get("scanner", {})
        self._close_window_seconds = int(scanner.get("close_window_seconds", 90))
        self._min_close_window_seconds = int(scanner.get("min_close_window_seconds", 5))

        criteria = self._blitz_config.get("entry_criteria", {})
        self._min_confidence = float(criteria.get("min_confidence", 0.85))
        self._min_ev = float(criteria.get("min_ev", 0.04))
        self._min_price_gap = float(criteria.get("min_price_gap", 0.05))
        self._max_price_gap = float(criteria.get("max_price_gap", 0.30))

        risk = self._blitz_config.get("risk_policy", {})
        self._max_concurrent_positions = int(risk.get("max_concurrent_positions", 5))

        # Load target patterns for keyword matching
        self._target_patterns = self._blitz_config.get("target_patterns", {})

        self.logger.info(
            "Blitz Scanner started (window=%d-%ds, min_conf=%.2f, min_ev=%.2f)",
            self._min_close_window_seconds, self._close_window_seconds,
            self._min_confidence, self._min_ev,
        )

    async def run_cycle(self) -> None:
        """Scan for Blitz-eligible markets and generate BLITZ signals."""
        if not self._enabled:
            return

        # ── Gate: check concurrent position limit ─────────────────────
        open_blitz = await self.db.fetchone(
            """SELECT COUNT(*) as cnt FROM positions
               WHERE engine = 'SGE_BLITZ' AND status = 'OPEN'"""
        )
        current_open = open_blitz["cnt"] if open_blitz else 0
        if current_open >= self._max_concurrent_positions:
            return  # At capacity

        # ── Gate: check for existing pending BLITZ signals ────────────
        pending = await self.db.fetchone(
            """SELECT COUNT(*) as cnt FROM signals
               WHERE signal_type = ? AND status = 'BLITZ_READY'""",
            (BLITZ_SIGNAL_TYPE,),
        )
        pending_count = pending["cnt"] if pending else 0
        slots_available = self._max_concurrent_positions - current_open - pending_count
        if slots_available <= 0:
            return

        # ── Step 1: Find markets closing within the Blitz window ──────
        now = datetime.now(timezone.utc)
        close_max = now + timedelta(seconds=self._close_window_seconds)
        close_min = now + timedelta(seconds=self._min_close_window_seconds)

        candidates = await self.db.fetchall(
            """SELECT m.id, m.title, m.category, m.close_date, m.status
               FROM markets m
               WHERE m.status = 'active'
                 AND m.close_date IS NOT NULL
                 AND m.close_date > ?
                 AND m.close_date <= ?
               ORDER BY m.close_date ASC""",
            (close_min.isoformat(), close_max.isoformat()),
        )

        if not candidates:
            return

        self._markets_scanned += len(candidates)

        # ── Step 2: Evaluate each candidate ───────────────────────────
        signals_this_cycle = 0
        for market in candidates:
            if signals_this_cycle >= slots_available:
                break

            signal = await self._evaluate_market(market, now)
            if signal:
                await self._write_blitz_signal(signal)
                signals_this_cycle += 1
                self._signals_generated += 1

    async def stop(self) -> None:
        """Log final stats and shutdown."""
        self.logger.info(
            "Blitz Scanner stopped (scanned=%d markets, generated=%d signals)",
            self._markets_scanned, self._signals_generated,
        )

    # ── Evaluation Logic ──────────────────────────────────────────────────

    async def _evaluate_market(
        self, market: dict, now: datetime
    ) -> dict | None:
        """Evaluate a single market for Blitz eligibility.

        Returns a signal dict if the market qualifies, None otherwise.
        """
        market_id = market["id"]
        category = market["category"] or ""

        # ── Check for duplicate: already have a BLITZ signal for this market?
        existing = await self.db.fetchone(
            """SELECT id FROM signals
               WHERE market_id = ? AND signal_type = ?
               AND status IN ('BLITZ_READY', 'ROUTED', 'EXECUTED')
               AND timestamp >= datetime('now', '-5 minutes')""",
            (market_id, BLITZ_SIGNAL_TYPE),
        )
        if existing:
            return None  # Already targeting this market

        # ── Get latest price ──────────────────────────────────────────
        price_row = await self.db.fetchone(
            """SELECT yes_price, no_price, timestamp
               FROM prices
               WHERE market_id = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (market_id,),
        )
        if not price_row:
            return None

        yes_price = float(price_row["yes_price"])

        # ── Price gap check: must be near a terminal value ────────────
        # We want markets where the outcome is nearly decided.
        # Price near 1.0 → YES is winning → buy YES at discount
        # Price near 0.0 → NO is winning → buy NO at discount
        gap_from_yes = 1.0 - yes_price   # Distance from YES terminal (1.0)
        gap_from_no = yes_price           # Distance from NO terminal (0.0)

        # Determine which side has the near-certain outcome
        if gap_from_yes <= self._max_price_gap:
            # YES is likely winning — buy YES
            direction = "YES"
            price_gap = gap_from_yes
            entry_price = yes_price
        elif gap_from_no <= self._max_price_gap:
            # NO is likely winning — buy NO
            direction = "NO"
            price_gap = gap_from_no
            entry_price = 1.0 - yes_price  # NO price
        else:
            return None  # Price too far from any terminal — not Blitz material

        # Price gap must be above minimum (otherwise there's no edge left)
        if price_gap < self._min_price_gap:
            return None  # Price already converged — no profit left

        # ── Confidence estimation ─────────────────────────────────────
        # Check for existing pipeline signals as confidence source
        confidence = await self._estimate_confidence(market_id, direction)
        if confidence < self._min_confidence:
            return None

        # ── EV calculation ────────────────────────────────────────────
        # For Blitz: we buy at entry_price, expect to collect 1.0 at resolution
        # EV = (confidence × 1.0) - entry_price = confidence - entry_price
        ev = confidence - entry_price
        if ev < self._min_ev:
            return None

        # ── Compute time to close ─────────────────────────────────────
        close_date = market["close_date"]
        if isinstance(close_date, str):
            try:
                close_dt = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            close_dt = close_date

        seconds_to_close = max(0, (close_dt - now).total_seconds())

        # ── Pattern matching for reasoning ────────────────────────────
        pattern_match = self._match_target_pattern(market.get("title", ""), category)

        reasoning = (
            f"BLITZ: {direction} @ {entry_price:.2f} "
            f"({seconds_to_close:.0f}s to close, "
            f"conf={confidence:.2f}, ev={ev:.3f}, gap={price_gap:.2f})"
        )
        if pattern_match:
            reasoning += f" [Pattern: {pattern_match}]"

        return {
            "market_id": market_id,
            "signal_type": BLITZ_SIGNAL_TYPE,
            "confidence": round(confidence, 4),
            "ev_estimate": round(ev, 4),
            "direction": direction,
            "entry_price": entry_price,
            "seconds_to_close": seconds_to_close,
            "category": category,
            "reasoning": reasoning,
            "pattern": pattern_match,
        }

    async def _estimate_confidence(
        self, market_id: str, direction: str
    ) -> float:
        """Estimate confidence for a market using available signals and price data.

        Confidence sources (in priority order):
        1. Recent pipeline signal for this market (from category pipelines)
        2. Price-implied confidence from orderbook depth
        3. Price momentum (if price has been trending toward terminal)

        Returns:
            Float confidence score (0.0–1.0).
        """
        # Source 1: Recent pipeline signal
        recent_signal = await self.db.fetchone(
            """SELECT confidence, ev_estimate, detection_modes_triggered
               FROM signals
               WHERE market_id = ?
                 AND status IN ('PENDING', 'ROUTED', 'EXECUTED')
                 AND timestamp >= datetime('now', '-60 minutes')
               ORDER BY confidence DESC
               LIMIT 1""",
            (market_id,),
        )
        if recent_signal:
            pipeline_conf = float(recent_signal["confidence"])
            # If a pipeline already has high confidence, use it
            if pipeline_conf >= self._min_confidence:
                return pipeline_conf

        # Source 2: Price-implied confidence
        # If YES price is 0.92, implied probability of YES = 92%
        price_row = await self.db.fetchone(
            """SELECT yes_price FROM prices
               WHERE market_id = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (market_id,),
        )
        if price_row:
            yes_price = float(price_row["yes_price"])
            if direction == "YES":
                price_implied = yes_price
            else:
                price_implied = 1.0 - yes_price

            # Source 3: Price momentum (compare to price 5 minutes ago)
            momentum_row = await self.db.fetchone(
                """SELECT yes_price FROM prices
                   WHERE market_id = ?
                     AND timestamp <= datetime('now', '-3 minutes')
                   ORDER BY timestamp DESC LIMIT 1""",
                (market_id,),
            )
            momentum_boost = 0.0
            if momentum_row:
                old_price = float(momentum_row["yes_price"])
                if direction == "YES" and yes_price > old_price:
                    # Price moving toward YES → confirming YES direction
                    momentum_boost = min((yes_price - old_price) * 0.5, 0.05)
                elif direction == "NO" and yes_price < old_price:
                    # Price moving toward NO → confirming NO direction
                    momentum_boost = min((old_price - yes_price) * 0.5, 0.05)

            # Blitz confidence = price-implied + momentum boost
            # Near close, price IS the best predictor — markets are efficient
            # But we add a small boost for confirming momentum
            blitz_confidence = min(price_implied + momentum_boost, 0.99)
            return blitz_confidence

        return 0.0  # No price data — can't evaluate

    def _match_target_pattern(self, title: str, category: str) -> str | None:
        """Check if a market matches any of the target Blitz patterns.

        Uses word-boundary matching to avoid false positives (e.g., 'nfl'
        matching inside 'inflation'). Multi-word keywords use substring
        matching since they're specific enough.

        Returns the pattern name if matched, None otherwise.
        """
        import re
        title_lower = title.lower()

        for pattern_name, pattern_config in self._target_patterns.items():
            keywords = pattern_config.get("keywords", [])
            for kw in keywords:
                kw_lower = kw.lower()
                if " " in kw_lower:
                    # Multi-word keyword: substring match is fine
                    if kw_lower in title_lower:
                        return pattern_name
                else:
                    # Single-word keyword: require word boundary match
                    if re.search(r'\b' + re.escape(kw_lower) + r'\b', title_lower):
                        return pattern_name

        return None

    # ── Signal Writing ────────────────────────────────────────────────────

    async def _write_blitz_signal(self, signal: dict) -> None:
        """Write a BLITZ signal to the signals table with status BLITZ_READY.

        BLITZ_READY is a special status that the Blitz Executor watches for.
        It bypasses the standard signal routing pipeline entirely.
        """
        try:
            await self.db.execute(
                """INSERT INTO signals
                   (market_id, signal_type, confidence, ev_estimate,
                    status, routed_to, detection_modes_triggered, reasoning)
                   VALUES (?, ?, ?, ?, 'BLITZ_READY', 'SGE_BLITZ', ?, ?)""",
                (
                    signal["market_id"],
                    signal["signal_type"],
                    signal["confidence"],
                    signal["ev_estimate"],
                    f"BLITZ|DIR:{signal['direction']}|SEC:{signal['seconds_to_close']:.0f}",
                    signal["reasoning"],
                ),
            )
            await self.db.commit()
            self.logger.info(
                "BLITZ SIGNAL: %s %s (conf=%.2f, ev=%.3f, %ds to close)",
                signal["direction"], signal["market_id"],
                signal["confidence"], signal["ev_estimate"],
                int(signal["seconds_to_close"]),
            )
        except Exception as e:
            self.logger.error("Failed to write Blitz signal: %s", e)

    # ── Health ────────────────────────────────────────────────────────────

    def health_check(self) -> dict[str, Any]:
        """Return Blitz Scanner health status."""
        base = super().health_check()
        base.update({
            "enabled": self._enabled,
            "markets_scanned": self._markets_scanned,
            "signals_generated": self._signals_generated,
            "close_window_seconds": self._close_window_seconds,
        })
        return base
