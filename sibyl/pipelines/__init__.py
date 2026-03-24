"""
Category Signal Pipelines — Phase 3 of the Sibyl data pipeline.

Transforms raw API data from Sprint 12 data clients into actionable
trading signals for Kalshi prediction markets.

DATA FLOW:
    Data Clients (Sprint 12)
        → Category Signal Pipelines (Sprint 13)
            → signals table (status=PENDING)
                → SignalRouter → SGE/ACE engines
"""
