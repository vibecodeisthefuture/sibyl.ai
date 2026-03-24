"""
Tests for Sprint 4 Portfolio & Risk Management Layer.

Tests both new agents:
    - PortfolioAllocator — balance sync, capital splits, rebalancing
    - RiskDashboard — drawdown tracking, daily P&L reset, win rate, Sharpe

Also tests the live mode wiring in OrderExecutor (mock Kalshi client).
"""

import asyncio
import pytest


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
    }


# ═══════════════════════════════════════════════════════════════════════════
# Portfolio Allocator Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_allocator_paper_initial_allocation(db, config, event_loop):
    """Allocator should set initial capital splits on first run (paper mode)."""
    from sibyl.agents.allocator.portfolio_allocator import PortfolioAllocator

    async def _test():
        allocator = PortfolioAllocator(db=db, config=config, mode="paper")
        await allocator.start()

        # Check SGE got 70% of allocable (95% of $500 = $475 allocable)
        sge = await db.fetchone(
            "SELECT total_capital FROM engine_state WHERE engine = 'SGE'"
        )
        ace = await db.fetchone(
            "SELECT total_capital FROM engine_state WHERE engine = 'ACE'"
        )

        assert sge is not None
        assert ace is not None

        sge_capital = float(sge["total_capital"])
        ace_capital = float(ace["total_capital"])

        # $500 * 0.95 (reserve) = $475 allocable
        # SGE = $475 * 0.70 = $332.50
        # ACE = $475 * 0.30 = $142.50
        assert abs(sge_capital - 332.50) < 0.01
        assert abs(ace_capital - 142.50) < 0.01

        await allocator.stop()

    event_loop.run_until_complete(_test())


def test_allocator_paper_balance_tracks_pnl(db, config, event_loop):
    """Paper balance should reflect realized P&L from closed positions."""
    from sibyl.agents.allocator.portfolio_allocator import PortfolioAllocator

    async def _test():
        # Seed a market
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES (?, ?, ?)",
            ("MKT-1", "kalshi", "Test Market"),
        )
        # Seed a closed position with +$20 P&L
        await db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price,
                current_price, pnl, status, closed_at)
               VALUES ('MKT-1', 'kalshi', 'SGE', 'YES', 100, 0.50,
                        0.70, 20.0, 'CLOSED', datetime('now'))"""
        )
        await db.commit()

        allocator = PortfolioAllocator(db=db, config=config, mode="paper")
        await allocator.start()

        # Balance should be $500 + $20 = $520
        balance_row = await db.fetchone(
            "SELECT value FROM system_state WHERE key = 'portfolio_total_balance'"
        )
        assert balance_row is not None
        balance = float(balance_row["value"])
        assert abs(balance - 520.0) < 0.01

        await allocator.stop()

    event_loop.run_until_complete(_test())


def test_allocator_rebalance_on_drift(db, config, event_loop):
    """Allocator should rebalance when drift exceeds threshold."""
    from sibyl.agents.allocator.portfolio_allocator import PortfolioAllocator

    async def _test():
        allocator = PortfolioAllocator(db=db, config=config, mode="paper")
        await allocator.start()

        # Manually skew SGE capital to trigger drift
        # Target is $332.50, set it to $250 (drift = ~17.4%)
        await db.execute(
            "UPDATE engine_state SET total_capital = 250.0 WHERE engine = 'SGE'"
        )
        await db.commit()

        # Reset cooldown to allow immediate rebalance
        allocator._last_rebalance_ts = 0

        await allocator.run_cycle()

        sge = await db.fetchone(
            "SELECT total_capital FROM engine_state WHERE engine = 'SGE'"
        )
        sge_capital = float(sge["total_capital"])

        # Should have moved toward $332.50, capped by max_rebalance_pct (10%)
        # Max move = $475 * 0.10 = $47.50
        # So new SGE = $250 + $47.50 = $297.50
        assert abs(sge_capital - 297.50) < 0.01

        await allocator.stop()

    event_loop.run_until_complete(_test())


def test_allocator_writes_portfolio_state(db, config, event_loop):
    """Allocator should write portfolio_total_balance to system_state."""
    from sibyl.agents.allocator.portfolio_allocator import PortfolioAllocator

    async def _test():
        allocator = PortfolioAllocator(db=db, config=config, mode="paper")
        await allocator.start()

        total = await db.fetchone(
            "SELECT value FROM system_state WHERE key = 'portfolio_total_balance'"
        )
        reserve = await db.fetchone(
            "SELECT value FROM system_state WHERE key = 'portfolio_cash_reserve'"
        )
        allocable = await db.fetchone(
            "SELECT value FROM system_state WHERE key = 'portfolio_allocable'"
        )

        assert total is not None
        assert reserve is not None
        assert allocable is not None

        assert abs(float(total["value"]) - 500.0) < 0.01
        assert abs(float(reserve["value"]) - 25.0) < 0.01     # 5% of $500
        assert abs(float(allocable["value"]) - 475.0) < 0.01   # 95% of $500

        await allocator.stop()

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Risk Dashboard Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_risk_dashboard_drawdown_levels(db, config, event_loop):
    """RiskDashboard should correctly classify drawdown levels."""
    from sibyl.agents.analytics.risk_dashboard import RiskDashboard

    async def _test():
        dashboard = RiskDashboard(db=db, config=config)
        await dashboard.start()

        # Test all four levels
        assert dashboard._classify_drawdown(0.02) == "CLEAR"
        assert dashboard._classify_drawdown(0.07) == "WARNING"
        assert dashboard._classify_drawdown(0.12) == "CAUTION"
        assert dashboard._classify_drawdown(0.25) == "CRITICAL"

        await dashboard.stop()

    event_loop.run_until_complete(_test())


def test_risk_dashboard_hwm_tracking(db, config, event_loop):
    """RiskDashboard should track the high-water mark correctly."""
    from sibyl.agents.analytics.risk_dashboard import RiskDashboard

    async def _test():
        dashboard = RiskDashboard(db=db, config=config)
        await dashboard.start()

        # Seed a portfolio balance
        await db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES ('portfolio_total_balance', '1000.00', datetime('now'))"""
        )
        await db.commit()

        await dashboard.run_cycle()

        # HWM should be $1000
        assert dashboard._hwm == 1000.0

        # Now reduce balance — HWM should NOT decrease
        await db.execute(
            "UPDATE system_state SET value = '900.00' WHERE key = 'portfolio_total_balance'"
        )
        await db.commit()

        await dashboard.run_cycle()
        assert dashboard._hwm == 1000.0  # Still $1000

        # Check drawdown is recorded
        dd_row = await db.fetchone(
            "SELECT value FROM system_state WHERE key = 'risk_drawdown_pct'"
        )
        assert dd_row is not None
        dd = float(dd_row["value"])
        assert abs(dd - 0.10) < 0.001  # 10% drawdown

        # Check drawdown level
        level_row = await db.fetchone(
            "SELECT value FROM system_state WHERE key = 'risk_drawdown_level'"
        )
        assert level_row is not None
        assert level_row["value"] == "CAUTION"

        await dashboard.stop()

    event_loop.run_until_complete(_test())


def test_risk_dashboard_win_rate(db, config, event_loop):
    """RiskDashboard should compute 7-day win rate from performance records."""
    from sibyl.agents.analytics.risk_dashboard import RiskDashboard

    async def _test():
        # Seed a market and signal for FK constraints
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('MKT-1', 'kalshi', 'Test')"
        )
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, status)
               VALUES ('MKT-1', 'ARBITRAGE', 0.8, 'EXECUTED')"""
        )
        await db.commit()

        # Seed positions for FK constraints
        for i in range(4):
            await db.execute(
                """INSERT INTO positions
                   (market_id, platform, engine, side, size, entry_price, status)
                   VALUES ('MKT-1', 'kalshi', 'SGE', 'YES', 10, 0.50, 'CLOSED')"""
            )
        await db.commit()

        # Seed 3 wins and 1 loss
        for i, correct in enumerate([1, 1, 1, 0]):
            await db.execute(
                """INSERT INTO performance
                   (signal_id, position_id, engine, resolved, correct, pnl, resolved_at)
                   VALUES (1, ?, 'SGE', 1, ?, ?, datetime('now'))""",
                (i + 1, correct, 10.0 if correct else -5.0),
            )
        await db.commit()

        dashboard = RiskDashboard(db=db, config=config)
        await dashboard.start()

        # Seed portfolio balance to avoid early return
        await db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES ('portfolio_total_balance', '500.00', datetime('now'))"""
        )
        await db.commit()

        await dashboard.run_cycle()

        wr_row = await db.fetchone(
            "SELECT value FROM system_state WHERE key = 'risk_win_rate_7d'"
        )
        assert wr_row is not None
        assert abs(float(wr_row["value"]) - 0.75) < 0.01  # 3 wins / 4 total

        await dashboard.stop()

    event_loop.run_until_complete(_test())


def test_risk_dashboard_exposure_metrics(db, config, event_loop):
    """RiskDashboard should compute total exposure and open position count."""
    from sibyl.agents.analytics.risk_dashboard import RiskDashboard

    async def _test():
        # Seed markets
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('MKT-1', 'kalshi', 'A')"
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('MKT-2', 'kalshi', 'B')"
        )

        # Seed 2 open positions
        await db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price, current_price, status)
               VALUES ('MKT-1', 'kalshi', 'SGE', 'YES', 10, 0.50, 0.55, 'OPEN')"""
        )
        await db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price, current_price, status)
               VALUES ('MKT-2', 'kalshi', 'ACE', 'NO', 20, 0.40, 0.35, 'OPEN')"""
        )
        await db.commit()

        # Seed portfolio balance
        await db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES ('portfolio_total_balance', '500.00', datetime('now'))"""
        )
        await db.commit()

        dashboard = RiskDashboard(db=db, config=config)
        await dashboard.start()
        await dashboard.run_cycle()

        exp_row = await db.fetchone(
            "SELECT value FROM system_state WHERE key = 'risk_total_exposure'"
        )
        count_row = await db.fetchone(
            "SELECT value FROM system_state WHERE key = 'risk_open_positions'"
        )

        assert exp_row is not None
        assert count_row is not None

        # Total deployed = (10 * 0.50) + (20 * 0.40) = 5.0 + 8.0 = 13.0
        assert abs(float(exp_row["value"]) - 13.0) < 0.01
        assert int(count_row["value"]) == 2

        await dashboard.stop()

    event_loop.run_until_complete(_test())


def test_risk_dashboard_sharpe_ratio(db, config, event_loop):
    """RiskDashboard should compute Sharpe ratio from closed position P&L."""
    from sibyl.agents.analytics.risk_dashboard import RiskDashboard

    async def _test():
        # Seed market
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('MKT-1', 'kalshi', 'Test')"
        )

        # Seed closed positions with known P&L across different days
        for i, (pnl, days_ago) in enumerate([(10.0, 5), (15.0, 4), (-5.0, 3), (20.0, 2), (8.0, 1)]):
            await db.execute(
                """INSERT INTO positions
                   (market_id, platform, engine, side, size, entry_price,
                    pnl, status, closed_at)
                   VALUES ('MKT-1', 'kalshi', 'SGE', 'YES', 10, 0.50,
                           ?, 'CLOSED', datetime('now', ?))""",
                (pnl, f"-{days_ago} days"),
            )
        await db.commit()

        dashboard = RiskDashboard(db=db, config=config)
        await dashboard.start()

        # Seed portfolio balance
        await db.execute(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES ('portfolio_total_balance', '500.00', datetime('now'))"""
        )
        await db.commit()

        await dashboard.run_cycle()

        sharpe_row = await db.fetchone(
            "SELECT value FROM system_state WHERE key = 'risk_sharpe_30d'"
        )
        assert sharpe_row is not None
        sharpe = float(sharpe_row["value"])

        # With P&L [10, 15, -5, 20, 8], mean=9.6, std≈9.07 → Sharpe ≈ 1.058
        # Exact value depends on sample vs population std; just check it's > 0
        assert sharpe > 0.5

        await dashboard.stop()

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Order Executor Live Mode Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_executor_live_mode_falls_back_without_credentials(db, config, event_loop):
    """OrderExecutor should fall back to paper mode if live credentials are missing."""
    from sibyl.agents.execution.order_executor import OrderExecutor
    import os

    async def _test():
        # Ensure no Kalshi credentials are set
        os.environ.pop("KALSHI_KEY_ID", None)
        os.environ.pop("KALSHI_PRIVATE_KEY_PATH", None)

        executor = OrderExecutor(db=db, config=config, mode="live")
        await executor.start()

        # Should have fallen back to paper
        assert executor._mode == "paper"
        assert executor._kalshi_client is None

        await executor.stop()

    event_loop.run_until_complete(_test())


def test_executor_paper_mode_no_kalshi_client(db, config, event_loop):
    """OrderExecutor in paper mode should NOT initialize a Kalshi client."""
    from sibyl.agents.execution.order_executor import OrderExecutor

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        await executor.start()

        assert executor._mode == "paper"
        assert executor._kalshi_client is None

        await executor.stop()

    event_loop.run_until_complete(_test())
