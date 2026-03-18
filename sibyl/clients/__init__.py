"""
Platform API clients package.

This package contains async HTTP clients for interacting with prediction
market platform APIs.  Each client handles:
  - Rate limiting (to avoid being throttled)
  - Retry logic (for transient network errors)
  - Response parsing and normalization

Available clients:
    PolymarketClient — Read-only client for Polymarket (US geo-restricted)
    KalshiClient     — Full-featured client for Kalshi (primary trading platform)
"""

from sibyl.clients.polymarket_client import PolymarketClient
from sibyl.clients.kalshi_client import KalshiClient

__all__ = ["PolymarketClient", "KalshiClient"]
