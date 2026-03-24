"""
Sibyl.ai — Demo Dashboard Server
=================================
Standalone mock server that feeds the React dashboard with realistic,
slowly-drifting random portfolio data.  No database required.

Usage:
    python demo_dashboard.py          # starts at http://0.0.0.0:8088
    python demo_dashboard.py --port 9000

The data simulates a real Sibyl instance that has been running for ~30 days
with a $500 starting balance, currently around $600-700.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from datetime import datetime, timedelta, timezone

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from sibyl.dashboard.frontend import DASHBOARD_HTML

# ═══════════════════════════════════════════════════════════════════════
#  Simulated World State (mutates on every request cycle)
# ═══════════════════════════════════════════════════════════════════════

CATEGORIES = [
    "Politics", "Sports", "Culture", "Crypto", "Climate",
    "Economics", "Mentions", "Companies", "Financials", "Tech & Science",
]

SIGNAL_TYPES = ["WHALE", "VOLUME", "ORDER_BOOK", "ARBITRAGE", "SENTIMENT"]

# Realistic Kalshi market titles per category
MARKET_TITLES = {
    "Politics": [
        "Will Biden win the 2026 midterms approval bet?",
        "Will Republicans win the Senate majority?",
        "Will Trump be indicted before July 2026?",
        "Will a government shutdown occur in Q2 2026?",
        "Will the Supreme Court overturn any major ruling?",
    ],
    "Sports": [
        "Will the Lakers make the NBA Finals?",
        "Will Ohtani hit 50+ home runs in 2026?",
        "Will Manchester City win the Premier League?",
        "Will the US win 40+ gold medals at the Olympics?",
        "Will the Super Bowl LXI have over 100M viewers?",
    ],
    "Culture": [
        "Will a Marvel movie gross $1B in 2026?",
        "Will Taylor Swift announce a new album by June?",
        "Will Threads surpass 500M monthly users?",
        "Will the Oscar Best Picture go to an AI-assisted film?",
        "Will a podcast top 100M streams in a single month?",
    ],
    "Crypto": [
        "Will Bitcoin exceed $150K by June 2026?",
        "Will Ethereum merge to single-slot finality?",
        "Will Solana TVL surpass $50B?",
        "Will a US spot ETH ETF be approved?",
        "Will Tether lose its dollar peg (>2% deviation)?",
    ],
    "Climate": [
        "Will 2026 be the hottest year on record?",
        "Will a Category 5 hurricane hit the US mainland?",
        "Will global CO2 exceed 430 ppm annual average?",
        "Will the Arctic sea ice hit a new minimum?",
        "Will the EU carbon price exceed €120?",
    ],
    "Economics": [
        "Will the Fed cut rates before September 2026?",
        "Will US unemployment exceed 5%?",
        "Will US CPI fall below 2.5% YoY?",
        "Will the US GDP growth exceed 3% in Q2?",
        "Will the yield curve un-invert by July?",
    ],
    "Mentions": [
        "Will Elon Musk tweet about Dogecoin this week?",
        "Will 'recession' trend on Google above 80?",
        "Will any tech CEO testify before Congress?",
        "Will 'AI bubble' appear in NYT front page?",
        "Will a viral meme move a stock price by 5%?",
    ],
    "Companies": [
        "Will Apple announce a foldable device?",
        "Will Tesla deliver 500K vehicles in Q2?",
        "Will NVIDIA market cap exceed $5 trillion?",
        "Will OpenAI complete its for-profit conversion?",
        "Will Amazon launch a satellite internet service?",
    ],
    "Financials": [
        "Will the S&P 500 close above 6,500 by June?",
        "Will the VIX spike above 35 this quarter?",
        "Will 10Y Treasury yield fall below 3.5%?",
        "Will the USD/EUR rate hit parity?",
        "Will a major US bank report a quarterly loss?",
    ],
    "Tech & Science": [
        "Will GPT-5 be released before July 2026?",
        "Will SpaceX achieve Starship orbital success?",
        "Will a quantum computer factor RSA-2048?",
        "Will an AI system pass the full Turing test?",
        "Will CRISPR gene therapy get FDA approval for cancer?",
    ],
}

# ── Mutable global state ──────────────────────────────────────────────

_state = {
    "starting_balance": 500.0,
    "total_balance": 0.0,
    "daily_pnl": 0.0,
    "sge_capital": 0.0,
    "ace_capital": 0.0,
    "sge_deployed": 0.0,
    "ace_deployed": 0.0,
    "high_water_mark": 0.0,
    "win_rate_7d": 0.0,
    "sharpe_30d": 0.0,
    "open_positions": [],
    "closed_positions": [],
    "signals": [],
    "chart_data": [],
    "categories_data": [],
    "research_data": [],
    "activity_log": [],
    "tick": 0,
    "boot_time": time.time(),
}


def _init_state():
    """Generate initial realistic state — called once at startup."""
    starting = 500.0
    now = datetime.now(timezone.utc)

    # ── Chart: 30 days of portfolio value history ─────────────────
    balance = starting
    chart = []
    daily_returns = []
    for d in range(30):
        date = now - timedelta(days=30 - d)
        # Random walk with slight upward drift (+0.3% daily mean)
        daily_ret = random.gauss(0.003, 0.015)
        daily_pnl = balance * daily_ret
        balance += daily_pnl
        balance = max(balance, starting * 0.7)  # floor at 70% of starting
        daily_returns.append(daily_ret)
        chart.append({
            "date": date.strftime("%Y-%m-%d"),
            "value": round(balance, 2),
            "daily_pnl": round(daily_pnl, 2),
        })

    total = round(balance, 2)
    hwm = round(max(p["value"] for p in chart), 2)

    # ── Sharpe & win rate ─────────────────────────────────────────
    if daily_returns:
        mean_r = sum(daily_returns) / len(daily_returns)
        std_r = (sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)) ** 0.5
        sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # ── Open positions (6-12 active) ──────────────────────────────
    num_open = random.randint(6, 12)
    open_positions = []
    used_titles = set()
    for i in range(num_open):
        cat = random.choice(CATEGORIES)
        title = random.choice([t for t in MARKET_TITLES[cat] if t not in used_titles] or MARKET_TITLES[cat])
        used_titles.add(title)
        engine = random.choice(["SGE", "ACE"])
        side = random.choice(["YES", "NO"])
        entry = round(random.uniform(0.15, 0.85), 2)
        current = round(entry + random.gauss(0.0, 0.08), 2)
        current = max(0.02, min(0.98, current))
        size = round(random.uniform(8, 60), 2)
        pnl = round(size * (current - entry) * (1 if side == "YES" else -1), 2)
        opened_hours_ago = random.uniform(0.5, 72)
        open_positions.append({
            "id": i + 1,
            "market_id": f"kalshi-{cat.lower().replace(' ', '-')}-{i:03d}",
            "platform": "kalshi",
            "engine": engine,
            "side": side,
            "size": size,
            "entry_price": entry,
            "current_price": current,
            "target_price": round(entry + random.uniform(0.05, 0.25) * (1 if side == "YES" else -1), 2),
            "stop_loss": round(entry - random.uniform(0.08, 0.20) * (1 if side == "YES" else -1), 2),
            "pnl": pnl,
            "ev_current": round(random.uniform(0.02, 0.18), 3),
            "status": "OPEN",
            "thesis": f"{'Whale accumulation' if random.random() > 0.5 else 'Volume spike'} detected — {cat} vertical {side} bias",
            "opened_at": (now - timedelta(hours=opened_hours_ago)).isoformat(),
            "title": title,
            "category": cat,
        })

    # ── Closed positions (last 20) ───────────────────────────────
    closed_positions = []
    wins = 0
    for i in range(20):
        cat = random.choice(CATEGORIES)
        title = random.choice(MARKET_TITLES[cat])
        engine = random.choice(["SGE", "ACE"])
        won = random.random() < 0.62
        if won:
            wins += 1
        pnl = round(random.uniform(2, 25), 2) if won else round(-random.uniform(3, 20), 2)
        closed_hours_ago = random.uniform(1, 168)
        opened_hours_ago = closed_hours_ago + random.uniform(1, 48)
        closed_positions.append({
            "id": num_open + i + 1,
            "market_id": f"kalshi-hist-{i:03d}",
            "platform": "kalshi",
            "engine": engine,
            "side": random.choice(["YES", "NO"]),
            "size": round(random.uniform(10, 50), 2),
            "entry_price": round(random.uniform(0.20, 0.80), 2),
            "current_price": round(random.uniform(0.10, 0.95), 2),
            "pnl": pnl,
            "status": "CLOSED" if won else random.choice(["CLOSED", "STOPPED"]),
            "opened_at": (now - timedelta(hours=opened_hours_ago)).isoformat(),
            "closed_at": (now - timedelta(hours=closed_hours_ago)).isoformat(),
            "title": title,
        })

    win_rate = wins / 20

    # ── Signals (last 50) ─────────────────────────────────────────
    signals = []
    for i in range(50):
        cat = random.choice(CATEGORIES)
        title = random.choice(MARKET_TITLES[cat])
        sig_type = random.choice(SIGNAL_TYPES)
        conf = round(random.uniform(0.55, 0.92), 3)
        ev = round(random.uniform(0.02, 0.22), 3)
        routed = random.choice(["SGE_ROUTED", "ACE_ROUTED", "BOTH", "DEFERRED"])
        hours_ago = random.uniform(0, 96)
        signals.append({
            "id": 1000 + i,
            "market_id": f"kalshi-sig-{i:03d}",
            "timestamp": (now - timedelta(hours=hours_ago)).isoformat(),
            "signal_type": sig_type,
            "confidence": conf,
            "ev_estimate": ev,
            "routed_to": routed,
            "status": routed,
            "reasoning": f"{sig_type} detected in {cat}: confidence {conf:.1%}, EV ${ev:.3f}",
            "detection_modes_triggered": sig_type[0],
            "title": title,
            "platform": "kalshi",
        })
    signals.sort(key=lambda s: s["timestamp"], reverse=True)

    # ── Capital split ─────────────────────────────────────────────
    sge_total = round(total * 0.70, 2)
    ace_total = round(total * 0.30, 2)
    sge_deployed = round(sum(p["size"] * p["entry_price"] for p in open_positions if p["engine"] == "SGE"), 2)
    ace_deployed = round(sum(p["size"] * p["entry_price"] for p in open_positions if p["engine"] == "ACE"), 2)

    # ── Category aggregation ──────────────────────────────────────
    cat_agg = {}
    for p in open_positions:
        c = p["category"]
        if c not in cat_agg:
            cat_agg[c] = {"category": c, "position_count": 0, "total_deployed": 0.0, "total_pnl": 0.0}
        cat_agg[c]["position_count"] += 1
        cat_agg[c]["total_deployed"] += p["size"] * p["entry_price"]
        cat_agg[c]["total_pnl"] += p["pnl"]
    categories_data = sorted(cat_agg.values(), key=lambda x: x["total_deployed"], reverse=True)
    for c in categories_data:
        c["total_deployed"] = round(c["total_deployed"], 2)
        c["total_pnl"] = round(c["total_pnl"], 2)

    # ── Research data ─────────────────────────────────────────────
    research = []
    for cat in random.sample(CATEGORIES, 8):
        title = random.choice(MARKET_TITLES[cat])
        research.append({
            "market_id": f"kalshi-res-{cat.lower()[:3]}",
            "sentiment_score": round(random.uniform(-0.8, 0.9), 2),
            "sentiment_label": random.choice(["BULLISH", "BEARISH", "NEUTRAL", "CONTESTED"]),
            "key_arguments": json.dumps([
                f"Source A indicates {'positive' if random.random() > 0.4 else 'negative'} momentum",
                f"Reddit sentiment {'rising' if random.random() > 0.5 else 'declining'} in r/{cat.lower()}",
                f"Perplexity finds {'supporting' if random.random() > 0.5 else 'conflicting'} evidence"
            ]),
            "synthesis": f"Multi-source analysis for {cat}: {random.choice(['momentum building', 'mixed signals', 'divergence detected', 'consensus forming'])}",
            "freshness": round(random.uniform(0.3, 1.0), 2),
            "created_at": (now - timedelta(hours=random.uniform(0, 12))).isoformat(),
            "title": title,
            "category": cat,
        })

    # ── Daily P&L ─────────────────────────────────────────────────
    daily_pnl = sum(p["pnl"] for p in open_positions)

    _state.update({
        "total_balance": total,
        "daily_pnl": round(daily_pnl, 2),
        "sge_capital": sge_total,
        "ace_capital": ace_total,
        "sge_deployed": min(sge_deployed, sge_total * 0.8),
        "ace_deployed": min(ace_deployed, ace_total * 0.8),
        "high_water_mark": hwm,
        "win_rate_7d": round(win_rate, 3),
        "sharpe_30d": round(sharpe, 2),
        "open_positions": open_positions,
        "closed_positions": closed_positions,
        "signals": signals,
        "chart_data": chart,
        "categories_data": categories_data,
        "research_data": research,
    })


def _tick():
    """Small random drift applied every API call to simulate live markets."""
    _state["tick"] += 1
    tick = _state["tick"]

    # Every tick, slightly drift open position prices
    for p in _state["open_positions"]:
        drift = random.gauss(0.0, 0.003)
        p["current_price"] = round(max(0.02, min(0.98, p["current_price"] + drift)), 2)
        p["pnl"] = round(p["size"] * (p["current_price"] - p["entry_price"]) * (1 if p["side"] == "YES" else -1), 2)
        p["ev_current"] = round(max(0.001, p["ev_current"] + random.gauss(0, 0.002)), 3)

    # Slowly drift total balance
    balance_drift = random.gauss(0.3, 1.5)
    _state["total_balance"] = round(_state["total_balance"] + balance_drift, 2)
    _state["high_water_mark"] = max(_state["high_water_mark"], _state["total_balance"])

    # Update daily P&L
    _state["daily_pnl"] = round(sum(p["pnl"] for p in _state["open_positions"]), 2)

    # Update engine capitals
    _state["sge_capital"] = round(_state["total_balance"] * 0.70, 2)
    _state["ace_capital"] = round(_state["total_balance"] * 0.30, 2)
    _state["sge_deployed"] = round(
        sum(p["size"] * p["entry_price"] for p in _state["open_positions"] if p["engine"] == "SGE"), 2
    )
    _state["ace_deployed"] = round(
        sum(p["size"] * p["entry_price"] for p in _state["open_positions"] if p["engine"] == "ACE"), 2
    )

    # Recalculate category aggregation
    cat_agg = {}
    for p in _state["open_positions"]:
        c = p["category"]
        if c not in cat_agg:
            cat_agg[c] = {"category": c, "position_count": 0, "total_deployed": 0.0, "total_pnl": 0.0}
        cat_agg[c]["position_count"] += 1
        cat_agg[c]["total_deployed"] += p["size"] * p["entry_price"]
        cat_agg[c]["total_pnl"] += p["pnl"]
    _state["categories_data"] = sorted(
        [{"category": c["category"], "position_count": c["position_count"],
          "total_deployed": round(c["total_deployed"], 2), "total_pnl": round(c["total_pnl"], 2)}
         for c in cat_agg.values()],
        key=lambda x: x["total_deployed"], reverse=True,
    )

    # Every ~15 ticks, inject a new signal
    if tick % 3 == 0:
        now = datetime.now(timezone.utc)
        cat = random.choice(CATEGORIES)
        sig_type = random.choice(SIGNAL_TYPES)
        conf = round(random.uniform(0.58, 0.91), 3)
        ev = round(random.uniform(0.03, 0.19), 3)
        new_sig = {
            "id": 1000 + tick + 50,
            "market_id": f"kalshi-live-{tick:04d}",
            "timestamp": now.isoformat(),
            "signal_type": sig_type,
            "confidence": conf,
            "ev_estimate": ev,
            "routed_to": random.choice(["SGE_ROUTED", "ACE_ROUTED", "BOTH"]),
            "status": "SGE_ROUTED",
            "reasoning": f"Live {sig_type} in {cat}: conf={conf:.1%}, EV=${ev:.3f}",
            "detection_modes_triggered": sig_type[0],
            "title": random.choice(MARKET_TITLES[cat]),
            "platform": "kalshi",
        }
        _state["signals"].insert(0, new_sig)
        _state["signals"] = _state["signals"][:50]


# ═══════════════════════════════════════════════════════════════════════
#  FastAPI App
# ═══════════════════════════════════════════════════════════════════════

app = FastAPI(title="Sibyl.ai Demo Dashboard", version="0.2.0-demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Frontend ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    return HTMLResponse(content=DASHBOARD_HTML, status_code=200)


# ── API Endpoints (mirror the real API shape) ─────────────────────────

@app.get("/api/health")
async def health():
    _tick()
    uptime = int(time.time() - _state["boot_time"])
    return {"status": "ok", "database": "connected", "mode": "demo", "uptime_seconds": uptime}


@app.get("/api/portfolio")
async def portfolio():
    _tick()
    total = _state["total_balance"]
    reserve = round(total * 0.05, 2)
    allocable = round(total - reserve, 2)
    deployed = round(_state["sge_deployed"] + _state["ace_deployed"], 2)
    return {
        "total_balance": total,
        "cash_reserve": reserve,
        "allocable_capital": allocable,
        "deployed_capital": deployed,
        "available_capital": round(allocable - deployed, 2),
        "daily_pnl": _state["daily_pnl"],
        "engines": {
            "SGE": {
                "total_capital": _state["sge_capital"],
                "deployed_capital": _state["sge_deployed"],
                "available_capital": round(_state["sge_capital"] - _state["sge_deployed"], 2),
                "exposure_pct": round(_state["sge_deployed"] / max(_state["sge_capital"], 1) * 100, 1),
                "drawdown_pct": round(random.uniform(0, 3), 1),
                "daily_pnl": round(_state["daily_pnl"] * 0.6, 2),
                "circuit_breaker": "CLEAR",
            },
            "ACE": {
                "total_capital": _state["ace_capital"],
                "deployed_capital": _state["ace_deployed"],
                "available_capital": round(_state["ace_capital"] - _state["ace_deployed"], 2),
                "exposure_pct": round(_state["ace_deployed"] / max(_state["ace_capital"], 1) * 100, 1),
                "drawdown_pct": round(random.uniform(0, 6), 1),
                "daily_pnl": round(_state["daily_pnl"] * 0.4, 2),
                "circuit_breaker": "CLEAR",
            },
        },
    }


@app.get("/api/positions")
async def positions():
    _tick()
    return _state["open_positions"]


@app.get("/api/positions/history")
async def positions_history():
    return _state["closed_positions"]


@app.get("/api/signals")
async def signals():
    _tick()
    return _state["signals"]


@app.get("/api/risk")
async def risk():
    _tick()
    total = _state["total_balance"]
    hwm = _state["high_water_mark"]
    dd_pct = round((hwm - total) / hwm * 100, 2) if hwm > 0 else 0.0
    dd_level = "CLEAR" if dd_pct < 5 else "WARNING" if dd_pct < 10 else "CAUTION" if dd_pct < 20 else "CRITICAL"
    deployed = _state["sge_deployed"] + _state["ace_deployed"]
    return {
        "high_water_mark": hwm,
        "drawdown_pct": dd_pct,
        "drawdown_level": dd_level,
        "total_exposure": round(deployed, 2),
        "open_positions": len(_state["open_positions"]),
        "daily_pnl_sge": round(_state["daily_pnl"] * 0.6, 2),
        "daily_pnl_ace": round(_state["daily_pnl"] * 0.4, 2),
        "win_rate_7d": _state["win_rate_7d"],
        "sharpe_30d": _state["sharpe_30d"],
    }


@app.get("/api/chart/portfolio")
async def chart_portfolio():
    # Append today's live value
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    chart = list(_state["chart_data"])
    if chart and chart[-1]["date"] != today:
        chart.append({"date": today, "value": _state["total_balance"], "daily_pnl": _state["daily_pnl"]})
    elif chart:
        chart[-1]["value"] = _state["total_balance"]
        chart[-1]["daily_pnl"] = _state["daily_pnl"]
    return chart


@app.get("/api/engines")
async def engines():
    _tick()
    return {
        "SGE": {
            "total_capital": _state["sge_capital"],
            "deployed_capital": _state["sge_deployed"],
            "available_capital": round(_state["sge_capital"] - _state["sge_deployed"], 2),
            "exposure_pct": round(_state["sge_deployed"] / max(_state["sge_capital"], 1) * 100, 1),
            "drawdown_pct": round(max(0, (_state["high_water_mark"] * 0.7 - _state["sge_capital"]) / max(_state["high_water_mark"] * 0.7, 1) * 100), 1),
            "daily_pnl": round(_state["daily_pnl"] * 0.6, 2),
            "circuit_breaker": "CLEAR",
        },
        "ACE": {
            "total_capital": _state["ace_capital"],
            "deployed_capital": _state["ace_deployed"],
            "available_capital": round(_state["ace_capital"] - _state["ace_deployed"], 2),
            "exposure_pct": round(_state["ace_deployed"] / max(_state["ace_capital"], 1) * 100, 1),
            "drawdown_pct": round(max(0, (_state["high_water_mark"] * 0.3 - _state["ace_capital"]) / max(_state["high_water_mark"] * 0.3, 1) * 100), 1),
            "daily_pnl": round(_state["daily_pnl"] * 0.4, 2),
            "circuit_breaker": "CLEAR",
        },
    }


@app.get("/api/categories")
async def categories():
    return _state["categories_data"]


@app.get("/api/research")
async def research():
    return _state["research_data"]


# ═══════════════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sibyl.ai Demo Dashboard")
    parser.add_argument("--port", type=int, default=8088, help="HTTP port (default: 8088)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address")
    args = parser.parse_args()

    _init_state()
    print(f"\n  ╔══════════════════════════════════════════════════╗")
    print(f"  ║   Sibyl.ai Demo Dashboard                       ║")
    print(f"  ║   http://localhost:{args.port}                       ║")
    print(f"  ║   Balance: ${_state['total_balance']:.2f}  |  {len(_state['open_positions'])} open positions   ║")
    print(f"  ║   Mode: DEMO (random data, live drift)           ║")
    print(f"  ╚══════════════════════════════════════════════════╝\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
