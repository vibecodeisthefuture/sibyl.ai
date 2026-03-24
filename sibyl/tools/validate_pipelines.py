"""
Live Pipeline Validation — read-only validation of all 8 category pipelines
against real Kalshi market data.

PURPOSE:
    One-shot validation tool that:
    1. Connects to the real Kalshi API (read-only) via KalshiClient
    2. Fetches all active events with nested markets
    3. Categorizes markets into the 8 Sibyl pipeline categories
    4. Initializes each pipeline and runs analysis
    5. Reports signal quality metrics per pipeline

    NO orders are placed. This is pure read-only analysis.

USAGE:
    python -m sibyl.tools.validate_pipelines              # All pipelines
    python -m sibyl.tools.validate_pipelines --categories crypto,sports
    python -m sibyl.tools.validate_pipelines --verbose     # Debug output
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from sibyl.clients.kalshi_client import KalshiClient
from sibyl.core.database import DatabaseManager
from sibyl.core.config import SibylConfig
from sibyl.pipelines.pipeline_manager import PipelineManager, PipelineRunResult
from sibyl.pipelines.base_pipeline import PipelineSignal

logger = logging.getLogger("sibyl.tools.validate_pipelines")


# ── Kalshi category → Sibyl pipeline mapping ─────────────────────────────
# Actual Kalshi API categories (discovered via live enumeration 2026-03-22):
#   Elections (663), Politics (116), Entertainment (70), Economics (62),
#   Sports (40), Science and Technology (11), Companies (11),
#   Climate and Weather (10), Social (6), Health (4), World (3),
#   Financials (3), Transportation (1)
#
# Map each to one of 8 Sibyl pipelines.
CATEGORY_MAP: dict[str, str] = {
    # ── Direct Kalshi category matches ────────────────────────────────
    "Economics": "economics",
    "Financials": "financial",
    "Companies": "financial",
    "Climate and Weather": "weather",
    "Sports": "sports",
    "Entertainment": "culture",
    "Social": "culture",
    "Mentions": "culture",
    "Science and Technology": "science",
    "Health": "science",
    "Elections": "geopolitics",
    "Politics": "geopolitics",
    "World": "geopolitics",
    "Transportation": "geopolitics",
    # ── Fallback partial matches (for future categories) ──────────────
    "Fed": "economics",
    "Econ": "economics",
    "Financial": "financial",
    "Finance": "financial",
    "Stocks": "financial",
    "Weather": "weather",
    "Climate": "weather",
    "NFL": "sports",
    "NBA": "sports",
    "MLB": "sports",
    "Crypto": "crypto",
    "Bitcoin": "crypto",
    "Culture": "culture",
    "Science": "science",
    "FDA": "science",
    "Congress": "geopolitics",
    "Supreme Court": "geopolitics",
    "Legal": "geopolitics",
}


@dataclass
class PipelineValidationResult:
    """Validation metrics for a single pipeline."""
    pipeline_name: str
    markets_available: int = 0
    markets_with_price: int = 0
    signals_generated: int = 0
    signals_by_type: dict[str, int] = field(default_factory=dict)
    confidence_distribution: dict[str, int] = field(default_factory=dict)
    avg_confidence: float = 0.0
    avg_ev: float = 0.0
    max_confidence: float = 0.0
    max_ev: float = 0.0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    sample_signals: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"  {self.pipeline_name.upper()} Pipeline",
            f"    Markets available: {self.markets_available} ({self.markets_with_price} with price)",
            f"    Signals generated: {self.signals_generated}",
            f"    Duration: {self.duration_seconds:.1f}s",
        ]
        if self.signals_generated > 0:
            lines.extend([
                f"    Avg confidence: {self.avg_confidence:.3f}",
                f"    Max confidence: {self.max_confidence:.3f}",
                f"    Avg EV: {self.avg_ev:.4f}",
                f"    Max EV: {self.max_ev:.4f}",
            ])
            if self.signals_by_type:
                type_str = ", ".join(f"{k}: {v}" for k, v in sorted(self.signals_by_type.items()))
                lines.append(f"    Signal types: {type_str}")
            if self.confidence_distribution:
                dist_str = ", ".join(
                    f"{k}: {v}" for k, v in sorted(self.confidence_distribution.items())
                )
                lines.append(f"    Confidence dist: {dist_str}")
        if self.errors:
            lines.append(f"    ERRORS: {len(self.errors)}")
            for err in self.errors[:3]:
                lines.append(f"      - {err[:100]}")
        return "\n".join(lines)


@dataclass
class ValidationReport:
    """Full validation report across all pipelines."""
    timestamp: str = ""
    total_kalshi_events: int = 0
    total_kalshi_markets: int = 0
    categorized_markets: int = 0
    uncategorized_markets: int = 0
    pipeline_results: dict[str, PipelineValidationResult] = field(default_factory=dict)
    total_signals: int = 0
    total_duration_seconds: float = 0.0
    kalshi_fetch_duration: float = 0.0
    db_seed_duration: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 70,
            "  SIBYL LIVE PIPELINE VALIDATION REPORT",
            f"  {self.timestamp}",
            "=" * 70,
            "",
            f"  Kalshi Data: {self.total_kalshi_events} events, {self.total_kalshi_markets} markets",
            f"  Categorized: {self.categorized_markets} | Uncategorized: {self.uncategorized_markets}",
            f"  Kalshi fetch: {self.kalshi_fetch_duration:.1f}s | DB seed: {self.db_seed_duration:.1f}s",
            "",
            "-" * 70,
        ]

        for name in sorted(self.pipeline_results.keys()):
            result = self.pipeline_results[name]
            lines.append(result.summary())
            lines.append("")

        lines.append("-" * 70)
        lines.append(f"  TOTAL SIGNALS: {self.total_signals}")
        lines.append(f"  TOTAL DURATION: {self.total_duration_seconds:.1f}s")
        lines.append("")

        # Flags / warnings
        for name, result in sorted(self.pipeline_results.items()):
            if result.signals_generated == 0:
                lines.append(f"  ⚠ WARNING: {name} pipeline generated 0 signals")
            elif result.signals_generated > 50:
                lines.append(f"  ⚠ WARNING: {name} pipeline generated {result.signals_generated} signals (potential false positive flood)")
            if result.errors:
                lines.append(f"  ⚠ WARNING: {name} pipeline had {len(result.errors)} error(s)")

        for w in self.warnings:
            lines.append(f"  ⚠ {w}")

        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """JSON-serializable dict."""
        return {
            "timestamp": self.timestamp,
            "kalshi_events": self.total_kalshi_events,
            "kalshi_markets": self.total_kalshi_markets,
            "categorized": self.categorized_markets,
            "uncategorized": self.uncategorized_markets,
            "total_signals": self.total_signals,
            "total_duration_seconds": self.total_duration_seconds,
            "pipelines": {
                name: {
                    "markets_available": r.markets_available,
                    "signals_generated": r.signals_generated,
                    "avg_confidence": r.avg_confidence,
                    "avg_ev": r.avg_ev,
                    "max_confidence": r.max_confidence,
                    "max_ev": r.max_ev,
                    "signals_by_type": r.signals_by_type,
                    "confidence_distribution": r.confidence_distribution,
                    "errors": r.errors,
                    "duration_seconds": r.duration_seconds,
                    "sample_signals": r.sample_signals,
                }
                for name, r in sorted(self.pipeline_results.items())
            },
            "warnings": self.warnings,
        }


def _classify_category(event_category: str | None, event_title: str) -> str:
    """Map a Kalshi event category/title to a Sibyl pipeline name.

    Returns 'uncategorized' if no match.

    NOTE: Crypto keywords are checked FIRST against the title because
    Kalshi lists crypto markets under "Financials" or "Economics" events.
    Without this priority check, crypto markets would be classified under
    the parent event's category and the crypto pipeline would see nothing.
    """
    title_lower = (event_title or "").lower()

    # ── Priority 1: Crypto keyword match on title ────────────────────
    # Checked before event category because Kalshi has no "Crypto" category.
    CRYPTO_KEYWORDS = {
        "btc", "bitcoin", "eth", "ethereum", "sol", "solana",
        "xrp", "doge", "dogecoin", "bnb", "hype", "fdv", "crypto",
    }
    for kw in CRYPTO_KEYWORDS:
        if kw in title_lower:
            return "crypto"

    # ── Priority 2: Event-level Kalshi category ──────────────────────
    if event_category:
        # Direct match
        if event_category in CATEGORY_MAP:
            return CATEGORY_MAP[event_category]
        # Case-insensitive substring match
        cat_lower = event_category.lower()
        for key, pipeline in CATEGORY_MAP.items():
            if key.lower() in cat_lower:
                return pipeline

    # ── Priority 3: General keyword match on title ───────────────────
    keyword_map = {
        # Economics
        "fed": "economics", "rate": "economics", "inflation": "economics",
        "cpi": "economics", "gdp": "economics", "unemployment": "economics",
        "jobs": "economics", "nonfarm": "economics", "payroll": "economics",
        "pce": "economics", "jobless": "economics",
        # Weather
        "weather": "weather", "temperature": "weather", "hurricane": "weather",
        "snow": "weather", "rain": "weather", "tornado": "weather",
        "highest temp": "weather", "lowest temp": "weather",
        # Sports (expanded for gap-fill)
        "nfl": "sports", "nba": "sports", "mlb": "sports", "nhl": "sports",
        "soccer": "sports", "ufc": "sports", "tennis": "sports",
        "super bowl": "sports", "march madness": "sports",
        "ncaa": "sports", "pga": "sports", "golf": "sports",
        "nascar": "sports", "f1": "sports", "formula 1": "sports",
        "cs2": "sports", "esports": "sports", "counter-strike": "sports",
        # Culture (expanded for gap-fill)
        "oscar": "culture", "grammy": "culture", "emmy": "culture",
        "box office": "culture", "streaming": "culture",
        "billboard": "culture", "hot 100": "culture",
        "american idol": "culture", "bachelor": "culture",
        "baby name": "culture", "mention": "culture",
        # Science (expanded for gap-fill)
        "fda": "science", "nasa": "science", "spacex": "science",
        "covid": "science", "vaccine": "science",
        "ai ": "science", "artificial intelligence": "science",
        "chatgpt": "science", "openai": "science",
        "rolex": "science", "gpu": "science", "nvidia": "science",
        "nuclear": "science", "fusion": "science",
        # Geopolitics
        "trump": "geopolitics", "biden": "geopolitics", "congress": "geopolitics",
        "election": "geopolitics", "senate": "geopolitics", "house": "geopolitics",
        "supreme court": "geopolitics", "war": "geopolitics",
        # Financial (expanded for gap-fill)
        "stock": "financial", "s&p": "financial", "nasdaq": "financial",
        "earnings": "financial", "ipo": "financial", "treasury": "financial",
        "gold": "financial", "silver": "financial", "copper": "financial",
        "oil": "financial", "crude": "financial", "wti": "financial",
        "dow": "financial", "russell": "financial", "vix": "financial",
        "forex": "financial", "eur/usd": "financial",
    }
    for kw, pipeline in keyword_map.items():
        if kw in title_lower:
            return pipeline

    return "uncategorized"


def _confidence_bucket(conf: float) -> str:
    """Bucket confidence scores for distribution reporting."""
    if conf >= 0.90:
        return "0.90-1.00"
    elif conf >= 0.80:
        return "0.80-0.89"
    elif conf >= 0.70:
        return "0.70-0.79"
    elif conf >= 0.60:
        return "0.60-0.69"
    else:
        return "0.50-0.59"


async def fetch_all_kalshi_markets(
    kalshi: KalshiClient,
    max_pages: int = 10,
    target_categories: set[str] | None = None,
    gap_fill: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Fetch active events and flatten their markets with category filtering.

    Args:
        gap_fill: If True, run a supplementary pass to discover events beyond
                  the main pagination window. Set to False after the first cycle
                  to speed up subsequent fetches (~7s vs ~600s).

    Two-pass approach for speed:
      1. Fetch events WITHOUT nested markets (fast — small payloads)
      2. Classify each event by category, skip irrelevant ones
      3. Only fetch nested markets for events in target categories

    If target_categories is None, fetches everything (legacy behavior).

    Args:
        kalshi: Initialized KalshiClient.
        max_pages: Max pagination pages.
        target_categories: Sibyl pipeline names to include (e.g., {"crypto", "sports"}).
                          None means all categories.

    Returns:
        (events_list, markets_list) — each market dict includes parent event info.
    """
    all_events: list[dict] = []
    all_markets: list[dict] = []

    if target_categories is not None:
        # ── FAST PATH: Two-pass category-filtered fetch ──────────────────
        # Pass 1: Fetch event metadata only (no nested markets)
        logger.info("Category-filtered fetch: targeting %s", target_categories)
        matching_event_tickers: list[str] = []
        cursor = None
        events_scanned = 0

        for page in range(max_pages):
            try:
                data = await kalshi.get_events(
                    limit=100,
                    cursor=cursor,
                    status="open",
                    with_nested_markets=False,
                )
            except Exception as e:
                logger.error("Failed to fetch events (page %d): %s", page, e)
                break

            events = data.get("events", [])
            if not events:
                break

            for event in events:
                events_scanned += 1
                event_category = event.get("category", "")
                event_title = event.get("title", "")
                sibyl_cat = _classify_category(event_category, event_title)

                if sibyl_cat in target_categories:
                    all_events.append(event)
                    matching_event_tickers.append(event.get("event_ticker", ""))

            cursor = data.get("cursor")
            if not cursor:
                break

        logger.info(
            "Pass 1: scanned %d events, %d match target categories",
            events_scanned, len(matching_event_tickers),
        )

        # Pass 2: Fetch markets for matching events via get_markets(event_ticker=...)
        # The single-event endpoint doesn't return nested markets, so we use
        # the markets endpoint filtered by event_ticker instead.
        event_info = {
            e.get("event_ticker", ""): e for e in all_events
        }
        for ticker in matching_event_tickers:
            if not ticker:
                continue
            try:
                data = await kalshi.get_markets(
                    event_ticker=ticker, limit=100, status="open",
                )
                markets_list = data.get("markets", [])
            except Exception as e:
                logger.warning("Failed to fetch markets for event %s: %s", ticker, e)
                continue

            event = event_info.get(ticker, {})
            event_title = event.get("title", "")
            event_category = event.get("category", "")

            for market in markets_list:
                market["_event_ticker"] = ticker
                market["_event_title"] = event_title
                market["_event_category"] = event_category
                market["_sibyl_category"] = _classify_category(
                    event_category, event_title
                )
                all_markets.append(market)

        logger.info(
            "Pass 2: fetched %d markets from %d matching events",
            len(all_markets), len(matching_event_tickers),
        )

    else:
        # ── LEGACY PATH: Fetch everything with nested markets ────────────
        cursor = None
        for page in range(max_pages):
            try:
                data = await kalshi.get_events(
                    limit=100,
                    cursor=cursor,
                    status="open",
                    with_nested_markets=True,
                )
            except Exception as e:
                logger.error("Failed to fetch Kalshi events (page %d): %s", page, e)
                break

            events = data.get("events", [])
            if not events:
                break

            for event in events:
                all_events.append(event)
                event_ticker = event.get("event_ticker", "")
                event_title = event.get("title", "")
                event_category = event.get("category", "")

                for market in event.get("markets", []):
                    market["_event_ticker"] = event_ticker
                    market["_event_title"] = event_title
                    market["_event_category"] = event_category
                    market["_sibyl_category"] = _classify_category(
                        event_category, event_title
                    )
                    all_markets.append(market)

            cursor = data.get("cursor")
            if not cursor:
                break

    if not gap_fill:
        return all_events, all_markets

    # ── GAP-FILL PASS: Discover events beyond the pagination window ─────
    # The main fetch (with_nested_markets=True) is limited to ~1500 events
    # due to payload size. Many daily markets (weather, sports, crypto,
    # economics, financials) fall beyond that window. We do a lightweight
    # scan WITHOUT nested markets to discover ALL open events, classify
    # them, and then fetch markets only for the high-value ones our
    # pipelines can actually act on.
    #
    # Priority categories — pipelines that actively generate signals:
    _GAP_FILL_PRIORITY_PIPELINES = {
        "weather", "crypto", "economics", "financial",
        "culture", "science", "sports",
    }

    seen_tickers = {e.get("event_ticker", "") for e in all_events}
    gap_new_events: list[dict] = []
    cursor = None

    logger.info("Gap-fill pass: scanning for events beyond main fetch window...")
    for page in range(50):  # Up to 10K events at 200/page
        try:
            data = await kalshi.get_events(
                limit=200, cursor=cursor, status="open",
                with_nested_markets=False,
            )
        except Exception as e:
            logger.warning("Gap-fill scan page %d failed: %s", page, e)
            break

        events = data.get("events", [])
        if not events:
            break

        for event in events:
            eticker = event.get("event_ticker", "")
            if eticker and eticker not in seen_tickers:
                seen_tickers.add(eticker)
                # Classify to decide if this event is worth fetching markets for
                ecat = event.get("category", "")
                etitle = event.get("title", "")
                sibyl_cat = _classify_category(ecat, etitle)
                if sibyl_cat in _GAP_FILL_PRIORITY_PIPELINES:
                    gap_new_events.append(event)
                    all_events.append(event)

        cursor = data.get("cursor")
        if not cursor:
            break

    if gap_new_events:
        logger.info(
            "Gap-fill scan: found %d priority events beyond main window",
            len(gap_new_events),
        )

        # Fetch markets for new events with concurrency control.
        # We use a semaphore to limit concurrent API calls and avoid 429s.
        import asyncio as _aio

        gap_extra_markets = 0
        gap_market_buf: list[dict] = []
        sem = _aio.Semaphore(5)  # Max 5 concurrent market fetches

        async def _fetch_event_markets(event: dict) -> list[dict]:
            eticker = event.get("event_ticker", "")
            if not eticker:
                return []
            async with sem:
                await _aio.sleep(0.15)  # ~7 rps effective rate
                try:
                    mdata = await kalshi.get_markets(
                        event_ticker=eticker, limit=100, status="open",
                    )
                    return mdata.get("markets", [])
                except Exception as e:
                    logger.warning("Gap-fill markets for %s failed: %s", eticker, e)
                    return []

        # Process in batches of 50 events
        batch_size = 50
        for batch_start in range(0, len(gap_new_events), batch_size):
            batch = gap_new_events[batch_start:batch_start + batch_size]
            tasks = [_fetch_event_markets(ev) for ev in batch]
            results = await _aio.gather(*tasks)

            for event, mkt_list in zip(batch, results):
                eticker = event.get("event_ticker", "")
                ecat = event.get("category", "")
                etitle = event.get("title", "")
                for market in mkt_list:
                    market["_event_ticker"] = eticker
                    market["_event_title"] = etitle
                    market["_event_category"] = ecat
                    market["_sibyl_category"] = _classify_category(ecat, etitle)
                    gap_market_buf.append(market)
                    gap_extra_markets += 1

            if (batch_start + batch_size) % 200 == 0:
                logger.info(
                    "Gap-fill progress: %d/%d events processed, %d markets",
                    min(batch_start + batch_size, len(gap_new_events)),
                    len(gap_new_events), gap_extra_markets,
                )

        all_markets.extend(gap_market_buf)
        logger.info(
            "Gap-fill complete: added %d events, %d markets",
            len(gap_new_events), gap_extra_markets,
        )
    else:
        logger.info("Gap-fill: no additional events found")

    return all_events, all_markets


async def seed_markets_to_db(
    db: DatabaseManager,
    markets: list[dict],
) -> int:
    """Write fetched Kalshi markets into the markets table for pipeline analysis.

    This is a temporary seed — pipelines read from the markets table.
    Returns count of markets written.
    """
    written = 0
    for m in markets:
        ticker = m.get("ticker", "")
        if not ticker:
            continue

        # Extract price — Kalshi v2 API uses _dollars suffix (already in decimal)
        # and _fp suffix for volume/OI (floating point strings).
        yes_price = None
        yes_ask = m.get("yes_ask_dollars") or m.get("yes_ask")
        yes_bid = m.get("yes_bid_dollars") or m.get("yes_bid")
        last_price = m.get("last_price_dollars") or m.get("last_price")

        if yes_ask is not None and yes_bid is not None:
            try:
                ask_f = float(yes_ask)
                bid_f = float(yes_bid)
                if ask_f > 0 or bid_f > 0:
                    yes_price = (bid_f + ask_f) / 2.0
            except (ValueError, TypeError):
                pass

        if yes_price is None and last_price is not None:
            try:
                lp = float(last_price)
                if lp > 0:
                    yes_price = lp
            except (ValueError, TypeError):
                pass

        # Volume and OI — _fp fields are decimal strings
        volume_raw = m.get("volume_24h_fp") or m.get("volume_24h") or m.get("volume_fp") or 0
        oi_raw = m.get("open_interest_fp") or m.get("open_interest") or 0
        try:
            volume = int(float(volume_raw))
        except (ValueError, TypeError):
            volume = 0
        try:
            oi = int(float(oi_raw))
        except (ValueError, TypeError):
            oi = 0

        try:
            await db.execute(
                """INSERT OR REPLACE INTO markets
                   (id, title, category, status, platform, close_date,
                    event_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticker,
                    m.get("title", m.get("subtitle", ticker)),
                    m.get("_sibyl_category", "uncategorized"),
                    "active",
                    "kalshi",
                    m.get("close_time", m.get("expiration_time", "")),
                    m.get("_event_ticker", ""),
                ),
            )

            # Also seed a price row so pipelines can look up market prices
            if yes_price is not None:
                await db.execute(
                    """INSERT INTO prices (market_id, yes_price, no_price, volume_24h, open_interest)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        ticker,
                        yes_price,
                        1.0 - yes_price if yes_price else None,
                        volume,
                        oi,
                    ),
                )

            written += 1
        except Exception as e:
            logger.debug("Failed to seed market %s: %s", ticker, e)

    if written:
        await db.commit()
    return written


async def run_validation(
    categories_filter: set[str] | None = None,
    verbose: bool = False,
) -> ValidationReport:
    """Run the full live pipeline validation.

    Args:
        categories_filter: If set, only validate these pipelines.
        verbose: If True, include sample signals in output.

    Returns:
        ValidationReport with all metrics.
    """
    report = ValidationReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )

    overall_start = time.monotonic()

    # ── Step 1: Connect to Kalshi (read-only, graceful fallback) ────────
    key_id = os.environ.get("KALSHI_KEY_ID")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")

    # Only use auth if key file is actually accessible
    if key_path and not os.path.exists(key_path):
        logger.warning(
            "Kalshi key file not found at %s — running in public-only mode", key_path
        )
        key_id = None
        key_path = None

    kalshi = KalshiClient(
        key_id=key_id,
        private_key_path=key_path,
    )
    logger.info(
        "Kalshi client ready (authenticated=%s)", kalshi.is_authenticated
    )

    # ── Step 2: Fetch active markets (category-filtered if specified) ───
    fetch_start = time.monotonic()
    events, markets = await fetch_all_kalshi_markets(
        kalshi, target_categories=categories_filter,
    )
    report.kalshi_fetch_duration = time.monotonic() - fetch_start

    report.total_kalshi_events = len(events)
    report.total_kalshi_markets = len(markets)

    # Count categorization
    category_counts: dict[str, int] = defaultdict(int)
    for m in markets:
        cat = m.get("_sibyl_category", "uncategorized")
        category_counts[cat] += 1

    report.categorized_markets = sum(
        v for k, v in category_counts.items() if k != "uncategorized"
    )
    report.uncategorized_markets = category_counts.get("uncategorized", 0)

    logger.info(
        "Fetched %d events, %d markets (categorized: %d, uncategorized: %d)",
        len(events), len(markets),
        report.categorized_markets, report.uncategorized_markets,
    )

    for cat, count in sorted(category_counts.items()):
        logger.info("  %s: %d markets", cat, count)

    # ── Step 3: Initialize DB and seed markets ──────────────────────────
    # Prefer DATABASE_PATH env var; fall back to SibylConfig; then default.
    # NOTE: aiosqlite WAL mode may fail on mounted/network filesystems,
    # so we default to /tmp for validation runs (ephemeral data only).
    db_path = os.environ.get("DATABASE_PATH", "")
    if not db_path:
        try:
            config = SibylConfig()
            db_path = config.db_path
        except Exception:
            db_path = "data/sibyl.db"
    # Resolve relative paths against the project root
    if not os.path.isabs(db_path):
        db_path = os.path.join(project_root, db_path)
    # If the resolved path is on a mounted filesystem and /tmp is available,
    # use /tmp to avoid WAL mode disk I/O issues.
    if "/mnt/" in db_path and os.path.isdir("/tmp"):
        db_path = "/tmp/sibyl_validation.db"
    db = DatabaseManager(db_path)
    await db.initialize()

    seed_start = time.monotonic()
    seeded = await seed_markets_to_db(db, markets)
    report.db_seed_duration = time.monotonic() - seed_start
    logger.info("Seeded %d markets to DB in %.1fs", seeded, report.db_seed_duration)

    # ── Step 4: Initialize and run each pipeline ────────────────────────
    manager = PipelineManager(db)
    ready_count = await manager.initialize()
    logger.info("PipelineManager initialized: %d pipelines ready", ready_count)

    # Run all pipelines through the manager
    pipeline_start = time.monotonic()
    run_result: PipelineRunResult = await manager.run_all()
    pipeline_duration = time.monotonic() - pipeline_start

    # ── Step 5: Collect metrics per pipeline ────────────────────────────
    all_pipelines = [
        "economics", "weather", "sports", "crypto",
        "culture", "science", "geopolitics", "financial",
    ]

    for pname in all_pipelines:
        if categories_filter and pname not in categories_filter:
            continue

        pvr = PipelineValidationResult(pipeline_name=pname)
        pvr.markets_available = category_counts.get(pname, 0)

        # Count markets with a non-zero price (Kalshi v2 uses _dollars suffix)
        def _has_price(m: dict) -> bool:
            for key in ("yes_ask_dollars", "yes_bid_dollars", "last_price_dollars",
                        "yes_ask", "yes_bid", "last_price"):
                val = m.get(key)
                if val is not None:
                    try:
                        if float(val) > 0:
                            return True
                    except (ValueError, TypeError):
                        pass
            return False

        pvr.markets_with_price = sum(
            1 for m in markets
            if m.get("_sibyl_category") == pname and _has_price(m)
        )

        signals = run_result.signals_by_pipeline.get(pname, [])
        pvr.signals_generated = len(signals)

        if signals:
            confs = [s.confidence for s in signals]
            evs = [s.ev_estimate for s in signals]
            pvr.avg_confidence = sum(confs) / len(confs)
            pvr.max_confidence = max(confs)
            pvr.avg_ev = sum(evs) / len(evs)
            pvr.max_ev = max(evs)

            # Signal type distribution
            for s in signals:
                pvr.signals_by_type[s.signal_type] = pvr.signals_by_type.get(s.signal_type, 0) + 1

            # Confidence distribution
            for s in signals:
                bucket = _confidence_bucket(s.confidence)
                pvr.confidence_distribution[bucket] = pvr.confidence_distribution.get(bucket, 0) + 1

            # Sample signals (top 3 by confidence)
            if verbose:
                sorted_sigs = sorted(signals, key=lambda s: s.confidence, reverse=True)
                for s in sorted_sigs[:3]:
                    pvr.sample_signals.append({
                        "market_id": s.market_id,
                        "signal_type": s.signal_type,
                        "confidence": round(s.confidence, 4),
                        "ev_estimate": round(s.ev_estimate, 4),
                        "direction": s.direction,
                        "reasoning": s.reasoning[:120],
                    })

        if pname in run_result.errors:
            pvr.errors.append(run_result.errors[pname])

        report.pipeline_results[pname] = pvr

    # ── Step 6: Correlation engine metrics ──────────────────────────────
    if run_result.correlation_result:
        cr = run_result.correlation_result
        report.warnings.append(
            f"Correlation engine: {len(cr.composite_signals)} composites, "
            f"{len(cr.boosted_signals)} boosted, {len(cr.correlation_warnings)} warnings"
        )

    report.total_signals = run_result.total_signals
    report.total_duration_seconds = time.monotonic() - overall_start

    # ── Cleanup ─────────────────────────────────────────────────────────
    await manager.close()
    await kalshi.close()
    await db.close()

    return report


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sibyl Live Pipeline Validation — read-only analysis against Kalshi data"
    )
    parser.add_argument(
        "--categories", type=str, default=None,
        help="Comma-separated pipeline categories (e.g., 'crypto,sports'). Default: all.",
    )
    parser.add_argument(
        "--verbose", action="store_true", default=False,
        help="Include sample signals in the report.",
    )
    parser.add_argument(
        "--json", dest="json_output", action="store_true", default=False,
        help="Output JSON instead of human-readable report.",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Write report to file (default: stdout + data/validation_report.json).",
    )
    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    categories_filter = None
    if args.categories:
        categories_filter = {c.strip().lower() for c in args.categories.split(",")}

    report = await run_validation(
        categories_filter=categories_filter,
        verbose=args.verbose,
    )

    # Output
    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())

    # Always save JSON to data/
    output_path = args.output or "data/validation_report.json"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2)
    logger.info("Full report written to %s", output_path)


if __name__ == "__main__":
    asyncio.run(main())
