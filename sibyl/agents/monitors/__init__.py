"""
Monitor agents package — data ingestion layer (Sprint 1).

These agents continuously poll external APIs and write market data to SQLite.
They are the foundation of Sibyl's data pipeline — all other agents (Intelligence,
Signal Router, etc.) consume the data these monitors produce.

Available agents:
    PolymarketMonitorAgent — Ingests data from Polymarket (READ-ONLY due to US geo)
    KalshiMonitorAgent     — Ingests data from Kalshi (PRIMARY trading platform)
    CrossPlatformSyncAgent — Matches markets across platforms + detects price divergences

Data flow:
    Polymarket API ─→ PolymarketMonitorAgent ─→ SQLite ←─ CrossPlatformSyncAgent
    Kalshi API     ─→ KalshiMonitorAgent     ─→ SQLite ←─        ↑ (reads both)
"""

from sibyl.agents.monitors.polymarket_monitor import PolymarketMonitorAgent
from sibyl.agents.monitors.kalshi_monitor import KalshiMonitorAgent
from sibyl.agents.monitors.sync_agent import CrossPlatformSyncAgent

__all__ = [
    "PolymarketMonitorAgent",
    "KalshiMonitorAgent",
    "CrossPlatformSyncAgent",
]
