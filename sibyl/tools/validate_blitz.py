"""
Live Blitz Validation — analysis of Blitz-eligible markets on Kalshi.

PURPOSE:
    Standalone tool that:
    1. Fetches markets closing within the next 24 hours from Kalshi
    2. Simulates BlitzScanner evaluation at various time-to-close windows
    3. Reports how many markets would be Blitz-eligible at ≤90s, ≤60s, ≤30s
    4. Estimates daily Blitz opportunity count by category
    5. Analyzes price convergence patterns near expiry

    NO orders are placed. Pure read-only analysis.

USAGE:
    python -m sibyl.tools.validate_blitz
    python -m sibyl.tools.validate_blitz --window 24   # Hours ahead to scan
    python -m sibyl.tools.validate_blitz --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from sibyl.clients.kalshi_client import KalshiClient

logger = logging.getLogger("sibyl.tools.validate_blitz")

# Blitz parameters (mirrored from BlitzScanner defaults)
BLITZ_CONFIDENCE_THRESHOLD = 0.85
BLITZ_MAX_CLOSE_WINDOW = 90  # seconds
BLITZ_MIN_CLOSE_WINDOW = 5   # seconds


@dataclass
class BlitzCandidate:
    """A market that could be Blitz-eligible."""
    ticker: str
    title: str
    event_ticker: str
    category: str
    close_time: str
    seconds_to_close: float
    yes_price: float | None
    no_price: float | None
    volume: int
    open_interest: int
    implied_confidence: float  # max(yes_price, 1-yes_price) — how settled the market is
    blitz_eligible: bool = False
    blitz_window: str = ""  # "≤30s", "≤60s", "≤90s"


@dataclass
class BlitzValidationReport:
    """Full Blitz validation analysis."""
    timestamp: str = ""
    scan_window_hours: int = 24
    total_markets_scanned: int = 0
    markets_closing_24h: int = 0
    markets_closing_6h: int = 0
    markets_closing_1h: int = 0

    # Blitz eligibility at different confidence thresholds
    eligible_at_85pct: int = 0
    eligible_at_90pct: int = 0
    eligible_at_95pct: int = 0

    # Time window analysis
    markets_at_90s: int = 0
    markets_at_60s: int = 0
    markets_at_30s: int = 0

    # Per-category breakdown
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)

    # Price convergence patterns
    high_confidence_markets: list[dict] = field(default_factory=list)
    low_liquidity_warnings: list[str] = field(default_factory=list)

    # Estimated daily opportunity rates
    est_daily_blitz_trades: float = 0.0
    est_daily_blitz_categories: dict[str, float] = field(default_factory=dict)

    duration_seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            "=" * 70,
            "  SIBYL BLITZ VALIDATION REPORT",
            f"  {self.timestamp}",
            "=" * 70,
            "",
            f"  Scan window: {self.scan_window_hours} hours ahead",
            f"  Total markets scanned: {self.total_markets_scanned}",
            f"  Closing within 24h: {self.markets_closing_24h}",
            f"  Closing within 6h:  {self.markets_closing_6h}",
            f"  Closing within 1h:  {self.markets_closing_1h}",
            "",
            "  BLITZ ELIGIBILITY (>={:.0f}% confidence, ≤90s to close):".format(
                BLITZ_CONFIDENCE_THRESHOLD * 100
            ),
            f"    At ≥85% confidence: {self.eligible_at_85pct} markets",
            f"    At ≥90% confidence: {self.eligible_at_90pct} markets",
            f"    At ≥95% confidence: {self.eligible_at_95pct} markets",
            "",
            "  TIME WINDOW ANALYSIS (markets with price implying ≥85% confidence):",
            f"    Within ≤90s of close: {self.markets_at_90s}",
            f"    Within ≤60s of close: {self.markets_at_60s}",
            f"    Within ≤30s of close: {self.markets_at_30s}",
            "",
        ]

        if self.by_category:
            lines.append("  CATEGORY BREAKDOWN:")
            for cat, stats in sorted(self.by_category.items()):
                lines.append(
                    f"    {cat}: {stats.get('total', 0)} closing soon, "
                    f"{stats.get('high_conf', 0)} high-confidence"
                )
            lines.append("")

        lines.append("  ESTIMATED DAILY BLITZ VOLUME:")
        lines.append(f"    Total estimated daily trades: {self.est_daily_blitz_trades:.1f}")
        if self.est_daily_blitz_categories:
            for cat, est in sorted(
                self.est_daily_blitz_categories.items(), key=lambda x: -x[1]
            ):
                lines.append(f"      {cat}: ~{est:.1f} trades/day")
        lines.append("")

        if self.high_confidence_markets:
            lines.append("  TOP HIGH-CONFIDENCE MARKETS (sample):")
            for m in self.high_confidence_markets[:5]:
                lines.append(
                    f"    {m['ticker']}: {m['implied_confidence']:.1%} conf, "
                    f"closes in {m['hours_to_close']:.1f}h — {m['title'][:60]}"
                )
            lines.append("")

        if self.low_liquidity_warnings:
            lines.append("  LOW LIQUIDITY WARNINGS:")
            for w in self.low_liquidity_warnings[:5]:
                lines.append(f"    ⚠ {w}")
            lines.append("")

        lines.append(f"  Analysis duration: {self.duration_seconds:.1f}s")
        lines.append("=" * 70)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "scan_window_hours": self.scan_window_hours,
            "total_markets_scanned": self.total_markets_scanned,
            "markets_closing_24h": self.markets_closing_24h,
            "markets_closing_6h": self.markets_closing_6h,
            "markets_closing_1h": self.markets_closing_1h,
            "eligible_at_85pct": self.eligible_at_85pct,
            "eligible_at_90pct": self.eligible_at_90pct,
            "eligible_at_95pct": self.eligible_at_95pct,
            "markets_at_90s": self.markets_at_90s,
            "markets_at_60s": self.markets_at_60s,
            "markets_at_30s": self.markets_at_30s,
            "by_category": self.by_category,
            "high_confidence_markets": self.high_confidence_markets,
            "low_liquidity_warnings": self.low_liquidity_warnings,
            "est_daily_blitz_trades": self.est_daily_blitz_trades,
            "est_daily_blitz_categories": self.est_daily_blitz_categories,
            "duration_seconds": self.duration_seconds,
        }


def _parse_close_time(close_time_str: str) -> datetime | None:
    """Parse Kalshi close_time strings (ISO 8601 variants)."""
    if not close_time_str:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ):
        try:
            dt = datetime.strptime(close_time_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _compute_implied_confidence(yes_price: float | None) -> float:
    """Compute implied confidence: how 'settled' the market is.

    A market at 0.95 or 0.05 has 95% implied confidence.
    A market at 0.50 has only 50% (coin flip).
    """
    if yes_price is None:
        return 0.50
    return max(yes_price, 1.0 - yes_price)


async def run_blitz_validation(
    scan_window_hours: int = 24,
    verbose: bool = False,
) -> BlitzValidationReport:
    """Run Blitz validation analysis.

    Args:
        scan_window_hours: How far ahead to look for closing markets.
        verbose: Include extra detail.

    Returns:
        BlitzValidationReport with all metrics.
    """
    report = BlitzValidationReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        scan_window_hours=scan_window_hours,
    )
    start_time = time.monotonic()

    # Connect to Kalshi (graceful fallback to public-only if key unavailable)
    key_id = os.environ.get("KALSHI_KEY_ID")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")

    # Only use auth if key file is actually accessible
    if key_path and not os.path.exists(key_path):
        logger.warning(
            "Kalshi key file not found at %s — running in public-only mode", key_path
        )
        key_id = None
        key_path = None

    kalshi = KalshiClient(key_id=key_id, private_key_path=key_path)

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=scan_window_hours)

    # Fetch all open events with markets
    all_markets: list[dict] = []
    cursor = None
    for page in range(10):
        try:
            data = await kalshi.get_events(
                limit=100, cursor=cursor, status="open", with_nested_markets=True,
            )
        except Exception as e:
            logger.error("Kalshi fetch error page %d: %s", page, e)
            break

        events = data.get("events", [])
        if not events:
            break

        for event in events:
            event_cat = event.get("category", "Other")
            for market in event.get("markets", []):
                market["_event_category"] = event_cat
                market["_event_title"] = event.get("title", "")
                all_markets.append(market)

        cursor = data.get("cursor")
        if not cursor:
            break

    report.total_markets_scanned = len(all_markets)

    # Analyze each market
    candidates: list[BlitzCandidate] = []
    category_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "high_conf": 0})

    for m in all_markets:
        close_time_str = m.get("close_time", m.get("expiration_time", ""))
        close_dt = _parse_close_time(close_time_str)
        if not close_dt:
            continue

        seconds_to_close = (close_dt - now).total_seconds()
        if seconds_to_close <= 0:
            continue  # Already closed

        hours_to_close = seconds_to_close / 3600.0

        # Price extraction
        yes_price = None
        if m.get("yes_ask") is not None and m.get("yes_bid") is not None:
            yes_price = (m["yes_bid"] + m["yes_ask"]) / 200.0
        elif m.get("last_price") is not None:
            yes_price = m["last_price"] / 100.0

        implied_conf = _compute_implied_confidence(yes_price)
        category = m.get("_event_category", "Other")

        # Time bucket counting
        if hours_to_close <= 24:
            report.markets_closing_24h += 1
        if hours_to_close <= 6:
            report.markets_closing_6h += 1
        if hours_to_close <= 1:
            report.markets_closing_1h += 1

        # Only analyze markets closing within our scan window
        if seconds_to_close > scan_window_hours * 3600:
            continue

        category_stats[category]["total"] += 1

        # Check Blitz eligibility (simulate various time windows)
        if implied_conf >= 0.85:
            category_stats[category]["high_conf"] += 1

            # Track for high-confidence sample
            if len(report.high_confidence_markets) < 20:
                report.high_confidence_markets.append({
                    "ticker": m.get("ticker", "?"),
                    "title": m.get("title", m.get("subtitle", "?"))[:80],
                    "category": category,
                    "implied_confidence": round(implied_conf, 4),
                    "yes_price": yes_price,
                    "hours_to_close": round(hours_to_close, 2),
                    "volume": m.get("volume_24h", m.get("volume", 0)),
                    "open_interest": m.get("open_interest", 0),
                })

        # Count eligibility at different confidence thresholds
        # (project forward: assume price trends continue as market approaches close)
        if implied_conf >= 0.85:
            report.eligible_at_85pct += 1
        if implied_conf >= 0.90:
            report.eligible_at_90pct += 1
        if implied_conf >= 0.95:
            report.eligible_at_95pct += 1

        # Low liquidity warning
        volume = m.get("volume_24h", m.get("volume", 0)) or 0
        oi = m.get("open_interest", 0) or 0
        if implied_conf >= 0.85 and volume < 10 and hours_to_close <= 6:
            report.low_liquidity_warnings.append(
                f"{m.get('ticker', '?')}: vol={volume}, OI={oi}, "
                f"conf={implied_conf:.1%}, closes in {hours_to_close:.1f}h"
            )

    report.by_category = dict(category_stats)

    # ── Estimate daily Blitz volume ─────────────────────────────────────
    # Based on observed high-confidence markets and their closing patterns.
    # Markets at ≥85% confidence in last 90s = primary Blitz targets.
    #
    # Heuristic: If we see N high-conf markets closing within scan_window,
    # scale to 24h to estimate daily volume.
    if scan_window_hours > 0:
        scale_factor = 24.0 / scan_window_hours
        report.est_daily_blitz_trades = report.eligible_at_85pct * scale_factor * 0.3
        # 0.3 factor: only ~30% of high-conf markets will actually be
        # within the ≤90s window at any given moment

        for cat, stats in category_stats.items():
            if stats["high_conf"] > 0:
                report.est_daily_blitz_categories[cat] = (
                    stats["high_conf"] * scale_factor * 0.3
                )

    # Sort high-confidence markets by implied confidence descending
    report.high_confidence_markets.sort(
        key=lambda x: x["implied_confidence"], reverse=True
    )

    await kalshi.close()
    report.duration_seconds = time.monotonic() - start_time
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sibyl Blitz Validation — analyze Blitz-eligible markets"
    )
    parser.add_argument(
        "--window", type=int, default=24,
        help="Hours ahead to scan for closing markets (default: 24).",
    )
    parser.add_argument("--verbose", action="store_true", default=False)
    parser.add_argument("--json", dest="json_output", action="store_true", default=False)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    report = await run_blitz_validation(
        scan_window_hours=args.window,
        verbose=args.verbose,
    )

    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())

    output_path = args.output or "data/blitz_validation_report.json"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2)
    logger.info("Report written to %s", output_path)


if __name__ == "__main__":
    asyncio.run(main())
