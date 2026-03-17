"""Signal and routing data models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SignalType(str, Enum):
    ARBITRAGE = "ARBITRAGE"
    MEAN_REVERSION = "MEAN_REVERSION"
    LIQUIDITY_VACUUM = "LIQUIDITY_VACUUM"
    MOMENTUM = "MOMENTUM"
    VOLUME_SURGE = "VOLUME_SURGE"
    STALE_MARKET = "STALE_MARKET"
    HIGH_CONVICTION_ARB = "HIGH_CONVICTION_ARB"
    DUAL_ENGINE = "DUAL_ENGINE"
    SCOUT_HIGH_CONVICTION = "SCOUT_HIGH_CONVICTION"
    SCOUT_MODERATE = "SCOUT_MODERATE"
    SCOUT_CONTESTED = "SCOUT_CONTESTED"
    RESOLUTION_OPPORTUNITY = "RESOLUTION_OPPORTUNITY"
    COMPOSITE_HIGH_CONVICTION = "COMPOSITE_HIGH_CONVICTION"


class EngineRouting(str, Enum):
    SGE = "SGE"
    ACE = "ACE"
    BOTH = "BOTH"
    DEFERRED = "DEFERRED"


class SignalStatus(str, Enum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    DEFERRED = "DEFERRED"
    VOID = "VOID"
    REJECTED = "REJECTED"


class CorrelationStatus(str, Enum):
    CLEAR = "CLEAR"
    FLAGGED = "FLAGGED"
    BLOCKED = "BLOCKED"


class DetectionMode(str, Enum):
    WHALE = "WHALE"
    VOLUME_SURGE = "VOLUME_SURGE"
    SPREAD_EXPANSION = "SPREAD_EXPANSION"
    WALL_APPEARED = "WALL_APPEARED"
    WALL_DISAPPEARED = "WALL_DISAPPEARED"
    LIQUIDITY_VACUUM = "LIQUIDITY_VACUUM"


class Signal(BaseModel):
    """A generated trading signal."""

    id: int | None = None
    market_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    signal_type: SignalType
    confidence: float = Field(ge=0.0, le=1.0)
    ev_estimate: float | None = None
    routed_to: EngineRouting | None = None
    status: SignalStatus = SignalStatus.PENDING
    routing_override: str | None = None
    confidence_adjusted: float | None = None
    counter_thesis: str | None = None
    reasoning: str | None = None
    scout_consensus_alignment: str | None = None
    detection_modes_triggered: list[DetectionMode] = Field(default_factory=list)
    pre_entry_correlation: CorrelationStatus | None = None


class WhaleEvent(BaseModel):
    """A whale trade detection event from Market Intelligence Mode A."""

    market_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    platform: str
    side: str = Field(pattern=r"^(YES|NO)$")
    size: float = Field(gt=0.0)
    price: float = Field(ge=0.0, le=1.0)
    threshold: float
    wallet_id: str | None = None
    severity: str = "NORMAL"


class MarketResearch(BaseModel):
    """Breakout Scout research packet for a market."""

    market_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    sentiment_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    sentiment_label: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_breakdown: dict | None = None
    key_yes_args: list[str] = Field(default_factory=list)
    key_no_args: list[str] = Field(default_factory=list)
    notable_dissent: str | None = None
    synthesis: str | None = None
    freshness_score: float = 1.0
    routing_priority: str | None = None
