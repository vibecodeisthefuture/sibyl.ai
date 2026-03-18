"""
Intelligence agents package — signal detection and routing layer (Sprint 2).

These agents analyze the raw market data collected by Sprint 1's monitors
and produce actionable trading signals for the execution layer (Sprint 3).

PIPELINE ARCHITECTURE:
    MarketIntelligenceAgent (detects unusual activity)
        │
        ▼ detection events (in-memory queue)
        │
    SignalGenerator (scores detections → signals)
        │
        ▼ writes to `signals` table (status=PENDING)
        │
    SignalRouter (routes to SGE/ACE engines)
        │
        ▼ updates signals (status=ROUTED, routed_to=SGE|ACE|BOTH)
        │
    (Sprint 3) Position Lifecycle Manager → executes trades

Available agents:
    MarketIntelligenceAgent — 3 surveillance modes (Whale, Volume, OrderBook)
    SignalGenerator         — Composite scoring, EV estimation, signal classification
    SignalRouter            — Routes signals based on engine whitelists + risk thresholds

Data flow summary:
    trades_log + prices + orderbook → Intelligence → signals → Router → engines
"""

from sibyl.agents.intelligence.market_intelligence import MarketIntelligenceAgent
from sibyl.agents.intelligence.signal_generator import SignalGenerator
from sibyl.agents.intelligence.signal_router import SignalRouter

__all__ = [
    "MarketIntelligenceAgent",
    "SignalGenerator",
    "SignalRouter",
]
