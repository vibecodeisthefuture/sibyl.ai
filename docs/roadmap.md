# Sibyl.ai — Development Roadmap

## Next Steps

### Phase 2: Data Source Clients — COMPLETE (Sprint 12)
- [x] BaseDataClient: shared async HTTP infrastructure with rate limiting, retries
- [x] 19 async data source clients across 8 categories (all verified live)
- [x] Rate limiting, error handling, and health checks per client
- [x] Master verification script (`verify_all.py`)
- [ ] YouTube Data API client (deferred — Google Cloud setup needed)
- [ ] CME FedWatch client (deferred — manual setup guidance needed)
- [ ] Pytrends client (Google Trends scraper — low priority)
- [ ] Glassnode client (on-chain analytics — $29/mo, low priority)

### Phase 3: Category Signal Pipelines — COMPLETE (Sprint 13)
- [x] BasePipeline infrastructure: edge computation, signal validation, market matching, DB writing
- [x] 8 category signal pipelines with 32 analysis strategies total
- [x] Cross-category correlation engine with reinforcement, composite, and risk detection
- [x] PipelineManager orchestration with full-run and single-pipeline modes
- [x] 5 new signal types (DATA_FUNDAMENTAL, DATA_SENTIMENT, DATA_MOMENTUM, DATA_DIVERGENCE, DATA_CATALYST)
- [x] 56 new tests (402 total, all passing)
- [ ] Perplexity Sonar integration for research enrichment across all pipelines (enhancement)
- [ ] Sports in-game real-time circuit breakers (requires WebSocket infrastructure)

### Phase 3.5: Pipeline Integration & Live Calibration — Sprint 16 COMPLETE
- [x] Wire PipelineManager into `__main__.py` CLI (`--agents pipeline` mode)
- [x] Wire BlitzScanner + BlitzExecutor into `__main__.py` CLI (`--agents blitz` mode)
- [x] PipelineAgent scheduling wrapper with configurable interval and category filtering
- [x] SonarLLMClient for synthesis + digest tasks (replaces Anthropic dependency)
- [x] BreakoutScout refactored: Sonar for LLM synthesis, Perplexity for news (replaces NewsAPI)
- [x] Narrator refactored: Sonar for digest generation (replaces Claude Haiku)
- [x] system_config.yaml expanded with pipeline + blitz config blocks
- [x] Phase 5 domain deployment roadmap (docs/phase_5_domain_deployment.md)
- [x] Concurrent pipeline execution: `asyncio.gather()` for init, run, and close (8x speedup)
- [x] Per-pipeline timeouts (45s run, 15s init) — no slow source blocks the system
- [x] Live pipeline validation against real Kalshi API (3,477 markets, 500 events)
- [x] Live Blitz validation (4,613 markets scanned)
- [x] Signal deduplication tuned per category (8 custom windows: 15min–360min)
- [x] Confidence calibration framework (retrospective Brier score + calibration curves)
- [x] BasePipeline `clients` property + sync/async `_analyze()` compatibility shim
- [x] 69 new tests across Sprint 15+16 (528 total)
- [x] **RESOLVED: Pipeline→client API mismatches** — all 6 active pipelines rewired with correct client methods + bracket analysis
- [x] Universal gap-fill system: series ticker prefix scanning discovers 27,044 markets (3.1x improvement)
- [x] CoinGecko Basic tier upgrade + batched multi-coin API calls (7 coins)
- [x] Crypto pipeline: daily bracket + monthly extremes analysis with normal CDF probability model
- [x] Economics pipeline: FRED-backed bracket analysis with caching for CPI/PCE/payrolls/GDP/fed funds
- [x] Sports pipeline: 13-sport series ticker detection + ESPN scoreboard game outcome analysis
- [x] Financial pipeline: commodity/index/forex bracket analysis via FMP quotes
- [x] Science pipeline: AI/space/watch/GPU market analysis with catalyst signals
- [x] Culture pipeline: Billboard/reality TV/social media analysis + timeout fix (MAX_WIKI_CALLS rate limiting)
- [x] Weather pipeline: complete rewrite for actual Kalshi temperature/rain/snow markets + Open-Meteo archive fix
- [x] Validation tooling: expanded category classification with 25+ new keyword entries
- [x] **892 total signals** from 8,801 markets (8/8 pipelines, 0 failures)
- **RESOLVED:** Blitz policy compliance — Option B (policy-exempt) approved by stakeholder. Section 20 implemented in `investment_policy_config.yaml` and enforced in `PolicyEngine`. See `docs/blitz_policy_analysis.md`.

### Sprint 17: Gap-Fill Integration & Signal Amplification — COMPLETE
- [x] Wire gap-fill into live pipeline orchestrator (27,044 markets → all pipelines)
- [x] Gap-fill rate limiting: reduced to ~2 rps to respect Kalshi API limits
- [ ] Full gap-fill validation: confirm Sports, Financial, Weather pipelines produce signals with expanded market inventory
- [ ] Gap-fill caching: store discovered series tickers to avoid re-scanning on every cycle

### Sprint 18: Full Stack Verification & Live Test Prep — COMPLETE
- [x] Fix SGE_BLITZ CHECK constraint across 4 tables (positions, performance, executions, engine_state)
- [x] Full 18-agent paper-mode startup test: ZERO errors
- [x] Gap-fill rate limit optimization (429s: 100+ → ~18)
- [x] 24-hour live trading launch script with 4-step verification
- [x] Kalshi auth verified: $200.21 balance, 1 open position

### Sprint 19: First Live Trading Test — COMPLETE
- [x] Execute live test with $200 funded Kalshi account (5h52m test, 26 real trades)
- [x] Fix critical pipeline market filtering bug (29K markets → 7K actionable)
- [x] Fix critical portfolio allocator capital erosion bug (Blitz carve-out)
- [x] Monitor signal generation across all 8 pipelines (30,042 signals generated)
- [x] Verify order execution via OrderExecutor in live mode (26 Kalshi fills)
- [x] Verify risk management system (drawdown tracking, circuit breakers, policy enforcement)
- [x] Generate post-test analysis report with full trade log + component ranking
- [ ] ~~After successful 24hr test → begin 30-day no-X-API baseline~~ (deferred: need data fixes first)

### Sprint 20: CRYPTO-ONLY PIVOT — Depth Over Breadth (Next)
**STRATEGIC REDIRECT: Disable all 7 non-crypto pipelines. Single engine (SGE, 95% capital). Per-category risk management. Fix data plumbing.**

**Root cause of Sprint 19 failure:** 26,282 crypto signals, ZERO executed. Dead zone: Signal Router approved (ev=0.0475 > SGE floor 0.03) but OrderExecutor rejected (ev=0.0475 < Tier 2 floor 0.06).

**Phase 1: Config Surgery**
- [ ] Disable all non-crypto pipelines (system_config.yaml categories: "crypto")
- [ ] Disable ACE + BLITZ engines. SGE=95%, cash_reserve=5%.
- [ ] Implement per-category risk profiles in investment_policy_config.yaml
- [ ] Crypto profile: min_conf=0.55, min_ev=0.02, kelly=0.25, max_pos=5%, max_cat=60%, stop_loss=25%
- [ ] Align routing/execution thresholds (no dead zone)
- [ ] Loosen risk dashboard: circuit breaker -15%, daily loss -8%, drawdown CAUTION -15%
- [ ] Faster cycles: dedup 5min, pipeline interval 5min, horizon 7d

**Phase 2: Crypto Pipeline Enhancement**
- [ ] Real-time price tracking (fix $0 P&L bug)
- [ ] Position aggregation (no duplicate rows)
- [ ] Enhanced bracket analysis (Black-Scholes-style for BTC/ETH above/below)
- [ ] Orderbook-informed EV (use Kalshi bid/ask)
- [ ] Multi-timeframe momentum (1h, 4h, 1d)

**Phase 3: Live Validation**
- [ ] 8-hour crypto-only live test: target >50 trades, >5 markets, break-even P&L
- [ ] Sprint 20 test report. Decision gate for 24-hour extension.

See: `Sprint20_Crypto_Pivot_Plan.docx` for full plan.

### Sprint 21: Extended Crypto Trading & Performance Baseline
- [ ] 24-hour crypto-only live test
- [ ] Begin 30-day crypto performance baseline
- [ ] Confidence calibration: retrospective Brier score on live crypto outcomes
- [ ] Evaluate re-enabling economics pipeline (was +$4.69 in Sprint 19)

### Sprint 22+: Selective Category Re-Enablement
- [ ] Re-enable economics with its own risk profile (if crypto baseline profitable)
- [ ] Re-enable weather with its own risk profile
- [ ] Each category gets dedicated risk profile before re-enabling
- [ ] Re-enable ACE engine with category-specific routing
- [ ] Evaluate sports, culture, financial for data quality before re-enabling

### Future: Backtesting & Confidence Tuning
- [ ] Backtest pipelines against historical market data (Sprint 10 engine integration)
- [ ] Run confidence calibration framework on live signals to compute per-pipeline Brier scores
- [ ] Apply calibration adjustments: over/under-confident pipeline tuning
- [ ] Signal quality scoring: rank pipelines by ROI contribution
- [ ] Docker Compose integration: pipeline workers + Blitz scanner as separate containers

### Phase 4: PostgreSQL Migration
- [ ] Database abstraction layer (`sibyl/core/db_backend.py`) with async interface
- [ ] asyncpg-based PostgreSQL backend behind same interface
- [ ] Docker Compose PostgreSQL 16 service
- [ ] Migration tool: SQLite → PostgreSQL data transfer
- [ ] `--db postgres` CLI flag to select backend
- [ ] Future: per-user schema isolation, PgBouncer connection pooling, RBAC

### Sprint 6: Production Hardening (deferred — awaiting hardware)
- [ ] WebSocket support for real-time Polymarket + Kalshi data
- [ ] Kubernetes deployment manifests for homelab (Talos Linux K8s cluster)
- [ ] NAS-hosted persistent PostgreSQL (QNAP TS-832XU)
- [ ] Automated CI/CD pipeline (GitHub Actions → container registry → K8s rollout)
- [ ] Health check endpoint for K8s liveness/readiness probes
- [ ] Add CLI `--query` mode for ad-hoc database queries

### Remaining Backlog
- [ ] Narrator digest delivery to email via SIBYL_EMAIL (in addition to ntfy.sh)
- [ ] Dashboard: research tab showing Scout sentiment data per market
- [ ] Dashboard: X sentiment feed showing live windows and signals
- [ ] FinBERT sentiment model integration (deferred to GPU workstation)
- [ ] Perplexity Sonar Pro upgrade path for high-conviction markets
- [ ] Auto-tuning: adjust category modifiers based on historical performance
- [ ] Multi-user auth + subscription-gated access (far future, post-homelab)
