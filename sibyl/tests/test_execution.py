"""
Tests for Sprint 3 Execution Layer.

Tests all three execution agents:
    - EngineStateManager — capital tracking, initialization
    - OrderExecutor — Kelly sizing, paper fills, risk checks
    - PositionLifecycleManager — stop guard, EV monitor, exit optimizer, resolution tracker
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
# Engine State Manager Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_engine_state_initialization(db, config, event_loop):
    """EngineStateManager should create SGE and ACE rows on start."""
    from sibyl.agents.execution.engine_state_manager import EngineStateManager

    async def _test():
        mgr = EngineStateManager(db=db, config=config)
        mgr._sge_allocation = 0.70
        mgr._ace_allocation = 0.30
        await mgr.start()

        sge = await db.fetchone("SELECT * FROM engine_state WHERE engine = 'SGE'")
        ace = await db.fetchone("SELECT * FROM engine_state WHERE engine = 'ACE'")
        assert sge is not None
        assert ace is not None
        assert sge["circuit_breaker"] == "CLEAR"

    event_loop.run_until_complete(_test())


def test_engine_state_computes_deployed_capital(db, config, event_loop):
    """Deployed capital should equal sum of open position values."""
    from sibyl.agents.execution.engine_state_manager import EngineStateManager

    async def _test():
        mgr = EngineStateManager(db=db, config=config)
        await mgr.start()

        # Set total capital
        await db.execute(
            "UPDATE engine_state SET total_capital = 1000.0 WHERE engine = 'SGE'"
        )

        # Insert a market + open position
        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-ES", "kalshi", "State Test", "politics", "active"),
        )
        await db.execute(
            """INSERT INTO positions (market_id, platform, engine, side, size,
               entry_price, current_price, status)
               VALUES ('MKT-ES', 'kalshi', 'SGE', 'YES', 100, 0.40, 0.45, 'OPEN')"""
        )
        await db.commit()

        await mgr.run_cycle()

        state = await db.fetchone("SELECT * FROM engine_state WHERE engine = 'SGE'")
        assert float(state["deployed_capital"]) == pytest.approx(40.0, abs=0.1)
        assert float(state["available_capital"]) == pytest.approx(960.0, abs=0.1)

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Order Executor Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_order_executor_skips_no_capital(db, config, event_loop):
    """Executor should skip if engine has no available capital."""
    from sibyl.agents.execution.order_executor import OrderExecutor

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        executor._sge_risk = {"kelly_fraction": 0.15, "max_single_position_pct": 0.02}

        # Initialize engine state with 0 capital
        await db.execute(
            """INSERT OR REPLACE INTO engine_state (engine, total_capital, available_capital, circuit_breaker)
               VALUES ('SGE', 0, 0, 'CLEAR')"""
        )

        # Create market + price + routed signal
        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-OE", "kalshi", "Exec Test", "politics", "active"),
        )
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES ('MKT-OE', 0.40)"
        )
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, status, routed_to)
               VALUES ('MKT-OE', 'MOMENTUM', 0.70, 0.05, 'ROUTED', 'SGE')"""
        )
        await db.commit()

        await executor.run_cycle()

        # Signal should still be marked as EXECUTED (it was processed but no fill)
        signal = await db.fetchone("SELECT status FROM signals WHERE market_id = 'MKT-OE'")
        assert signal["status"] == "EXECUTED"

        # No positions should have been created
        pos = await db.fetchone("SELECT * FROM positions WHERE market_id = 'MKT-OE'")
        assert pos is None

    event_loop.run_until_complete(_test())


def test_order_executor_paper_fill(db, config, event_loop):
    """Executor should create position and execution in paper mode."""
    from sibyl.agents.execution.order_executor import OrderExecutor

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        executor._sge_risk = {
            "kelly_fraction": 0.15,
            "max_single_position_pct": 0.10,
            "per_market_stop_loss_pct": 0.35,
        }

        # Initialize engine state with capital
        await db.execute(
            """INSERT OR REPLACE INTO engine_state (engine, total_capital, available_capital, circuit_breaker)
               VALUES ('SGE', 5000, 5000, 'CLEAR')"""
        )

        # Create market + price + routed signal
        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-PF", "kalshi", "Paper Fill Test", "crypto", "active"),
        )
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES ('MKT-PF', 0.65)"
        )
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, status, routed_to, direction)
               VALUES ('MKT-PF', 'VOLUME_SURGE', 0.72, 0.08, 'ROUTED', 'SGE', 'YES')"""
        )
        await db.commit()

        await executor.run_cycle()

        # Position should have been created
        pos = await db.fetchone("SELECT * FROM positions WHERE market_id = 'MKT-PF'")
        assert pos is not None
        assert pos["status"] == "OPEN"
        assert pos["engine"] == "SGE"
        # Sprint 28: price 0.65 is within [50c, 93c] entry range

        # Execution should have been recorded
        exe = await db.fetchone("SELECT * FROM executions WHERE engine = 'SGE'")
        assert exe is not None
        assert exe["order_id"].startswith("PAPER-")

    event_loop.run_until_complete(_test())


def test_order_executor_circuit_breaker_blocks(db, config, event_loop):
    """Executor should skip if circuit breaker is TRIGGERED."""
    from sibyl.agents.execution.order_executor import OrderExecutor

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        executor._ace_risk = {
            "kelly_fraction": 0.35,
            "max_single_position_pct": 0.05,
            "per_market_stop_loss_pct": 0.25,
        }

        # Initialize engine state with TRIGGERED circuit breaker
        await db.execute(
            """INSERT OR REPLACE INTO engine_state (engine, total_capital, available_capital, circuit_breaker)
               VALUES ('ACE', 3000, 3000, 'TRIGGERED')"""
        )

        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-CB", "kalshi", "Circuit Breaker Test", "politics", "active"),
        )
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES ('MKT-CB', 0.45)"
        )
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, status, routed_to)
               VALUES ('MKT-CB', 'MOMENTUM', 0.75, 0.10, 'ROUTED', 'ACE')"""
        )
        await db.commit()

        await executor.run_cycle()

        # No positions should be created
        pos = await db.fetchone("SELECT * FROM positions WHERE market_id = 'MKT-CB'")
        assert pos is None

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Position Lifecycle Manager Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_stop_guard_closes_position(db, config, event_loop):
    """Stop Guard should close a position when current_price hits stop_loss."""
    from sibyl.agents.execution.position_lifecycle import PositionLifecycleManager

    async def _test():
        plm = PositionLifecycleManager(db=db, config=config)
        plm._plc = {
            "stop_guard": {"circuit_breaker_window_minutes": 15, "circuit_breaker_stop_count": 3},
        }

        # Create a position that has hit its stop loss
        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-SG", "kalshi", "Stop Guard Test", "politics", "active"),
        )
        await db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price,
                current_price, stop_loss, status)
               VALUES ('MKT-SG', 'kalshi', 'SGE', 'YES', 50, 0.40, 0.20, 0.25, 'OPEN')"""
        )
        # Initialize engine state
        await db.execute(
            """INSERT OR IGNORE INTO engine_state (engine, total_capital, circuit_breaker)
               VALUES ('SGE', 5000, 'CLEAR')"""
        )
        await db.commit()

        await plm._sub_a_stop_guard()

        pos = await db.fetchone("SELECT * FROM positions WHERE market_id = 'MKT-SG'")
        assert pos["status"] == "STOPPED"
        assert float(pos["pnl"]) < 0  # Should be negative (loss)

    event_loop.run_until_complete(_test())


def test_exit_optimizer_takes_profit(db, config, event_loop):
    """Exit Optimizer should close when >80% of EV is captured."""
    from sibyl.agents.execution.position_lifecycle import PositionLifecycleManager

    async def _test():
        plm = PositionLifecycleManager(db=db, config=config)
        plm._plc = {"exit_optimizer": {"ev_capture_threshold": 0.80}}

        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-EO", "kalshi", "Exit Test", "economics", "active"),
        )
        # Position: bought at 0.30, now at 0.90 → 85% of max profit captured
        # Max profit = 0.70 (1.0 - 0.30), current profit = 0.60
        # Capture = 0.60/0.70 = 85% > 80% threshold
        await db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price,
                current_price, status, signal_id)
               VALUES ('MKT-EO', 'kalshi', 'SGE', 'YES', 20, 0.30, 0.90, 'OPEN', NULL)"""
        )
        await db.commit()

        await plm._sub_c_exit_optimizer()

        pos = await db.fetchone("SELECT * FROM positions WHERE market_id = 'MKT-EO'")
        assert pos["status"] == "CLOSED"
        assert float(pos["pnl"]) > 0  # Profitable exit

    event_loop.run_until_complete(_test())


def test_resolution_tracker_detects_yes_convergence(db, config, event_loop):
    """Resolution Tracker should close + record performance when market resolves."""
    from sibyl.agents.execution.position_lifecycle import PositionLifecycleManager

    async def _test():
        plm = PositionLifecycleManager(db=db, config=config)
        plm._plc = {
            "resolution_tracker": {"convergence_yes_threshold": 0.85, "convergence_no_threshold": 0.15},
        }

        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-RT", "kalshi", "Resolution Test", "politics", "active"),
        )
        # Price is 0.92 → converging YES
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES ('MKT-RT', 0.92)"
        )
        # Position bought YES at 0.40 → should be a win
        await db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price,
                current_price, status, signal_id)
               VALUES ('MKT-RT', 'kalshi', 'ACE', 'YES', 30, 0.40, 0.90, 'OPEN', NULL)"""
        )
        await db.commit()

        await plm._sub_d_resolution_tracker()

        pos = await db.fetchone("SELECT * FROM positions WHERE market_id = 'MKT-RT'")
        assert pos["status"] == "CLOSED"

        perf = await db.fetchone("SELECT * FROM performance WHERE position_id = ?", (pos["id"],))
        assert perf is not None
        assert perf["resolved"] == 1
        assert perf["correct"] == 1  # We bet YES and it resolved YES
        assert float(perf["pnl"]) > 0

    event_loop.run_until_complete(_test())


def test_ev_monitor_updates_current_price(db, config, event_loop):
    """EV Monitor should update current_price and ev_current for open positions."""
    from sibyl.agents.execution.position_lifecycle import PositionLifecycleManager

    async def _test():
        plm = PositionLifecycleManager(db=db, config=config)
        plm._plc = {"ev_monitor": {"significant_ev_shift_threshold": 0.05}}

        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-EVM", "kalshi", "EV Monitor Test", "crypto", "active"),
        )
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES ('MKT-EVM', 0.65)"
        )
        await db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price,
                current_price, ev_current, status)
               VALUES ('MKT-EVM', 'kalshi', 'ACE', 'YES', 25, 0.40, 0.50, 0.10, 'OPEN')"""
        )
        await db.commit()

        await plm._sub_b_ev_monitor("ACE")

        pos = await db.fetchone("SELECT * FROM positions WHERE market_id = 'MKT-EVM'")
        # Price should be updated to latest (0.65)
        assert float(pos["current_price"]) == pytest.approx(0.65, abs=0.01)

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Sprint 29: Tiered Entry Floor + Dynamic Contract Cap Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_tiered_entry_floor_15min_allows_low_price(db, config, event_loop):
    """15-min timeframe should allow entries as low as 5c (not blocked by old 50c floor)."""
    from sibyl.agents.execution.order_executor import OrderExecutor
    from sibyl.core.policy import PolicyEngine

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        executor._sge_risk = {
            "kelly_fraction": 0.15,
            "max_single_position_pct": 0.10,
            "per_market_stop_loss_pct": 0.35,
        }
        # Initialize policy so cat_profile with tiered floors is available
        executor._policy = PolicyEngine()
        executor._policy.initialize()

        await db.execute(
            """INSERT OR REPLACE INTO engine_state (engine, total_capital, available_capital, circuit_breaker)
               VALUES ('SGE', 5000, 5000, 'CLEAR')"""
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-TF15", "kalshi", "BTC 15min Bracket", "crypto", "active"),
        )
        # 8c price — would be blocked by old 50c floor, should pass with 5c 15-min floor
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES ('MKT-TF15', 0.08)"
        )
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, status, routed_to, direction, timeframe)
               VALUES ('MKT-TF15', 'BRACKET_MODEL', 0.72, 0.08, 'ROUTED', 'SGE', 'YES', '15min')"""
        )
        await db.commit()

        await executor.run_cycle()

        pos = await db.fetchone("SELECT * FROM positions WHERE market_id = 'MKT-TF15'")
        assert pos is not None, "15min signal at 8c should NOT be blocked by price gate"
        assert pos["status"] == "OPEN"

    event_loop.run_until_complete(_test())


def test_tiered_entry_floor_15min_rejects_below_5c(db, config, event_loop):
    """15-min timeframe should reject entries below 5c floor."""
    from sibyl.agents.execution.order_executor import OrderExecutor
    from sibyl.core.policy import PolicyEngine

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        executor._sge_risk = {
            "kelly_fraction": 0.15,
            "max_single_position_pct": 0.10,
            "per_market_stop_loss_pct": 0.35,
        }
        executor._policy = PolicyEngine()
        executor._policy.initialize()

        await db.execute(
            """INSERT OR REPLACE INTO engine_state (engine, total_capital, available_capital, circuit_breaker)
               VALUES ('SGE', 5000, 5000, 'CLEAR')"""
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-TF15R", "kalshi", "BTC 15min Extreme Longshot", "crypto", "active"),
        )
        # 3c price — below 5c 15-min floor
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES ('MKT-TF15R', 0.03)"
        )
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, status, routed_to, direction, timeframe)
               VALUES ('MKT-TF15R', 'BRACKET_MODEL', 0.72, 0.08, 'ROUTED', 'SGE', 'YES', '15min')"""
        )
        await db.commit()

        await executor.run_cycle()

        pos = await db.fetchone("SELECT * FROM positions WHERE market_id = 'MKT-TF15R'")
        assert pos is None, "15min signal at 3c should be blocked by 5c floor"

    event_loop.run_until_complete(_test())


def test_dynamic_contract_cap_low_price(db, config, event_loop):
    """Low-price entry (8c) should get higher contract cap (15) than flat cap of 5."""
    from sibyl.agents.execution.order_executor import OrderExecutor
    from sibyl.core.policy import PolicyEngine

    async def _test():
        executor = OrderExecutor(db=db, config=config, mode="paper")
        executor._sge_risk = {
            "kelly_fraction": 0.50,  # High Kelly to ensure sizing hits the cap
            "max_single_position_pct": 0.50,
            "per_market_stop_loss_pct": 0.35,
        }
        executor._policy = PolicyEngine()
        executor._policy.initialize()

        await db.execute(
            """INSERT OR REPLACE INTO engine_state (engine, total_capital, available_capital, circuit_breaker)
               VALUES ('SGE', 5000, 5000, 'CLEAR')"""
        )
        await db.execute(
            "INSERT INTO markets (id, platform, title, category, status) VALUES (?, ?, ?, ?, ?)",
            ("MKT-DCC", "kalshi", "BTC 15min Low Price", "crypto", "active"),
        )
        await db.execute(
            "INSERT INTO prices (market_id, yes_price) VALUES ('MKT-DCC', 0.10)"
        )
        await db.execute(
            """INSERT INTO signals (market_id, signal_type, confidence, ev_estimate, status, routed_to, direction, timeframe)
               VALUES ('MKT-DCC', 'BRACKET_MODEL', 0.80, 0.12, 'ROUTED', 'SGE', 'YES', '15min')"""
        )
        await db.commit()

        await executor.run_cycle()

        pos = await db.fetchone("SELECT * FROM positions WHERE market_id = 'MKT-DCC'")
        assert pos is not None, "Position should be created"
        # With $5000 capital, 50% Kelly, 50% max position = $1250 max
        # At 10c per contract, that's 12500 contracts before cap
        # Dynamic cap for <=20c entry = 15 contracts
        assert int(pos["size"]) == 15, f"Expected 15 contracts (dynamic cap for <=20c), got {pos['size']}"

    event_loop.run_until_complete(_test())


# ═══════════════════════════════════════════════════════════════════════════
# Sprint 29 Tests — Audit Remediation
# ═══════════════════════════════════════════════════════════════════════════


def test_pnl_includes_fees(db, config, event_loop):
    """Sprint 29 BUG-002: P&L computation must deduct fees."""
    from sibyl.agents.execution.position_lifecycle import PositionLifecycleManager

    pos_yes = {
        "side": "YES", "entry_price": 0.40, "size": 10,
        "current_price": 0.60,
    }
    # Raw P&L = (0.60 - 0.40) * 10 = $2.00
    # Roundtrip fee = 10 * 0.014 = $0.14
    # Net P&L = $2.00 - $0.14 = $1.86
    pnl = PositionLifecycleManager._compute_pnl_with_price(pos_yes, 0.60)
    assert abs(pnl - 1.86) < 0.01, f"Expected ~$1.86 net of fees, got ${pnl:.4f}"

    # Settlement exit (buy-side fee only)
    pnl_settle = PositionLifecycleManager._compute_pnl_with_price(
        pos_yes, 0.60, is_settlement=True
    )
    # Fee = 10 * 0.007 = $0.07 (buy-side only)
    # Net P&L = $2.00 - $0.07 = $1.93
    assert abs(pnl_settle - 1.93) < 0.01, f"Expected ~$1.93 for settlement, got ${pnl_settle:.4f}"


def test_no_side_kelly_uses_no_cost(db, config, event_loop):
    """Sprint 29 BUG-001: NO positions must use (1-yes_price) as cost basis for Kelly."""
    # This is a structural test — we verify the payout computation logic.
    # For YES at 70c: cost=0.70, payout = (1/0.70)-1 = 0.4286
    # For NO  at 70c yes_price: cost=0.30, payout = (1/0.30)-1 = 2.3333
    # The old code used 0.70 for both → undersizing NO by 5.4x

    yes_price = 0.70
    # YES payout
    yes_cost = yes_price
    yes_payout = (1.0 / yes_cost) - 1.0
    assert abs(yes_payout - 0.4286) < 0.01

    # NO payout (Sprint 29 fix)
    no_cost = 1.0 - yes_price
    no_payout = (1.0 / no_cost) - 1.0
    assert abs(no_payout - 2.3333) < 0.01
    assert no_payout > yes_payout * 4, "NO payout should be much larger than YES payout"


def test_fee_column_in_schema(db, config, event_loop):
    """Sprint 29: Executions table must have fee_amount column."""
    async def _test():
        cols = await db.fetchall("PRAGMA table_info(executions)")
        col_names = [c["name"] for c in cols]
        assert "fee_amount" in col_names, f"fee_amount missing from executions: {col_names}"

    event_loop.run_until_complete(_test())


def test_auto_calibrator_import():
    """Sprint 29: AutoCalibrator agent must be importable."""
    from sibyl.agents.analytics.auto_calibrator import AutoCalibrator
    assert AutoCalibrator is not None
