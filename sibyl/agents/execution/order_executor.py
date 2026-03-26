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

    @property
    def poll_interval(self) -> float:
        """Run every 3 seconds — minimal latency from signal to position."""
        return 3.0

    async def start(self) -> None:
        """Load engine risk policies, category strategies, policy engine, and Kalshi client."""
        import os
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

        # ── Initialize Kalshi client for LIVE order placement ────────────
        if self._mode == "live":
            from sibyl.clients.kalshi_client import KalshiClient
            key_id = os.environ.get("KALSHI_KEY_ID")
            key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
            if key_id and key_path:
                rate_limit = float(
                    self.config.get("platforms", {}).get("kalshi", {}).get(
                        "rate_limit_per_second", 8
                    )
                )
                self._kalshi_client = KalshiClient(
                    key_id=key_id,
                    private_key_path=key_path,
                    rate_limit=rate_limit,
                )
                self.logger.info("Kalshi client initialized for LIVE order placement")
            else:
                self.logger.error(
                    "LIVE mode requires KALSHI_KEY_ID + KALSHI_PRIVATE_KEY_PATH — "
                    "falling back to PAPER mode"
                )
                self._mode = "paper"

        self.logger.info("Order Executor started (mode=%s)", self._mode)

    async def run_cycle(self) -> None:
        """Process one ROUTED signal per cycle (oldest first)."""
        signal = await self.db.fetchone(
            """SELECT s.id, s.market_id, s.signal_type, s.confidence, s.ev_estimate,
                      s.routed_to, s.policy_tier, s.sports_sub_type, s.override_flag,
                      s.direction, s.detection_modes_triggered,
                      m.category
               FROM signals s
               LEFT JOIN markets m ON s.market_id = m.id
               WHERE s.status = 'ROUTED'
               ORDER BY s.timestamp ASC
               LIMIT 1"""
        )

        if not signal:
            return

        engine = signal["routed_to"]
        if engine == "DEFERRED":
            return  # Should not happen, but safety check

        # If routed to BOTH, execute for SGE first (conservative sizing)
        engines_to_execute = ["SGE", "ACE"] if engine == "BOTH" else [engine]

        for eng in engines_to_execute:
            await self._execute_for_engine(signal, eng)

        # Mark signal as executed
        await self.db.execute(
            "UPDATE signals SET status = 'EXECUTED' WHERE id = ?",
            (signal["id"],),
        )
        await self.db.commit()

    async def stop(self) -> None:
        """Graceful shutdown — close Kalshi client if open."""
        if self._kalshi_client:
            await self._kalshi_client.close()
        self.logger.info("Order Executor stopped")

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

        # ── Position Sizing (Kelly) ───────────────────────────────────
        # Sprint 20: Per-category profile overrides engine-level risk params
        if cat_profile:
            kelly_frac = float(cat_profile.get("kelly_fraction", risk.get("kelly_fraction", 0.15)))
            max_position_pct = float(cat_profile.get("max_position_pct", risk.get("max_single_position_pct", 0.02)))
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

        # Kelly: optimal fraction of bankroll to wager
        # For binary markets: kelly = (confidence × payout - (1-confidence)) / payout
        # Where payout = (1 / price) - 1 for YES bets
        payout = (1.0 / current_price) - 1.0 if current_price > 0 else 0
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

        # Position size in dollars (with correlation + policy adjustments)
        position_dollars = (
            min(kelly_capped, max_position_pct) * available
            * corr_multiplier * override_multiplier
        )
        if position_dollars < 1.0:
            return  # Too small to trade

        # ── Determine trade side from signal direction ─────────────────
        # Sprint 17 fix: use the pipeline's data-driven direction instead of
        # price-based heuristic.  The pipeline knows whether the market is
        # underpriced (buy YES) or overpriced (buy NO) based on real data.
        # Fallback: parse from detection_modes_triggered if direction column
        # is NULL (legacy signals before Sprint 17 migration).
        raw_direction = signal["direction"] if "direction" in signal.keys() and signal["direction"] else None
        if not raw_direction:
            # Legacy fallback: parse "DIR:YES" or "DIR:NO" from detection_modes_triggered
            dmt = signal["detection_modes_triggered"] if "detection_modes_triggered" in signal.keys() else ""
            if dmt and "DIR:" in (dmt or ""):
                raw_direction = dmt.split("DIR:")[1].split("|")[0].strip().upper()
        if not raw_direction or raw_direction not in ("YES", "NO"):
            # Final fallback: original price-based heuristic
            raw_direction = "YES" if current_price < 0.50 else "NO"
            self.logger.debug(
                "No direction for signal %s — using price-based fallback: %s",
                signal["id"], raw_direction,
            )

        side = raw_direction

        # Sprint 22: Spread-aware entry pricing.
        # Use best_ask for YES buys (cross the spread to fill), best_bid
        # complement for NO buys.  Falls back to mid-price if orderbook
        # data is unavailable.  This is the single biggest fill-rate fix —
        # limit orders at mid-price sit behind the spread and never fill.
        if side == "YES":
            if best_ask and 0 < best_ask < 1.0:
                entry_price = best_ask
                self.logger.debug(
                    "Using best_ask=%.4f for YES entry (mid=%.4f)", best_ask, current_price
                )
            else:
                entry_price = current_price
        else:
            # For NO: we buy NO contracts.  The price we pay = 1 - yes_bid.
            # Cross the spread by using best_bid (lower = more aggressive NO price).
            if best_bid and 0 < best_bid < 1.0:
                entry_price = 1.0 - best_bid
                self.logger.debug(
                    "Using 1-best_bid=%.4f for NO entry (mid=%.4f)", entry_price, 1.0 - current_price
                )
            else:
                entry_price = 1.0 - current_price

        size_contracts = int(position_dollars / entry_price) if entry_price > 0 else 0
        if size_contracts < 1:
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

                    # Sprint 22: Robust fill confirmation with polling loop.
                    # Poll up to 5 times (2s intervals = 10s max) to confirm fill.
                    # If confirmation fails entirely, do NOT record the position —
                    # prevents ghost trades (positions in DB with no Kalshi fill).
                    import asyncio
                    actual_fill_price: float | None = None
                    fill_confirmed = order_status in ("executed", "filled")

                    if not fill_confirmed:
                        for attempt in range(5):
                            await asyncio.sleep(2)
                            try:
                                confirm = await self._kalshi_client.get_order(order_id)
                                if confirm and "order" in confirm:
                                    confirmed_status = confirm["order"].get("status", "unknown")
                                    remaining = confirm["order"].get("remaining_count", 0)
                                    self.logger.info(
                                        "ORDER CONFIRM [%d/5]: %s status=%s remaining=%s",
                                        attempt + 1, order_id, confirmed_status, remaining,
                                    )
                                    if confirmed_status in ("executed", "filled"):
                                        fill_confirmed = True
                                        # Extract actual fill price from response
                                        avg_price = confirm["order"].get("average_fill_price")
                                        if avg_price is not None:
                                            actual_fill_price = float(avg_price) / 100.0
                                        break
                                    elif confirmed_status in ("canceled", "expired"):
                                        self.logger.error(
                                            "ORDER REJECTED: %s was %s — not recording position",
                                            order_id, confirmed_status,
                                        )
                                        return
                                    # else: still "resting" or "pending" — keep polling
                            except Exception as e:
                                self.logger.warning(
                                    "Fill confirm attempt %d/5 failed for %s: %s",
                                    attempt + 1, order_id, e,
                                )

                    if not fill_confirmed:
                        # After 10 seconds, order hasn't filled — cancel it
                        self.logger.error(
                            "ORDER NOT FILLED after 10s: %s — cancelling and aborting. "
                            "No phantom position will be recorded.",
                            order_id,
                        )
                        try:
                            await self._kalshi_client.cancel_order(order_id)
                            self.logger.info("Cancelled unfilled order: %s", order_id)
                        except Exception:
                            self.logger.warning("Could not cancel order %s", order_id)
                        return

                    # Use actual fill price if available, otherwise keep entry_price
                    if actual_fill_price and actual_fill_price > 0:
                        self.logger.info(
                            "FILL PRICE: requested=%.4f actual=%.4f (slippage=%.1fbps)",
                            entry_price, actual_fill_price,
                            abs(actual_fill_price - entry_price) * 10000,
                        )
                        entry_price = actual_fill_price
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
        exec_order_type = "paper" if self._mode == "paper" else order_type
        await self.db.execute(
            """INSERT INTO executions
               (signal_id, engine, platform, order_id, side, fill_price, size, order_type)
               VALUES (?, ?, 'kalshi', ?, 'BUY', ?, ?, ?)""",
            (
                signal["id"], engine, order_id, entry_price,
                float(size_contracts), exec_order_type,
            ),
        )

        self.logger.info(
            "EXECUTED: %s %s %d contracts @ %.2f on %s (engine=%s, kelly=%.3f, corr=%.2f)",
            side, signal["signal_type"], size_contracts, entry_price,
            market_id, engine, kelly_capped, corr_multiplier,
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
