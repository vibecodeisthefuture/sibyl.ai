"""Tests for the Polymarket API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from sibyl.clients.polymarket_client import PolymarketClient, RateLimiter


# ── RateLimiter Tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limiter_allows_first_call():
    """First call should pass without delay."""
    limiter = RateLimiter(max_per_second=10.0)
    await limiter.acquire()  # Should not raise


# ── PolymarketClient Tests ───────────────────────────────────────────

@pytest.fixture
def client():
    return PolymarketClient(rate_limit=100.0, timeout=5.0)


@pytest.mark.asyncio
async def test_get_markets_returns_list(client):
    """get_markets should return a list of market dicts."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"condition_id": "abc123", "question": "Will it rain?", "tags": ["weather"]},
        {"condition_id": "def456", "question": "BTC > 100k?", "tags": ["crypto"]},
    ]
    mock_response.raise_for_status = MagicMock()

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = [
            {"condition_id": "abc123", "question": "Will it rain?"},
            {"condition_id": "def456", "question": "BTC > 100k?"},
        ]
        result = await client.get_markets(limit=10)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["condition_id"] == "abc123"


@pytest.mark.asyncio
async def test_get_midpoint_returns_float(client):
    """get_midpoint should return a float price."""
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"mid": "0.65"}
        result = await client.get_midpoint("token123")
        assert result == 0.65


@pytest.mark.asyncio
async def test_get_midpoint_returns_none_on_missing(client):
    """get_midpoint should return None when data is missing."""
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {}
        result = await client.get_midpoint("token123")
        assert result is None


@pytest.mark.asyncio
async def test_get_orderbook_parses_levels(client):
    """get_orderbook should parse bid/ask levels."""
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "bids": [{"price": "0.60", "size": "100"}, {"price": "0.55", "size": "200"}],
            "asks": [{"price": "0.65", "size": "150"}],
        }
        result = await client.get_orderbook("token123")
        assert result is not None
        assert len(result["bids"]) == 2
        assert len(result["asks"]) == 1
        assert result["bids"][0]["price"] == 0.60
        assert result["asks"][0]["size"] == 150.0


@pytest.mark.asyncio
async def test_get_trades_returns_data(client):
    """get_trades should return dict with data key."""
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "data": [{"side": "BUY", "size": "50", "price": "0.70"}],
            "next_cursor": "abc",
        }
        result = await client.get_trades(token_id="t1")
        assert "data" in result
        assert len(result["data"]) == 1


@pytest.mark.asyncio
async def test_get_price_returns_float(client):
    """get_price should return a float."""
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"price": "0.72"}
        result = await client.get_price("token123")
        assert result == 0.72


@pytest.mark.asyncio
async def test_client_close(client):
    """close should not raise even when client was never opened."""
    await client.close()  # Should not raise
