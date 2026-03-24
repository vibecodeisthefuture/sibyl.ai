"""
API Verification Script — Health-checks all data source clients.

Run: python -m sibyl.clients.verify_all

Tests connectivity and authentication for every configured API.
Reports: OK / FAIL / SKIP (no key) for each service.
"""

from __future__ import annotations

import asyncio
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv

load_dotenv()

from sibyl.clients.fred_client import FredClient
from sibyl.clients.bls_client import BlsClient
from sibyl.clients.bea_client import BeaClient
from sibyl.clients.fmp_client import FmpClient
from sibyl.clients.open_meteo_client import OpenMeteoClient
from sibyl.clients.noaa_client import NoaaClient
from sibyl.clients.api_sports_client import ApiSportsClient
from sibyl.clients.balldontlie_client import BallDontLieClient
from sibyl.clients.thesportsdb_client import TheSportsDbClient
from sibyl.clients.espn_client import EspnClient
from sibyl.clients.tmdb_client import TmdbClient
from sibyl.clients.wikipedia_client import WikipediaClient
from sibyl.clients.coingecko_client import CoinGeckoClient
from sibyl.clients.feargreed_client import FearGreedClient
from sibyl.clients.openfda_client import OpenFdaClient
from sibyl.clients.clinicaltrials_client import ClinicalTrialsClient
from sibyl.clients.courtlistener_client import CourtListenerClient
from sibyl.clients.gdelt_client import GdeltClient
from sibyl.clients.congress_client import CongressClient


async def verify_all() -> dict[str, dict]:
    """Initialize and health-check all data source clients."""

    clients = [
        # Economics & Macro
        FredClient(),
        BlsClient(),
        BeaClient(),
        FmpClient(),
        # Weather
        OpenMeteoClient(),
        NoaaClient(),
        # Sports
        ApiSportsClient("football"),
        BallDontLieClient(),
        TheSportsDbClient(),
        EspnClient(),
        # Culture & Entertainment
        TmdbClient(),
        WikipediaClient(),
        # Crypto
        CoinGeckoClient(),
        FearGreedClient(),
        # Science & Technology
        OpenFdaClient(),
        ClinicalTrialsClient(),
        # Geopolitics & Legal
        CourtListenerClient(),
        GdeltClient(),
        CongressClient(),
    ]

    results = {}

    for client in clients:
        name = client.name
        init_ok = client.initialize()
        if not init_ok:
            results[name] = {"ok": False, "service": name, "detail": "SKIP — missing API key"}
            print(f"  SKIP  {name:25s} — missing API key")
            continue

        try:
            result = await client.health_check()
            results[name] = result
            status = "  OK  " if result["ok"] else " FAIL "
            print(f"{status} {name:25s} — {result.get('detail', '')}")
        except Exception as e:
            results[name] = {"ok": False, "service": name, "detail": str(e)}
            print(f" FAIL  {name:25s} — {e}")
        finally:
            await client.close()

    return results


def main():
    print("=" * 70)
    print("  Sibyl.ai — Data Source API Verification")
    print("=" * 70)
    print()

    results = asyncio.run(verify_all())

    print()
    print("-" * 70)
    ok_count = sum(1 for r in results.values() if r["ok"])
    skip_count = sum(1 for r in results.values() if "SKIP" in r.get("detail", ""))
    fail_count = len(results) - ok_count - skip_count
    total = len(results)

    print(f"Results: {ok_count}/{total} OK, {skip_count} SKIP, {fail_count} FAIL")
    print("=" * 70)

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
