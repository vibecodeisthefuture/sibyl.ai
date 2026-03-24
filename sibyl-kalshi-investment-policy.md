---
project: Sibyl — Prediction Market Tracker & Autonomous Investing Agent
type: policy
status: active
started: 2026-03-20
last_updated: 2026-03-20
area: personal
tags:
  - sibyl
  - kalshi
  - investment-policy
  - market-strategy
  - data-sources
  - policy
related:
  - "[[Projects/sibyl-overview]]"
  - "[[Projects/sibyl-kalshi-market-policy-brainstorm]]"
  - "[[Projects/sibyl-x-sentiment-framework]]"
  - "[[Learning/sports-betting-market-dynamics-in-game-vs-pre-game]]"
---

# Sibyl — Kalshi Investment Policy

> **Classification:** Finalized — Graduated from [[Projects/sibyl-kalshi-market-policy-brainstorm]]
> **Effective date:** 2026-03-20
> **Authority:** This policy governs all Sibyl engine behavior on Kalshi by default. Deviations require the **Policy Override Protocol** defined in Section 12. All Signal Router, Signal Generator, and Portfolio Allocator logic must reference this document at runtime.

---

## 1. Policy Scope and Governance

This document is the **machine-readable, authoritative investment policy** for all Sibyl operations on the Kalshi platform. It defines per-category strategy, approved data sources, signal architecture, capital allocation caps, avoidance rules, and the override protocol.

**Governing principles (from stakeholder directive):**

- An educated risk is ALWAYS acceptable. An uneducated risk is NEVER acceptable.
- Markets with no Sibyl data source coverage are flagged `no_signal_coverage` and skipped — no exceptions.
- No hard limit on total open position count. Positions must favor a blend of high Sharpe ratio and high return. Overall portfolio value exposure limits are enforced per-category.
- Sibyl operates as an independent entity. There is no cross-system exposure tracking with TradeBot. All crypto and other category caps are self-contained within Sibyl's own capital.
- The override protocol is fully autonomous. Rafael reviews override reasoning post-hoc — no human approval gate.

---

## 2. Tier Classification

| Tier | Description | Engines | Default Stance |
| :--- | :--- | :--- | :--- |
| **Tier 1 — Steady** | Data-rich, scheduled, predictable base rates | SGE (primary), ACE (surprises only) | Auto-trade within policy |
| **Tier 2 — Volatile** | Momentum-driven, sentiment-sensitive, live data required | ACE (primary), SGE (if sustained) | Auto-trade within policy + signal quality floor |
| **Tier 3 — Restricted** | High uncertainty, limited advance signal, black swan risk | ACE only (with override) | No auto-entry; override protocol required |

---

## 3. Market Category Index

| # | Category | Engine Fit | Return Ceiling | Volatility | Policy Tier |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | [[#4. Weather]] | SGE | Low | Low | Tier 1 — Steady |
| 2 | [[#5. Sports]] | ACE | High | Very High | Tier 2 — Volatile |
| 3 | [[#6. Mentions (Political & Financial)]] | SGE + ACE | Medium | Low–Medium | Tier 1/2 — Hybrid |
| 4 | [[#7. Culture & Entertainment]] | ACE | Medium–High | Medium | Tier 2 — Sentiment-Driven |
| 5 | [[#8. Economics & Macro]] | SGE | Low–Medium | Low | Tier 1 — Data-Rich |
| 6 | [[#9. Crypto & Digital Assets]] | ACE | High | Very High | Tier 2 — Independent |
| 7 | [[#10. Science & Technology]] | SGE + ACE | Medium–High | Low–High | Tier 1/2 — Binary Events |
| 8 | [[#11. Geopolitics & Legal]] | ACE (cautious) | High | Very High | Tier 3 — Restricted |

---

## 4. Weather

**Policy tier:** Tier 1 — Steady
**Engine fit:** SGE — consistent, model-backed signals
**Return ceiling:** Low | **Volatility:** Low

### Edge Source

Ensemble model lag arbitrage. Most bettors anchor to a single consumer forecast updated infrequently. Sibyl consumes ensemble model data hourly and detects when model consensus has shifted but the market price has not repriced.

### Ensemble Confidence Handling

Ensemble model confidence is used as a **continuous weight** on position size, calibrated quarterly against historical outcomes. If backtesting demonstrates that a binary gate (enter only when ensemble spread < threshold) produces higher average profit return and more consistent prediction outcomes, the system switches to binary gate mode. The Portfolio Allocator tracks both approaches in shadow mode during the first 30-day calibration period, then locks to whichever produces superior results.

### Approved Data Sources

| API | Cost | Function | Latency | Role |
| :--- | :--- | :--- | :--- | :--- |
| **open-meteo** | Free | Temperature, precipitation, wind, solar, air quality; 7-day forecast + historical to 1940 | Hourly; 1km resolution | Primary forecast source |
| **open-meteo Ensemble API** | Free | Multi-model outputs (GFS, ECMWF, ICON, Gemini) — compute spread for confidence scoring | Hourly | Confidence scoring — narrow spread = high conviction |
| **open-meteo Historical Weather API** | Free | Historical data back to 1940 at any global lat/lon | On-demand query | Base rate calibration (e.g., "How often does NYC hit 95°F in July?") |
| **NOAA Climate Data Online** | Free | US weather station records, monthly/annual summaries | Daily batch | Backup for US markets; extreme-event base rate calculation |
| **NWS API** (api.weather.gov) | Free | Official US National Weather Service hourly/daily forecast | Hourly | Secondary US model; divergence signal vs. open-meteo |

### Signal Types

- **Forecast Consensus Signal:** ≥4 of 6 ensemble models agree on an outcome → SGE signal
- **Forecast Shift Signal:** 24h model consensus shift >15% probability for a market that hasn't repriced → mispricing flag
- **Extreme Threshold Signal:** Market odds deviate >10% from computed historical base rate for record events

### Avoidances

- Markets resolving > 7 days out
- Tropical storm/hurricane track markets
- International locations without open-meteo ensemble coverage
- Markets with < $1,000 open interest

---

## 5. Sports

**Policy tier:** Tier 2 — Volatile
**Engine fit:** ACE — momentum-driven, requires live data, high variance
**Return ceiling:** High | **Volatility:** Very High

> [!IMPORTANT] Architectural Decoupling — Pre-Game vs. In-Game
> Per [[Learning/sports-betting-market-dynamics-in-game-vs-pre-game]], pre-game and in-game markets are treated as **architecturally distinct asset classes** with separate position sizing, Kelly fractions, and circuit breakers. The Signal Router must tag every sports signal as `PRE_GAME` or `IN_GAME` before routing.

### Pre-Game Policy

Pre-game signals are generated from composite multi-layer analysis. Standard ACE position sizing applies (0.35× fractional Kelly).

**Signal layers (ordered by reliability):**

| Layer | Signal Type | Reliability | Update Frequency |
| :--- | :--- | :--- | :--- |
| Team form | Win/loss streak, points differential, recent SOS | High | Daily |
| Injury reports | Starting player status (out/doubtful/probable/questionable) | High — time-sensitive | Hours before game |
| Venue & schedule | Home/away, rest days, travel distance | Medium-High | Weekly |
| Weather (outdoor) | Temperature, wind, precipitation at game venue | Medium-High | Hourly |
| Head-to-head history | Historical matchup record | Medium | Static |
| Player health history | Career injury frequency by body part and season period | Medium | Weekly |

**Pre-game entry window:** 1 hour to 7 days before game time. Composite signal from all available layers. Focus on injury impact (star player out = significant odds shift opportunity if market hasn't repriced) and weather impact for outdoor venues.

### In-Game Policy

In-game markets operate under a separate, more conservative risk framework.

**In-game position sizing rules:**

- **Partial Kelly shrinkage factor:** 0.50× applied to all in-game confidence estimates (on top of ACE's standard 0.35× Kelly fraction, yielding an effective 0.175× Kelly for in-game)
- **Liquidity-capped sizing:** No single in-game wager exceeds 2% of top-of-book depth to prevent slippage and price impact
- **Iterative exposure monitoring:** Sizing for live wagers must account for existing pre-game exposure on the same event; the combined position is optimized collectively, not independently
- **Polymarket 3-second delay:** ACE executor uses limit orders (not market orders) for all in-game Polymarket sports contracts to mitigate slippage from the mandatory marketable order delay

**In-game circuit breakers (event-triggered, discrete):**

| Trigger | Action | Cooldown |
| :--- | :--- | :--- |
| Material Event (goal, red card, ejection, major penalty) | Suspend all new entries on that market | 5 seconds post-event for order book rebuild |
| VAR / Official Review | Suspend all new entries on that market | Until review concludes + 3 seconds |
| Suspicious activity alert / integrity flag | Immediate halt; no new entries | Manual review required |
| Rapid odds swing (>25% price move in <60 seconds) | Suspend new entries | 10 seconds; re-evaluate signal confidence before re-entry |

**In-game signal source:** Live momentum tracking only — score differential, scoring runs, foul trouble, turnovers. Cross-reference with venue crowd signals if available.

### Approved Data Sources

| API | Cost | Coverage | Latency | Role |
| :--- | :--- | :--- | :--- | :--- |
| **SportSRC** | Free | Football, NBA, UFC | ~50ms REST; WebSocket coming | Primary live game state — 30s polling for live games |
| **API-SPORTS** (api-sports.io) | $10/mo minimum | 30+ sports, 2,000+ competitions | 15-second updates on paid tier | Primary paid dependency — live coverage, lineup changes, real-time game state |
| **BALLDONTLIE** | Free tier | NBA, NFL, MLB, NHL, EPL, NCAAF, NCAAB, MMA | REST | Static enrichment: box scores, player stats, injury history |
| **TheSportsDB** | Free (community) | Broad sports metadata | Daily batch | Static enrichment: team metadata, venue info, historical head-to-head |
| **open-meteo** | Free | Weather at venue coordinates | Hourly | Outdoor game weather signal (cross-category integration) |
| **X API** | Developer account | Breaking injury/conflict news | Real-time stream | Per [[Projects/sibyl-x-sentiment-framework]] — injury news breaks on X before official reports |
| **Reddit API** | Free (100 req/min OAuth) | r/nfl, r/nba, r/baseball, r/soccer | Near-real-time | Injury news, insider reports, live game thread crowd momentum |

**Dropped:** Sportmonks — free plan limited to soccer/cricket, overlaps API-SPORTS coverage at the paid tier. Insufficient marginal value.

### Tiered Data Architecture

```
STATIC (refresh daily)
├── TheSportsDB — team metadata, venue, head-to-head history
└── BALLDONTLIE — player injury history, season stats

SCHEDULED (refresh every 2h pre-game, every 30s live)
├── API-SPORTS ($10/mo) — live score, lineup changes, real-time game state
├── SportSRC — secondary live feed, latency validation
└── open-meteo — outdoor game weather at venue coordinates

LIVE SENTIMENT (continuous stream)
├── X Filtered Stream — injury breaks, conflict reporting, official lineup tweets
└── Reddit OAuth — subreddit live game threads for crowd momentum reads
```

### Regulatory Risk Flag

Per research findings, Kalshi sports contracts face active state-level litigation (Arizona criminal charges, Massachusetts injunction, Nevada case remand). The Signal Router must monitor Kalshi platform announcements for any sports category suspension or restriction changes. If a state-level ruling restricts sports contracts, all open sports positions are flagged for immediate review.

### Avoidances

- Sports announcement polls (e.g., "Will [player] be named MVP?") — governed by small committees, not market forces
- Games where starting lineup has not been confirmed and market resolves within 2 hours
- Outdoor markets in severe weather season without weather signal confirmation
- Sports with very low Kalshi liquidity (MMA undercard, minor league soccer)
- Prop-style cultural crossover markets (jersey sales, athlete endorsements, appearance bets)

---

## 6. Mentions (Political & Financial)

**Policy tier:** Tier 1/2 — Hybrid
**Engine fit:** SGE (scheduled events) + ACE (surprise prints, breaking political decisions)
**Return ceiling:** Medium | **Volatility:** Low–Medium (scheduled) / Medium–High (surprises)

### Edge Source

Pre-instantiated indications — public data signals that exist before market resolution:

| Market Type | Pre-Existing Signal | Signal Source |
| :--- | :--- | :--- |
| Senate/House vote outcomes | Published whip counts, announced vote commitments | Congress.gov API, X stream |
| Fed rate decisions | Fed Funds Futures implied probability | CME FedWatch (web scrape) |
| Company earnings beats | EPS consensus vs. historical beat rate per company | FMP Earnings Calendar API |
| CPI / inflation prints | Economist survey consensus | FRED API, BLS API |
| Merger/acquisition closing | SEC filing timeline, regulatory approval calendar | SEC EDGAR API |
| Government shutdown | Floor vote schedule, leadership statements | Congress.gov API, X stream |

### Earnings Call Real-Time Tracking

Per stakeholder directive: Sibyl tracks earnings call transcripts in real-time via SEC 8-K filings and FMP transcript API. Guidance language (raised/lowered guidance, surprise commentary) is parsed as an in-call signal for post-release ACE entry within the 30–120 second reaction window.

**Speed hierarchy:** Information first, speed second. The 8-K filing feed + BLS API surface results before most market participants process them. This is ACE territory for fast, high-conviction post-release entry.

### Approved Data Sources

| API | Cost | Function | Latency | Role |
| :--- | :--- | :--- | :--- | :--- |
| **FRED API** | Free (key required) | 840,000 economic series: CPI, GDP, unemployment, Fed funds rate | Near-real-time on release day | Primary macro signal source |
| **BLS API** | Free (key required) | Jobs reports, CPI, PPI, unemployment | On BLS schedule; available immediately on release | Core US macro releases; BLS publishes 60s before most data providers |
| **SEC EDGAR API** | Free (no key) | All public company filings: 10-K, 10-Q, 8-K (material events), earnings transcripts | Real-time on filing | 8-K filings = breaking news: earnings, mergers, leadership changes |
| **Financial Modeling Prep (FMP)** | Free tier (250 req/day); $14/mo Basic | Earnings calendar, EPS consensus vs. actual, earnings transcripts | Free: pre-event; paid: real-time | Earnings calendar (pre-event positioning) + beat/miss rate history |
| **Congress.gov API** | Free (key required) | Bill status, vote counts, amendment tracking, committee schedules | Updated throughout business day | Legislative event calendar; whip count data |
| **CME FedWatch** | Free (web scrape) | Implied Fed rate probabilities from futures market | Real-time during market hours | Best proxy for market consensus on rate decisions |
| **OpenSecrets API** | Free (key required) | Political finance, campaign contributions, lobbying data | Periodic updates | Secondary signal: political finance flows for vote prediction |
| **realtime-newsapi** | Open-source (self-hosted) | Financial news aggregation from Reuters, Bloomberg, WSJ, SEC, Seeking Alpha | WebSocket real-time stream | Self-hosted; near-zero latency financial news ingestion |

**Dropped:** Alpha Vantage — limited free tier (25 req/day), duplicates FMP and FRED functionality. $50/mo premium tier offers insufficient marginal value over existing stack.

### Strategy

**Pre-event positioning:** Enter 48–72 hours before scheduled events when consensus distribution supports a clear directional bet. Size per SGE rules.

**Post-release reaction (ACE):** First 30–120 seconds after an economic release or vote outcome. SEC 8-K feed + BLS API surface results before most participants. Fast, high-conviction entry.

**Earnings beat rate baseline:** Historical beat rates per company serve as a Bayesian prior. When Kalshi market prices diverge from the computed prior by >10%, that's a signal.

### Avoidances

- Sports announcement polls — per stakeholder directive
- Markets with vague resolution criteria — always read Kalshi resolution specification before entering
- Political markets decided by a single individual's private decision with no advance signal (e.g., presidential pardons, unannounced executive orders)

---

## 7. Culture & Entertainment

**Policy tier:** Tier 2 — Sentiment-Driven
**Engine fit:** ACE — sentiment-driven, momentum-sensitive
**Return ceiling:** Medium–High | **Volatility:** Medium

### Core Distinction: Authority-Gated vs. Popularity-Driven

| Pursue | Avoid |
| :--- | :--- |
| Box office #1 ranking (weekend gross) | Rotten Tomatoes Tomatometer score |
| Streaming chart position (Netflix, Spotify) | Oscar/Emmy/Grammy winners |
| Audience Choice Awards (People's Choice, MTV) | Critics' Circle awards |
| Billboard chart positions | Metacritic score thresholds |
| Social media vote polls (official fan votes) | Film festival jury prizes |
| YouTube trending #1 | Pulitzer, Man Booker, literary prizes |

### Approved Data Sources

| API | Cost | Function | Latency | Role |
| :--- | :--- | :--- | :--- | :--- |
| **X API** | Developer account | Real-time public sentiment, trending topics, viral content | Real-time stream | Primary sentiment source — per [[Projects/sibyl-x-sentiment-framework]] |
| **Reddit API** | Free (100 req/min OAuth) | r/movies, r/television, r/music, r/boxoffice — community sentiment | Near-real-time | Box office predictions from r/boxoffice are well-calibrated |
| **pytrends** (Google Trends) | Free (unofficial Python wrapper) | Search interest by term, region, trending topics | ~1h delay; batch | Leading indicator for public interest surge |
| **YouTube Data API v3** | Free (10,000 units/day) | View counts, like ratio, trending videos | Near-real-time | Trailer/teaser engagement as pre-release indicator |
| **TMDb API** | Free (key required) | Movie/show metadata, user ratings, watchlist counts, release calendars | Daily updates | Audience scores (popularity-driven), not critic scores |
| **Spotify Charts** | Free (web scrape) | Daily/weekly top charts by country | Daily | Streaming chart position for music markets |
| **Wikipedia Pageviews API** | Free (no key) | Per-article daily view counts | Daily batch | Celebrity page view spikes = public interest surge |

### Signal Architecture

```
TRENDING SIGNAL (7+ days out)
└── pytrends surge + YouTube engagement spike = early momentum indicator

MOMENTUM SIGNAL (2–7 days out)
└── X sentiment shift + Reddit community sentiment = directional confirmation

LATE SIGNAL (< 48h before resolution)
└── Wikipedia page views + Spotify/streaming chart movement = final momentum lock
```

### Avoidances

- Any market with Rotten Tomatoes, Metacritic, Academy, or BAFTA as final arbiter
- Markets where resolution depends on a single person's subjective opinion
- Award show markets involving guild/peer votes that are not publicly tracked
- Music awards decided by critic panels

---

## 8. Economics & Macro

**Policy tier:** Tier 1 — Data-Rich
**Engine fit:** SGE — scheduled releases, well-documented consensus, computable edge
**Return ceiling:** Low–Medium | **Volatility:** Low (consensus plays) / High (surprise prints)

### Edge Source

The edge is in the **distribution**, not the median. When 80% of forecasters predict outcome A but the market prices it at 55%, that's an SGE signal. Sibyl maintains a `macro_release_calendar.yaml` updated annually from BLS, BEA, and Fed published schedules.

### Approved Data Sources

| API | Cost | Function | Latency | Role |
| :--- | :--- | :--- | :--- | :--- |
| **FRED API** | Free (key required) | 840,000 macro series; CPI, GDP, PCE, unemployment, Fed funds rate | Real-time on release | Core macro data source |
| **BLS API** | Free (key required) | CPI, PPI, jobs report, unemployment, wage growth | On release schedule | Raw release data; calendar published annually |
| **BEA API** | Free (key required) | GDP, personal income, PCE inflation | Quarterly GDP; monthly PCE | GDP advance/revised estimates |
| **CME FedWatch** | Free (web scrape) | Implied probability of Fed rate changes | Real-time during market hours | Market consensus proxy for rate decisions |
| **FMP Earnings Calendar** | Free tier / $14/mo | Earnings surprise data, economic event calendar | Pre-event + real-time on release | Macro-earnings crossover context |

**Dropped:** World Bank API — quarterly/annual cadence too slow for Kalshi market timelines; primarily international focus which is low priority. OECD API — same reasoning; monthly leading indicators are useful but overlap with FRED's superior dataset.

### Strategy

**Calendar arbitrage:** Pre-position 48–72h before release when consensus distribution gives a clear edge.

**Surprise premium:** Releases with a history of large misses (e.g., NFP Payrolls) should have market prices adjusted for surprise risk before entering.

### Avoidances

- Markets resolving on Fed decisions within 24h of the meeting (spread too tight)
- International macro markets for countries with unreliable data release calendars
- Qualitative macro markets with ambiguous resolution criteria (e.g., "Will the economy enter recession?")

---

## 9. Crypto & Digital Assets

**Policy tier:** Tier 2 — Independent
**Engine fit:** ACE (price/adoption markets) + SGE (regulatory/approval markets)
**Return ceiling:** High | **Volatility:** Very High

> [!IMPORTANT] Independence from TradeBot
> Per stakeholder directive: Sibyl and TradeBot operate as two separate entities. Sibyl does NOT adjust crypto exposure based on TradeBot's investments and operates on the premise of zero access to TradeBot infrastructure. All caps in this section are self-contained within Sibyl's own capital allocation.

### Market Sub-Types

| Sub-type | Engine | Edge | Example |
| :--- | :--- | :--- | :--- |
| Price level markets (BTC above $X) | ACE | On-chain accumulation, derivative funding rates | BTC above $100k by year-end |
| ETF approval/flows | SGE | SEC filing timeline, commissioner vote patterns | Spot ETH ETF approved by Q2 |
| Regulatory action | SGE | Public agency calendars, enforcement patterns | SEC suit dismissed by EOY |
| Adoption rate markets | ACE | On-chain transaction counts, wallet growth | Lightning Network reaches X TXs/day |

### Approved Data Sources

| API | Cost | Function | Latency | Role |
| :--- | :--- | :--- | :--- | :--- |
| **CoinGecko API** | Free (30 req/min) | Price, market cap, volume, trending coins, exchanges | Real-time on free | Primary crypto price data; global aggregation across 600+ exchanges |
| **Glassnode** | Free tier (limited) | On-chain data: active addresses, transaction counts, miner flows, exchange flows | Daily on free; real-time on paid | Strongest leading indicators for BTC/ETH price direction |
| **SEC EDGAR** | Free | ETF filing status, crypto-related S-1 and 8-K filings | Real-time on filing | ETF approval pipeline tracking for SGE regulatory signals |
| **Alternative.me Fear & Greed Index** | Free (no key) | Composite crypto sentiment score (0–100) | Daily | Contrarian signal — extreme fear/greed historically precedes reversals |
| **X Sentiment** | Developer account | Real-time crypto narrative, influencer signals | Real-time | Per [[Projects/sibyl-x-sentiment-framework]] — crypto narrative on X is a leading price indicator |

**Dropped:** CoinMarketCap API — overlaps CoinGecko with fewer free requests (10,000/month vs. CoinGecko's 30/min). Insufficient marginal value.

### Avoidances

- Price-based markets with resolution dates > 90 days (too much variance)
- Markets on small-cap altcoins with < $50M market cap (manipulation risk)
- Any rug-pull adjacent new token launch markets

---

## 10. Science & Technology

**Policy tier:** Tier 1/2 — Binary Events
**Engine fit:** SGE (FDA PDUFA dates) + ACE (clinical trial results, tech announcements)
**Return ceiling:** Medium–High | **Volatility:** Low pre-event / High on event day

### Edge Source

FDA approval base rates by indication, AdCom vote outcome as predictor. PDUFA dates published months in advance. Historical approval rates are quantifiable and exploitable.

**Approval base rate signals:**

- AdCom vote 14-2 in favor → historical approval rate >85%
- Standard NME NDA approval rate ~90% when AdCom recommends approval
- AdCom split or negative → significant odds shift required
- Complete Response Letters (rejection) are public; re-submission timeline is predictable

### Approved Data Sources

| API | Cost | Function | Latency | Role |
| :--- | :--- | :--- | :--- | :--- |
| **openFDA API** | Free (no key) | Drug approvals, adverse events, device approvals | Updated as decisions issued | Primary FDA data — NDA/BLA status tracking |
| **ClinicalTrials.gov API** | Free (no key) | All registered clinical trials, phase, status, completion dates, results | Updated continuously | Trial completion and results posting as pre-approval signal |
| **BiopharmaWatch PDUFA Calendar** | Free (web scrape) | Upcoming PDUFA decision dates, AdCom dates | Daily updates | PDUFA calendar for SGE pre-event positioning |
| **SEC EDGAR** | Free | Biotech 8-K filings (trial results, FDA decisions) | Real-time on filing | 8-K filing within 4 business days of material FDA decisions |

**Dropped:** NASA API — Kalshi space markets are rare and typically low liquidity; insufficient signal frequency to justify integration. GitHub API — too indirect a proxy for tech adoption; signal quality too low for reliable prediction.

### Strategy

**PDUFA positioning:** Enter 7–14 days before PDUFA date when AdCom data supports a clear directional bet. Monitor ClinicalTrials.gov for trial status updates.

**Tech announcement markets (WWDC, CES, Google I/O):** Use X sentiment + pre-event rumor monitoring. ACE territory — unpredictable content but exploitable momentum.

### Avoidances

- Phase 1 or early Phase 2 trial outcome markets (approval probability near-zero)
- Gene therapy / first-in-class novel modality markets where FDA has no prior precedent
- Markets on tech products under NDA embargo (no advance signal available)

---

## 11. Geopolitics & Legal

**Policy tier:** Tier 3 — Restricted
**Engine fit:** ACE (cautious) — high potential return but extreme information risk
**Return ceiling:** High | **Volatility:** Very High

> [!WARNING] Tier 3 — No Auto-Entry
> Sibyl will NOT auto-enter geopolitics/legal markets from standard signal generation. Entry requires the full Policy Override Protocol (Section 12).

### Override Entry Requirements (All Must Be True)

- Signal confidence ≥ 0.85
- At least 3 independent corroborating data sources
- EV estimate ≥ 15%
- No universal avoidance rules violated
- Override logged with full reasoning for stakeholder post-hoc review

### Markets Worth Monitoring (Not Auto-Trading)

| Sub-type | Why Interesting | Why Restricted |
| :--- | :--- | :--- |
| Supreme Court decisions | Oral argument analysis ~70% predictive | Small voter count; noisy signals |
| International elections | Polling data when available | Polls fail systematically on surprises |
| DOJ/FTC antitrust outcomes | Public court filings show case strength | Judge is sole decision-maker |
| Treaty ratification | Senate vote count publicly trackable | Geopolitical complexity; timing uncertain |

### Approved Data Sources (Monitoring Only)

| API | Cost | Function | Role |
| :--- | :--- | :--- | :--- |
| **CourtListener API** | Free | Federal court filings, case dockets, opinions | DOJ/FTC/SEC case status tracking |
| **GovTrack API** | Free (no key) | Congressional bill/vote tracking, legislator records | Senate treaty/confirmation vote counts |
| **GDELT Project API** | Free | Global event database: conflict, protest, geopolitical events | Geopolitical event spike monitoring |
| **X Sentiment** | Developer account | Real-time geopolitical sentiment | Per [[Projects/sibyl-x-sentiment-framework]] |

**Dropped:** ACLED API — academic/NGO access requirements create friction; conflict monitoring overlaps with GDELT. Reuters/AP Wire — paid service; the self-hosted realtime-newsapi covers financial wire news adequately.

---

## 12. Capital Allocation Caps by Category

These caps apply per engine as a percentage of that engine's available capital:

| Category | SGE Cap | ACE Cap | Combined Cap |
| :--- | :--- | :--- | :--- |
| Weather | 15% | 5% | 15% |
| Sports (Pre-Game) | 5% | 15% | 18% |
| Sports (In-Game) | 3% | 10% | 12% |
| Mentions (Political) | 15% | 10% | 20% |
| Mentions (Financial/Earnings) | 15% | 15% | 25% |
| Culture & Entertainment | 5% | 20% | 22% |
| Economics & Macro | 20% | 10% | 25% |
| Crypto & Digital Assets | 10% | 20% | 25% |
| Science & Technology | 15% | 15% | 25% |
| Geopolitics & Legal | 0% | 10% | 10% |

> [!NOTE] Sports Cap Split
> Sports pre-game and in-game have separate caps reflecting their architectural decoupling. The combined sports cap across both sub-types is 28% (consistent with the brainstorm allocation). The split ensures in-game's higher-risk, higher-speed trading does not consume pre-game's more stable allocation.

---

## 13. Universal Avoidance Rules

Regardless of signal strength or category, Sibyl will **never** enter a market matching any of these conditions:

- **Liquidity floor:** Market open interest < $1,000
- **Resolution ambiguity:** Resolution criteria includes subjective language ("at the discretion of", "as determined by") without a specific, named, public arbiter
- **Authority-gated outcomes:** Resolution depends solely on a small expert panel with no public leading data
- **Duplicate exposure:** Both SGE and ACE hold the same directional position on the same Kalshi market simultaneously
- **Announcement polls:** Sports or entertainment "Will X be named Y?" markets decided by selection committees
- **Sports prop crossover:** Sports markets that are really culture markets (jersey sales, endorsements, appearance bets)
- **No signal coverage:** Any market where Sibyl has zero approved data source coverage is flagged `no_signal_coverage` and skipped

---

## 14. Signal Quality Floor by Tier

| Tier | Min Confidence | Min EV | Min Signal Count |
| :--- | :--- | :--- | :--- |
| Tier 1 — Steady (SGE) | 0.60 | +3% | N/A (data-driven) |
| Tier 2 — Volatile (ACE) | 0.65 | +6% | ≥ 20 tweets/signals in window |
| Tier 2 — In-Game (ACE) | 0.70 | +8% | ≥ 2 independent live data confirmations |
| Tier 3 — Restricted (ACE) | 0.85 | +15% | ≥ 3 independent source confirmations |

> [!NOTE] In-Game Quality Floor
> In-game signals carry a higher confidence and EV threshold than standard Tier 2, reflecting the elevated uncertainty from small-sample game-state estimation and the behavioral impulsivity trap documented in the sports research.

---

## 15. Data Freshness Rules

| Data Type | Maximum Staleness Before Signal Invalidation |
| :--- | :--- |
| Weather forecast (open-meteo) | 90 minutes |
| Sports injury report | 2 hours pre-game |
| Live game score/state | 60 seconds |
| In-game circuit breaker cooldown | Per event type (5s / review duration / 10s) |
| Economic release data | 30 minutes post-release |
| X sentiment window | 5-minute rolling window (per [[Projects/sibyl-x-sentiment-framework]]) |
| Earnings consensus | 24 hours |
| Earnings call transcript (live) | Real-time — stale after 5 minutes post-call |
| FDA approval calendar | 48 hours |
| Geopolitical event data | 1 hour |

---

## 16. Multi-Category Market Handling

When a Kalshi market spans multiple categories (e.g., "Will OPEC+ cut oil production?" = geopolitics + economics):

1. The Signal Router assigns the market to the category where Sibyl has the **strongest data source coverage**
2. The signal is generated using only approved data sources from the assigned category
3. If data coverage is approximately equal across categories, the market is assigned to the **higher tier** (more restrictive) category
4. If no category provides adequate data coverage, the market is flagged `no_signal_coverage` and skipped
5. The governing principle: an educated risk from the best-covered angle is always acceptable; an uneducated risk from a poorly-covered angle is never acceptable

---

## 17. Policy Override Protocol

The override protocol is a **safety valve**, not a loophole. It is fully autonomous — no human approval gate. Rafael reviews override reasoning post-hoc.

### Override Eligibility (ALL Must Be True)

- Signal confidence ≥ 0.90
- EV estimate ≥ 20%
- At least 3 independent signal sources confirm the directional bet (not 3 signals from the same pipeline)
- Portfolio Allocator flags the override opportunity in `system_state`
- **No universal avoidance rules are violated** — overrides apply to category caps and tier restrictions only

### Override Execution

1. Portfolio Allocator writes an `override_candidate` record to `system_state` with full signal metadata and **human-readable reasoning summary**
2. Signal Router routes the market to ACE with a `POLICY_OVERRIDE` flag
3. ACE Signal Filter applies **reduced position size** — max 50% of normal ACE max position size
4. Position is logged to `executions` with `override: true` and `override_reasoning: [text]` fields
5. Override performance is tracked separately in `performance` table for quarterly calibration

### Override Review Protocol

- All override instances are surfaced in the Daily Analytics Digest with full reasoning
- Override performance data is reviewed quarterly against policy-compliant performance
- If overrides underperform policy-compliant entries over a trailing 90-day period, the override confidence threshold is raised by 0.02
- If overrides outperform, the relevant policy rule is reconsidered for permanent adjustment

---

## 18. Consolidated Approved API Stack

### Zero-Cost APIs (No Payment Required)

| API | Categories Served |
| :--- | :--- |
| open-meteo (all endpoints) | Weather |
| NOAA Climate Data Online | Weather |
| NWS API | Weather |
| SportSRC | Sports |
| BALLDONTLIE | Sports |
| TheSportsDB (community) | Sports |
| FRED API | Mentions, Economics |
| BLS API | Mentions, Economics |
| BEA API | Economics |
| SEC EDGAR API | Mentions, Crypto, Science |
| Congress.gov API | Mentions |
| OpenSecrets API | Mentions |
| CME FedWatch (scrape) | Mentions, Economics |
| CoinGecko API | Crypto |
| Alternative.me Fear & Greed | Crypto |
| openFDA API | Science |
| ClinicalTrials.gov API | Science |
| CourtListener API | Geopolitics |
| GovTrack API | Geopolitics |
| GDELT Project API | Geopolitics |
| Reddit API (OAuth) | Sports, Culture |
| pytrends (Google Trends) | Culture |
| YouTube Data API v3 | Culture |
| TMDb API | Culture |
| Wikipedia Pageviews API | Culture |
| Spotify Charts (scrape) | Culture |
| realtime-newsapi (self-hosted) | Mentions |

### Paid APIs (Budget Required)

| API | Cost | Category | Justification |
| :--- | :--- | :--- | :--- |
| **API-SPORTS** | $10/mo | Sports | Only paid dependency; enables live game tracking at 15s intervals — not feasible on free tier |
| **FMP (Financial Modeling Prep)** | $14/mo (Basic) | Mentions, Economics | Real-time earnings data, transcripts, calendar — free tier (250 req/day) may suffice initially |
| **X API** | Developer account | All sentiment categories | Per [[Projects/sibyl-x-sentiment-framework]] — already established via `@SibylA71720` |

### Dropped APIs (Not Retained)

| API | Reason for Removal |
| :--- | :--- |
| Sportmonks | Free plan limited to soccer/cricket; overlaps API-SPORTS paid tier |
| Alpha Vantage | 25 req/day free tier too restrictive; $50/mo premium duplicates FMP + FRED |
| CoinMarketCap | Overlaps CoinGecko with fewer free requests; no marginal value |
| World Bank API | Quarterly/annual cadence too slow for Kalshi timelines; international focus is low priority |
| OECD API | Monthly cadence overlaps FRED's superior dataset; international focus |
| NASA API | Kalshi space markets too rare and low-liquidity to justify integration |
| GitHub API | Too indirect a proxy for tech adoption; signal quality insufficient |
| ACLED API | Academic/NGO access friction; conflict monitoring overlaps GDELT |
| Reuters/AP Wire | Paid service; realtime-newsapi (self-hosted) covers wire news adequately |

**Total monthly data cost:** $10–24/mo (API-SPORTS required; FMP recommended but deferrable to free tier)

---

## 19. Implementation Priority

| Priority | Action | Rationale |
| :--- | :--- | :--- |
| 1 | Weather signal pipeline | Lowest complexity, cleanest data, highest confidence — validates SGE infrastructure |
| 2 | Economics & Macro calendar pipeline | Scheduled releases with public consensus — second SGE validation |
| 3 | Mentions — earnings beat-rate baseline | Prototype using FMP free tier; validates pre-instantiated signal pattern |
| 4 | Sports — pre-game composite signal | Multi-layer but manageable; validates ACE signal routing |
| 5 | Culture — sentiment cascade pipeline | Validates X + Reddit + pytrends signal chain |
| 6 | Crypto — on-chain + sentiment hybrid | Validates independent ACE crypto framework |
| 7 | Science — PDUFA calendar positioning | Event-driven SGE; lower market frequency |
| 8 | Sports — in-game live tracking | Highest complexity; requires event-triggered circuit breakers and separate sizing |
| 9 | Geopolitics — monitoring only | Tier 3; override-only entry; last priority |

---

## Log

**2026-03-20:** Policy graduated from [[Projects/sibyl-kalshi-market-policy-brainstorm]]. All stakeholder answers from open questions integrated. Sports category architecturally decoupled into pre-game and in-game per [[Learning/sports-betting-market-dynamics-in-game-vs-pre-game]] research findings (Partial Kelly 0.50× shrinkage, event-triggered circuit breakers, liquidity-capped sizing, separate capital caps). TradeBot cross-system exposure tracking removed per stakeholder directive — Sibyl operates independently. Override protocol confirmed as fully autonomous with post-hoc reasoning review. Earnings call real-time transcript tracking approved. API stack curated: 9 APIs dropped for insufficient value fit; 27 zero-cost + 3 paid APIs retained. Total monthly data cost: $10–24.
