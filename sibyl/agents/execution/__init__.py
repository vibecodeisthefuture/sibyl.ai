"""
Execution agents package — position management and trade execution (Sprint 3).

These agents consume the routed signals from Sprint 2's Intelligence Layer
and manage the full lifecycle of trading positions.

PIPELINE ARCHITECTURE:
    signals (status=ROUTED)
        │
        ▼
    OrderExecutor (Kelly sizing → paper/live fill)
        │
        ▼ writes to positions + executions tables
        │
    PositionLifecycleManager (5 sub-routines)
        │ A: Stop Guard         (7s  — stop loss + circuit breaker)
        │ B: EV Monitor         (90s/300s — re-estimate expected value)
        │ C: Exit Optimizer     (120s — profit taking)
        │ D: Resolution Tracker (300s — market convergence)
        │ E: Correlation Scanner(10m  — event exposure limits)
        ▼
    EngineStateManager (capital tracking, circuit breaker state)

Available agents:
    OrderExecutor          — Signal → position via Kelly-sized orders
    PositionLifecycleManager — 5 sub-routines monitoring open positions
    EngineStateManager     — Capital allocation + circuit breaker tracking
"""

from sibyl.agents.execution.order_executor import OrderExecutor
from sibyl.agents.execution.position_lifecycle import PositionLifecycleManager
from sibyl.agents.execution.engine_state_manager import EngineStateManager

__all__ = [
    "OrderExecutor",
    "PositionLifecycleManager",
    "EngineStateManager",
]
