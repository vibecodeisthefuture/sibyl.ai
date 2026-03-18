"""
Sibyl — Autonomous Prediction Market Investing System.

This is the top-level Python package.  When you import `sibyl` or run
`python -m sibyl`, this file is loaded first.

Package structure:
    sibyl/
    ├── __init__.py        ← You are here
    ├── __main__.py        ← Entry point (python -m sibyl)
    ├── core/              ← Foundation: database, config, logging, base agent
    ├── clients/           ← HTTP clients for Polymarket and Kalshi APIs
    ├── agents/            ← Agent implementations
    │   └── monitors/      ← Data ingestion agents (Sprint 1)
    ├── models/            ← Pydantic data models (Market, Signal, Position)
    └── tests/             ← Unit and integration tests
"""

__version__ = "0.1.0"
