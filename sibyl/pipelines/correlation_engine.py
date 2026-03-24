"""
Cross-Category Correlation Engine — detects reinforcing signals across pipelines.

PURPOSE:
    When multiple category pipelines independently generate signals pointing
    in the same direction for related markets, the correlation engine:
    1. Detects the convergence
    2. Boosts confidence on the strongest correlated signal
    3. Generates COMPOSITE_HIGH_CONVICTION signals for multi-source agreement
    4. Flags correlation risks (too many bets in correlated categories)

CORRELATION LOGIC:
    - Economics + Financials: Strong GDP + rising stock market → correlated
    - Crypto + Fear&Greed: BTC momentum + extreme greed → correlated
    - Weather + Economics: Extreme weather → supply chain disruption → economic impact
    - Politics + Geopolitics: Legislation activity + court rulings → policy shift
    - Sports (multiple games): Same-league markets may be weakly correlated

ANTI-CORRELATION (risk management):
    - If too many signals point in one direction across categories,
      reduce aggregate confidence (crowded trade risk)
    - If signals in correlated categories conflict, flag as uncertain
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from sibyl.pipelines.base_pipeline import PipelineSignal

if TYPE_CHECKING:
    from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.pipelines.correlation")


# ── Correlation Rules ────────────────────────────────────────────────────

CATEGORY_CORRELATIONS: dict[tuple[str, str], float] = {
    # (cat_a, cat_b): correlation_strength (0-1)
    ("Economics", "Financials"): 0.70,
    ("Economics", "Companies"): 0.55,
    ("Financials", "Companies"): 0.65,
    ("Crypto", "Financials"): 0.35,
    ("Politics", "Geopolitics & Legal"): 0.60,
    ("Politics", "Economics"): 0.40,
    ("Weather", "Economics"): 0.20,  # Extreme weather → supply chain
    ("Culture", "Mentions"): 0.45,
}

# Max allowed aggregate exposure to correlated categories
MAX_CORRELATED_EXPOSURE = 0.30


@dataclass
class CorrelationResult:
    """Result of cross-category correlation analysis."""
    boosted_signals: list[PipelineSignal] = field(default_factory=list)
    composite_signals: list[PipelineSignal] = field(default_factory=list)
    correlation_warnings: list[str] = field(default_factory=list)
    confidence_adjustments: dict[str, float] = field(default_factory=dict)


class CrossCategoryCorrelationEngine:
    """Detects and exploits signal convergence across category pipelines.

    Usage:
        engine = CrossCategoryCorrelationEngine(db)
        all_signals = econ_signals + weather_signals + crypto_signals + ...
        result = await engine.analyze(all_signals)
        # result.boosted_signals = signals with confidence boosted
        # result.composite_signals = new composite signals created
        # result.correlation_warnings = risk warnings
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def analyze(
        self, all_signals: list[PipelineSignal]
    ) -> CorrelationResult:
        """Run cross-category correlation analysis on all pipeline signals.

        Args:
            all_signals: Combined signals from all category pipelines.

        Returns:
            CorrelationResult with boosted/composite signals and warnings.
        """
        result = CorrelationResult()

        if len(all_signals) < 2:
            return result

        # Group signals by category
        by_category: dict[str, list[PipelineSignal]] = defaultdict(list)
        for sig in all_signals:
            by_category[sig.category].append(sig)

        # Step 1: Detect cross-category reinforcement
        categories = list(by_category.keys())
        for i, cat_a in enumerate(categories):
            for cat_b in categories[i + 1:]:
                correlation = self._get_correlation(cat_a, cat_b)
                if correlation < 0.20:
                    continue  # Not meaningfully correlated

                reinforcing = self._find_reinforcing_signals(
                    by_category[cat_a], by_category[cat_b]
                )
                for sig_a, sig_b in reinforcing:
                    # Boost the stronger signal
                    boost = correlation * 0.10  # Max 7% boost for 0.70 correlation
                    stronger = sig_a if sig_a.confidence >= sig_b.confidence else sig_b
                    stronger.confidence = min(stronger.confidence + boost, 0.99)
                    result.boosted_signals.append(stronger)
                    result.confidence_adjustments[stronger.market_id] = boost

                    logger.info(
                        "Cross-category reinforcement: %s ↔ %s (corr=%.2f, boost=+%.2f)",
                        cat_a, cat_b, correlation, boost,
                    )

        # Step 2: Generate composite signals for strong multi-source agreement
        composites = self._generate_composite_signals(by_category)
        result.composite_signals = composites

        # Step 3: Check for crowded trade risk
        warnings = self._check_crowded_trade_risk(by_category)
        result.correlation_warnings = warnings

        # Step 4: Check for conflicting signals
        conflicts = self._detect_conflicting_signals(by_category)
        result.correlation_warnings.extend(conflicts)

        # Step 5: Write composite signals to DB
        for sig in composites:
            try:
                await self._db.execute(
                    """INSERT INTO signals
                       (market_id, signal_type, confidence, ev_estimate,
                        status, detection_modes_triggered, reasoning)
                       VALUES (?, ?, ?, ?, 'PENDING', ?, ?)""",
                    (
                        sig.market_id,
                        "COMPOSITE_HIGH_CONVICTION",
                        round(sig.confidence, 4),
                        round(sig.ev_estimate, 4),
                        f"PIPELINE:correlation|DIR:{sig.direction}",
                        sig.reasoning,
                    ),
                )
            except Exception as e:
                logger.error("Failed to write composite signal: %s", e)

        if composites:
            await self._db.commit()
            logger.info(
                "Correlation engine: %d composite signals, %d boosted, %d warnings",
                len(composites), len(result.boosted_signals),
                len(result.correlation_warnings),
            )

        return result

    def _get_correlation(self, cat_a: str, cat_b: str) -> float:
        """Look up correlation strength between two categories."""
        key1 = (cat_a, cat_b)
        key2 = (cat_b, cat_a)
        return CATEGORY_CORRELATIONS.get(
            key1, CATEGORY_CORRELATIONS.get(key2, 0.0)
        )

    @staticmethod
    def _find_reinforcing_signals(
        signals_a: list[PipelineSignal],
        signals_b: list[PipelineSignal],
    ) -> list[tuple[PipelineSignal, PipelineSignal]]:
        """Find pairs of signals that reinforce each other.

        Two signals reinforce if they:
        1. Target the same market, OR
        2. Have the same directional bias (both bullish/bearish on economy)
        """
        pairs = []

        for sig_a in signals_a:
            for sig_b in signals_b:
                # Same market — direct reinforcement
                if sig_a.market_id == sig_b.market_id:
                    if sig_a.direction == sig_b.direction:
                        pairs.append((sig_a, sig_b))
                    continue

                # Directional alignment — indirect reinforcement
                # Both point same direction with high confidence
                if (
                    sig_a.direction == sig_b.direction
                    and sig_a.confidence >= 0.60
                    and sig_b.confidence >= 0.60
                ):
                    pairs.append((sig_a, sig_b))

        return pairs

    @staticmethod
    def _generate_composite_signals(
        by_category: dict[str, list[PipelineSignal]],
    ) -> list[PipelineSignal]:
        """Generate COMPOSITE_HIGH_CONVICTION signals for multi-source agreement.

        If 3+ categories produce signals pointing the same direction,
        create a composite signal with boosted confidence.
        """
        composites: list[PipelineSignal] = []

        # Group all signals by market_id
        by_market: dict[str, list[PipelineSignal]] = defaultdict(list)
        for signals in by_category.values():
            for sig in signals:
                by_market[sig.market_id].append(sig)

        for market_id, market_signals in by_market.items():
            if len(market_signals) < 2:
                continue

            # Check directional consensus
            yes_count = sum(1 for s in market_signals if s.direction == "YES")
            no_count = sum(1 for s in market_signals if s.direction == "NO")

            if yes_count >= 2 or no_count >= 2:
                # Multi-source agreement
                direction = "YES" if yes_count > no_count else "NO"
                agreeing = [
                    s for s in market_signals if s.direction == direction
                ]

                # Composite confidence: weighted average + consensus bonus
                avg_conf = sum(s.confidence for s in agreeing) / len(agreeing)
                consensus_bonus = min(len(agreeing) * 0.05, 0.15)
                composite_conf = min(avg_conf + consensus_bonus, 0.95)

                avg_ev = sum(s.ev_estimate for s in agreeing) / len(agreeing)

                sources = set(s.source_pipeline for s in agreeing)
                reasoning = (
                    f"COMPOSITE: {len(agreeing)} pipelines agree ({', '.join(sources)}). "
                    f"Direction: {direction}. "
                    f"Individual confidences: "
                    + ", ".join(f"{s.source_pipeline}={s.confidence:.2f}" for s in agreeing)
                )

                composites.append(PipelineSignal(
                    market_id=market_id,
                    signal_type="COMPOSITE_HIGH_CONVICTION",
                    confidence=composite_conf,
                    ev_estimate=avg_ev,
                    direction=direction,
                    reasoning=reasoning,
                    source_pipeline="correlation",
                    category=agreeing[0].category,
                ))

        return composites

    @staticmethod
    def _check_crowded_trade_risk(
        by_category: dict[str, list[PipelineSignal]],
    ) -> list[str]:
        """Warn if too many signals point the same direction (crowded trade)."""
        warnings = []

        total_signals = sum(len(sigs) for sigs in by_category.values())
        if total_signals == 0:
            return warnings

        yes_total = sum(
            1 for sigs in by_category.values()
            for s in sigs if s.direction == "YES"
        )
        no_total = total_signals - yes_total

        # If >80% of signals point one direction, warn
        dominant_pct = max(yes_total, no_total) / total_signals
        if dominant_pct > 0.80 and total_signals >= 5:
            dominant_dir = "YES" if yes_total > no_total else "NO"
            warnings.append(
                f"CROWDED_TRADE: {dominant_pct:.0%} of {total_signals} signals "
                f"point {dominant_dir}. Consider reducing aggregate exposure."
            )

        # Check correlated category clusters
        for (cat_a, cat_b), corr in CATEGORY_CORRELATIONS.items():
            if corr < 0.50:
                continue
            sigs_a = by_category.get(cat_a, [])
            sigs_b = by_category.get(cat_b, [])
            if len(sigs_a) >= 2 and len(sigs_b) >= 2:
                warnings.append(
                    f"CORRELATED_CLUSTER: {cat_a} ({len(sigs_a)} signals) and "
                    f"{cat_b} ({len(sigs_b)} signals) are correlated (r={corr:.2f}). "
                    f"Monitor aggregate exposure."
                )

        return warnings

    @staticmethod
    def _detect_conflicting_signals(
        by_category: dict[str, list[PipelineSignal]],
    ) -> list[str]:
        """Detect conflicting signals on the same market from different categories."""
        warnings = []

        by_market: dict[str, list[PipelineSignal]] = defaultdict(list)
        for sigs in by_category.values():
            for sig in sigs:
                by_market[sig.market_id].append(sig)

        for market_id, sigs in by_market.items():
            if len(sigs) < 2:
                continue
            directions = set(s.direction for s in sigs)
            if len(directions) > 1:
                sources = [f"{s.source_pipeline}→{s.direction}" for s in sigs]
                warnings.append(
                    f"CONFLICT: Market {market_id} has conflicting signals: "
                    + ", ".join(sources)
                )

        return warnings
