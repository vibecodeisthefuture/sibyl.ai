# Sprint 11 Completion Report & Stakeholder Action Items

## What Was Delivered

**Phase 1: Investment Policy Engine — Complete**

The entire 19-section investment policy framework from `sibyl-kalshi-investment-policy.md` is now implemented and enforced across all trading paths. Specifically:

- **PolicyEngine** (`sibyl/core/policy.py`, ~520 lines): Pure-function enforcement module with no DB dependency. Handles tier classification, signal quality floors, capital caps, avoidance rules, data freshness, override protocol, sports decoupling, and multi-category resolution.
- **Policy Config** (`config/investment_policy_config.yaml`, ~280 lines): Machine-readable encoding of all 19 policy sections.
- **Signal Router Integration**: Routes now check policy tier, signal quality floor, no-signal-coverage, and Tier 3 auto-entry blocks before routing.
- **Order Executor Integration**: Pre-trade gate runs full policy check (avoidance rules, capital caps, data freshness) before every execution. In-game Kelly shrinkage (0.50x) and override sizing (0.50x) applied automatically.
- **Portfolio Allocator Integration**: Per-category exposure tracking written to `system_state` for cap enforcement.
- **Database Migrations**: 9 new columns across signals/executions/positions + new `override_log` table for Section 17 post-hoc review.
- **Category Strategies Updated**: "Weather" and "Geopolitics & Legal" added. All categories now have `policy_tier`, `sge_cap`, `ace_cap`, `combined_cap` fields.
- **Backtest Fix**: Backtest engine now uses actual market prices for Kelly sizing instead of confidence-as-price (which produced zero edge by mathematical identity).

**Test Suite: 301 tests, all passing. Zero regressions.**
- 83 new policy tests covering all enforcement paths
- All 218 pre-existing tests continue to pass

---

## Accounts & API Keys You Need to Create

### Already Configured (Working)
| Service | Status | Notes |
|---------|--------|-------|
| Kalshi | Active | Key ID + private key path set |
| X / Twitter | Active | Bearer token + OAuth keys set |
| ntfy.sh | Active | Push notifications configured |

### Need API Keys (Free Tier Available)
| Service | Category | Sign Up URL | Cost | Priority |
|---------|----------|-------------|------|----------|
| FRED (Federal Reserve) | Economics, Mentions | https://fred.stlouisfed.org/docs/api/api_key.html | Free | HIGH |
| BLS (Bureau of Labor Statistics) | Economics | https://data.bls.gov/registrationEngine/ | Free | HIGH |
| BEA (Bureau of Economic Analysis) | Economics | https://apps.bea.gov/api/signup/ | Free | HIGH |
| Open-Meteo | Weather, Sports | https://open-meteo.com/en/docs | Free (no key needed) | HIGH |
| NOAA Climate Data | Weather | https://www.ncdc.noaa.gov/cdo-web/token | Free | HIGH |
| CoinGecko | Crypto | https://www.coingecko.com/en/api | Free tier | MEDIUM |
| OpenFDA | Science & Tech | https://open.fda.gov/apis/authentication/ | Free | MEDIUM |
| ClinicalTrials.gov | Science & Tech | https://clinicaltrials.gov/data-api/about-api | Free (no key needed) | MEDIUM |
| Congress.gov | Mentions | https://api.congress.gov/sign-up/ | Free | MEDIUM |
| GovTrack | Geopolitics | https://www.govtrack.us/developers/api | Free (no key needed) | LOW |
| CourtListener | Geopolitics | https://www.courtlistener.com/api/rest-info/ | Free | LOW |
| GDELT | Geopolitics | https://www.gdeltproject.org/ | Free (no key needed) | LOW |
| Wikipedia Pageviews | Culture | https://wikimedia.org/api/rest_v1/ | Free (no key needed) | LOW |
| Fear & Greed Index | Crypto | https://alternative.me/crypto/fear-and-greed-index/ | Free (no key needed) | LOW |

### Need API Keys (Paid/Freemium)
| Service | Category | Cost Estimate | Priority | Notes |
|---------|----------|---------------|----------|-------|
| Perplexity AI | All categories | ~$20/mo (Pro) | HIGH | Breakout Scout research. Key slot exists in .env but empty |
| Reddit API | Sports, Culture | Free (rate-limited) | HIGH | Client ID + secret needed. Slot in .env but empty |
| Financial Modeling Prep (FMP) | Economics, Mentions | Free tier (250 req/day) or $14/mo | HIGH | Earnings, macro data |
| API-SPORTS | Sports | ~$10-30/mo | MEDIUM | Real-time scores, odds, lineups |
| TMDb (The Movie Database) | Culture | Free (API key required) | MEDIUM | Box office, release data |
| YouTube Data API | Culture | Free (10K quota/day via Google Cloud) | MEDIUM | Trending, view counts |
| Spotify Charts | Culture | Unofficial / scraping | LOW | Chart data for music markets |
| Glassnode | Crypto | Free tier limited, $29/mo for Standard | LOW | On-chain analytics |
| OpenSecrets | Mentions | Free (limited), $15K/yr enterprise | LOW | Campaign finance, lobbying |
| CME FedWatch | Economics | Free via CME Group website | MEDIUM | Fed rate probability tool |
| TheSportsDB | Sports | Free tier available | MEDIUM | Team/player metadata |
| BallDontLie | Sports | Free tier (60 req/min) | MEDIUM | NBA stats |
| SportsRC | Sports | Varies | MEDIUM | Sports research data |

### Also Empty in .env (Pre-existing)
| Service | Notes |
|---------|-------|
| ANTHROPIC_API_KEY | Needed for LLM-powered signal reasoning (Claude API) |
| POLYMARKET_API_KEY | Read-only cross-platform arb. US geo-restricted. |
| NEWSAPI_KEY | Breakout Scout news source. Free tier: 100 req/day |

---

## PostgreSQL Strategy

**Decision: SQLite for dev now, PostgreSQL as the production backend.**

Sibyl's long-term vision is a locally-hosted, multi-user service platform. PostgreSQL is the right choice for this because it handles concurrent connections from multiple users, supports row-level security for per-user data isolation, and scales gracefully under the homelab's EPYC 7532 workstation. The migration roadmap:

1. **Now (Sprint 12+):** Build a database abstraction layer (`sibyl/core/db_backend.py`) that defines an async interface. The existing SQLite backend becomes one implementation.
2. **Phase 4:** Add an `asyncpg`-based PostgreSQL backend behind the same interface. Both backends pass the same integration test suite.
3. **Docker Compose:** Add a PostgreSQL 16 service to `docker-compose.yaml`. Local dev stays on SQLite; `--db postgres` flag switches to PostgreSQL.
4. **Homelab Deployment:** PostgreSQL runs as a dedicated container on the home server, with data persisted to the NAS (QNAP TS-832XU). Each future subscriber gets a separate schema or tenant_id column for data isolation.
5. **Multi-User (Future):** Add user authentication, per-user portfolio isolation, and subscription-gated access. PostgreSQL's RBAC and connection pooling (via PgBouncer) support this cleanly.

---

## Recommended Next Steps (Priority Order)

1. **Create free API accounts** (FRED, BLS, BEA, NOAA, CoinGecko, Congress.gov) — these unlock 5 of 8 category pipelines at zero cost
2. **Set up Reddit API** (free) — unlocks Sports + Culture sentiment signals
3. **Subscribe to Perplexity Pro** (~$20/mo) — single highest-leverage paid API, used across all categories
4. **Phase 2 implementation**: Build async data source clients for the free APIs first, paid APIs as accounts are created
5. **Phase 3**: Build category signal pipelines in policy priority order (Weather → Economics → Mentions → Sports pre-game → Culture → Crypto → Science → Sports in-game → Geopolitics)
6. **Phase 4**: PostgreSQL migration + Docker containerization

---

*Sprint 11 complete. 301 tests passing. Policy enforcement active across all trading paths.*
