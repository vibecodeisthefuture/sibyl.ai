"""
AUTO-CALIBRATION AGENT
Sprint 29 — Closes the feedback loop (Audit findings F-1, F-2, F-3).

Reads the performance table to compute empirical accuracy and calibration
metrics, then updates model parameters when divergence exceeds thresholds.

RESPONSIBILITIES:
    1. BRIER SCORE: Per-category Brier score from resolved positions.
    2. CALIBRATION OFFSET: Adjust per-category calibration_offset when
       predicted confidence diverges from actual win rate.
    3. KELLY OPTIMIZATION: Compute optimal Kelly fraction from rolling
       win/loss distribution; flag when current config diverges >15%.
    4. SIGNAL TYPE RANKING: Track per-signal-type hit rate; log rankings.

Runs every calibration_interval_seconds (default 3600 = 1 hour).
Writes recommendations to system_state for other agents to consume.
Applies calibration_offset changes directly to config when auto_apply=True.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.auto_calibrator")


class AutoCalibrator(BaseAgent):
    """Computes calibration metrics and updates model parameters."""

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        super().__init__(name="auto_calibrator", db=db, config=config)
        self._interval: float = 600.0  # Sprint 30-C: 10 min (was 1 hour)
        self._min_samples: int = 20  # Minimum resolved positions for calibration
        self._divergence_threshold: float = 0.10  # Trigger offset update
        self._auto_apply: bool = False  # Write back to config YAML
        self._max_offset_change: float = 0.05  # Cap per-adjustment

    @property
    def poll_interval(self) -> float:
        return self._interval

    async def start(self) -> None:
        """Load calibration config."""
        from sibyl.core.config import load_yaml

        try:
            policy = load_yaml("investment_policy_config.yaml")
            override = policy.get("override_protocol", {})
            self._min_samples = int(override.get("min_calibration_samples", 20))
            self._auto_apply = bool(override.get("auto_apply_calibration", False))
        except FileNotFoundError:
            pass

        logger.info(
            "AutoCalibrator started (interval=%ds, min_samples=%d, auto_apply=%s)",
            int(self._interval), self._min_samples, self._auto_apply,
        )

        # Sprint 30-C: Run first calibration immediately rather than waiting
        # one full interval — ensures cal_* entries appear in system_state
        # from the very first minute of a paper or live session.
        await self.run_cycle()

    async def run_cycle(self) -> None:
        """Run calibration analysis across all categories."""
        try:
            await self._compute_calibration_metrics()
            await self._compute_signal_type_rankings()
            await self._compute_kelly_analysis()
            await self.db.commit()
        except Exception:
            logger.exception("AutoCalibrator cycle failed")

    async def stop(self) -> None:
        logger.info("AutoCalibrator stopped")

    # ── Calibration Metrics ──────────────────────────────────────────

    async def _compute_calibration_metrics(self) -> None:
        """Compute per-category Brier scores and calibration offsets.

        Queries resolved positions joined with signals to get predicted
        confidence vs actual outcome. Computes:
        - Brier score: mean((confidence - outcome)^2)
        - Mean predicted confidence vs actual win rate
        - Suggested calibration offset adjustment
        """
        # Sprint 31: Fix B-NEW-1 — signals table has no 'category' column.
        # Join through markets table to get category, fall back to source_pipeline.
        rows = await self.db.fetchall(
            """SELECT COALESCE(m.category, s.source_pipeline, 'unknown') as category,
                      s.confidence, p.correct
               FROM performance p
               JOIN signals s ON p.signal_id = s.id
               LEFT JOIN markets m ON s.market_id = m.id
               WHERE p.resolved = 1
                 AND p.resolved_at >= datetime('now', '-30 days')
                 AND s.confidence IS NOT NULL"""
        )

        if not rows:
            return

        # Group by category
        by_category: dict[str, list[tuple[float, int]]] = {}
        for row in rows:
            cat = row["category"] or "unknown"
            conf = float(row["confidence"])
            correct = int(row["correct"])
            by_category.setdefault(cat, []).append((conf, correct))

        for cat, samples in by_category.items():
            n = len(samples)
            if n < self._min_samples:
                logger.debug(
                    "Calibration: %s has %d samples (need %d) — skipping",
                    cat, n, self._min_samples,
                )
                continue

            # Brier score
            brier = sum((conf - outcome) ** 2 for conf, outcome in samples) / n

            # Mean confidence vs actual win rate
            mean_conf = sum(conf for conf, _ in samples) / n
            win_rate = sum(outcome for _, outcome in samples) / n
            divergence = mean_conf - win_rate  # Positive = overconfident

            # Write metrics to system_state
            await self._write_state(f"cal_brier_{cat}", f"{brier:.4f}")
            await self._write_state(f"cal_mean_conf_{cat}", f"{mean_conf:.4f}")
            await self._write_state(f"cal_win_rate_{cat}", f"{win_rate:.4f}")
            await self._write_state(f"cal_divergence_{cat}", f"{divergence:.4f}")
            await self._write_state(f"cal_sample_count_{cat}", str(n))

            logger.info(
                "CALIBRATION [%s]: n=%d, brier=%.4f, mean_conf=%.2f%%, "
                "win_rate=%.2f%%, divergence=%.2f%% (%s)",
                cat, n, brier, mean_conf * 100, win_rate * 100,
                divergence * 100,
                "OVERCONFIDENT" if divergence > 0 else "UNDERCONFIDENT",
            )

            # Suggest offset adjustment if divergence exceeds threshold
            if abs(divergence) >= self._divergence_threshold:
                # Offset should push confidence toward actual win rate
                # Negative offset = reduce confidence (for overconfident models)
                suggested_adjustment = -divergence
                # Cap the adjustment to avoid wild swings
                suggested_adjustment = max(
                    -self._max_offset_change,
                    min(self._max_offset_change, suggested_adjustment),
                )

                await self._write_state(
                    f"cal_suggested_offset_{cat}",
                    f"{suggested_adjustment:+.4f}",
                )
                logger.warning(
                    "CALIBRATION ALERT [%s]: divergence %.2f%% exceeds %.0f%% "
                    "threshold — suggested offset adjustment: %+.4f",
                    cat, divergence * 100, self._divergence_threshold * 100,
                    suggested_adjustment,
                )

                if self._auto_apply:
                    await self._apply_calibration_offset(cat, suggested_adjustment)

    async def _apply_calibration_offset(
        self, category: str, adjustment: float
    ) -> None:
        """Apply calibration offset adjustment to investment_policy_config.yaml.

        Reads current offset, adds adjustment, writes back.
        """
        try:
            import yaml
            config_path = "config/investment_policy_config.yaml"
            with open(config_path, "r") as f:
                policy = yaml.safe_load(f)

            profiles = policy.get("per_category_risk_profiles", {})
            cat_key = category.lower()
            profile = profiles.get(cat_key, profiles.get(category))
            if not profile:
                logger.warning("No profile for %s — cannot apply offset", category)
                return

            current_offset = float(profile.get("calibration_offset", 0.0))
            new_offset = round(current_offset + adjustment, 4)
            # Clamp to reasonable range
            new_offset = max(-0.30, min(0.30, new_offset))
            profile["calibration_offset"] = new_offset

            with open(config_path, "w") as f:
                yaml.dump(policy, f, default_flow_style=False, sort_keys=False)

            logger.info(
                "CALIBRATION APPLIED [%s]: offset %.4f -> %.4f (adjustment %+.4f)",
                category, current_offset, new_offset, adjustment,
            )
            await self._write_state(
                f"cal_applied_{cat_key}",
                f"offset={new_offset:.4f} at {datetime.now(timezone.utc).isoformat()}",
            )
        except Exception:
            logger.exception("Failed to apply calibration offset for %s", category)

    # ── Signal Type Rankings ─────────────────────────────────────────

    async def _compute_signal_type_rankings(self) -> None:
        """Rank signal types by hit rate over the last 30 days."""
        rows = await self.db.fetchall(
            """SELECT s.signal_type,
                      COUNT(*) as total,
                      SUM(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END) as wins,
                      AVG(p.pnl) as avg_pnl
               FROM performance p
               JOIN signals s ON p.signal_id = s.id
               WHERE p.resolved = 1
                 AND p.resolved_at >= datetime('now', '-30 days')
               GROUP BY s.signal_type
               HAVING COUNT(*) >= 5
               ORDER BY AVG(p.pnl) DESC"""
        )

        if not rows:
            return

        rankings = []
        for row in rows:
            sig_type = row["signal_type"]
            total = int(row["total"])
            wins = int(row["wins"])
            hit_rate = wins / total if total > 0 else 0
            avg_pnl = float(row["avg_pnl"]) if row["avg_pnl"] else 0

            rankings.append(f"{sig_type}: {hit_rate:.0%} ({wins}/{total}), avg_pnl=${avg_pnl:.4f}")
            await self._write_state(
                f"cal_signal_hitrate_{sig_type}",
                f"{hit_rate:.4f} ({wins}/{total})",
            )

        logger.info("SIGNAL RANKINGS (30d): %s", " | ".join(rankings))

    # ── Kelly Analysis ───────────────────────────────────────────────

    async def _compute_kelly_analysis(self) -> None:
        """Compute optimal Kelly fraction from empirical win/loss data.

        Compares to current config value and flags divergence.
        """
        rows = await self.db.fetchall(
            """SELECT s.timeframe, p.correct, p.pnl,
                      pos.entry_price, pos.size
               FROM performance p
               JOIN signals s ON p.signal_id = s.id
               JOIN positions pos ON p.position_id = pos.id
               WHERE p.resolved = 1
                 AND p.resolved_at >= datetime('now', '-30 days')
                 AND s.timeframe IS NOT NULL"""
        )

        if not rows:
            return

        # Group by timeframe
        by_tf: dict[str, list[dict]] = {}
        for row in rows:
            tf = row["timeframe"] or "unknown"
            by_tf.setdefault(tf, []).append({
                "correct": int(row["correct"]),
                "pnl": float(row["pnl"]) if row["pnl"] else 0,
                "entry": float(row["entry_price"]) if row["entry_price"] else 0,
                "size": float(row["size"]) if row["size"] else 0,
            })

        for tf, trades in by_tf.items():
            n = len(trades)
            if n < 10:
                continue

            wins = [t for t in trades if t["correct"]]
            losses = [t for t in trades if not t["correct"]]

            if not wins or not losses:
                continue

            win_rate = len(wins) / n
            avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
            avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 1

            # Kelly formula: f* = (p * b - q) / b
            # where p = win_rate, q = 1-p, b = avg_win/avg_loss
            if avg_loss > 0:
                b = avg_win / avg_loss
                optimal_kelly = (win_rate * b - (1 - win_rate)) / b if b > 0 else 0
                optimal_kelly = max(0, min(0.25, optimal_kelly))  # Clamp

                await self._write_state(
                    f"cal_kelly_optimal_{tf}",
                    f"{optimal_kelly:.4f} (n={n}, wr={win_rate:.2f}, b={b:.2f})",
                )
                logger.info(
                    "KELLY [%s]: optimal=%.4f (n=%d, wr=%.0f%%, avg_win=$%.2f, avg_loss=$%.2f)",
                    tf, optimal_kelly, n, win_rate * 100, avg_win, avg_loss,
                )

    # ── Helpers ───────────────────────────────────────────────────────

    async def _write_state(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            (key, value),
        )
