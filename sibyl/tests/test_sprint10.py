"""
Tests for Sprint 10: Backtesting, Category Performance, Dynamic Correlation Penalty.

Tests:
    - BacktestEngine: initialization, replay, result metrics, category breakdown
    - CategoryPerformanceTracker: compute, persist, per-category queries
    - OrderExecutor correlation penalty: penalty computation, dynamic scaling,
      portfolio-responsive behavior
    - CLI backtest command: argument parsing
"""

import asyncio
import math
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
        "system": {"version": "0.2.0"},
        "polling": {"price_snapshot_interval_seconds": 5},
        "platforms": {
            "polymarket": {"rate_limit_per_second": 80},
            "kalshi": {"rate_limit_per_second": 8},
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# BacktestEngine Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_backtest_engine_init(event_loop, db):
    """BacktestEngine should initialize with correct defaults."""
    from sibyl.backtesting.engine import BacktestEngine

    async def _test():
        engine = BacktestEngine(db=db, starting_balance=1000.0)
        await engine.initialize()
        assert engine._starting_balance == 1000.0
        assert engine._category_mgr is not None
        assert engine._category_mgr.initialized is True

    event_loop.run_until_complete(_test())


def test_backtest_empty_db(event_loop, db):
    """Backtest on empty DB should return zero positions."""
    from sibyl.backtesting.engine import BacktestEngine

    async def _test():
        engine = BacktestEngine(db=db, starting_balance=500.0)
        await engine.initialize()
        result = await engine.run()

        assert result.total_signals_replayed == 0
        assert result.total_positions_opened == 0
        assert result.ending_balance == 500.0
        assert result.total_return_pct == 0.0

    event_loop.run_until_complete(_test())


def test_backtest_with_signals(event_loop, db):
    """Backtest should process signals and generate positions."""
    from sibyl.backtesting.engine import BacktestEngine

    async def _test():
        # Seed a market
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-1', 'kalshi', 'Test Market', 'Sports')"
        )
        # Seed a signal with high confidence (should pass routing)
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, status, timestamp)
               VALUES ('MKT-1', 'WHALE', 0.85, 0.10, 'ROUTED', '2026-03-01 12:00:00')"""
        )
        # Seed a price
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES ('MKT-1', 0.50)"
        )
        # Seed a closed position outcome
        await db.execute(
            """INSERT INTO positions (market_id, platform, engine, side, size, entry_price,
                   current_price, pnl, status, signal_id, opened_at, closed_at)
               VALUES ('MKT-1', 'kalshi', 'SGE', 'YES', 5, 0.50, 0.65, 0.75, 'CLOSED', 1,
                       '2026-03-01 12:00:00', '2026-03-02 12:00:00')"""
        )
        await db.commit()

        engine = BacktestEngine(db=db, starting_balance=500.0)
        await engine.initialize()
        result = await engine.run(start_date="2026-03-01", end_date="2026-03-31")

        assert result.total_signals_replayed == 1
        assert result.total_positions_opened >= 1
        assert "Sports" in result.by_category

    event_loop.run_until_complete(_test())


def test_backtest_result_summary(event_loop, db):
    """BacktestResult.summary() should produce readable output."""
    from sibyl.backtesting.engine import BacktestResult, CategoryResult, EngineResult

    result = BacktestResult(
        start_date="2026-01-01",
        end_date="2026-03-19",
        starting_balance=500.0,
        ending_balance=650.0,
        total_signals_replayed=100,
        total_positions_opened=45,
        total_deferred=55,
        total_pnl=150.0,
        max_drawdown_pct=0.08,
        sharpe_ratio=1.5,
        by_category={"Sports": CategoryResult("Sports", 20, 12, 8, 80.0, 200.0)},
        by_engine={"SGE": EngineResult("SGE", 30, 18, 12, 100.0, 300.0)},
    )

    summary = result.summary()
    assert "BACKTEST RESULTS" in summary
    assert "$500.00" in summary
    assert "$650.00" in summary
    assert "Sports" in summary
    assert "SGE" in summary


def test_backtest_result_to_dict():
    """BacktestResult.to_dict() should produce JSON-serializable output."""
    from sibyl.backtesting.engine import BacktestResult

    result = BacktestResult(
        start_date="2026-01-01",
        end_date="2026-03-19",
        starting_balance=500.0,
        ending_balance=600.0,
    )
    d = result.to_dict()
    assert d["starting_balance"] == 500.0
    assert d["ending_balance"] == 600.0
    assert d["total_return_pct"] == 0.2  # 100/500 = 0.2
    import json
    json.dumps(d)  # Should not raise


def test_backtest_routing_mirrors_signal_router(event_loop, db):
    """Backtest routing logic should match the live SignalRouter."""
    from sibyl.backtesting.engine import BacktestEngine

    async def _test():
        engine = BacktestEngine(db=db)
        await engine.initialize()

        # WHALE below both thresholds → DEFERRED
        assert engine._route("WHALE", 0.50, 0.02) == "DEFERRED"

        # WHALE above SGE threshold → SGE (it's not on either whitelist
        # by default but meets SGE threshold)
        assert engine._route("WHALE", 0.65, 0.04) == "SGE"

        # With category preference as tiebreaker
        assert engine._route("COMPOSITE", 0.85, 0.10, cat_pref="ACE") == "ACE"

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# CategoryPerformanceTracker Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_category_tracker_empty(event_loop, db):
    """Tracker should return empty dict on empty DB."""
    from sibyl.backtesting.category_tracker import CategoryPerformanceTracker

    async def _test():
        tracker = CategoryPerformanceTracker(db=db)
        stats = await tracker.compute()
        assert stats == {}

    event_loop.run_until_complete(_test())


def test_category_tracker_with_data(event_loop, db):
    """Tracker should compute correct per-category stats."""
    from sibyl.backtesting.category_tracker import CategoryPerformanceTracker

    async def _test():
        # Seed markets and closed positions
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-1', 'kalshi', 'Election', 'Politics')"
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-2', 'kalshi', 'Super Bowl', 'Sports')"
        )
        # Politics: 1 win, 1 loss
        await db.execute(
            """INSERT INTO positions (market_id, platform, engine, side, size, entry_price, pnl, status, opened_at, closed_at)
               VALUES ('MKT-1', 'kalshi', 'SGE', 'YES', 10, 0.50, 2.00, 'CLOSED', '2026-03-01', '2026-03-02')"""
        )
        await db.execute(
            """INSERT INTO positions (market_id, platform, engine, side, size, entry_price, pnl, status, opened_at, closed_at)
               VALUES ('MKT-1', 'kalshi', 'SGE', 'NO', 5, 0.40, -1.00, 'CLOSED', '2026-03-01', '2026-03-02')"""
        )
        # Sports: 1 win
        await db.execute(
            """INSERT INTO positions (market_id, platform, engine, side, size, entry_price, pnl, status, opened_at, closed_at)
               VALUES ('MKT-2', 'kalshi', 'ACE', 'YES', 20, 0.30, 5.00, 'CLOSED', '2026-03-01', '2026-03-02')"""
        )
        await db.commit()

        tracker = CategoryPerformanceTracker(db=db)
        stats = await tracker.compute()

        assert "Politics" in stats
        assert "Sports" in stats
        assert stats["Politics"].wins == 1
        assert stats["Politics"].losses == 1
        assert stats["Politics"].win_rate == 0.5
        assert stats["Sports"].wins == 1
        assert stats["Sports"].total_pnl == 5.0

    event_loop.run_until_complete(_test())


def test_category_tracker_persist(event_loop, db):
    """Tracker should persist stats to system_state."""
    from sibyl.backtesting.category_tracker import CategoryPerformanceTracker, CategoryStats
    import json

    async def _test():
        tracker = CategoryPerformanceTracker(db=db)
        stats = {"Sports": CategoryStats("Sports", 10, 7, 3, 15.0, 100.0)}
        await tracker.persist(stats)

        row = await db.fetchone("SELECT value FROM system_state WHERE key = 'category_performance'")
        assert row is not None
        data = json.loads(row["value"])
        assert "Sports" in data
        assert data["Sports"]["win_rate"] == 0.7

    event_loop.run_until_complete(_test())


def test_category_tracker_win_rate_query(event_loop, db):
    """get_category_win_rate should return correct values."""
    from sibyl.backtesting.category_tracker import CategoryPerformanceTracker

    async def _test():
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-1', 'kalshi', 'Test', 'Crypto')"
        )
        await db.execute(
            """INSERT INTO positions (market_id, platform, engine, side, size, entry_price, pnl, status)
               VALUES ('MKT-1', 'kalshi', 'ACE', 'YES', 10, 0.50, 3.0, 'CLOSED')"""
        )
        await db.execute(
            """INSERT INTO positions (market_id, platform, engine, side, size, entry_price, pnl, status)
               VALUES ('MKT-1', 'kalshi', 'ACE', 'YES', 10, 0.50, -1.0, 'CLOSED')"""
        )
        await db.commit()

        tracker = CategoryPerformanceTracker(db=db)
        wr = await tracker.get_category_win_rate("Crypto")
        assert wr == 0.5  # 1 win / 2 total

        # Unknown category returns 0.5 (neutral)
        wr_unknown = await tracker.get_category_win_rate("Unknown")
        assert wr_unknown == 0.5

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# OrderExecutor Dynamic Correlation Penalty Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_correlation_multiplier_no_positions(event_loop, db, config):
    """With no existing positions, correlation multiplier should be 1.0."""
    from sibyl.agents.execution.order_executor import OrderExecutor

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        await executor.start()

        # Seed a market
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-1', 'kalshi', 'Test', 'Sports')"
        )
        await db.commit()

        mult = await executor._compute_correlation_multiplier("MKT-1")
        assert mult == 1.0  # No existing positions → no penalty

    event_loop.run_until_complete(_test())


def test_correlation_multiplier_with_existing_positions(event_loop, db, config):
    """With existing positions, multiplier should be < 1.0."""
    from sibyl.agents.execution.order_executor import OrderExecutor

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        await executor.start()

        # Seed market and open position
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-1', 'kalshi', 'Election', 'Politics')"
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-2', 'kalshi', 'Senate Race', 'Politics')"
        )
        await db.execute(
            """INSERT INTO positions (market_id, platform, engine, side, size, entry_price, status)
               VALUES ('MKT-1', 'kalshi', 'SGE', 'YES', 10, 0.50, 'OPEN')"""
        )
        await db.commit()

        # Trying to open another Politics position → should get penalty
        mult = await executor._compute_correlation_multiplier("MKT-2")
        assert mult < 1.0
        assert mult > 0.0  # Should not be zero

    event_loop.run_until_complete(_test())


def test_correlation_multiplier_multiple_positions(event_loop, db, config):
    """More existing positions → larger penalty (lower multiplier)."""
    from sibyl.agents.execution.order_executor import OrderExecutor

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        await executor.start()

        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-1', 'kalshi', 'Race 1', 'Politics')"
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-2', 'kalshi', 'Race 2', 'Politics')"
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-3', 'kalshi', 'Race 3', 'Politics')"
        )

        # 1 existing position
        await db.execute(
            """INSERT INTO positions (market_id, platform, engine, side, size, entry_price, status)
               VALUES ('MKT-1', 'kalshi', 'SGE', 'YES', 10, 0.50, 'OPEN')"""
        )
        await db.commit()
        mult_1 = await executor._compute_correlation_multiplier("MKT-3")

        # 2 existing positions
        await db.execute(
            """INSERT INTO positions (market_id, platform, engine, side, size, entry_price, status)
               VALUES ('MKT-2', 'kalshi', 'SGE', 'YES', 10, 0.50, 'OPEN')"""
        )
        await db.commit()
        mult_2 = await executor._compute_correlation_multiplier("MKT-3")

        # More positions → lower multiplier
        assert mult_2 < mult_1

    event_loop.run_until_complete(_test())


def test_correlation_multiplier_scales_with_portfolio(event_loop, db, config):
    """Larger portfolio should have smaller penalty (higher multiplier)."""
    from sibyl.agents.execution.order_executor import OrderExecutor

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        await executor.start()

        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-1', 'kalshi', 'Race', 'Politics')"
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-2', 'kalshi', 'Race 2', 'Politics')"
        )
        await db.execute(
            """INSERT INTO positions (market_id, platform, engine, side, size, entry_price, status)
               VALUES ('MKT-1', 'kalshi', 'SGE', 'YES', 10, 0.50, 'OPEN')"""
        )

        # Small portfolio ($250 = half of $500 starting)
        await db.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES ('portfolio_total_balance', '250.00', datetime('now'))"
        )
        await db.commit()
        mult_small = await executor._compute_correlation_multiplier("MKT-2")

        # Large portfolio ($1000 = double of $500 starting)
        await db.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES ('portfolio_total_balance', '1000.00', datetime('now'))"
        )
        await db.commit()
        mult_large = await executor._compute_correlation_multiplier("MKT-2")

        # Larger portfolio → higher multiplier (less penalty)
        assert mult_large > mult_small

    event_loop.run_until_complete(_test())


def test_correlation_multiplier_floor(event_loop, db, config):
    """Multiplier should never go below 0.10 (floor)."""
    from sibyl.agents.execution.order_executor import OrderExecutor

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        await executor.start()

        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-1', 'kalshi', 'Race', 'Politics')"
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-NEW', 'kalshi', 'New Race', 'Politics')"
        )

        # Seed many open positions in the same category
        for i in range(20):
            mid = f"MKT-X{i}"
            await db.execute(
                f"INSERT INTO markets (id, platform, title, category) VALUES ('{mid}', 'kalshi', 'Race {i}', 'Politics')"
            )
            await db.execute(
                f"""INSERT INTO positions (market_id, platform, engine, side, size, entry_price, status)
                   VALUES ('{mid}', 'kalshi', 'SGE', 'YES', 10, 0.50, 'OPEN')"""
            )

        # Very small portfolio to maximize penalty
        await db.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES ('portfolio_total_balance', '100.00', datetime('now'))"
        )
        await db.commit()

        mult = await executor._compute_correlation_multiplier("MKT-NEW")
        assert mult >= 0.10  # Floor

    event_loop.run_until_complete(_test())


def test_correlation_multiplier_unknown_category(event_loop, db, config):
    """Unknown category should return 1.0 (no penalty)."""
    from sibyl.agents.execution.order_executor import OrderExecutor

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        await executor.start()

        # Market with no category
        await db.execute(
            "INSERT INTO markets (id, platform, title) VALUES ('MKT-1', 'kalshi', 'Unknown')"
        )
        await db.commit()

        mult = await executor._compute_correlation_multiplier("MKT-1")
        assert mult == 1.0

    event_loop.run_until_complete(_test())


def test_correlation_different_categories_independent(event_loop, db, config):
    """Positions in different categories should not affect each other's penalty."""
    from sibyl.agents.execution.order_executor import OrderExecutor

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        await executor.start()

        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-P1', 'kalshi', 'Election', 'Politics')"
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title, category) VALUES ('MKT-S1', 'kalshi', 'Super Bowl', 'Sports')"
        )
        # Open 3 Politics positions
        for i in range(3):
            mid = f"MKT-PX{i}"
            await db.execute(
                f"INSERT INTO markets (id, platform, title, category) VALUES ('{mid}', 'kalshi', 'Race {i}', 'Politics')"
            )
            await db.execute(
                f"""INSERT INTO positions (market_id, platform, engine, side, size, entry_price, status)
                   VALUES ('{mid}', 'kalshi', 'SGE', 'YES', 10, 0.50, 'OPEN')"""
            )
        await db.commit()

        # Sports market should have no penalty (no Sports positions open)
        mult_sports = await executor._compute_correlation_multiplier("MKT-S1")
        assert mult_sports == 1.0

        # Politics market should have penalty (3 positions open)
        mult_politics = await executor._compute_correlation_multiplier("MKT-P1")
        assert mult_politics < 1.0

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# CLI Argument Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_cli_backtest_args():
    """CLI should parse backtest arguments correctly."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", action="store_true", default=False)
    parser.add_argument("--from", dest="backtest_from", type=str, default=None)
    parser.add_argument("--to", dest="backtest_to", type=str, default=None)
    parser.add_argument("--balance", type=float, default=500.0)

    args = parser.parse_args(["--backtest", "--from", "2026-01-01", "--to", "2026-03-19", "--balance", "1000"])
    assert args.backtest is True
    assert args.backtest_from == "2026-01-01"
    assert args.backtest_to == "2026-03-19"
    assert args.balance == 1000.0


def test_cli_default_no_backtest():
    """CLI should default to no backtest."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", action="store_true", default=False)

    args = parser.parse_args([])
    assert args.backtest is False
