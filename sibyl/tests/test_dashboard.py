"""
Tests for Sprint 5 Dashboard & Notifications.

Tests:
    - Notifier agent — cursor tracking, event detection, ntfy.sh transport mock
    - Dashboard API — all REST endpoints return expected shapes
    - Dashboard frontend — HTML content served at root
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def db(event_loop):
    from sibyl.core.database import DatabaseManager

    async def _setup():
        db = DatabaseManager(":memory:")
        await db.initialize()
        return db

    return event_loop.run_until_complete(_setup())


@pytest.fixture
def config():
    return {
        "polling": {
            "price_snapshot_interval_seconds": 5,
            "position_sync_interval_seconds": 15,
        },
        "platforms": {
            "polymarket": {"rate_limit_per_second": 80},
            "kalshi": {"rate_limit_per_second": 8},
        },
        "cross_platform": {
            "similarity_threshold": 0.55,
            "price_divergence_alert_pct": 0.05,
        },
        "notifications": {
            "enabled": True,
            "channel": "ntfy",
            "ntfy_server": "https://ntfy.sh",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# Notifier Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_notifier_disabled_without_config(db, event_loop):
    """Notifier should be idle when notifications.enabled is False."""
    from sibyl.agents.notifications.notifier import Notifier

    config_disabled = {"notifications": {"enabled": False}}

    async def _test():
        notifier = Notifier(db=db, config=config_disabled)
        await notifier.start()
        assert notifier._enabled is False
        await notifier.stop()

    event_loop.run_until_complete(_test())


def test_notifier_starts_with_ntfy_url(db, config, event_loop):
    """Notifier should build the ntfy URL from env vars."""
    from sibyl.agents.notifications.notifier import Notifier
    import os

    async def _test():
        os.environ["NTFY_TOPIC"] = "test-sibyl"
        os.environ["NTFY_SERVER"] = "https://ntfy.example.com"

        notifier = Notifier(db=db, config=config)
        await notifier.start()

        assert notifier._enabled is True
        assert notifier._ntfy_url == "https://ntfy.example.com/test-sibyl"

        await notifier.stop()

        # Cleanup
        os.environ.pop("NTFY_TOPIC", None)
        os.environ.pop("NTFY_SERVER", None)

    event_loop.run_until_complete(_test())


def test_notifier_restores_cursors(db, config, event_loop):
    """Notifier should restore last-seen cursors from system_state."""
    from sibyl.agents.notifications.notifier import Notifier
    import os

    async def _test():
        # Pre-seed cursor values
        await db.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("notifier_last_signal_id", "42"),
        )
        await db.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("notifier_last_position_id", "17"),
        )
        await db.commit()

        os.environ["NTFY_TOPIC"] = "test-sibyl"
        notifier = Notifier(db=db, config=config)
        await notifier.start()

        assert notifier._last_signal_id == 42
        assert notifier._last_position_id == 17

        await notifier.stop()
        os.environ.pop("NTFY_TOPIC", None)

    event_loop.run_until_complete(_test())


def test_notifier_detects_new_signals(db, config, event_loop):
    """Notifier should detect new ROUTED signals and send notifications."""
    from sibyl.agents.notifications.notifier import Notifier
    import os

    async def _test():
        os.environ["NTFY_TOPIC"] = "test-sibyl"
        notifier = Notifier(db=db, config=config)
        await notifier.start()

        # Seed a market and a routed signal
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('MKT-1', 'kalshi', 'Fed Rate Decision')"
        )
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, routed_to, status)
               VALUES ('MKT-1', 'WHALE', 0.85, 0.12, 'SGE', 'ROUTED')"""
        )
        await db.commit()

        # Mock the HTTP client to capture the notification
        sent_notifications = []

        async def mock_post(url, content, headers):
            sent_notifications.append({"url": url, "content": content, "headers": headers})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            return mock_resp

        mock_client = MagicMock()
        mock_client.post = mock_post
        mock_client.aclose = AsyncMock()
        notifier._http_client = mock_client

        await notifier._check_new_signals()

        assert len(sent_notifications) == 1
        assert "Fed Rate Decision" in sent_notifications[0]["content"]
        assert "SGE" in sent_notifications[0]["headers"]["Title"]
        assert notifier._last_signal_id == 1

        await notifier.stop()
        os.environ.pop("NTFY_TOPIC", None)

    event_loop.run_until_complete(_test())


def test_notifier_detects_circuit_breaker_change(db, config, event_loop):
    """Notifier should detect circuit breaker state changes."""
    from sibyl.agents.notifications.notifier import Notifier
    import os

    async def _test():
        os.environ["NTFY_TOPIC"] = "test-sibyl"
        notifier = Notifier(db=db, config=config)
        await notifier.start()

        # Set SGE circuit breaker to TRIGGERED
        await db.execute(
            "UPDATE engine_state SET circuit_breaker = 'TRIGGERED' WHERE engine = 'SGE'"
        )
        await db.commit()

        sent = []

        async def mock_post(url, content, headers):
            sent.append({"content": content, "headers": headers})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            return mock_resp

        mock_client = MagicMock()
        mock_client.post = mock_post
        mock_client.aclose = AsyncMock()
        notifier._http_client = mock_client

        await notifier._check_circuit_breakers()

        assert len(sent) == 1
        assert "CIRCUIT BREAKER" in sent[0]["headers"]["Title"]
        assert notifier._last_circuit_sge == "TRIGGERED"

        # Second check should NOT re-send (same state)
        sent.clear()
        await notifier._check_circuit_breakers()
        assert len(sent) == 0

        await notifier.stop()
        os.environ.pop("NTFY_TOPIC", None)

    event_loop.run_until_complete(_test())


def test_notifier_drawdown_level(db, config):
    """Notifier drawdown level classification."""
    from sibyl.agents.notifications.notifier import PRIORITY_MAP
    assert PRIORITY_MAP["drawdown"] == "5"
    assert PRIORITY_MAP["signal"] == "3"


# ═══════════════════════════════════════════════════════════════════════════
# Dashboard API Tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def app(db, event_loop):
    """Create a FastAPI test app with an initialized database."""
    from sibyl.dashboard.api import create_app
    return create_app(db)


def test_api_health(app, event_loop):
    """Health endpoint should return ok status."""
    from httpx import AsyncClient, ASGITransport

    async def _test():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    event_loop.run_until_complete(_test())


def test_api_portfolio(app, db, event_loop):
    """Portfolio endpoint should return balance data."""
    from httpx import AsyncClient, ASGITransport

    async def _test():
        # Seed portfolio balance
        await db.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES ('portfolio_total_balance', '500.00', datetime('now'))"
        )
        await db.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES ('portfolio_cash_reserve', '25.00', datetime('now'))"
        )
        await db.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES ('portfolio_allocable', '475.00', datetime('now'))"
        )
        await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/portfolio")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_balance"] == 500.0
            assert data["cash_reserve"] == 25.0

    event_loop.run_until_complete(_test())


def test_api_positions_empty(app, event_loop):
    """Positions endpoint should return empty list when no open positions."""
    from httpx import AsyncClient, ASGITransport

    async def _test():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/positions")
            assert resp.status_code == 200
            assert resp.json() == []

    event_loop.run_until_complete(_test())


def test_api_positions_with_data(app, db, event_loop):
    """Positions endpoint should return open positions with market title."""
    from httpx import AsyncClient, ASGITransport

    async def _test():
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('MKT-1', 'kalshi', 'Test Market')"
        )
        await db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price, current_price, status)
               VALUES ('MKT-1', 'kalshi', 'SGE', 'YES', 10, 0.50, 0.60, 'OPEN')"""
        )
        await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/positions")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["title"] == "Test Market"
            assert data[0]["engine"] == "SGE"

    event_loop.run_until_complete(_test())


def test_api_signals(app, db, event_loop):
    """Signals endpoint should return recent signals."""
    from httpx import AsyncClient, ASGITransport

    async def _test():
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('MKT-1', 'kalshi', 'Test')"
        )
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, routed_to, status)
               VALUES ('MKT-1', 'WHALE', 0.85, 0.12, 'SGE', 'ROUTED')"""
        )
        await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/signals")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["signal_type"] == "WHALE"

    event_loop.run_until_complete(_test())


def test_api_risk(app, db, event_loop):
    """Risk endpoint should return drawdown and win rate metrics."""
    from httpx import AsyncClient, ASGITransport

    async def _test():
        for key, value in [
            ("risk_hwm", "1000.00"),
            ("risk_drawdown_pct", "0.05"),
            ("risk_drawdown_level", "WARNING"),
            ("risk_win_rate_7d", "0.75"),
            ("risk_sharpe_30d", "1.2"),
        ]:
            await db.execute(
                "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                (key, value),
            )
        await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/risk")
            assert resp.status_code == 200
            data = resp.json()
            assert data["high_water_mark"] == 1000.0
            assert data["drawdown_level"] == "WARNING"
            assert data["win_rate_7d"] == 0.75

    event_loop.run_until_complete(_test())


def test_api_engines(app, db, event_loop):
    """Engines endpoint should return SGE and ACE state."""
    from httpx import AsyncClient, ASGITransport

    async def _test():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/engines")
            assert resp.status_code == 200
            data = resp.json()
            assert "SGE" in data
            assert "ACE" in data
            assert "total_capital" in data["SGE"]

    event_loop.run_until_complete(_test())


def test_api_chart_portfolio(app, db, event_loop):
    """Chart endpoint should return time-series data."""
    from httpx import AsyncClient, ASGITransport

    async def _test():
        # Seed some data so the chart has at least one point
        await db.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES ('portfolio_total_balance', '500.00', datetime('now'))"
        )
        await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/chart/portfolio")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            # Should have at least the "today" point
            assert len(data) >= 1
            assert "date" in data[0]
            assert "value" in data[0]

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Frontend Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_frontend_html_contains_react():
    """Frontend HTML should contain React app and Recharts."""
    from sibyl.dashboard.frontend import DASHBOARD_HTML
    assert "Sibyl" in DASHBOARD_HTML
    assert "react" in DASHBOARD_HTML.lower()
    assert "recharts" in DASHBOARD_HTML.lower()
    assert "AreaChart" in DASHBOARD_HTML
    assert "portfolio" in DASHBOARD_HTML  # fetcher('/portfolio') in JS


def test_frontend_html_uses_design_system():
    """Frontend should use the Priscey-inspired holographic design system."""
    from sibyl.dashboard.frontend import DASHBOARD_HTML
    # Check for the design system colors
    assert "#0F0E1A" in DASHBOARD_HTML  # --bg
    assert "#1A1930" in DASHBOARD_HTML  # --card
    assert "#3D3C6B" in DASHBOARD_HTML  # --border
    # Holographic gradient colors
    assert "#C8A587" in DASHBOARD_HTML  # --holo-gold
    assert "#C29194" in DASHBOARD_HTML  # --holo-rose
    assert "#56549D" in DASHBOARD_HTML  # --holo-purple
    # Fonts
    assert "DM Sans" in DASHBOARD_HTML
    assert "JetBrains Mono" in DASHBOARD_HTML
    # Category colors for Kalshi market verticals
    assert "cat-politics" in DASHBOARD_HTML
    assert "cat-crypto" in DASHBOARD_HTML


def test_frontend_html_has_category_allocation():
    """Frontend should include the category allocation visualization."""
    from sibyl.dashboard.frontend import DASHBOARD_HTML
    assert "CategoryAllocation" in DASHBOARD_HTML
    assert "PieChart" in DASHBOARD_HTML


# ═══════════════════════════════════════════════════════════════════════════
# New API Endpoint Tests (Sprint 9 Dashboard Enhancements)
# ═══════════════════════════════════════════════════════════════════════════


def test_api_categories_empty(app, event_loop):
    """Categories endpoint should return empty list when no open positions."""
    from httpx import AsyncClient, ASGITransport

    async def _test():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/categories")
            assert resp.status_code == 200
            assert resp.json() == []

    event_loop.run_until_complete(_test())


def test_api_categories_with_data(app, db, event_loop):
    """Categories endpoint should aggregate position data by market category."""
    from httpx import AsyncClient, ASGITransport

    async def _test():
        # Seed markets with categories
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-P1', 'kalshi', 'Election Winner', 'Politics')"
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-S1', 'kalshi', 'Super Bowl', 'Sports')"
        )
        # Seed open positions
        await db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price, current_price, pnl, status)
               VALUES ('MKT-P1', 'kalshi', 'SGE', 'YES', 10, 0.50, 0.55, 0.50, 'OPEN')"""
        )
        await db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price, current_price, pnl, status)
               VALUES ('MKT-S1', 'kalshi', 'ACE', 'YES', 20, 0.30, 0.35, 1.00, 'OPEN')"""
        )
        await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/categories")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 2
            # Both categories should be present
            cats = {d["category"] for d in data}
            assert "Politics" in cats
            assert "Sports" in cats

    event_loop.run_until_complete(_test())


def test_api_research_empty(app, event_loop):
    """Research endpoint should return empty list when no research exists."""
    from httpx import AsyncClient, ASGITransport

    async def _test():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/research")
            assert resp.status_code == 200
            assert resp.json() == []

    event_loop.run_until_complete(_test())
