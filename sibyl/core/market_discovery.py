"""
Market Discovery — shared gap-fill and category classification for Kalshi markets.

PURPOSE:
    Provides reusable market discovery functions used by both the live
    KalshiMonitorAgent and validation tools.  The key innovation is the
    "gap-fill" system that discovers markets beyond Kalshi's standard
    pagination window (3.1× improvement: 8,801 → 27,044 markets).

CATEGORY CLASSIFICATION:
    Maps Kalshi's native categories (Elections, Economics, etc.) plus
    title keyword matching into Sibyl's 8 pipeline categories:
    economics, weather, sports, crypto, culture, science, geopolitics, financial

GAP-FILL ALGORITHM:
    1. Standard fetch: get_events(with_nested_markets=True) — discovers ~8,800 markets
    2. Gap-fill scan: get_events(with_nested_markets=False) for ALL open events
    3. Classify each event and filter for priority pipelines
    4. Fetch nested markets only for matching events (concurrency-limited)
    5. Total: ~27,000 markets across all categories

USAGE:
    from sibyl.core.market_discovery import classify_category, discover_markets, seed_markets

    category = classify_category("Economics", "Will CPI exceed 3%?")
    events, markets = await discover_markets(kalshi_client, gap_fill=True)
    count = await seed_markets(db, markets)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sibyl.clients.kalshi_client import KalshiClient
    from sibyl.core.database import DatabaseManager

logger = logging.getLogger("sibyl.core.market_discovery")


# ── Kalshi category → Sibyl pipeline mapping ─────────────────────────────
# Actual Kalshi API categories (discovered via live enumeration 2026-03-22):
#   Elections (663), Politics (116), Entertainment (70), Economics (62),
#   Sports (40), Science and Technology (11), Companies (11),
#   Climate and Weather (10), Social (6), Health (4), World (3),
#   Financials (3), Transportation (1)
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
    # ── Partial matches for fallback ──────────────────────────────────
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


def classify_category(kalshi_category: str, title: str) -> str:
    """Classify a Kalshi market into one of Sibyl's 8 pipeline categories.

    Uses a three-tier approach:
        1. Direct Kalshi category match (fastest, most reliable)
        2. Partial category match (handles variations)
        3. Title keyword search (catches miscategorized markets)

    Args:
        kalshi_category: Kalshi's native category string (e.g., "Economics").
        title: Market or event title for keyword fallback.

    Returns:
        Sibyl pipeline name: "economics", "weather", "sports", "crypto",
        "culture", "science", "geopolitics", "financial", or "uncategorized".
    """
    # Tier 1: Direct category match
    if kalshi_category in CATEGORY_MAP:
        return CATEGORY_MAP[kalshi_category]

    # Tier 2: Case-insensitive partial category match
    cat_lower = kalshi_category.lower() if kalshi_category else ""
    for key, pipeline in CATEGORY_MAP.items():
        if key.lower() in cat_lower:
            return pipeline

    # Tier 3: Title keyword matching
    title_lower = (title or "").lower()
    keyword_map: dict[str, str] = {
        # Crypto
        "bitcoin": "crypto", "btc": "crypto", "ethereum": "crypto",
        "eth": "crypto", "solana": "crypto", "sol": "crypto",
        "dogecoin": "crypto", "doge": "crypto", "crypto": "crypto",
        "xrp": "crypto", "cardano": "crypto", "avalanche": "crypto",
        # Weather
        "temperature": "weather", "weather": "weather", "rain": "weather",
        "snow": "weather", "hurricane": "weather", "tornado": "weather",
        "hottest": "weather", "coldest": "weather", "heat": "weather",
        "flood": "weather", "drought": "weather",
        # Sports
        "nfl": "sports", "nba": "sports", "mlb": "sports", "nhl": "sports",
        "ncaa": "sports", "ufc": "sports", "mls": "sports", "epl": "sports",
        "premier league": "sports", "champions league": "sports",
        "world cup": "sports", "super bowl": "sports", "march madness": "sports",
        "f1": "sports", "nascar": "sports", "tennis": "sports", "golf": "sports",
        "boxing": "sports", "pga": "sports", "wnba": "sports",
        "touchdown": "sports", "home run": "sports", "grand slam": "sports",
        "cs2": "sports", "counter-strike": "sports",
        # Economics
        "fed ": "economics", "federal reserve": "economics", "fomc": "economics",
        "interest rate": "economics", "gdp": "economics", "cpi": "economics",
        "inflation": "economics", "unemployment": "economics",
        "payroll": "economics", "pce": "economics", "recession": "economics",
        "jobs report": "economics", "nonfarm": "economics",
        # Science & Tech
        "fda": "science", "drug": "science", "clinical trial": "science",
        "spacex": "science", "nasa": "science", "ai ": "science",
        "artificial intelligence": "science", "nuclear": "science",
        "openai": "science", "chatgpt": "science", "gpu": "science",
        "apple": "science", "nvidia": "science",
        # Culture
        "oscar": "culture", "grammy": "culture", "emmy": "culture",
        "billboard": "culture", "box office": "culture", "movie": "culture",
        "streaming": "culture", "tiktok": "culture", "instagram": "culture",
        "youtube": "culture", "twitter followers": "culture",
        "baby name": "culture", "reality tv": "culture", "bachelor": "culture",
        "survivor": "culture", "hot 100": "culture",
        # Geopolitics
        "president": "geopolitics", "congress": "geopolitics",
        "senate": "geopolitics", "supreme court": "geopolitics",
        "election": "geopolitics", "vote": "geopolitics",
        "governor": "geopolitics", "impeach": "geopolitics",
        "war": "geopolitics", "sanctions": "geopolitics",
        "tariff": "geopolitics", "treaty": "geopolitics",
        # Financial
        "s&p 500": "financial", "s&p": "financial", "nasdaq": "financial",
        "dow jones": "financial", "dow ": "financial", "russell": "financial",
        "stock": "financial", "earnings": "financial", "ipo": "financial",
        "treasury": "financial", "yield": "financial", "10-year": "financial",
        "gold": "financial", "silver": "financial", "copper": "financial",
        "oil": "financial", "crude": "financial", "wti": "financial",
        "vix": "financial", "forex": "financial", "eur/usd": "financial",
        "natural gas": "financial", "platinum": "financial",
    }
    for kw, pipeline in keyword_map.items():
        if kw in title_lower:
            return pipeline

    return "uncategorized"


# ── Priority pipelines for gap-fill (pipelines that actively generate signals) ──
_GAP_FILL_PRIORITY_PIPELINES = {
    "weather", "crypto", "economics", "financial",
    "culture", "science", "sports",
}


async def discover_markets(
    kalshi: KalshiClient,
    max_pages: int = 10,
    gap_fill: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Fetch all active Kalshi events and flatten their markets.

    Two-phase approach:
        1. Standard pagination: get_events(with_nested_markets=True) — ~8,800 markets
        2. Gap-fill scan (optional): discover events beyond pagination window — up to ~27,000 markets

    Args:
        kalshi:    Initialized KalshiClient.
        max_pages: Max pagination pages for the standard fetch.
        gap_fill:  If True, run supplementary scan to discover extra markets.
                   Set to False after first cycle for speed (~7s vs ~600s).

    Returns:
        (events_list, markets_list) — each market dict includes parent event info
        and Sibyl category under key '_sibyl_category'.
    """
    all_events: list[dict] = []
    all_markets: list[dict] = []

    # ── Standard fetch: events with nested markets ─────────────────────
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
                market["_sibyl_category"] = classify_category(
                    event_category, market.get("title", event_title)
                )
                all_markets.append(market)

        cursor = data.get("cursor")
        if not cursor:
            break

    logger.info(
        "Standard fetch: %d events, %d markets",
        len(all_events), len(all_markets),
    )

    if not gap_fill:
        return all_events, all_markets

    # ── Gap-fill: discover events beyond the pagination window ─────────
    seen_tickers = {e.get("event_ticker", "") for e in all_events}
    gap_new_events: list[dict] = []
    cursor = None

    logger.info("Gap-fill: scanning for events beyond main fetch window...")
    for page in range(50):  # Up to 10K events at 200/page
        try:
            data = await kalshi.get_events(
                limit=200, cursor=cursor, status="open",
                with_nested_markets=False,
            )
        except Exception as e:
            logger.warning("Gap-fill scan page %d failed: %s", page, e)
            break

        # Rate-limit gap-fill scan pages (~2 rps)
        await asyncio.sleep(0.5)

        events = data.get("events", [])
        if not events:
            break

        for event in events:
            eticker = event.get("event_ticker", "")
            if eticker and eticker not in seen_tickers:
                seen_tickers.add(eticker)
                ecat = event.get("category", "")
                etitle = event.get("title", "")
                sibyl_cat = classify_category(ecat, etitle)
                if sibyl_cat in _GAP_FILL_PRIORITY_PIPELINES:
                    gap_new_events.append(event)
                    all_events.append(event)

        cursor = data.get("cursor")
        if not cursor:
            break

    if not gap_new_events:
        logger.info("Gap-fill: no additional events found")
        return all_events, all_markets

    logger.info(
        "Gap-fill: found %d priority events beyond main window — fetching markets...",
        len(gap_new_events),
    )

    # Fetch markets for gap-fill events with concurrency control
    # Kalshi rate limit is ~8 req/s; gap-fill is background so we go slow:
    # sem=2 + 1.0s delay → ~2 rps effective, leaving 6 rps for live polling
    gap_extra_markets = 0
    sem = asyncio.Semaphore(2)  # Max 2 concurrent market fetches

    async def _fetch_event_markets(event: dict) -> list[dict]:
        eticker = event.get("event_ticker", "")
        if not eticker:
            return []
        async with sem:
            await asyncio.sleep(1.0)  # ~2 rps effective rate (gap-fill is background)
            try:
                mdata = await kalshi.get_markets(
                    event_ticker=eticker, limit=100, status="open",
                )
                return mdata.get("markets", [])
            except Exception as e:
                logger.warning("Gap-fill markets for %s failed: %s", eticker, e)
                return []

    # Process in batches of 20 events to limit memory + rate limit pressure
    batch_size = 20
    for batch_start in range(0, len(gap_new_events), batch_size):
        batch = gap_new_events[batch_start:batch_start + batch_size]
        tasks = [_fetch_event_markets(ev) for ev in batch]
        results = await asyncio.gather(*tasks)

        for event, mkt_list in zip(batch, results):
            eticker = event.get("event_ticker", "")
            ecat = event.get("category", "")
            etitle = event.get("title", "")
            for market in mkt_list:
                market["_event_ticker"] = eticker
                market["_event_title"] = etitle
                market["_event_category"] = ecat
                market["_sibyl_category"] = classify_category(
                    ecat, market.get("title", etitle)
                )
                all_markets.append(market)
                gap_extra_markets += 1

        if (batch_start + batch_size) % 200 == 0:
            logger.info(
                "Gap-fill progress: %d/%d events processed, %d markets",
                min(batch_start + batch_size, len(gap_new_events)),
                len(gap_new_events), gap_extra_markets,
            )

    logger.info(
        "Gap-fill complete: +%d events, +%d markets (total: %d markets)",
        len(gap_new_events), gap_extra_markets, len(all_markets),
    )
    return all_events, all_markets


async def seed_markets(
    db: DatabaseManager,
    markets: list[dict],
) -> int:
    """Write fetched Kalshi markets into the markets table for pipeline analysis.

    Handles Kalshi v2 API price formats:
    - _dollars suffix fields (decimal format, already in 0-1 range)
    - _fp suffix for volume/OI (floating point strings)
    - Legacy integer cents format (0-100, converted to 0-1)

    Args:
        db:      DatabaseManager instance.
        markets: Market dicts from discover_markets() with _sibyl_category.

    Returns:
        Count of markets written to DB.
    """
    written = 0
    for m in markets:
        ticker = m.get("ticker", "")
        if not ticker:
            continue

        # Extract price — Kalshi v2 API uses _dollars suffix (already decimal)
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
                    # If values look like cents (>1), convert to decimals
                    if yes_price > 1.0:
                        yes_price /= 100.0
            except (ValueError, TypeError):
                pass

        if yes_price is None and last_price is not None:
            try:
                lp = float(last_price)
                if lp > 0:
                    yes_price = lp if lp <= 1.0 else lp / 100.0
            except (ValueError, TypeError):
                pass

        # Volume and OI
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

        title = m.get("title", m.get("subtitle", ticker))
        sibyl_category = m.get("_sibyl_category", "uncategorized")

        try:
            await db.execute(
                """INSERT OR REPLACE INTO markets
                   (id, title, category, status, platform, close_date, event_id,
                    updated_at)
                   VALUES (?, ?, ?, 'active', 'kalshi', ?, ?, datetime('now'))""",
                (
                    ticker,
                    title,
                    sibyl_category,
                    m.get("close_time", m.get("expiration_time", "")),
                    m.get("_event_ticker", ""),
                ),
            )

            # Seed a price row so pipelines can look up market prices
            if yes_price is not None and 0 < yes_price < 1.0:
                await db.execute(
                    """INSERT INTO prices
                       (market_id, yes_price, no_price, volume_24h, open_interest)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        ticker,
                        yes_price,
                        1.0 - yes_price,
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
