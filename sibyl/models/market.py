"""Market, Price, and OrderBook data models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Platform(str, Enum):
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"


class MarketStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"
    SUSPENDED = "suspended"


class MarketCategory(str, Enum):
    POLITICS = "politics"
    SPORTS = "sports"
    CRYPTO = "crypto"
    SCIENCE_TECH = "science_tech"
    ECONOMICS = "economics"
    OTHER = "other"


class Market(BaseModel):
    """A prediction market tracked by Sibyl."""

    id: str
    platform: Platform
    title: str
    category: MarketCategory | None = None
    close_date: datetime | None = None
    status: MarketStatus = MarketStatus.ACTIVE
    event_id: str | None = None
    event_id_confidence: float | None = None
    discovery_source: str | None = None
    breakout_score: float | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Price(BaseModel):
    """A point-in-time price snapshot for a market."""

    market_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    yes_price: float = Field(ge=0.0, le=1.0)
    no_price: float | None = Field(default=None, ge=0.0, le=1.0)
    volume_24h: float | None = None
    open_interest: float | None = None


class OrderLevel(BaseModel):
    """A single level in the order book."""

    price: float = Field(ge=0.0, le=1.0)
    quantity: float = Field(ge=0.0)


class OrderBookSnapshot(BaseModel):
    """L2 order book snapshot for a market."""

    market_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    bids: list[OrderLevel] = Field(default_factory=list)
    asks: list[OrderLevel] = Field(default_factory=list)

    @property
    def best_bid(self) -> float | None:
        return max((b.price for b in self.bids), default=None)

    @property
    def best_ask(self) -> float | None:
        return min((a.price for a in self.asks), default=None)

    @property
    def mid_price(self) -> float | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        return None

    @property
    def spread(self) -> float | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is not None and ask is not None:
            return ask - bid
        return None

    @property
    def normalized_spread(self) -> float | None:
        mid = self.mid_price
        spread = self.spread
        if mid and mid > 0 and spread is not None:
            return spread / mid
        return None


class Trade(BaseModel):
    """An observed market trade."""

    market_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    side: str = Field(pattern=r"^(YES|NO)$")
    size: float = Field(gt=0.0)
    price: float = Field(ge=0.0, le=1.0)
