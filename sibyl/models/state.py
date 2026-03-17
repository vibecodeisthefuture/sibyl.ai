"""System and engine state models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class CircuitBreakerStatus(str, Enum):
    CLEAR = "CLEAR"
    TRIPPED = "TRIPPED"
    COOLDOWN = "COOLDOWN"


class RiskPolicyLevel(str, Enum):
    CONSERVATIVE = "CONSERVATIVE"
    MODERATE = "MODERATE"
    AGGRESSIVE = "AGGRESSIVE"


class EngineState(BaseModel):
    """Runtime state of an engine (SGE or ACE)."""

    engine: str
    total_capital: float = 0.0
    deployed_capital: float = 0.0
    available_capital: float = 0.0
    exposure_pct: float = 0.0
    drawdown_pct: float = 0.0
    daily_pnl: float = 0.0
    circuit_breaker: CircuitBreakerStatus = CircuitBreakerStatus.CLEAR
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_circuit_broken(self) -> bool:
        return self.circuit_breaker != CircuitBreakerStatus.CLEAR

    @property
    def utilization_pct(self) -> float:
        if self.total_capital == 0:
            return 0.0
        return self.deployed_capital / self.total_capital


class AllocatorState(BaseModel):
    """Portfolio Allocator state — manages the 70/30 split."""

    sge_target_pct: float = 0.70
    ace_target_pct: float = 0.30
    sge_actual_pct: float = 0.70
    ace_actual_pct: float = 0.30
    rebalance_queue_count: int = 0
    cross_engine_blocks: list[str] = Field(default_factory=list)
    total_portfolio_value: float = 0.0
    portfolio_circuit_breaker: CircuitBreakerStatus = CircuitBreakerStatus.CLEAR
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def drift_pct(self) -> float:
        """How far the SGE allocation has drifted from target."""
        return abs(self.sge_actual_pct - self.sge_target_pct)

    @property
    def needs_rebalance(self) -> bool:
        return self.drift_pct > 0.05  # >5% drift triggers rebalance


class RiskPolicy(BaseModel):
    """Risk policy parameters for an engine."""

    level: RiskPolicyLevel
    kelly_fraction: float = Field(ge=0.0, le=1.0)
    min_ev_threshold: float
    min_confidence: float = Field(ge=0.0, le=1.0)
    max_single_position_pct: float = Field(ge=0.0, le=1.0)
    max_platform_exposure_pct: float = Field(ge=0.0, le=1.0)
    max_total_exposure_pct: float = Field(ge=0.0, le=1.0)
    circuit_breaker_drawdown_pct: float = Field(ge=0.0, le=1.0)
    daily_loss_limit_pct: float = Field(ge=0.0, le=1.0)
    per_market_stop_loss_pct: float = Field(ge=0.0, le=1.0)


class SystemHealth(BaseModel):
    """Overall system health snapshot for the Narrator."""

    sge_state: EngineState
    ace_state: EngineState
    allocator: AllocatorState
    active_alerts: list[str] = Field(default_factory=list)
    agent_statuses: dict[str, bool] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @property
    def alert_count(self) -> int:
        return len(self.active_alerts)

    @property
    def needs_immediate_digest(self) -> bool:
        """If >=2 active alerts, digest fires immediately."""
        return self.alert_count >= 2
