"""Position, Execution, and Performance data models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class PositionSide(str, Enum):
    YES = "YES"
    NO = "NO"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    STOPPED = "STOPPED"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Engine(str, Enum):
    SGE = "SGE"
    ACE = "ACE"


class Position(BaseModel):
    """An open or closed trading position."""

    id: int | None = None
    market_id: str
    platform: str
    engine: Engine
    side: PositionSide
    size: float = Field(gt=0.0)
    entry_price: float = Field(ge=0.0, le=1.0)
    current_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    pnl: float = 0.0
    ev_current: float | None = None
    status: PositionStatus = PositionStatus.OPEN
    thesis: str | None = None
    signal_id: int | None = None
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None

    @property
    def unrealized_pnl(self) -> float | None:
        if self.current_price is None:
            return None
        if self.side == PositionSide.YES:
            return (self.current_price - self.entry_price) * self.size
        else:
            return (self.entry_price - self.current_price) * self.size

    @property
    def pnl_pct(self) -> float | None:
        if self.current_price is None:
            return None
        if self.entry_price == 0:
            return None
        if self.side == PositionSide.YES:
            return (self.current_price - self.entry_price) / self.entry_price
        else:
            return (self.entry_price - self.current_price) / self.entry_price


class Execution(BaseModel):
    """A trade execution record."""

    id: int | None = None
    signal_id: int | None = None
    position_id: int | None = None
    engine: Engine
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    platform: str
    order_id: str | None = None
    side: OrderSide
    fill_price: float = Field(ge=0.0, le=1.0)
    size: float = Field(gt=0.0)
    order_type: str = "LIMIT"


class PerformanceRecord(BaseModel):
    """Outcome tracking for a resolved position."""

    id: int | None = None
    signal_id: int | None = None
    position_id: int | None = None
    engine: Engine
    resolved: bool = False
    correct: bool | None = None
    pnl: float | None = None
    ev_estimated: float | None = None
    ev_realized: float | None = None
    post_mortem: str | None = None
    resolved_at: datetime | None = None
