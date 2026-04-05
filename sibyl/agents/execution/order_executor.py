"""
Order Executor — converts routed signals into live positions.

PURPOSE:
    Reads ROUTED signals from the database and converts them into trading
    positions.  In PAPER mode, fills are simulated at current market price.
    In LIVE mode, orders are placed on Kalshi via `KalshiClient.place_order()`.

EXECUTION FLOW:
    1. Fetch oldest ROUTED signal from `signals` table.
    2. Risk check: verify engine has available capital + circuit breaker is CLEAR.
    3. Position sizing: Kelly fraction × engine capital × confidence.
    4. Dynamic correlation penalty: reduce sizing when multiple positions exist
       in the same market category (Sprint 10).
    5. Order placement:
       - Paper mode: simulate fill at current YES/NO price.
       - Live mode:  call KalshiClient.place_order().
    6. Record: write to `positions` + `executions` tables.
    7. Update signal status → EXECUTED.

PAPER MODE (default):
    No real money changes hands.  The executor:
      - Looks up the current market price from the `prices` table.
      - Creates a position as if the order was filled at that price.
      - Writes to `executions` with order_id = "PAPER-{timestamp}".

POSITION SIZING (Kelly Criterion):
    The Kelly formula determines optimal bet size:
        kelly_size = (confidence × payout - (1-confidence)) / payout
    We then apply the engine's kelly_fraction cap (SGE=0.15, ACE=0.35).
    Final size = min(kelly_size, max_single_position_pct) × available_capital.

DYNAMIC CORRELATION PENALTY (Sprint 10):
    When multiple open positions exist in the same market category, position
    sizing is reduced to prevent cascading losses from a single event:

        effective_penalty = base_penalty / portfolio_scale_factor
        size_multiplier = max(0.10, 1.0 - effective_penalty × existing_count)

    The portfolio_scale_factor grows as the portfolio grows (larger portfolios
    can tolerate more concentration), and shrinks when the portfolio is under
    stress (amplifying diversification when it matters most).

    Scale factor = clamp(current_balance / starting_balance, 0.5, 2.0)
      - Portfolio at $500 (starting) → scale = 1.0 (base penalty)
      - Portfolio at $1000 (doubled) → scale = 2.0 (penalty halved)
      - Portfolio at $250 (halved)   → scale = 0.5 (penalty doubled)

    This creates an asymmetric risk profile: as the portfolio shrinks,
    the penalty increases, forcing tighter diversification exactly when
    the portfolio can least afford correlated losses.

POLLING: Every 3 seconds (same as Signal Router for minimal latency).
"""

from __future__ import annotations

import logging
import time

from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.order_executor")


class OrderExecutor(BaseAgent):
    """Converts ROUTED signals into positions via Kelly-sized orders.

    Sprint 10 Enhancement: Dynamic correlation penalty.
    When multiple open positions exist in the same market category,
    sizing is reduced by a penalty that auto-scales with portfolio value.
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: dict[str, Any],
        mode: str = "paper",
    ) -> None:
        """Initialize the Order Executor.

        Args:
            db:     Shared DatabaseManager.
            config: System config dict.
            mode:   "paper" (simulated) or "live" (real money).
        """
        super().__init__(name="order_executor", db=db, config=config)
        self._mode = mode
        self._sge_risk: dict[str, Any] = {}
        self._ace_risk: dict[str, Any] = {}

        # Kalshi client for live order placement (initialized in start())
        self._kalshi_client = None

        # Category strategy manager for correlation penalties (Sprint 10)
        self._category_mgr = None

        # Starting balance for dynamic penalty scaling (loaded from DB in start())
        self._starting_balance: float = 500.0

        # Policy engine for pre-trade gate checks (Sprint 11)
        self._policy = None

        # Sprint 23B: Async fill tracking — fire-and-forget orders.
        # Maps order_id → metadata dict for background fill checking.
        self._pending_orders: dict[str, dict] = {}

        # Sprint 23B: Max signals to process per cycle (was 1, now batch)
        self._max_signals_per_cycle = 5

        # Sprint 23B: Max time (seconds) to wait for a fill before cancelling
        self._fill_timeout_seconds = 60

        # Sprint 27: Taker-only execution — always cross the spread.
        # "There are never market maker winners, only market taker winners."

    @property
    def poll_interval(self) -> float:
        """Run every 3 seconds — minimal latency from signal to position."""
        return 3.0

    async def start(self) -> None:
        """Load engine risk policies, category strategies, policy engine, and Kalshi client."""
        from sibyl.core.config import load_yaml
        from sibyl.agents.intelligence.category_strategy import CategoryStrategyManager
        from sibyl.core.policy import PolicyEngine

        try:
            sge = load_yaml("sge_config.yaml")
            self._sge_risk = sge.get("risk_policy", {})
        except FileNotFoundError:
            self._sge_risk = {}
        try:
            ace = load_yaml("ace_config.yaml")
            self._ace_risk = ace.get("risk_policy", {})
        except FileNotFoundError:
            self._ace_risk = {}

        # ── Load category strategies for correlation penalty ────────────
        self._category_mgr = CategoryStrategyManager()
        await self._category_mgr.initialize()

        # ── Initialize Policy Engine (Sprint 11) ────────────────────────
        self._policy = PolicyEngine()
        try:
            self._policy.initialize()
            self.logger.info("PolicyEngine loaded for order execution")
        except FileNotFoundError:
            self.logger.warning(
                "investment_policy_config.yaml not found — policy enforcement disabled"
            )
            self._policy = None

        # ── Read starting balance for dynamic penalty scaling ──────────
        balance_row = await self.db.fetchone(
            "SELECT value FROM system_state WHERE key = 'portfolio_total_balance'"
        )
        if balance_row:
            self._starting_balance = max(float(balance_row["value"]), 100.0)
        else:
            self._starting_balance = 500.0

        # ── Sprint 23D: Use shared Kalshi client ─────────────────────────
        if self._mode == "live":
            from sibyl.clients.kalshi_client import get_shared_kalshi_client
            self._kalshi_client = get_shared_kalshi_client(self.config)
            if not self._kalshi_client.is_authenticated:
                self.logger.error(
                    "LIVE mode requires KALSHI_KEY_ID + KALSHI_PRIVATE_KEY_PATH — "
                    "falling back to PAPER mode"
                )
                self._kalshi_client = None
                self._mode = "paper"
            else:
                self.logger.info("Order Executor using shared Kalshi client")

        self.logger.info("Order Executor started (mode=%s)", self._mode)

    async def run_cycle(self) -> None:
        """Process pending fill checks + batch of new ROUTED signals.

        Sprint 23B: Two-phase cycle for maximum throughput:
          Phase 1 — Check all pending orders for fills (non-blocking).
          Phase 2 — Fire-and-forget up to N new signals per cycle.
        """
        # Phase 1: Check pending orders for fills / timeouts
        if self._pending_orders:
            await self._check_pending_fills()

        # Phase 2: Process batch of ROUTED signals (fire-and-forget)
        signals = await self.db.fetchall(
            """SELECT s.id, s.market_id, s.signal_type, s.confidence, s.ev_estimate,
                      s.routed_to, s.policy_tier, s.sports_sub_type, s.override_flag,
                      s.direction, s.detection_modes_triggered, s.timeframe,
                      m.category
               FROM signals s
               LEFT JOIN markets m ON s.market_id = m.id
               WHERE s.status = 'ROUTED'
               ORDER BY s.timestamp ASC
               LIMIT ?""",
            (self._max_signals_per_cycle,),
        )

        for signal in signals:
            engine = signal["routed_to"]
            if engine == "DEFERRED":
                continue

            engines_to_execute = ["SGE", "ACE"] if engine == "BOTH" else [engine]
            for eng in engines_to_execute:
                await self._execute_for_engine(signal, eng)

            await self.db.execute(
                "UPDATE signals SET status = 'EXECUTED' WHERE id = ?",
                (signal["id"],),
            )

        if signals:
            await self.db.commit()

    async def stop(self) -> None:
        """Graceful shutdown — cancel pending orders."""
        if self._pending_orders and self._kalshi_client:
            self.logger.info(
                "Cancelling %d pending orders on shutdown...",
                len(self._pending_orders),
            )
            for oid in list(self._pending_orders):
                try:
                    await self._kalshi_client.cancel_order(oid)
                except Exception:
                    pass
            self._pending_orders.clear()
        # Don't close shared client — other agents may still need it
        self.logger.info("Order Executor stopped")

    # ── Sprint 23B: Async Fill Checker ─────────────────────────────────

    async def _check_pending_fills(self) -> None:
        """Check all pending orders for fills, cancellations, or timeouts.

        Runs every cycle (3s).  For each pending order:
          - If filled → record position + execution, remove from pending.
          - If cancelled/expired → remove from pending.
          - If timed out (>120s) → cancel on Kalshi, remove from pending.

        Uses one API call per pending order.  With 3s cycles and typical
        pending count <20, this adds ~7 API calls/s — well within limits.
        """
        now = time.time()
        resolved: list[str] = []

        for order_id, meta in self._pending_orders.items():
            age = now - meta["placed_at"]

            try:
                confirm = await self._kalshi_client.get_order(order_id)
            except Exception as e:
                self.logger.debug("Fill check for %s failed: %s", order_id, e)
                continue

            if not confirm or "order" not in confirm:
                continue

            status = confirm["order"].get("status", "unknown")

            if status in ("executed", "filled"):
                # Sprint 29: Check filled_count for partial fills (BUG-008).
                # Previously assumed all-or-nothing; partial fills recorded
                # wrong size and left remainder unhandled.
                entry_price = meta["entry_price"]
                avg_price = confirm["order"].get("average_fill_price")
                if avg_price is not None:
                    actual_price = float(avg_price) / 100.0
                    self.logger.info(
                        "ASYNC FILL: %s @ %.4f (requested %.4f, wait=%.0fs, slippage=%.1fbps)",
                        order_id, actual_price, entry_price, age,
                        abs(actual_price - entry_price) * 10000,
                    )
                    entry_price = actual_price

                # Check actual filled quantity vs requested
                filled_count = confirm["order"].get("filled_count")
                requested_count = meta["size_contracts"]
                if filled_count is not None:
                    filled_count = int(filled_count)
                    if filled_count < requested_count and filled_count > 0:
                        self.logger.info(
                            "PARTIAL FILL: %s filled %d/%d contracts — recording partial",
                            order_id, filled_count, requested_count,
                        )
                        meta = dict(meta)  # Copy to avoid mutating shared dict
                        meta["size_contracts"] = filled_count
                    elif filled_count == 0:
                        self.logger.info(
                            "ZERO FILL: %s — 0/%d contracts filled, discarding",
                            order_id, requested_count,
                        )
                        resolved.append(order_id)
                        continue

                await self._record_fill(order_id, meta, entry_price)
                resolved.append(order_id)

            elif status in ("canceled", "expired"):
                self.logger.info(
                    "ORDER %s: %s after %.0fs — not recording position",
                    status.upper(), order_id, age,
                )
                resolved.append(order_id)

            elif age > self._fill_timeout_seconds:
                # Timed out — cancel and remove
                self.logger.warning(
                    "ORDER TIMEOUT after %.0fs: %s — cancelling",
                    age, order_id,
                )
                try:
                    await self._kalshi_client.cancel_order(order_id)
                except Exception:
                    self.logger.debug("Cancel failed for %s (may already be filled)", order_id)
                resolved.append(order_id)

        for oid in resolved:
            self._pending_orders.pop(oid, None)

        if resolved:
            await self.db.commit()

    async def _record_fill(
        self, order_id: str, meta: dict, entry_price: float
    ) -> None:
        """Record a confirmed fill as a position + execution row.

        Extracted from _execute_for_engine so that both instant fills and
        async fills use the same recording logic.
        """
        signal = meta["signal"]
        engine = meta["engine"]
        side = meta["side"]
        size_contracts = meta["size_contracts"]
        current_price = meta["current_price"]
        confidence = meta["confidence"]
        risk = meta["risk"]
        cat_profile = meta.get("cat_profile")

        # Stop loss from per-category profile or engine config
        if cat_profile:
            stop_loss_pct = float(cat_profile.get(
                "stop_loss_pct", risk.get("per_market_stop_loss_pct", 0.35)
            ))
        else:
            stop_loss_pct = float(risk.get("per_market_stop_loss_pct", 0.35))
        stop_loss = entry_price * (1.0 - stop_loss_pct)

        market_id = meta["market_id"]

        await self.db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price,
                current_price, stop_loss, status, signal_id, thesis)
               VALUES (?, 'kalshi', ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)""",
            (
                market_id, engine, side, float(size_contracts), entry_price,
                current_price, stop_loss, signal["id"],
                f"Signal #{signal['id']}: {signal['signal_type']} (conf={confidence:.2f})",
            ),
        )

        # Sprint 29: Fee accounting — record per-execution fee for accurate P&L.
        # Kalshi taker fee ~0.7c per contract per side. Use config value if available.
        fee_rate = 0.007  # Default: 0.7% per side
        if cat_profile:
            # fee_per_contract in config is roundtrip (1.4%); per-side = half
            fee_rate = float(cat_profile.get("fee_per_contract", 0.014)) / 2.0
        buy_fee = float(size_contracts) * fee_rate

        await self.db.execute(
            """INSERT INTO executions
               (signal_id, engine, platform, order_id, side, fill_price, size, order_type, fee_amount)
               VALUES (?, ?, 'kalshi', ?, 'BUY', ?, ?, 'limit', ?)""",
            (signal["id"], engine, order_id, entry_price, float(size_contracts), buy_fee),
        )

        self.logger.info(
            "POSITION RECORDED: %s %s %d contracts @ %.2f on %s (engine=%s, fee=$%.4f)",
            side, signal["signal_type"], size_contracts, entry_price,
            market_id, engine, buy_fee,
        )

    # ── Execution Logic ───────────────────────────────────────────────

    async def _execute_for_engine(self, signal: Any, engine: str) -> None:
        """Size and execute a position for a specific engine.

        Sprint 10: Dynamic correlation penalty.
        Sprint 20: Per-category risk profiles override engine-level defaults.
        When a category has a risk profile, its kelly_fraction, max_position_pct,
        and stop_loss_pct are used instead of the engine's risk_policy values.
        """
        risk = self._sge_risk if engine == "SGE" else self._ace_risk
        market_id = signal["market_id"]
        confidence = float(signal["confidence"])
        category = signal["category"] if "category" in signal.keys() else None
        sports_sub_type = signal["sports_sub_type"] if "sports_sub_type" in signal.keys() else None
        is_override = bool(signal["override_flag"]) if "override_flag" in signal.keys() else False

        # ── Sprint 20: Load per-category risk profile ─────────────────
        cat_profile = None
        if self._policy and self._policy.initialized and category:
            cat_profile = self._policy.get_category_risk_profile(category)
            if cat_profile and cat_profile.get("locked", False):
                self.logger.debug(
                    "Category '%s' is locked — skipping execution for %s",
                    category, market_id,
                )
                return

        # ── Policy Pre-Trade Gate (Sprint 11) ─────────────────────────
        if self._policy and self._policy.initialized and category:
            cat_exposure = await self._get_category_exposure(engine, category)
            market_data = {"category": category, "market_id": market_id,
                           "open_interest": 99999}
            signal_data = {"confidence": confidence,
                           "ev": float(signal["ev_estimate"] or 0)}

            decision = self._policy.pre_trade_gate(
                signal_data=signal_data,
                market_data=market_data,
                engine=engine,
                current_category_exposure_pct=cat_exposure,
            )
            if not decision.approved:
                self.logger.info(
                    "Policy REJECTED execution for %s on %s: %s",
                    market_id, engine, decision.rejection_reason,
                )
                return

        # ── Risk Check ────────────────────────────────────────────────
        state = await self.db.fetchone(
            "SELECT * FROM engine_state WHERE engine = ?", (engine,)
        )
        if not state:
            self.logger.warning("No engine state for %s — skipping", engine)
            return

        available = float(state["available_capital"])
        circuit = state["circuit_breaker"]

        if circuit == "TRIGGERED":
            self.logger.warning("Circuit breaker TRIGGERED for %s — skipping", engine)
            return

        if available <= 0:
            self.logger.debug("No available capital for %s — skipping", engine)
            return

        # Sprint 23D: Order-level dedup — skip if we already have an OPEN
        # position or a pending order on this exact market ticker.
        existing = await self.db.fetchone(
            "SELECT id FROM positions WHERE market_id = ? AND status = 'OPEN' LIMIT 1",
            (market_id,),
        )
        if existing:
            self.logger.debug("Dedup: already have OPEN position on %s", market_id)
            return
        if market_id in {m["market_id"] for m in self._pending_orders.values()}:
            self.logger.debug("Dedup: already have pending order on %s", market_id)
            return

        # ── Sprint 26: Per-market accumulation guard ─────────────────────
        # Prevents single-market concentration across sessions.
        # LT3 showed one XRP position accumulated 116 contracts / $20.07 loss.
        # Hard-cap total exposure per market_id regardless of session boundaries.
        max_per_market_pct = float(
            cat_profile.get("max_position_pct", risk.get("max_single_position_pct", 0.12))
        ) if cat_profile else float(risk.get("max_single_position_pct", 0.12))
        engine_capital = float(state["total_capital"]) if state["total_capital"] else available

        existing_exposure_row = await self.db.fetchone(
            "SELECT COALESCE(SUM(size * entry_price), 0) as exposure "
            "FROM positions WHERE market_id = ? AND status = 'OPEN'",
            (market_id,),
        )
        existing_market_exposure = float(existing_exposure_row["exposure"]) if existing_exposure_row else 0.0
        max_market_dollars = max_per_market_pct * engine_capital
        if existing_market_exposure >= max_market_dollars:
            self.logger.debug(
                "ACCUMULATION GUARD: %s already has $%.2f exposure (cap=$%.2f) — skipping",
                market_id, existing_market_exposure, max_market_dollars,
            )
            return

        # ── Sprint 31: Stale market + horizon guard (B-NEW-2, B-NEW-4) ──
        # B-NEW-2: LT7 opened positions on markets that had already closed.
        # B-NEW-4: Monthly markets (30+ days out) entered in 3-hour tests.
        close_row = await self.db.fetchone(
            "SELECT close_date FROM markets WHERE id = ?", (market_id,)
        )
        if close_row and close_row["close_date"]:
            from datetime import datetime, timezone
            try:
                close_dt = datetime.fromisoformat(
                    close_row["close_date"].replace("Z", "+00:00")
                )
                now_utc = datetime.now(timezone.utc)
                if close_dt <= now_utc:
                    self.logger.debug(
                        "STALE MARKET: %s closed at %s — skipping",
                        market_id, close_row["close_date"],
                    )
                    return
                # Reject markets expiring more than 7 days out
                max_horizon_seconds = float(
                    (cat_profile or {}).get("max_horizon_seconds", 604800)
                )
                time_to_expiry = (close_dt - now_utc).total_seconds()
                if time_to_expiry > max_horizon_seconds:
                    self.logger.debug(
                        "HORIZON GUARD: %s expires in %.0f days — skipping",
                        market_id, time_to_expiry / 86400,
                    )
                    return
            except (ValueError, TypeError):
                pass

        # ── Sprint 29: Correlation block enforcement (BUG-004) ───────
        # Sub-E correlation scanner writes corr_block_{event_id} flags
        # when event exposure exceeds 7% of capital. Previously these
        # flags were write-only — executor never read them.
        event_row = await self.db.fetchone(
            "SELECT event_id FROM markets WHERE id = ?", (market_id,)
        )
        if event_row and event_row["event_id"]:
            block_row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = ?",
                (f"corr_block_{event_row['event_id']}",),
            )
            if block_row:
                self.logger.info(
                    "CORRELATION BLOCK: %s blocked — event %s: %s",
                    market_id, event_row["event_id"], block_row["value"],
                )
                return

        # ── Sprint 29: Per-underlying concentration cap (H-1) ────────
        # Prevents accumulating 25%+ exposure to a single underlying (BTC,
        # ETH, SOL, XRP) through multiple bracket positions at different
        # strikes/timeframes. A single adverse price move would hit all.
        max_underlying_pct = 0.15  # 15% of engine capital per underlying
        if cat_profile:
            max_underlying_pct = float(cat_profile.get("max_underlying_pct", 0.15))
        # Extract underlying from Kalshi ticker: KXBTC_*, KXBTCD_*, KXETH_*, etc.
        underlying = None
        ticker_upper = market_id.upper()
        for prefix, asset in [("KXBTC", "BTC"), ("KXETH", "ETH"),
                              ("KXSOL", "SOL"), ("KXXRP", "XRP")]:
            if ticker_upper.startswith(prefix):
                underlying = asset
                break
        if underlying:
            underlying_exposure_row = await self.db.fetchone(
                """SELECT COALESCE(SUM(p.size * p.entry_price), 0) as exposure
                   FROM positions p
                   WHERE p.status = 'OPEN' AND (
                       p.market_id LIKE ? OR p.market_id LIKE ?
                   )""",
                (f"KX{underlying}_%", f"Kx{underlying.lower()}_%"),
            )
            underlying_exposure = float(underlying_exposure_row["exposure"]) if underlying_exposure_row else 0.0
            max_underlying_dollars = max_underlying_pct * engine_capital
            if underlying_exposure >= max_underlying_dollars:
                self.logger.info(
                    "UNDERLYING CAP: %s exposure $%.2f >= $%.2f (%.0f%%) — skipping %s",
                    underlying, underlying_exposure, max_underlying_dollars,
                    max_underlying_pct * 100, market_id,
                )
                return

        # ── Sprint 25: Pre-execution balance gate ──────────────────────
        # In live mode, check actual Kalshi cash before sizing/placing.
        # Prevents thousands of order exceptions when capital is locked
        # in existing positions.
        _cash_available: float | None = None
        if self._mode == "live":
            cash_row = await self.db.fetchone(
                "SELECT value FROM system_state WHERE key = 'portfolio_cash_available'"
            )
            if cash_row:
                _cash_available = float(cash_row["value"])
                # Deduct pending orders from available cash
                pending_cost = sum(
                    m.get("size_contracts", 0) * m.get("entry_price", 0)
                    for m in self._pending_orders.values()
                )
                _cash_available -= pending_cost
                if _cash_available < 1.0:
                    self.logger.debug(
                        "BALANCE GATE: $%.2f cash (pending=$%.2f) — skipping %s",
                        float(cash_row["value"]), pending_cost, market_id,
                    )
                    return

        # ── Sprint 29: Drawdown-driven sizing reduction (H-4) ────────
        # Risk dashboard writes drawdown level to system_state. Apply
        # multiplier: CLEAR=1.0, WARNING=0.75, CAUTION=0.50, CRITICAL=0.0.
        drawdown_multiplier = 1.0
        dd_row = await self.db.fetchone(
            "SELECT value FROM system_state WHERE key = 'risk_drawdown_level'"
        )
        if dd_row:
            dd_level = dd_row["value"]
            if dd_level == "CRITICAL":
                self.logger.warning(
                    "DRAWDOWN HALT: level=%s — blocking all new positions", dd_level,
                )
                return
            elif dd_level == "CAUTION":
                drawdown_multiplier = 0.50
            elif dd_level == "WARNING":
                drawdown_multiplier = 0.75

        # ── Position Sizing (Kelly) ───────────────────────────────────
        # Sprint 20: Per-category profile overrides engine-level risk params
        # Sprint 24: Per-timeframe Kelly (Brier-tiered) overrides category default
        if cat_profile:
            kelly_frac = float(cat_profile.get("kelly_fraction", risk.get("kelly_fraction", 0.15)))
            max_position_pct = float(cat_profile.get("max_position_pct", risk.get("max_single_position_pct", 0.02)))
            # Sprint 24: Per-timeframe Kelly override
            try:
                timeframe = signal["timeframe"] or ""
            except (KeyError, IndexError):
                timeframe = ""
            kelly_by_tf = cat_profile.get("kelly_by_timeframe")
            if kelly_by_tf and timeframe and timeframe in kelly_by_tf:
                kelly_frac = float(kelly_by_tf[timeframe])
        else:
            kelly_frac = float(risk.get("kelly_fraction", 0.15))
            max_position_pct = float(risk.get("max_single_position_pct", 0.02))

        # Get current price for the market
        price_row = await self.db.fetchone(
            "SELECT yes_price FROM prices WHERE market_id = ? ORDER BY timestamp DESC LIMIT 1",
            (market_id,),
        )
        if not price_row:
            return

        current_price = float(price_row["yes_price"])
        if current_price <= 0 or current_price >= 1.0:
            return

        # Sprint 22: Read orderbook for spread-aware pricing
        best_bid: float | None = None
        best_ask: float | None = None
        try:
            book_row = await self.db.fetchone(
                "SELECT bids, asks FROM orderbook WHERE market_id = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (market_id,),
            )
            if book_row:
                import json
                try:
                    bids = json.loads(book_row["bids"]) if book_row["bids"] else []
                    asks = json.loads(book_row["asks"]) if book_row["asks"] else []
                    if bids:
                        best_bid = float(bids[0].get("price", 0))
                    if asks:
                        best_ask = float(asks[0].get("price", 0))
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass
        except Exception:
            pass

        # ── Determine trade side early (needed for correct Kelly payout) ──
        # Sprint 29: BUG-001 fix — side must be known before Kelly computation.
        # Previously, payout was always computed from YES price regardless of
        # trade direction, undersizing NO positions by 2-5x.
        raw_direction = signal["direction"] if "direction" in signal.keys() and signal["direction"] else None
        if not raw_direction:
            dmt = signal["detection_modes_triggered"] if "detection_modes_triggered" in signal.keys() else ""
            if dmt and "DIR:" in (dmt or ""):
                raw_direction = dmt.split("DIR:")[1].split("|")[0].strip().upper()
        if not raw_direction or raw_direction not in ("YES", "NO"):
            raw_direction = "YES" if current_price < 0.50 else "NO"
            self.logger.debug(
                "No direction for signal %s — using price-based fallback: %s",
                signal["id"], raw_direction,
            )
        side = raw_direction

        # Kelly: optimal fraction of bankroll to wager
        # For binary markets: kelly = (confidence × payout - (1-confidence)) / payout
        # Sprint 29 fix: compute payout from the correct side's cost basis.
        # YES cost = current_price, NO cost = 1 - current_price.
        if side == "YES":
            cost_basis = current_price
        else:
            cost_basis = 1.0 - current_price
        payout = (1.0 / cost_basis) - 1.0 if cost_basis > 0 else 0
        if payout <= 0:
            return

        kelly_raw = (confidence * payout - (1.0 - confidence)) / payout
        kelly_raw = max(kelly_raw, 0)  # Don't bet if negative Kelly

        # Apply engine's kelly fraction cap
        kelly_capped = min(kelly_raw, kelly_frac)

        # ── Dynamic Correlation Penalty (Sprint 10) ─────────────────
        # Reduce sizing when multiple open positions exist in the same
        # market category.  Penalty auto-scales with portfolio value:
        # larger portfolios tolerate more concentration, stressed
        # portfolios enforce tighter diversification.
        corr_multiplier = await self._compute_correlation_multiplier(market_id)

        # ── Policy Sizing Adjustments (Sprint 11) ────────────────────
        # In-game sports: apply Kelly shrinkage factor (0.50x)
        if self._policy and sports_sub_type == "IN_GAME":
            shrinkage = self._policy.get_in_game_kelly_shrinkage()
            kelly_capped *= shrinkage
            self.logger.debug(
                "In-game Kelly shrinkage: %.2fx → capped at %.4f", shrinkage, kelly_capped,
            )

        # Override trades: reduced position size (50% of normal max)
        override_multiplier = 1.0
        if is_override and self._policy:
            override_multiplier = self._policy.get_override_position_multiplier()
            self.logger.info(
                "Override position sizing: %.0f%% of normal", override_multiplier * 100,
            )

        # Position size in dollars (with correlation + policy + drawdown adjustments)
        position_dollars = (
            min(kelly_capped, max_position_pct) * available
            * corr_multiplier * override_multiplier * drawdown_multiplier
        )
        if position_dollars < 1.0:
            return  # Too small to trade

        # Sprint 26: Cap position_dollars to remaining per-market headroom
        remaining_headroom = max_market_dollars - existing_market_exposure
        if position_dollars > remaining_headroom:
            position_dollars = remaining_headroom
            if position_dollars < 1.0:
                return

        # Side already determined above (moved to pre-Kelly for BUG-001 fix)

        # Sprint 27: Taker-only execution — always cross the spread.
        # "There are never market maker winners, only market taker winners."
        # Taker guarantees immediate fills; maker sits and hopes.
        if side == "YES":
            if best_ask and 0 < best_ask < 1.0:
                entry_price = best_ask
            else:
                entry_price = current_price
        else:
            if best_bid and 0 < best_bid < 1.0:
                entry_price = 1.0 - best_bid
            else:
                entry_price = 1.0 - current_price

        # Sprint 27: Minimum NO premium gate — reject short positions with
        # poor risk/reward (small premium, large exposure). LT4: several NO
        # positions entered at 7-10c premium with 90-93c exposure.
        if side == "NO":
            no_premium = entry_price  # NO entry_price IS the premium we pay
            min_no_premium = 0.15     # Default: 15c minimum
            if cat_profile:
                min_no_premium = float(cat_profile.get("min_no_premium", 0.15))
            if no_premium < min_no_premium:
                self.logger.debug(
                    "SHORT PREMIUM GATE: NO premium %.0fc < %.0fc minimum for %s",
                    no_premium * 100, min_no_premium * 100, market_id,
                )
                return

        size_contracts = int(position_dollars / entry_price) if entry_price > 0 else 0

        # Sprint 29: Dynamic contract cap by entry price. Lower-price entries
        # have wider profit margin and can tolerate more contracts. Higher-price
        # entries have thin margins — keep tight caps.
        max_contracts = 5
        if cat_profile:
            price_tiers = cat_profile.get("max_contracts_by_entry_price")
            if price_tiers:
                for tier in price_tiers:
                    if entry_price <= tier[0]:
                        max_contracts = int(tier[1])
                        break
                else:
                    max_contracts = int(cat_profile.get("max_contracts_per_trade", 5))
            else:
                max_contracts = int(cat_profile.get("max_contracts_per_trade", 5))
        if size_contracts > max_contracts:
            size_contracts = max_contracts

        if size_contracts < 1:
            return

        # Sprint 25: Downsize to affordable amount if cash-limited
        if self._mode == "live" and _cash_available is not None:
            order_cost = size_contracts * entry_price
            if order_cost > _cash_available:
                affordable = int(_cash_available / entry_price) if entry_price > 0 else 0
                if affordable < 1:
                    self.logger.debug(
                        "BALANCE GATE: order $%.2f > cash $%.2f — skipping %s",
                        order_cost, _cash_available, market_id,
                    )
                    return
                self.logger.info(
                    "BALANCE GATE: downsized %s from %d to %d contracts (cash=$%.2f)",
                    market_id, size_contracts, affordable, _cash_available,
                )
                size_contracts = affordable

        # Sprint 29: Tiered entry floor by timeframe. LT5 showed flat 50c floor
        # blocked 97% of intraday/hourly crypto brackets. Short-timeframe markets
        # have naturally low prices (BTC 15-min avg 2.4c). Use per-timeframe
        # floors to unlock intraday markets while still rejecting extreme longshots.
        min_entry = 0.30  # Default floor
        if cat_profile:
            flb = cat_profile.get("flb_config", {})
            try:
                sig_timeframe = signal["timeframe"] or ""
            except (KeyError, IndexError):
                sig_timeframe = ""
            tf_floors = flb.get("longshot_reject_by_timeframe")
            if tf_floors and sig_timeframe and sig_timeframe in tf_floors:
                min_entry = float(tf_floors[sig_timeframe])
            else:
                min_entry = float(flb.get("longshot_reject_below", 0.30))
        if entry_price > 0.93 or entry_price < min_entry:
            self.logger.debug(
                "PRICE GATE: %.0fc outside [%.0fc, 93c] for %s (tf=%s)",
                entry_price * 100, min_entry * 100, market_id,
                sig_timeframe if 'sig_timeframe' in dir() else "?",
            )
            return

        # ── Execute ───────────────────────────────────────────────────
        order_id = f"PAPER-{int(time.time() * 1000)}"

        if self._mode == "live" and self._kalshi_client:
            # ── LIVE ORDER PLACEMENT ─────────────────────────────────
            # Convert to Kalshi's format:
            #   - side: "yes" or "no" (lowercase)
            #   - price_cents: integer 1-99
            #   - size: integer number of contracts
            # Determine order type from engine config
            order_type = "limit"
            if engine == "ACE":
                from sibyl.core.config import load_yaml
                try:
                    ace_cfg = load_yaml("ace_config.yaml")
                    order_type = ace_cfg.get("execution", {}).get("order_type", "market")
                    # Fallback to limit if spread is too wide
                    fallback_bps = int(ace_cfg.get("execution", {}).get(
                        "fallback_to_limit_if_spread_bps", 200
                    ))
                    # Check current spread from orderbook
                    book_row = await self.db.fetchone(
                        "SELECT bids, asks FROM orderbook WHERE market_id = ? "
                        "ORDER BY timestamp DESC LIMIT 1",
                        (market_id,),
                    )
                    if book_row and order_type == "market":
                        import json
                        try:
                            bids = json.loads(book_row["bids"])
                            asks = json.loads(book_row["asks"])
                            if bids and asks:
                                best_bid = float(bids[0].get("price", 0))
                                best_ask = float(asks[0].get("price", 1))
                                spread_bps = int((best_ask - best_bid) * 10000)
                                if spread_bps > fallback_bps:
                                    order_type = "limit"
                                    self.logger.info(
                                        "ACE spread %dbps > %dbps — using limit order",
                                        spread_bps, fallback_bps,
                                    )
                        except (json.JSONDecodeError, KeyError, IndexError):
                            pass
                except FileNotFoundError:
                    pass

            price_cents = int(entry_price * 100)
            try:
                result = await self._kalshi_client.place_order(
                    ticker=market_id,
                    side=side.lower(),
                    size=size_contracts,
                    price_cents=price_cents,
                    order_type=order_type,
                )
                if result and "order" in result:
                    order_id = result["order"].get("order_id", order_id)
                    order_status = result["order"].get("status", "unknown")
                    self.logger.info(
                        "LIVE ORDER PLACED: %s (order_id=%s, status=%s)",
                        market_id, order_id, order_status,
                    )

                    # Sprint 23B: Fire-and-forget — check if instantly filled,
                    # otherwise track in _pending_orders for background checking.
                    if order_status in ("executed", "filled"):
                        # Instant fill — extract price and record immediately
                        avg_price = result["order"].get("average_fill_price")
                        if avg_price is not None:
                            actual_price = float(avg_price) / 100.0
                            self.logger.info(
                                "INSTANT FILL: %s @ %.4f (requested %.4f, slippage=%.1fbps)",
                                order_id, actual_price, entry_price,
                                abs(actual_price - entry_price) * 10000,
                            )
                            entry_price = actual_price
                    else:
                        # Order is resting — track for background fill checking.
                        # Do NOT block; _check_pending_fills() handles it.
                        self._pending_orders[order_id] = {
                            "market_id": market_id,
                            "signal": signal,
                            "engine": engine,
                            "side": side,
                            "size_contracts": size_contracts,
                            "entry_price": entry_price,
                            "current_price": current_price,
                            "confidence": confidence,
                            "kelly_capped": kelly_capped,
                            "corr_multiplier": corr_multiplier,
                            "cat_profile": cat_profile,
                            "risk": risk,
                            "placed_at": time.time(),
                        }
                        self.logger.info(
                            "ORDER PENDING: %s — tracking for async fill (%d pending total)",
                            order_id, len(self._pending_orders),
                        )
                        return  # Don't record position yet — wait for fill
                else:
                    self.logger.error(
                        "LIVE ORDER FAILED for %s — result: %s", market_id, result,
                    )
                    return  # Don't record position if order failed
            except Exception:
                self.logger.exception("LIVE ORDER EXCEPTION for %s", market_id)
                return  # Don't record position if order threw

        # Stop loss from per-category profile or engine config
        if cat_profile:
            stop_loss_pct = float(cat_profile.get("stop_loss_pct", risk.get("per_market_stop_loss_pct", 0.35)))
        else:
            stop_loss_pct = float(risk.get("per_market_stop_loss_pct", 0.35))
        stop_loss = entry_price * (1.0 - stop_loss_pct)

        # ── Write Position ────────────────────────────────────────────
        await self.db.execute(
            """INSERT INTO positions
               (market_id, platform, engine, side, size, entry_price,
                current_price, stop_loss, status, signal_id, thesis)
               VALUES (?, 'kalshi', ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)""",
            (
                market_id, engine, side, float(size_contracts), entry_price,
                current_price, stop_loss, signal["id"],
                f"Signal #{signal['id']}: {signal['signal_type']} (conf={confidence:.2f})",
            ),
        )

        # ── Write Execution ───────────────────────────────────────────
        # Sprint 22: Record actual order_type used (not hardcoded "market")
        # Sprint 29: Fee accounting — record per-execution fee for accurate P&L.
        exec_order_type = "paper" if self._mode == "paper" else order_type
        fee_rate = 0.007  # Default: 0.7% per side
        if cat_profile:
            fee_rate = float(cat_profile.get("fee_per_contract", 0.014)) / 2.0
        buy_fee = float(size_contracts) * fee_rate

        await self.db.execute(
            """INSERT INTO executions
               (signal_id, engine, platform, order_id, side, fill_price, size, order_type, fee_amount)
               VALUES (?, ?, 'kalshi', ?, 'BUY', ?, ?, ?, ?)""",
            (
                signal["id"], engine, order_id, entry_price,
                float(size_contracts), exec_order_type, buy_fee,
            ),
        )

        self.logger.info(
            "EXECUTED: %s %s %d contracts @ %.2f on %s (engine=%s, kelly=%.3f, corr=%.2f, fee=$%.4f)",
            side, signal["signal_type"], size_contracts, entry_price,
            market_id, engine, kelly_capped, corr_multiplier, buy_fee,
        )

    # ── Policy Exposure Helpers (Sprint 11) ────────────────────────────

    async def _get_category_exposure(self, engine: str, category: str) -> float:
        """Compute current exposure in a category as fraction of engine capital.

        Args:
            engine:   "SGE" or "ACE".
            category: Market category.

        Returns:
            Exposure as a fraction (0.0-1.0).
        """
        state = await self.db.fetchone(
            "SELECT total_capital FROM engine_state WHERE engine = ?", (engine,)
        )
        if not state or float(state["total_capital"]) <= 0:
            return 0.0

        total_capital = float(state["total_capital"])

        deployed = await self.db.fetchone(
            """SELECT COALESCE(SUM(p.size * p.entry_price), 0) as deployed
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.engine = ? AND p.status = 'OPEN' AND m.category = ?""",
            (engine, category),
        )
        deployed_amt = float(deployed["deployed"]) if deployed else 0.0
        return deployed_amt / total_capital

    # ── Dynamic Correlation Penalty ───────────────────────────────────

    async def _compute_correlation_multiplier(self, market_id: str) -> float:
        """Compute the correlation-adjusted sizing multiplier for a market.

        This method:
        1. Looks up the market's category from the DB.
        2. Counts how many existing open positions share that category.
        3. Retrieves the base correlation penalty from CategoryStrategyManager.
        4. Scales the penalty by current portfolio value vs starting balance:
           - Portfolio doubled → penalty halved (can afford more concentration)
           - Portfolio halved  → penalty doubled (force diversification under stress)
        5. Returns a multiplier in [0.10, 1.00] applied to position size.

        Args:
            market_id: The market being traded.

        Returns:
            Float multiplier for position sizing (1.0 = no reduction).
        """
        # ── Get market category ──────────────────────────────────────
        market_row = await self.db.fetchone(
            "SELECT category FROM markets WHERE id = ?", (market_id,)
        )
        category = market_row["category"] if market_row else None

        if not category or not self._category_mgr:
            return 1.0  # No penalty if category unknown or manager not loaded

        # ── Count existing open positions in this category ───────────
        count_row = await self.db.fetchone(
            """SELECT COUNT(*) as cnt
               FROM positions p
               JOIN markets m ON p.market_id = m.id
               WHERE p.status = 'OPEN' AND m.category = ?""",
            (category,),
        )
        existing_count = count_row["cnt"] if count_row else 0

        if existing_count == 0:
            return 1.0  # First position in this category — no penalty

        # ── Get base penalty from category strategy ──────────────────
        base_penalty = self._category_mgr.get_correlation_penalty(category)

        # ── Dynamic scaling based on portfolio value ─────────────────
        # Read current portfolio balance
        balance_row = await self.db.fetchone(
            "SELECT value FROM system_state WHERE key = 'portfolio_total_balance'"
        )
        current_balance = float(balance_row["value"]) if balance_row else self._starting_balance

        # Scale factor: clamp(current / starting, 0.5, 2.0)
        # - Portfolio at $1000 (doubled from $500) → scale=2.0 → penalty halved
        # - Portfolio at $250 (halved from $500)   → scale=0.5 → penalty doubled
        portfolio_scale = max(0.5, min(2.0, current_balance / self._starting_balance))

        # Effective penalty per existing position (inversely scaled by portfolio growth)
        effective_penalty = base_penalty / portfolio_scale

        # Final multiplier: 1.0 - (penalty × count), floored at 0.10
        multiplier = max(0.10, 1.0 - effective_penalty * existing_count)

        if multiplier < 1.0:
            self.logger.debug(
                "Correlation penalty for %s (%s): %d existing positions, "
                "base_penalty=%.3f, portfolio_scale=%.2f, multiplier=%.2f",
                market_id, category, existing_count,
                base_penalty, portfolio_scale, multiplier,
            )

        return multiplier
