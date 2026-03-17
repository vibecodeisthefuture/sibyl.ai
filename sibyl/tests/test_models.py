"""Tests for Pydantic data models."""

import pytest
from pydantic import ValidationError

from sibyl.models.market import Market, Price, OrderBookSnapshot, OrderLevel, Trade, Platform
from sibyl.models.signal import Signal, SignalType, EngineRouting, WhaleEvent, MarketResearch
from sibyl.models.position import Position, Execution, PerformanceRecord, Engine, PositionSide
from sibyl.models.state import (
    EngineState, AllocatorState, RiskPolicy, SystemHealth,
    CircuitBreakerStatus, RiskPolicyLevel,
)


# ─── Market Models ────────────────────────────────────────────────────

class TestMarketModels:
    def test_market_creation(self):
        m = Market(id="test-1", platform=Platform.POLYMARKET, title="Will it rain?")
        assert m.id == "test-1"
        assert m.platform == Platform.POLYMARKET
        assert m.status.value == "active"

    def test_price_validation(self):
        p = Price(market_id="m1", yes_price=0.65)
        assert p.yes_price == 0.65

    def test_price_out_of_range(self):
        with pytest.raises(ValidationError):
            Price(market_id="m1", yes_price=1.5)

    def test_orderbook_computed_properties(self):
        ob = OrderBookSnapshot(
            market_id="m1",
            bids=[OrderLevel(price=0.45, quantity=100), OrderLevel(price=0.44, quantity=50)],
            asks=[OrderLevel(price=0.55, quantity=80), OrderLevel(price=0.56, quantity=60)],
        )
        assert ob.best_bid == 0.45
        assert ob.best_ask == 0.55
        assert ob.mid_price == pytest.approx(0.50)
        assert ob.spread == pytest.approx(0.10)
        assert ob.normalized_spread == pytest.approx(0.20)

    def test_orderbook_empty(self):
        ob = OrderBookSnapshot(market_id="m1")
        assert ob.best_bid is None
        assert ob.best_ask is None
        assert ob.mid_price is None

    def test_trade_valid(self):
        t = Trade(market_id="m1", side="YES", size=25.0, price=0.60)
        assert t.side == "YES"

    def test_trade_invalid_side(self):
        with pytest.raises(ValidationError):
            Trade(market_id="m1", side="MAYBE", size=25.0, price=0.60)


# ─── Signal Models ────────────────────────────────────────────────────

class TestSignalModels:
    def test_signal_creation(self):
        s = Signal(
            market_id="m1",
            signal_type=SignalType.MOMENTUM,
            confidence=0.75,
            ev_estimate=0.08,
            routed_to=EngineRouting.ACE,
        )
        assert s.signal_type == SignalType.MOMENTUM
        assert s.routed_to == EngineRouting.ACE

    def test_signal_confidence_range(self):
        with pytest.raises(ValidationError):
            Signal(market_id="m1", signal_type=SignalType.ARBITRAGE, confidence=1.5)

    def test_whale_event(self):
        w = WhaleEvent(
            market_id="m1", platform="polymarket",
            side="YES", size=500.0, price=0.65, threshold=200.0,
            wallet_id="0xabc123",
        )
        assert w.wallet_id == "0xabc123"

    def test_market_research(self):
        r = MarketResearch(
            market_id="m1",
            sentiment_score=0.7,
            sentiment_label="BULLISH",
            confidence=0.65,
            key_yes_args=["Strong polling data", "Historical pattern"],
            key_no_args=["Low sample size"],
        )
        assert r.sentiment_label == "BULLISH"
        assert len(r.key_yes_args) == 2

    def test_market_research_sentiment_range(self):
        with pytest.raises(ValidationError):
            MarketResearch(market_id="m1", sentiment_score=2.0)


# ─── Position Models ──────────────────────────────────────────────────

class TestPositionModels:
    def test_position_pnl_yes(self):
        p = Position(
            market_id="m1", platform="polymarket",
            engine=Engine.SGE, side=PositionSide.YES,
            size=100.0, entry_price=0.40, current_price=0.55,
        )
        assert p.unrealized_pnl == pytest.approx(15.0)  # (0.55 - 0.40) * 100
        assert p.pnl_pct == pytest.approx(0.375)  # (0.55 - 0.40) / 0.40

    def test_position_pnl_no(self):
        p = Position(
            market_id="m1", platform="kalshi",
            engine=Engine.ACE, side=PositionSide.NO,
            size=100.0, entry_price=0.60, current_price=0.45,
        )
        assert p.unrealized_pnl == pytest.approx(15.0)  # (0.60 - 0.45) * 100

    def test_position_no_current_price(self):
        p = Position(
            market_id="m1", platform="polymarket",
            engine=Engine.SGE, side=PositionSide.YES,
            size=100.0, entry_price=0.40,
        )
        assert p.unrealized_pnl is None
        assert p.pnl_pct is None

    def test_execution(self):
        e = Execution(
            engine=Engine.ACE, platform="polymarket",
            side="BUY", fill_price=0.65, size=50.0,
        )
        assert e.order_type == "LIMIT"

    def test_performance_record(self):
        pr = PerformanceRecord(
            engine=Engine.SGE, resolved=True, correct=True,
            pnl=12.50, ev_estimated=0.06, ev_realized=0.08,
        )
        assert pr.correct is True


# ─── State Models ──────────────────────────────────────────────────────

class TestStateModels:
    def test_engine_state_circuit_breaker(self):
        es = EngineState(engine="SGE", circuit_breaker=CircuitBreakerStatus.TRIPPED)
        assert es.is_circuit_broken is True

    def test_engine_state_utilization(self):
        es = EngineState(engine="ACE", total_capital=1000.0, deployed_capital=300.0)
        assert es.utilization_pct == pytest.approx(0.30)

    def test_allocator_drift(self):
        a = AllocatorState(sge_actual_pct=0.60)
        assert a.drift_pct == pytest.approx(0.10)
        assert a.needs_rebalance is True

    def test_allocator_no_drift(self):
        a = AllocatorState(sge_actual_pct=0.72)
        assert a.drift_pct == pytest.approx(0.02)
        assert a.needs_rebalance is False

    def test_risk_policy_validation(self):
        rp = RiskPolicy(
            level=RiskPolicyLevel.MODERATE,
            kelly_fraction=0.15,
            min_ev_threshold=0.03,
            min_confidence=0.60,
            max_single_position_pct=0.02,
            max_platform_exposure_pct=0.25,
            max_total_exposure_pct=0.55,
            circuit_breaker_drawdown_pct=0.10,
            daily_loss_limit_pct=0.04,
            per_market_stop_loss_pct=0.35,
        )
        assert rp.kelly_fraction == 0.15

    def test_system_health_alert_escalation(self):
        sge = EngineState(engine="SGE")
        ace = EngineState(engine="ACE")
        a = AllocatorState()
        sh = SystemHealth(
            sge_state=sge, ace_state=ace, allocator=a,
            active_alerts=["alert1", "alert2"],
        )
        assert sh.needs_immediate_digest is True

    def test_system_health_no_escalation(self):
        sge = EngineState(engine="SGE")
        ace = EngineState(engine="ACE")
        a = AllocatorState()
        sh = SystemHealth(sge_state=sge, ace_state=ace, allocator=a)
        assert sh.needs_immediate_digest is False
