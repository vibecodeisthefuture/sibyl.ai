"""
Category Strategy Manager — per-vertical strategy intelligence for Kalshi markets.

PURPOSE:
    Kalshi organizes its prediction markets into 10 distinct categories, each
    with fundamentally different dynamics:

        Politics, Sports, Culture, Crypto, Climate,
        Economics, Mentions, Companies, Financials, Tech & Science

    This module loads category-specific strategy configurations and provides
    signal-level adjustments (confidence, EV, sizing, routing preference) so
    the system can specialize its approach per vertical.

HOW IT WORKS:
    1. On startup, loads `config/category_strategies.yaml` which defines:
       - Confidence/EV modifiers per category
       - Signal type weight overrides (e.g., WHALE is 1.4x in Sports)
       - Position sizing scale factors
       - Maximum category exposure limits
       - Preferred engine (SGE vs ACE) per category
       - Correlation penalty for related markets
    2. When a signal arrives, `adjust_signal()` applies the category's
       modifiers to produce adjusted confidence, EV, and sizing params.
    3. `get_routing_preference()` tells the SignalRouter which engine
       should handle this category by default.
    4. `check_category_exposure()` enforces per-category portfolio limits.

USAGE:
    strategy_mgr = CategoryStrategyManager()
    await strategy_mgr.initialize()

    # When processing a signal:
    adjusted = strategy_mgr.adjust_signal(
        category="Sports",
        signal_type="WHALE",
        raw_confidence=0.82,
        raw_ev=0.08,
    )
    # adjusted.confidence = 0.82 * 1.05 (Sports modifier) = 0.861
    # adjusted.ev = 0.08 * 1.0 = 0.08
    # adjusted.signal_weight = 1.4 (WHALE is 1.4x in Sports)
    # adjusted.size_scale = 1.1 (Sports sizes slightly larger)
    # adjusted.preferred_engine = "ACE"

CONFIGURATION:
    config/category_strategies.yaml — full per-category definitions
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("sibyl.agents.category_strategy")


# ── Adjusted Signal Output ────────────────────────────────────────────

@dataclass
class AdjustedSignal:
    """Signal parameters after category-specific adjustments.

    Attributes:
        confidence:       Adjusted confidence (raw × category modifier).
        ev:               Adjusted EV (raw × category modifier).
        signal_weight:    Category-specific weight for this signal type (1.0 = neutral).
        size_scale:       Position size multiplier (0.5 = half-size, 1.5 = 150%).
        preferred_engine: Category's default engine ("SGE" or "ACE").
        max_exposure_pct: Maximum portfolio exposure to this category.
        correlation_penalty: Sizing reduction when multiple positions in category.
        strategy_type:    Human-readable strategy label (e.g., "data_driven_momentum").
        time_horizon:     Expected holding period ("short", "medium", "long").
    """
    confidence: float = 0.0
    ev: float = 0.0
    signal_weight: float = 1.0
    size_scale: float = 1.0
    preferred_engine: str = "SGE"
    max_exposure_pct: float = 0.08
    correlation_penalty: float = 0.05
    strategy_type: str = "balanced"
    time_horizon: str = "medium"


# ── Category Strategy Definition ──────────────────────────────────────

@dataclass
class CategoryStrategy:
    """Loaded strategy parameters for a single market category.

    Created from one entry in config/category_strategies.yaml.
    """
    name: str
    strategy_type: str = "balanced"
    preferred_engine: str = "SGE"
    confidence_modifier: float = 1.0
    ev_modifier: float = 1.0
    max_exposure_pct: float = 0.08
    position_size_scale: float = 1.0
    signal_weights: dict[str, float] = field(default_factory=dict)
    data_priority: list[str] = field(default_factory=list)
    time_horizon: str = "medium"
    volatility_profile: str = "medium"
    correlation_penalty: float = 0.05
    notes: str = ""


# ── Manager ───────────────────────────────────────────────────────────

class CategoryStrategyManager:
    """Loads and applies category-specific strategy adjustments.

    This class is stateless after initialization — it reads the config
    once and provides pure-function adjustments.  It does NOT poll or
    maintain background tasks.

    Usage:
        mgr = CategoryStrategyManager()
        await mgr.initialize()

        adjusted = mgr.adjust_signal("Sports", "WHALE", 0.82, 0.08)
    """

    def __init__(self) -> None:
        """Initialize with empty strategy map."""
        self._strategies: dict[str, CategoryStrategy] = {}
        self._defaults: CategoryStrategy = CategoryStrategy(name="_defaults")
        self._initialized: bool = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def categories(self) -> list[str]:
        """Return list of configured category names."""
        return [k for k in self._strategies if not k.startswith("_")]

    async def initialize(self) -> None:
        """Load category strategies from config/category_strategies.yaml.

        Safe to call multiple times — reloads on each call.
        Falls back to defaults if config file is missing.
        """
        from sibyl.core.config import load_yaml

        try:
            raw = load_yaml("category_strategies.yaml")
        except FileNotFoundError:
            logger.warning(
                "category_strategies.yaml not found — using defaults for all categories"
            )
            raw = {}

        self._strategies.clear()

        # Load defaults first
        if "_defaults" in raw:
            self._defaults = self._parse_strategy("_defaults", raw["_defaults"])
            del raw["_defaults"]

        # Load each category
        for cat_name, cat_config in raw.items():
            if isinstance(cat_config, dict):
                self._strategies[cat_name] = self._parse_strategy(cat_name, cat_config)
                logger.debug(
                    "Loaded strategy for %s: type=%s, engine=%s",
                    cat_name,
                    cat_config.get("strategy_type", "balanced"),
                    cat_config.get("preferred_engine", "SGE"),
                )

        self._initialized = True
        logger.info(
            "CategoryStrategyManager initialized: %d categories loaded (%s)",
            len(self._strategies),
            ", ".join(sorted(self._strategies.keys())),
        )

    def get_strategy(self, category: str | None) -> CategoryStrategy:
        """Get the strategy for a given category.

        Args:
            category: Market category string (e.g., "Politics", "Sports").
                      Returns defaults if category is None or unrecognized.

        Returns:
            CategoryStrategy for the given category.
        """
        if not category:
            return self._defaults

        # Try exact match first
        if category in self._strategies:
            return self._strategies[category]

        # Try case-insensitive match
        lower = category.lower()
        for name, strat in self._strategies.items():
            if name.lower() == lower:
                return strat

        return self._defaults

    def adjust_signal(
        self,
        category: str | None,
        signal_type: str,
        raw_confidence: float,
        raw_ev: float,
    ) -> AdjustedSignal:
        """Apply category-specific adjustments to a signal.

        This is the main entry point for the routing and execution layers.
        Takes raw signal parameters and returns adjusted values that account
        for category-specific dynamics.

        Args:
            category:       Market category string.
            signal_type:    Signal type (e.g., "WHALE", "SENTIMENT").
            raw_confidence: Raw confidence score (0.0–1.0).
            raw_ev:         Raw expected value estimate.

        Returns:
            AdjustedSignal with modified parameters.
        """
        strat = self.get_strategy(category)

        # Apply confidence and EV modifiers
        adj_confidence = min(raw_confidence * strat.confidence_modifier, 0.99)
        adj_ev = raw_ev * strat.ev_modifier

        # Get signal type weight (default 1.0 if not specified)
        signal_weight = strat.signal_weights.get(signal_type, 1.0)

        # Apply signal weight to confidence (blended approach)
        # Weight > 1.0 boosts confidence, < 1.0 reduces it
        # Use a mild blending factor to prevent extreme swings
        weight_factor = 0.3  # How much signal weight affects confidence
        weighted_confidence = adj_confidence * (
            1.0 + (signal_weight - 1.0) * weight_factor
        )
        weighted_confidence = max(0.0, min(weighted_confidence, 0.99))

        return AdjustedSignal(
            confidence=round(weighted_confidence, 4),
            ev=round(adj_ev, 4),
            signal_weight=signal_weight,
            size_scale=strat.position_size_scale,
            preferred_engine=strat.preferred_engine,
            max_exposure_pct=strat.max_exposure_pct,
            correlation_penalty=strat.correlation_penalty,
            strategy_type=strat.strategy_type,
            time_horizon=strat.time_horizon,
        )

    def get_routing_preference(self, category: str | None) -> str:
        """Get the preferred engine for a category.

        Returns "SGE" or "ACE" — used as a tiebreaker when a signal
        meets both engines' thresholds.

        Args:
            category: Market category string.

        Returns:
            "SGE" or "ACE".
        """
        return self.get_strategy(category).preferred_engine

    def get_max_exposure(self, category: str | None) -> float:
        """Get the maximum portfolio exposure for a category.

        Args:
            category: Market category string.

        Returns:
            Maximum exposure as a fraction (e.g., 0.15 = 15%).
        """
        return self.get_strategy(category).max_exposure_pct

    def get_correlation_penalty(self, category: str | None) -> float:
        """Get the correlation penalty for a category.

        Used to reduce position sizing when the portfolio already has
        multiple positions in the same category.

        Args:
            category: Market category string.

        Returns:
            Penalty as a fraction (e.g., 0.15 means reduce by 15% per existing position).
        """
        return self.get_strategy(category).correlation_penalty

    def get_data_priorities(self, category: str | None) -> list[str]:
        """Get the ordered list of most-valuable intelligence sources.

        Used by the BreakoutScout to prioritize research sources
        based on which category the market belongs to.

        Args:
            category: Market category string.

        Returns:
            Ordered list of data source names (most valuable first).
        """
        return self.get_strategy(category).data_priority

    # ── Internal ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_strategy(name: str, raw: dict[str, Any]) -> CategoryStrategy:
        """Parse a raw YAML dict into a CategoryStrategy dataclass.

        Args:
            name: Category name.
            raw:  Dictionary from category_strategies.yaml.

        Returns:
            CategoryStrategy instance.
        """
        return CategoryStrategy(
            name=name,
            strategy_type=raw.get("strategy_type", "balanced"),
            preferred_engine=raw.get("preferred_engine", "SGE"),
            confidence_modifier=float(raw.get("confidence_modifier", 1.0)),
            ev_modifier=float(raw.get("ev_modifier", 1.0)),
            max_exposure_pct=float(raw.get("max_exposure_pct", 0.08)),
            position_size_scale=float(raw.get("position_size_scale", 1.0)),
            signal_weights={
                k: float(v)
                for k, v in raw.get("signal_weights", {}).items()
            },
            data_priority=raw.get("data_priority", []),
            time_horizon=raw.get("time_horizon", "medium"),
            volatility_profile=raw.get("volatility_profile", "medium"),
            correlation_penalty=float(raw.get("correlation_penalty", 0.05)),
            notes=raw.get("notes", ""),
        )
