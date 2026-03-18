"""
Signal Router — dispatches scored signals to the appropriate trading engine.

PURPOSE:
    The Signal Generator creates PENDING signals in the database.  The Signal
    Router reads those signals and decides WHERE to send each one:
      - SGE (Stable Growth Engine) — conservative, patient, diversified
      - ACE (Alpha Capture Engine) — aggressive, concentrated, speed-focused
      - BOTH — high-conviction signals that both engines should act on
      - DEFERRED — signals that don't meet minimum thresholds

HOW ROUTING WORKS:

    1. READ: Fetch all signals with status = 'PENDING' from the database.

    2. FILTER: Check if the signal meets minimum confidence and EV thresholds.
       SGE requires: confidence ≥ 0.60, EV ≥ +3%
       ACE requires: confidence ≥ 0.68, EV ≥ +6%
       Signals below BOTH thresholds → DEFERRED.

    3. MATCH WHITELIST: Each engine has a `signal_whitelist` in its config:
       SGE accepts: ARBITRAGE, MEAN_REVERSION, LIQUIDITY_VACUUM
       ACE accepts: MOMENTUM, VOLUME_SURGE, STALE_MARKET, HIGH_CONVICTION_ARB
       If a signal type appears in BOTH whitelists → routed to BOTH.

    4. COMPOSITE HANDLING: COMPOSITE_HIGH_CONVICTION signals that exceed BOTH
       engines' thresholds are always routed to BOTH.

    5. UPDATE: Set `signals.routed_to` and `signals.status` in the database.
       PENDING → ROUTED (if matched) or DEFERRED (if filtered out).

CONFIGURATION:
    Routing rules are derived from:
      - config/sge_config.yaml → signal_whitelist + risk_policy
      - config/ace_config.yaml → signal_whitelist + risk_policy

DATA FLOW:
    signals table (status=PENDING)
        → SignalRouter.run_cycle() reads them
        → Applies thresholds + whitelist matching
        → Updates status to ROUTED or DEFERRED
        → (Sprint 3+) Position Lifecycle Manager picks up ROUTED signals
"""

from __future__ import annotations

import logging
from typing import Any

from sibyl.core.base_agent import BaseAgent
from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.agents.signal_router")


class SignalRouter(BaseAgent):
    """Routes PENDING signals to SGE, ACE, BOTH, or DEFERRED.

    This is the last agent in the Intelligence Layer pipeline.
    After routing, signals wait in the database for the Position Lifecycle
    Manager (Sprint 3) to pick them up and execute trades.

    Usage:
        router = SignalRouter(db=db, config=system_config)
        router.schedule()  # Runs in background
    """

    def __init__(self, db: DatabaseManager, config: dict[str, Any]) -> None:
        """Initialize with database and system config.

        Args:
            db:     Shared DatabaseManager.
            config: System config dict (from system_config.yaml).
        """
        super().__init__(name="signal_router", db=db, config=config)

        # Engine configurations — loaded in start()
        self._sge_config: dict[str, Any] = {}
        self._ace_config: dict[str, Any] = {}

        # Whitelists — which signal types each engine accepts
        self._sge_whitelist: set[str] = set()
        self._ace_whitelist: set[str] = set()

        # Threshold values (from engine risk policies)
        self._sge_min_confidence: float = 0.60
        self._sge_min_ev: float = 0.03
        self._ace_min_confidence: float = 0.68
        self._ace_min_ev: float = 0.06

    @property
    def poll_interval(self) -> float:
        """Run every 10 seconds — fast routing to minimize signal latency."""
        return 10.0

    async def start(self) -> None:
        """Load SGE and ACE engine configurations for routing decisions."""
        from sibyl.core.config import load_yaml

        try:
            self._sge_config = load_yaml("sge_config.yaml")
        except FileNotFoundError:
            self._sge_config = {}

        try:
            self._ace_config = load_yaml("ace_config.yaml")
        except FileNotFoundError:
            self._ace_config = {}

        # Extract signal whitelists
        self._sge_whitelist = set(self._sge_config.get("signal_whitelist", []))
        self._ace_whitelist = set(self._ace_config.get("signal_whitelist", []))

        # Extract risk policy thresholds
        sge_risk = self._sge_config.get("risk_policy", {})
        ace_risk = self._ace_config.get("risk_policy", {})
        self._sge_min_confidence = float(sge_risk.get("min_confidence", 0.60))
        self._sge_min_ev = float(sge_risk.get("min_ev_threshold", 0.03))
        self._ace_min_confidence = float(ace_risk.get("min_confidence", 0.68))
        self._ace_min_ev = float(ace_risk.get("min_ev_threshold", 0.06))

        self.logger.info(
            "Signal Router started (SGE whitelist=%s, ACE whitelist=%s)",
            self._sge_whitelist, self._ace_whitelist,
        )

    async def run_cycle(self) -> None:
        """Fetch PENDING signals and route each to the appropriate engine."""

        # Fetch all unrouted signals
        pending = await self.db.fetchall(
            """SELECT id, market_id, signal_type, confidence, ev_estimate,
                      detection_modes_triggered
               FROM signals
               WHERE status = 'PENDING'
               ORDER BY confidence DESC"""
        )

        if not pending:
            return  # Nothing to route

        routed_count = 0
        deferred_count = 0

        for signal in pending:
            signal_id = signal["id"]
            signal_type = signal["signal_type"]
            confidence = float(signal["confidence"])
            ev = float(signal["ev_estimate"] or 0)

            # ── Determine routing destination ─────────────────────────
            destination = self._route_signal(signal_type, confidence, ev)

            # ── Update the signal in the database ─────────────────────
            new_status = "DEFERRED" if destination == "DEFERRED" else "ROUTED"
            await self.db.execute(
                """UPDATE signals
                   SET routed_to = ?, status = ?
                   WHERE id = ?""",
                (destination, new_status, signal_id),
            )

            if destination == "DEFERRED":
                deferred_count += 1
            else:
                routed_count += 1
                self.logger.info(
                    "Signal #%d (%s, conf=%.2f, ev=%.3f) → %s",
                    signal_id, signal_type, confidence, ev, destination,
                )

        await self.db.commit()

        if routed_count or deferred_count:
            self.logger.info(
                "Routing complete: %d routed, %d deferred",
                routed_count, deferred_count,
            )

    async def stop(self) -> None:
        self.logger.info("Signal Router stopped")

    # ── Routing Logic ─────────────────────────────────────────────────

    def _route_signal(self, signal_type: str, confidence: float, ev: float) -> str:
        """Determine routing destination for a signal.

        Decision logic (in priority order):

        1. If signal doesn't meet ANY engine's minimum thresholds → DEFERRED.
        2. If signal type is on SGE's whitelist AND meets SGE thresholds → SGE.
        3. If signal type is on ACE's whitelist AND meets ACE thresholds → ACE.
        4. If it meets BOTH engines' thresholds and is on both whitelists → BOTH.
        5. COMPOSITE_HIGH_CONVICTION meeting both thresholds → always BOTH.
        6. If it meets one engine's thresholds but isn't on its whitelist,
           try the other engine.

        Args:
            signal_type: The classified signal type string.
            confidence:  Confidence score (0.0–1.0).
            ev:          Expected value estimate.

        Returns:
            "SGE", "ACE", "BOTH", or "DEFERRED".
        """
        # Check which engines' thresholds are met
        meets_sge = confidence >= self._sge_min_confidence and ev >= self._sge_min_ev
        meets_ace = confidence >= self._ace_min_confidence and ev >= self._ace_min_ev

        # If doesn't meet any threshold → defer
        if not meets_sge and not meets_ace:
            return "DEFERRED"

        # Check which engines' whitelists include this signal type
        on_sge_list = signal_type in self._sge_whitelist
        on_ace_list = signal_type in self._ace_whitelist

        # Composite high conviction → route to both if both thresholds met
        if signal_type == "COMPOSITE_HIGH_CONVICTION" and meets_sge and meets_ace:
            return "BOTH"

        # On both whitelists and meets both thresholds → BOTH
        if on_sge_list and on_ace_list and meets_sge and meets_ace:
            return "BOTH"

        # Matches SGE
        if on_sge_list and meets_sge:
            return "SGE"

        # Matches ACE
        if on_ace_list and meets_ace:
            return "ACE"

        # Meets thresholds but not on either whitelist — route to
        # whichever engine has the lower threshold (more permissive)
        if meets_sge:
            return "SGE"
        if meets_ace:
            return "ACE"

        return "DEFERRED"
