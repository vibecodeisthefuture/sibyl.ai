# Sibyl.ai — File Registry

## Key Files

### Agents
- `sibyl/agents/monitors/polymarket_monitor.py` — Polymarket data ingestion
- `sibyl/agents/monitors/kalshi_monitor.py` — Kalshi data ingestion
- `sibyl/agents/monitors/sync_agent.py` — cross-platform divergence detection
- `sibyl/agents/intelligence/market_intelligence.py` — 3 surveillance modes
- `sibyl/agents/intelligence/signal_generator.py` — composite scoring + EV
- `sibyl/agents/intelligence/signal_router.py` — SGE/ACE dispatch (category-aware)
- `sibyl/agents/intelligence/category_strategy.py` — per-category strategy manager
- `sibyl/agents/intelligence/pipeline_agent.py` — PipelineManager scheduling wrapper (15-min cycle)
- `sibyl/agents/execution/order_executor.py` — signal → position (Kelly sizing, live + paper)
- `sibyl/agents/execution/position_lifecycle.py` — 5 sub-routines
- `sibyl/agents/execution/engine_state_manager.py` — capital tracking
- `sibyl/agents/allocator/portfolio_allocator.py` — balance sync, capital splits, rebalancing
- `sibyl/agents/analytics/risk_dashboard.py` — drawdown, win rate, Sharpe, exposure metrics
- `sibyl/agents/notifications/notifier.py` — ntfy.sh push alerts
- `sibyl/agents/scout/breakout_scout.py` — multi-source sentiment research + LLM synthesis
- `sibyl/agents/narrator/narrator.py` — LLM health digests + alert escalation
- `sibyl/agents/sentiment/x_sentiment_agent.py` — X/Twitter 6-stage sentiment pipeline
- `sibyl/agents/sge/blitz_scanner.py` — Blitz: 1-second market close scanner (≤90s, >85% confidence)
- `sibyl/agents/sge/blitz_executor.py` — Blitz: fast-path market order executor (SGE_BLITZ sub-engine)
- `sibyl/dashboard/api.py` — FastAPI REST endpoints for dashboard
- `sibyl/dashboard/frontend.py` — React SPA (single HTML file)
- `sibyl/dashboard/server.py` — uvicorn async server launcher

### Backtesting
- `sibyl/backtesting/engine.py` — BacktestEngine: historical signal replay + P&L tracking
- `sibyl/backtesting/category_tracker.py` — CategoryPerformanceTracker: per-category win rate, ROI, trade stats

### Core
- `sibyl/core/database.py` — schema + async SQLite manager (18 tables, 9 migration columns)
- `sibyl/core/policy.py` — PolicyEngine: investment policy enforcement (tier classification, quality floors, avoidance, caps, overrides, sports decoupling, Blitz Section 20 exemption)
- `sibyl/core/base_agent.py` — BaseAgent with schedule/poll loop
- `sibyl/core/config.py` — load_yaml, load_env, SibylConfig
- `sibyl/__main__.py` — CLI entry point, agent orchestration

### Clients
- `sibyl/clients/base_data_client.py` — BaseDataClient: shared async HTTP with rate limiting, retries, lifecycle
- `sibyl/clients/kalshi_client.py` — Kalshi API v2 (RSA-PSS auth, place_order, cancel_order)
- `sibyl/clients/polymarket_client.py` — Polymarket CLOB API
- `sibyl/clients/x_client.py` — X API v2 (Bearer Token, Recent Search, Filtered Stream, Stream Rules)
- `sibyl/clients/perplexity_client.py` — Perplexity Sonar API (contextual web research)
- `sibyl/clients/sonar_llm_client.py` — Sonar LLM: synthesis + digest (replaces Anthropic Claude)
- `sibyl/clients/fred_client.py` — FRED: Federal Reserve Economic Data (14 key series)
- `sibyl/clients/bls_client.py` — BLS: Bureau of Labor Statistics v2 (7 key series)
- `sibyl/clients/bea_client.py` — BEA: Bureau of Economic Analysis (NIPA tables)
- `sibyl/clients/fmp_client.py` — FMP: Financial Modeling Prep (quotes, earnings, market movers)
- `sibyl/clients/open_meteo_client.py` — Open-Meteo: weather forecasts + historical (no key)
- `sibyl/clients/noaa_client.py` — NOAA: Climate Data Online (historical observations)
- `sibyl/clients/api_sports_client.py` — API-SPORTS: multi-sport scores/odds/standings
- `sibyl/clients/balldontlie_client.py` — BallDontLie: NBA stats v1
- `sibyl/clients/thesportsdb_client.py` — TheSportsDB: sports metadata
- `sibyl/clients/espn_client.py` — ESPN: public scoreboards/standings (unofficial)
- `sibyl/clients/tmdb_client.py` — TMDb: movie/TV trending, upcoming, search
- `sibyl/clients/wikipedia_client.py` — Wikipedia: pageview statistics
- `sibyl/clients/coingecko_client.py` — CoinGecko: crypto prices, market cap, trending
- `sibyl/clients/feargreed_client.py` — Fear & Greed Index: crypto sentiment
- `sibyl/clients/openfda_client.py` — OpenFDA: drug events, recalls
- `sibyl/clients/clinicaltrials_client.py` — ClinicalTrials.gov: trial search/lookup
- `sibyl/clients/courtlistener_client.py` — CourtListener: court opinions, dockets
- `sibyl/clients/gdelt_client.py` — GDELT: global events/news monitoring
- `sibyl/clients/congress_client.py` — Congress.gov: bills, members, nominations
- `sibyl/clients/verify_all.py` — Master verification script for all 19 data clients

### Pipelines (Sprint 13 + Sprint 16 Revamp)
- `sibyl/pipelines/base_pipeline.py` — BasePipeline: abstract base with edge computation, signal validation, market matching, DB writing
- `sibyl/pipelines/pipeline_manager.py` — PipelineManager: orchestrates all 8 pipelines + correlation engine
- `sibyl/pipelines/economics_pipeline.py` — FRED + BLS + BEA → bracket analysis (CPI, PCE, payrolls, GDP, unemployment, fed funds) + 6 base strategies; FRED series caching; normal CDF probability model
- `sibyl/pipelines/weather_pipeline.py` — Open-Meteo → temperature/rain/snow forecasts for city-specific Kalshi markets; archive API for historical; hottest year analysis
- `sibyl/pipelines/sports_pipeline.py` — ESPN scoreboards → 13-sport game outcome analysis (winner, spread, total score); series ticker detection via SERIES_SPORT_MAP (20+ prefixes); live blowout detection + team record win-rate
- `sibyl/pipelines/crypto_pipeline.py` — CoinGecko (Basic tier, 7 coins) + FearGreed → daily bracket + monthly extremes + sentiment + volatility; normal CDF probability model; series ticker prefix matching
- `sibyl/pipelines/culture_pipeline.py` — TMDb + Wikipedia → entertainment markets (Billboard, reality TV, social media) + trending movies + pageview surge; rate-limited Wikipedia calls (MAX_WIKI_CALLS)
- `sibyl/pipelines/science_pipeline.py` — OpenFDA + ClinicalTrials → FDA approval + trial completion + drug safety + AI/space/watch/GPU catalyst signals
- `sibyl/pipelines/geopolitics_pipeline.py` — CourtListener + GDELT + Congress → 3 strategies (court opinions, bill progress, news volume) [TIER 3 RESTRICTED]
- `sibyl/pipelines/financial_pipeline.py` — FMP → commodity/index/forex bracket analysis (gold, silver, oil, S&P 500, Nasdaq, Dow) + earnings catalyst + market movers; FMP quote caching
- `sibyl/pipelines/correlation_engine.py` — Cross-category correlation: reinforcement boost, composite signals, crowded trade warnings, conflict detection

### Config
- `config/system_config.yaml` — polling intervals, rate limits, platform URLs
- `config/market_intelligence_config.yaml` — whale/volume/orderbook thresholds
- `config/position_lifecycle_config.yaml` — stop guard, EV monitor, exit optimizer, resolution, correlation
- `config/sge_config.yaml` — SGE whitelist + risk policy + Blitz partition config
- `config/ace_config.yaml` — ACE whitelist + risk policy
- `config/portfolio_allocator_config.yaml` — balance sync, capital splits, rebalancing
- `config/risk_dashboard_config.yaml` — drawdown thresholds, daily reset, metric snapshots
- `config/narrator_config.yaml` — LLM model, digest schedule, alert escalation threshold
- `config/breakout_scout_config.yaml` — discovery weights, research sources, freshness decay, Perplexity config
- `config/x_sentiment_config.yaml` — collection mode, budget limits, sentiment/bias/authenticity thresholds
- `config/x_stream_rules.yaml` — 8 filtered stream rules for X API
- `config/category_strategies.yaml` — 12 category strategy profiles + defaults
- `config/investment_policy_config.yaml` — investment policy enforcement config (all 19 sections)

### Tests
- `sibyl/tests/test_execution.py` — 9 tests (Sprint 3)
- `sibyl/tests/test_intelligence.py` — 14 tests (Sprint 2)
- `sibyl/tests/test_portfolio.py` — 11 tests (Sprint 4)
- `sibyl/tests/test_kalshi_client.py` — Kalshi client tests
- `sibyl/tests/test_polymarket_client.py` — Polymarket client tests
- `sibyl/tests/test_database.py` — database tests
- `sibyl/tests/test_monitors.py` — monitor agent tests
- `sibyl/tests/test_dashboard.py` — 15 tests (Sprint 5: notifier + API + frontend)
- `sibyl/tests/test_config.py` — configuration loading tests
- `sibyl/tests/test_models.py` — data model tests
- `sibyl/tests/test_sprint7.py` — 19 tests (Sprint 7: scout + narrator + arbitrage)
- `sibyl/tests/test_sprint8.py` — 37 tests (Sprint 8: X sentiment + Perplexity + schema)
- `sibyl/tests/test_category_strategy.py` — 20 tests (Sprint 9: category strategies + router integration)
- `sibyl/tests/test_dashboard.py` — 20 tests (Sprint 5 + 9: notifier + API + frontend + new endpoints)
- `sibyl/tests/test_sprint10.py` — 22 tests (Sprint 10: backtesting engine + category tracker + correlation penalty + CLI)
- `sibyl/tests/test_policy.py` — 83 tests (Sprint 11: policy engine — tier classification, quality floors, avoidance, data freshness, caps, overrides, sports, multi-category, pre-trade gate)
- `sibyl/tests/test_data_clients.py` — 45 tests (Sprint 12: all 19 data source clients — init, auth, headers, params, constants, completeness)
- `sibyl/tests/test_pipelines.py` — 56 tests (Sprint 13: base pipeline, 8 category pipelines, correlation engine, pipeline manager, signal types, end-to-end, completeness)
- `sibyl/tests/test_blitz.py` — 57 tests (Sprint 14: signal model, config, scanner, executor, Kelly sizing, edge cases, capital isolation, applicable markets, policy exemption, completeness)

### Tools
- `sibyl/tools/validate_pipelines.py` — Live pipeline validation: fetches real Kalshi markets, runs all pipelines, reports signal counts per category
- `sibyl/tools/calibrate_confidence.py` — Confidence calibration: Brier score + calibration curves per pipeline
- `sibyl/tools/live_test_orchestrator.py` — Full-stack live test: gap-fill + pipeline execution + signal reporting
- `scripts/launch_live_test.py` — 24-hour live trading launch script: auth verification, DB check, paper sanity, live mode with auto-shutdown

### Demo
- `sibyl_demo_dashboard.html` — zero-dependency standalone dashboard with mock data + live drift (vanilla JS + SVG)
- `demo_dashboard.py` — FastAPI mock server (alternative, requires HTTP serving)

### Infrastructure
- `Dockerfile` — container build
- `docker-compose.yaml` — local dev stack
- `.env` — API keys (gitignored)

### Reference Documents
- `sibyl-kalshi-investment-policy.md` — authoritative 19-section investment policy (stakeholder-authored)
- `sprint11_stakeholder_summary.md` — Sprint 11 completion report, API account checklist, PostgreSQL strategy, next steps
- `docs/blitz_policy_analysis.md` — Sprint 14: Blitz vs. investment policy conflict analysis, Option A vs. B comparison, recommended policy amendment
