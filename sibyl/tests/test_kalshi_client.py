"""Tests for the Kalshi API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from sibyl.clients.kalshi_client import KalshiClient


@pytest.fixture
def client():
    """Create unauthenticated Kalshi client for testing."""
    return KalshiClient(rate_limit=100.0, timeout=5.0)


@pytest.fixture
def auth_client(tmp_path):
    """Create authenticated Kalshi client with test RSA key."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    # Generate a test RSA key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "test_key.pem"
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return KalshiClient(
        key_id="test-key-id",
        private_key_path=str(key_path),
        rate_limit=100.0,
    )


# ── Authentication Tests ─────────────────────────────────────────────

def test_unauthenticated_client(client):
    """Client without keys should report not authenticated."""
    assert client.is_authenticated is False


def test_authenticated_client(auth_client):
    """Client with keys should report authenticated."""
    assert auth_client.is_authenticated is True


def test_auth_headers_generated(auth_client):
    """Auth client should generate valid header keys."""
    headers = auth_client._auth_headers("GET", "/markets")
    assert "KALSHI-ACCESS-KEY" in headers
    assert "KALSHI-ACCESS-TIMESTAMP" in headers
    assert "KALSHI-ACCESS-SIGNATURE" in headers
    assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"


def test_unauthenticated_headers_empty(client):
    """Unauth client should return empty headers."""
    headers = client._auth_headers("GET", "/markets")
    assert headers == {}


# ── Market Endpoint Tests ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_events_returns_dict(client):
    """get_events should return dict with events key."""
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "events": [{"event_ticker": "EVT-1", "title": "Test Event"}],
            "cursor": None,
        }
        result = await client.get_events(limit=10)
        assert "events" in result
        assert len(result["events"]) == 1


@pytest.mark.asyncio
async def test_get_markets_returns_dict(client):
    """get_markets should return dict with markets key."""
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "markets": [{"ticker": "MKT-1", "title": "Test Market"}],
            "cursor": None,
        }
        result = await client.get_markets(limit=10)
        assert "markets" in result
        assert result["markets"][0]["ticker"] == "MKT-1"


@pytest.mark.asyncio
async def test_get_market_returns_market(client):
    """get_market should return single market dict."""
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "market": {"ticker": "MKT-1", "title": "Test", "status": "open"},
        }
        result = await client.get_market("MKT-1")
        assert result is not None
        assert result["ticker"] == "MKT-1"


# ── Orderbook Normalization Tests ────────────────────────────────────

@pytest.mark.asyncio
async def test_orderbook_normalization(client):
    """Kalshi's yes/no bid format should normalize to standard bids/asks."""
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "orderbook": {
                "yes": [[60, 100], [55, 200]],  # [price_cents, qty_cents]
                "no": [[35, 150], [40, 50]],
            }
        }
        result = await client.get_orderbook("MKT-1")
        assert result is not None

        # YES bids should map directly
        assert len(result["bids"]) == 2
        assert result["bids"][0]["price"] == 0.60
        assert result["bids"][0]["size"] == 1.00
        assert result["bids"][1]["price"] == 0.55

        # NO bids should convert to YES asks (ask = 1.0 - no_price)
        assert len(result["asks"]) == 2
        # NO bid at 35¢ → YES ask at 65¢, NO bid at 40¢ → YES ask at 60¢
        # Sorted ascending: 60¢, 65¢
        assert result["asks"][0]["price"] == pytest.approx(0.60)
        assert result["asks"][1]["price"] == pytest.approx(0.65)


@pytest.mark.asyncio
async def test_orderbook_empty(client):
    """Empty orderbook should return empty bids and asks."""
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"orderbook": {"yes": [], "no": []}}
        result = await client.get_orderbook("MKT-1")
        assert result is not None
        assert result["bids"] == []
        assert result["asks"] == []


# ── Trade Endpoint Tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_trades_returns_dict(client):
    """get_trades should return dict with trades key."""
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "trades": [{"taker_side": "yes", "count": 10, "yes_price": 65}],
            "cursor": None,
        }
        result = await client.get_trades(ticker="MKT-1")
        assert "trades" in result
        assert len(result["trades"]) == 1


# ── Auth Endpoint Tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_balance_unauthenticated(client):
    """get_balance without auth should return None."""
    result = await client.get_balance()
    assert result is None


@pytest.mark.asyncio
async def test_get_positions_unauthenticated(client):
    """get_positions without auth should return empty."""
    result = await client.get_positions()
    assert result == {"market_positions": [], "cursor": None}


@pytest.mark.asyncio
async def test_client_close(client):
    """close should not raise even when client was never opened."""
    await client.close()
