"""
Backtesting Engine — replays historical signals through the strategy pipeline.

PURPOSE:
    Validates category strategies and engine routing before risking real capital.
    Takes a snapshot of the database (signals, markets, prices) and replays them
    through the full pipeline: category adjustment → routing → Kelly sizing →
    simulated execution → P&L tracking.

HOW IT WORKS:

    1. SETUP:
       - Copy historical signals from the DB within a date range.
       - Initialize a virtual portfolio with configurable starting balance.
       - Load CategoryStrategyManager and engine configs (same as live system).

    2. REPLAY LOOP (chronological order):
       For each historical signal:
         a. Look up the market's category.
         b. Apply category-specific adjustments (confidence, EV, signal weight).
         c. Run routing logic (SGE/ACE/BOTH/DEFERRED).
         d. If routed: simulate Kelly-sized position at the historical entry price.
         e. Track the position through its historical outcome (closed P&L).

    3. ANALYSIS:
       After replay, compute:
         - Per-category: win rate, ROI, avg P&L, position count, Sharpe ratio
         - Per-engine: total P&L, win rate, capital efficiency
         - Per-signal-type: hit rate by signal type × category
         - Overall: total return, max drawdown, Sharpe, Calmar ratio

    4. OUTPUT:
       Returns a BacktestResult dataclass with all metrics.
       Can also write results to a JSON file for the dashboard.

USAGE:
    python -m sibyl --backtest --from 2026-01-01 --to 2026-03-19

    Or programmatically:
        engine = BacktestEngine(db=db)
        await engine.initialize()
        result = await engine.run(start_date="2026-01-01", end_date="2026-03-19")
        print(result.summary())

LIMITATIONS:
    - Simulated fills at historical prices (no slippage model yet).
    - Does not model order book impact or partial fills.
    - Correlation penalty is applied but does not model cascading liquidations.
    - Signal replay is sequential (no concurrent signal interference modeling).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.backtesting.engine")


# ── Result Dataclasses ────────────────────────────────────────────────

@dataclass
class CategoryResult:
    """Performance metrics for a single market category."""
    category: str
    position_count: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_deployed: float = 0.0
    avg_confidence: float = 0.0
    avg_ev: float = 0.0
    avg_hold_time_hours: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    @property
    def roi(self) -> float:
        return self.total_pnl / self.total_deployed if self.total_deployed > 0 else 0.0

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.position_count if self.position_count > 0 else 0.0


@dataclass
class EngineResult:
    """Performance metrics for a single engine (SGE or ACE)."""
    engine: str
    position_count: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_deployed: float = 0.0
    max_drawdown: float = 0.0
    peak_value: float = 0.0

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    @property
    def roi(self) -> float:
        return self.total_pnl / self.total_deployed if self.total_deployed > 0 else 0.0


@dataclass
class SignalTypeResult:
    """Performance metrics for a signal type within a category."""
    signal_type: str
    category: str
    count: int = 0
    wins: int = 0
    total_pnl: float = 0.0

    @property
    def hit_rate(self) -> float:
        return self.wins / self.count if self.count > 0 else 0.0


@dataclass
class BacktestResult:
    """Complete backtesting results."""
    start_date: str
    end_date: str
    starting_balance: float
    ending_balance: float
    total_signals_replayed: int = 0
    total_positions_opened: int = 0
    total_deferred: int = 0
    total_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    calmar_ratio: float = 0.0
    by_category: dict[str, CategoryResult] = field(default_factory=dict)
    by_engine: dict[str, EngineResult] = field(default_factory=dict)
    by_signal_type: list[SignalTypeResult] = field(default_factory=list)
    daily_pnl: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_return_pct(self) -> float:
        if self.starting_balance <= 0:
            return 0.0
        return (self.ending_balance - self.starting_balance) / self.starting_balance

    @property
    def win_rate(self) -> float:
        wins = sum(c.wins for c in self.by_category.values())
        total = sum(c.position_count for c in self.by_category.values())
        return wins / total if total > 0 else 0.0

    def summary(self) -> str:
        """Human-readable summary string."""
        lines = [
            f"═══ BACKTEST RESULTS ({self.start_date} → {self.end_date}) ═══",
            f"Starting Balance: ${self.starting_balance:.2f}",
            f"Ending Balance:   ${self.ending_balance:.2f}",
            f"Total Return:     {self.total_return_pct*100:.2f}%",
            f"Total P&L:        ${self.total_pnl:+.2f}",
            f"Max Drawdown:     {self.max_drawdown_pct*100:.2f}%",
            f"Sharpe Ratio:     {self.sharpe_ratio:.2f}",
            f"Win Rate:         {self.win_rate*100:.1f}%",
            f"Signals Replayed: {self.total_signals_replayed}",
            f"Positions Opened: {self.total_positions_opened}",
            f"Deferred:         {self.total_deferred}",
            "",
            "── By Category ──",
        ]
        for cat, cr in sorted(self.by_category.items(), key=lambda x: x[1].total_pnl, reverse=True):
            lines.append(
                f"  {cat:20s}  {cr.position_count:3d} trades  "
                f"WR={cr.win_rate*100:5.1f}%  "
                f"P&L=${cr.total_pnl:+8.2f}  "
                f"ROI={cr.roi*100:+6.2f}%"
            )
        lines.append("")
        lines.append("── By Engine ──")
        for eng, er in self.by_engine.items():
            lines.append(
                f"  {eng:5s}  {er.position_count:3d} trades  "
                f"WR={er.win_rate*100:5.1f}%  "
                f"P&L=${er.total_pnl:+8.2f}  "
                f"MaxDD={er.max_drawdown*100:.2f}%"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-friendly dict."""
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "starting_balance": self.starting_balance,
            "ending_balance": round(self.ending_balance, 2),
            "total_return_pct": round(self.total_return_pct, 4),
            "total_pnl": round(self.total_pnl, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "calmar_ratio": round(self.calmar_ratio, 2),
            "win_rate": round(self.win_rate, 4),
            "total_signals_replayed": self.total_signals_replayed,
            "total_positions_opened": self.total_positions_opened,
            "total_deferred": self.total_deferred,
            "by_category": {
                cat: {
                    "position_count": cr.position_count,
                    "win_rate": round(cr.win_rate, 4),
                    "total_pnl": round(cr.total_pnl, 2),
                    "roi": round(cr.roi, 4),
                    "avg_confidence": round(cr.avg_confidence, 4),
                }
                for cat, cr in self.by_category.items()
            },
            "by_engine": {
                eng: {
                    "position_count": er.position_count,
                    "win_rate": round(er.win_rate, 4),
                    "total_pnl": round(er.total_pnl, 2),
                    "roi": round(er.roi, 4),
                    "max_drawdown": round(er.max_drawdown, 4),
                }
                for eng, er in self.by_engine.items()
            },
            "daily_pnl": self.daily_pnl,
        }


# ── Virtual Position (for tracking during backtest) ───────────────────

@dataclass
class VirtualPosition:
    """In-memory position for backtesting (not written to DB)."""
    signal_id: int
    market_id: str
    category: str
    engine: str
    signal_type: str
    side: str
    size_contracts: int
    entry_price: float
    confidence: float
    ev: float
    opened_at: str = ""
    closed_at: str = ""
    close_price: float = 0.0
    pnl: float = 0.0
    outcome: str = ""  # "WIN" or "LOSS"


# ═══ Backtest Engine ══════════════════════════════════════════════════

class BacktestEngine:
    """Replays historical signals through the strategy pipeline.

    Usage:
        engine = BacktestEngine(db=db, starting_balance=500.0)
        await engine.initialize()
        result = await engine.run(start_date="2026-01-01", end_date="2026-03-19")
        print(result.summary())
    """

    def __init__(
        self,
        db: DatabaseManager,
        starting_balance: float = 500.0,
        sge_allocation: float = 0.70,
        ace_allocation: float = 0.30,
    ) -> None:
        """Initialize the backtest engine.

        Args:
            db:               DatabaseManager for reading historical data.
            starting_balance: Virtual starting portfolio balance.
            sge_allocation:   Fraction of capital allocated to SGE.
            ace_allocation:   Fraction of capital allocated to ACE.
        """
        self._db = db
        self._starting_balance = starting_balance
        self._sge_allocation = sge_allocation
        self._ace_allocation = ace_allocation

        # Strategy managers — loaded in initialize()
        self._category_mgr = None
        self._sge_risk: dict[str, Any] = {}
        self._ace_risk: dict[str, Any] = {}
        self._sge_whitelist: set[str] = set()
        self._ace_whitelist: set[str] = set()
        self._sge_min_conf: float = 0.60
        self._sge_min_ev: float = 0.03
        self._ace_min_conf: float = 0.68
        self._ace_min_ev: float = 0.06

    async def initialize(self) -> None:
        """Load strategy configs (same configs the live system uses)."""
        from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager
        from sibyl.core.config import load_yaml

        self._category_mgr = CategoryStrategyManager()
        await self._category_mgr.initialize()

        try:
            sge = load_yaml("sge_config.yaml")
            self._sge_risk = sge.get("risk_policy", {})
            self._sge_whitelist = set(sge.get("signal_whitelist", []))
        except FileNotFoundError:
            pass
        try:
            ace = load_yaml("ace_config.yaml")
            self._ace_risk = ace.get("risk_policy", {})
            self._ace_whitelist = set(ace.get("signal_whitelist", []))
        except FileNotFoundError:
            pass

        self._sge_min_conf = float(self._sge_risk.get("min_confidence", 0.60))
        self._sge_min_ev = float(self._sge_risk.get("min_ev_threshold", 0.03))
        self._ace_min_conf = float(self._ace_risk.get("min_confidence", 0.68))
        self._ace_min_ev = float(self._ace_risk.get("min_ev_threshold", 0.06))

        logger.info(
            "BacktestEngine initialized (balance=$%.2f, categories=%d)",
            self._starting_balance, len(self._category_mgr.categories),
        )

    async def run(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BacktestResult:
        """Execute the backtest over a date range.

        Args:
            start_date: ISO date string (YYYY-MM-DD). None = all history.
            end_date:   ISO date string (YYYY-MM-DD). None = today.

        Returns:
            BacktestResult with comprehensive performance metrics.
        """
        if not end_date:
            end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not start_date:
            start_date = "2020-01-01"  # Effectively "all history"

        # ── Fetch historical signals with market data ────────────────
        signals = await self._db.fetchall(
            """SELECT s.id, s.market_id, s.signal_type, s.confidence,
                      s.ev_estimate, s.timestamp, s.routed_to, s.status,
                      m.category, m.title
               FROM signals s
               JOIN markets m ON s.market_id = m.id
               WHERE date(s.timestamp) >= ? AND date(s.timestamp) <= ?
               ORDER BY s.timestamp ASC""",
            (start_date, end_date),
        )

        # ── Fetch historical position outcomes (for P&L resolution) ──
        position_outcomes = {}
        pos_rows = await self._db.fetchall(
            """SELECT p.signal_id, p.engine, p.side, p.entry_price,
                      p.current_price, p.pnl, p.status, p.opened_at, p.closed_at,
                      m.category
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status IN ('CLOSED', 'STOPPED')
                 AND date(p.opened_at) >= ? AND date(p.opened_at) <= ?""",
            (start_date, end_date),
        )
        for pr in pos_rows:
            signal_id = pr["signal_id"]
            if signal_id:
                position_outcomes[signal_id] = pr

        # ── Initialize virtual portfolio ─────────────────────────────
        balance = self._starting_balance
        sge_capital = balance * self._sge_allocation
        ace_capital = balance * self._ace_allocation
        sge_deployed = 0.0
        ace_deployed = 0.0

        # Track open positions per category for correlation penalty
        open_positions_by_cat: dict[str, int] = {}

        # Results tracking
        result = BacktestResult(
            start_date=start_date,
            end_date=end_date,
            starting_balance=self._starting_balance,
            ending_balance=self._starting_balance,
            by_engine={"SGE": EngineResult("SGE"), "ACE": EngineResult("ACE")},
        )

        # Drawdown tracking
        peak_balance = balance
        daily_balances: dict[str, float] = {}
        daily_pnl_list: list[float] = []

        positions: list[VirtualPosition] = []

        # ── Replay each signal ───────────────────────────────────────
        for sig in signals:
            result.total_signals_replayed += 1

            signal_type = sig["signal_type"]
            raw_conf = float(sig["confidence"])
            raw_ev = float(sig["ev_estimate"] or 0)
            category = sig["category"] or "Unknown"
            signal_id = sig["id"]

            # ── Category adjustment ──────────────────────────────────
            adjusted = self._category_mgr.adjust_signal(
                category=category,
                signal_type=signal_type,
                raw_confidence=raw_conf,
                raw_ev=raw_ev,
            )

            # ── Routing decision ─────────────────────────────────────
            destination = self._route(
                signal_type=signal_type,
                confidence=adjusted.confidence,
                ev=adjusted.ev,
                cat_pref=adjusted.preferred_engine,
            )

            if destination == "DEFERRED":
                result.total_deferred += 1
                continue

            # ── Execute for each target engine ───────────────────────
            engines = ["SGE", "ACE"] if destination == "BOTH" else [destination]

            for eng in engines:
                risk = self._sge_risk if eng == "SGE" else self._ace_risk
                available = (sge_capital - sge_deployed) if eng == "SGE" else (ace_capital - ace_deployed)

                if available <= 0:
                    continue

                # ── Kelly sizing ─────────────────────────────────────
                kelly_frac = float(risk.get("kelly_fraction", 0.15))
                max_pos_pct = float(risk.get("max_single_position_pct", 0.02))

                # Use actual market price from DB if available, else 0.50
                price_row = await self._db.fetchone(
                    "SELECT yes_price FROM prices WHERE market_id = ? ORDER BY timestamp DESC LIMIT 1",
                    (sig["market_id"],),
                )
                price = float(price_row["yes_price"]) if price_row else 0.50
                price = max(0.01, min(0.99, price))
                payout = (1.0 / price) - 1.0 if price > 0 else 0
                if payout <= 0:
                    continue

                kelly_raw = max(0, (adjusted.confidence * payout - (1 - adjusted.confidence)) / payout)
                kelly_capped = min(kelly_raw, kelly_frac)

                # ── Correlation penalty ──────────────────────────────
                existing_count = open_positions_by_cat.get(category, 0)
                corr_penalty = adjusted.correlation_penalty
                # Dynamic scaling: penalty reduces as portfolio grows
                portfolio_scale = max(0.5, min(2.0, balance / self._starting_balance))
                # Larger portfolio = less penalty (can tolerate more concentration)
                effective_penalty = corr_penalty / portfolio_scale
                corr_multiplier = max(0.1, 1.0 - effective_penalty * existing_count)

                # ── Category sizing ──────────────────────────────────
                size_scale = adjusted.size_scale
                position_dollars = min(kelly_capped, max_pos_pct) * available * size_scale * corr_multiplier
                if position_dollars < 1.0:
                    continue

                entry_price = price
                size_contracts = max(1, int(position_dollars / entry_price))

                # ── Resolve P&L from historical outcome ──────────────
                outcome_row = position_outcomes.get(signal_id)
                if outcome_row:
                    pnl = float(outcome_row["pnl"] or 0)
                    is_win = pnl > 0
                else:
                    # No historical outcome — simulate neutral
                    pnl = 0.0
                    is_win = False

                # Scale P&L by our position size relative to historical
                # (backtest might size differently than live)
                if outcome_row and float(outcome_row["entry_price"] or 0) > 0:
                    historical_value = 1.0  # Normalize
                    our_value = size_contracts * entry_price
                    pnl_scaled = pnl * (our_value / max(historical_value, 0.01))
                else:
                    pnl_scaled = pnl

                # ── Record position ──────────────────────────────────
                vpos = VirtualPosition(
                    signal_id=signal_id,
                    market_id=sig["market_id"],
                    category=category,
                    engine=eng,
                    signal_type=signal_type,
                    side="YES" if entry_price < 0.5 else "NO",
                    size_contracts=size_contracts,
                    entry_price=entry_price,
                    confidence=adjusted.confidence,
                    ev=adjusted.ev,
                    opened_at=sig["timestamp"] or "",
                    pnl=round(pnl_scaled, 4),
                    outcome="WIN" if is_win else "LOSS",
                )
                positions.append(vpos)

                # Track per-category open positions
                open_positions_by_cat[category] = open_positions_by_cat.get(category, 0) + 1

                # Update capital
                deployed = size_contracts * entry_price
                if eng == "SGE":
                    sge_deployed += deployed
                else:
                    ace_deployed += deployed

                balance += pnl_scaled

                # Update drawdown
                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
                if dd > result.max_drawdown_pct:
                    result.max_drawdown_pct = dd

                # ── Update result accumulators ───────────────────────
                result.total_positions_opened += 1
                result.total_pnl += pnl_scaled

                # Engine results
                er = result.by_engine[eng]
                er.position_count += 1
                er.total_pnl += pnl_scaled
                er.total_deployed += deployed
                if is_win:
                    er.wins += 1
                else:
                    er.losses += 1
                if balance > er.peak_value:
                    er.peak_value = balance
                eng_dd = (er.peak_value - balance) / er.peak_value if er.peak_value > 0 else 0
                if eng_dd > er.max_drawdown:
                    er.max_drawdown = eng_dd

                # Category results
                if category not in result.by_category:
                    result.by_category[category] = CategoryResult(category=category)
                cr = result.by_category[category]
                cr.position_count += 1
                cr.total_pnl += pnl_scaled
                cr.total_deployed += deployed
                cr.avg_confidence = (
                    (cr.avg_confidence * (cr.position_count - 1) + adjusted.confidence) / cr.position_count
                )
                cr.avg_ev = (
                    (cr.avg_ev * (cr.position_count - 1) + adjusted.ev) / cr.position_count
                )
                if is_win:
                    cr.wins += 1
                else:
                    cr.losses += 1
                if pnl_scaled > cr.best_trade_pnl:
                    cr.best_trade_pnl = pnl_scaled
                if pnl_scaled < cr.worst_trade_pnl:
                    cr.worst_trade_pnl = pnl_scaled

                # Signal type results (tracked in list, aggregated later)
                result.by_signal_type.append(SignalTypeResult(
                    signal_type=signal_type,
                    category=category,
                    count=1,
                    wins=1 if is_win else 0,
                    total_pnl=pnl_scaled,
                ))

                # Daily P&L tracking
                day = (sig["timestamp"] or "")[:10]
                if day:
                    daily_balances[day] = balance

        # ── Compute final metrics ────────────────────────────────────
        result.ending_balance = balance

        # Build daily P&L series
        sorted_days = sorted(daily_balances.items())
        prev_val = self._starting_balance
        for day, val in sorted_days:
            day_pnl = val - prev_val
            daily_pnl_list.append(day_pnl)
            result.daily_pnl.append({"date": day, "balance": round(val, 2), "pnl": round(day_pnl, 2)})
            prev_val = val

        # Sharpe ratio (annualized, assuming ~252 trading days)
        if len(daily_pnl_list) > 1:
            avg_daily = sum(daily_pnl_list) / len(daily_pnl_list)
            std_daily = math.sqrt(
                sum((x - avg_daily) ** 2 for x in daily_pnl_list) / (len(daily_pnl_list) - 1)
            )
            if std_daily > 0:
                result.sharpe_ratio = (avg_daily / std_daily) * math.sqrt(252)
            else:
                result.sharpe_ratio = 0.0
        else:
            result.sharpe_ratio = 0.0

        # Calmar ratio (annualized return / max drawdown)
        if result.max_drawdown_pct > 0 and len(daily_pnl_list) > 0:
            days = max(len(daily_pnl_list), 1)
            annual_return = result.total_return_pct * (365.0 / days)
            result.calmar_ratio = annual_return / result.max_drawdown_pct
        else:
            result.calmar_ratio = 0.0

        logger.info(
            "Backtest complete: %d signals → %d positions, P&L=$%.2f, return=%.2f%%",
            result.total_signals_replayed, result.total_positions_opened,
            result.total_pnl, result.total_return_pct * 100,
        )

        return result

    # ── Routing (mirrors SignalRouter logic) ───────────────────────────

    def _route(
        self,
        signal_type: str,
        confidence: float,
        ev: float,
        cat_pref: str | None = None,
    ) -> str:
        """Determine routing destination (same logic as SignalRouter)."""
        meets_sge = confidence >= self._sge_min_conf and ev >= self._sge_min_ev
        meets_ace = confidence >= self._ace_min_conf and ev >= self._ace_min_ev

        if not meets_sge and not meets_ace:
            return "DEFERRED"

        on_sge = signal_type in self._sge_whitelist
        on_ace = signal_type in self._ace_whitelist

        if signal_type == "COMPOSITE_HIGH_CONVICTION" and meets_sge and meets_ace:
            return "BOTH"
        if on_sge and on_ace and meets_sge and meets_ace:
            return "BOTH"
        if on_sge and meets_sge:
            return "SGE"
        if on_ace and meets_ace:
            return "ACE"
        if cat_pref and meets_sge and meets_ace:
            return cat_pref
        if meets_sge:
            return "SGE"
        if meets_ace:
            return "ACE"

        return "DEFERRED"
