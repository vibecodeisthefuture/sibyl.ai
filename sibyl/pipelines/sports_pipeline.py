"""
Sports category signal pipeline for Sibyl.

Transforms sports data from ESPN, API-Sports, BallDontLie, and TheSportsDB into
trading signals for Kalshi sports prediction markets.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

from sibyl.pipelines.base_pipeline import BasePipeline, PipelineSignal
from sibyl.clients.espn_client import EspnClient
from sibyl.clients.api_sports_client import ApiSportsClient
from sibyl.clients.balldontlie_client import BallDontLieClient
from sibyl.clients.thesportsdb_client import TheSportsDbClient

logger = logging.getLogger(__name__)


class SportsPipeline(BasePipeline):
    """
    Sports category signal pipeline.

    Analyzes sports data from multiple sources (ESPN, API-Sports, BallDontLie, TheSportsDB)
    to generate trading signals for Kalshi sports prediction markets.
    """

    CATEGORY = "Sports"
    PIPELINE_NAME = "sports"
    DEDUP_WINDOW_MINUTES = 30  # Sprint 16: game state changes quickly

    # Maximum markets to analyze per run — prevents timeout from free-tier API rate limits.
    # API-Sports is the bottleneck at 100 requests/day, so 50 markets is conservative.
    MAX_MARKETS_PER_RUN: int = 50

    # Sport keyword mappings for market matching — expanded for gap-fill markets
    SPORT_KEYWORDS = {
        "NFL": ["nfl", "football", "touchdown", "super bowl", "quarterback",
                "kxnflgame", "kxnfl"],
        "NBA": ["nba", "basketball", "points", "kxnbagame", "kxnba",
                "kxnbaplayoff"],
        "MLB": ["mlb", "baseball", "home run", "kxmlb", "kxmlbstgame",
                "kxmlbgame", "strikeout", "innings"],
        "NHL": ["nhl", "hockey", "goal", "kxnhlgame", "kxnhl"],
        "Soccer": ["soccer", "football", "premier league", "champions league",
                   "kxsoccer", "kxmls", "kxepl"],
        "NCAA_BB": ["ncaa", "march madness", "college basketball",
                    "kxncaambgame", "kxncaamb"],
        "NCAA_FB": ["college football", "cfb", "kxncaafb"],
        "UFC": ["ufc", "mma", "fight", "kxufc"],
        "Tennis": ["tennis", "wimbledon", "us open tennis", "kxtennis",
                  "atp", "wta"],
        "Golf": ["golf", "pga", "masters", "kxgolf"],
        "CS2": ["counter-strike", "cs2", "kxcs2game", "kxcs2", "esports"],
        "F1": ["formula 1", "f1", "grand prix", "kxf1"],
        "NASCAR": ["nascar", "kxnascar"],
    }

    # Series ticker patterns → sport mapping for gap-fill markets
    SERIES_SPORT_MAP = {
        "KXNFLGAME": "NFL", "KXNFL": "NFL",
        "KXNBAGAME": "NBA", "KXNBA": "NBA", "KXNBAPLAYOFF": "NBA",
        "KXMLBSTGAME": "MLB", "KXMLB": "MLB", "KXMLBGAME": "MLB",
        "KXNHLGAME": "NHL", "KXNHL": "NHL",
        "KXNCAAMBGAME": "NCAA_BB", "KXNCAAMB": "NCAA_BB",
        "KXNCAAFBGAME": "NCAA_FB",
        "KXUFCGAME": "UFC", "KXUFC": "UFC",
        "KXCS2GAME": "CS2",
        "KXGOLF": "Golf", "KXTENNIS": "Tennis",
        "KXSOCCER": "Soccer", "KXEPL": "Soccer", "KXMLS": "Soccer",
        "KXF1": "F1", "KXNASCAR": "NASCAR",
    }

    def __init__(self, *args, **kwargs):
        """Initialize the Sports pipeline."""
        super().__init__(*args, **kwargs)
        self.espn_client: Optional[EspnClient] = None
        self.api_sports_client: Optional[ApiSportsClient] = None
        self.balldontlie_client: Optional[BallDontLieClient] = None
        self.thesportsdb_client: Optional[TheSportsDbClient] = None

    def _create_clients(self) -> list:
        """
        Create and return all sports data clients.

        Returns:
            List of data client instances.
        """
        self.espn_client = EspnClient()
        self.api_sports_client = ApiSportsClient()
        self.balldontlie_client = BallDontLieClient()
        self.thesportsdb_client = TheSportsDbClient()

        return [
            self.espn_client,
            self.api_sports_client,
            self.balldontlie_client,
            self.thesportsdb_client,
        ]

    def _category_variants(self) -> List[str]:
        """
        Return category variants for market matching.

        Returns:
            List of category variants including pre-game and in-game signals.
        """
        return [
            "Sports",
            "sports",
            "Sports (Pre-Game)",
            "Sports (In-Game)",
        ]

    async def _analyze(self, markets: List[Dict[str, Any]]) -> List[PipelineSignal]:
        """
        Analyze sports markets and generate trading signals.

        Implements multi-source analysis:
        1. Upcoming Game Odds Signal (bookmaker vs market divergence)
        2. Live Score / In-Game Signal (blowout detection)
        3. Team Form / Momentum Signal (streak analysis)
        4. Player Stats Impact (key player availability)
        5. Cross-Source Consensus (multi-source agreement)

        Args:
            markets: List of market dictionaries from Kalshi API.

        Returns:
            List of PipelineSignal objects with recommended trades.
        """
        signals = []

        # Prioritize markets by closing soonest (most actionable) and filter to top N
        # to stay within free-tier API budgets (API-Sports: 100 req/day).
        if len(markets) > self.MAX_MARKETS_PER_RUN:
            original_count = len(markets)
            # Sort by close_date ascending (soonest first)
            markets = sorted(
                markets,
                key=lambda m: m.get("close_date", "9999-12-31"),
            )[:self.MAX_MARKETS_PER_RUN]
            logger.info(
                "Sports pipeline: limited to top %d/%d markets (by close date)",
                self.MAX_MARKETS_PER_RUN,
                original_count,
            )

        try:
            # NEW: Analyze game outcome markets (spread/total/winner) — handles
            # the bulk of gap-fill discovered markets (14K+ markets)
            game_signals = await self._analyze_game_outcomes(markets)
            signals.extend(game_signals)

            # Analyze upcoming game odds against bookmaker consensus
            odds_signals = await self._analyze_upcoming_odds(markets)
            signals.extend(odds_signals)

            # Analyze live games for in-game signals
            live_signals = await self._analyze_live_scores(markets)
            signals.extend(live_signals)

            # Analyze team form and momentum
            momentum_signals = await self._analyze_team_momentum(markets)
            signals.extend(momentum_signals)

            # Analyze player stats impact (primarily NBA)
            player_signals = await self._analyze_player_stats(markets)
            signals.extend(player_signals)

            # Generate cross-source consensus signals
            consensus_signals = await self._analyze_cross_source_consensus(markets)
            signals.extend(consensus_signals)

            logger.info(f"Generated {len(signals)} sports signals from {len(markets)} markets")

        except Exception as e:
            logger.error(f"Error analyzing sports markets: {e}", exc_info=True)

        return signals

    async def _analyze_game_outcomes(self, markets: List[Dict[str, Any]]) -> List[PipelineSignal]:
        """
        Analyze game outcome markets: winner, spread, and total score.

        This handles the bulk of gap-fill discovered sports markets (~14K).
        Uses ESPN scoreboard data to assess game state and generate signals
        for spread/total/winner markets based on current scores and game clock.

        Market types detected:
          - Winner: "Will [Team] win?" / "[Team] vs [Team]"
          - Spread: "Will [Team] win by 5+?" / "[Team] -5.5"
          - Total: "Over/under 210.5 points" / "Total goals over 3.5"
          - Player props: "Will [Player] score 25+ points?"
        """
        import re
        signals = []

        if not self.espn_client:
            return signals

        # Group markets by sport to batch ESPN calls
        sport_markets: Dict[str, List[Dict]] = {}
        for market in markets:
            sport = self._detect_sport(market)
            if sport:
                sport_markets.setdefault(sport, []).append(market)

        # Fetch scoreboards for active sports (ESPN API has no strict rate limit)
        ESPN_SPORTS = {"NFL", "NBA", "MLB", "NHL", "NCAA_BB", "Soccer"}
        sport_to_espn = {
            "NFL": "football/nfl", "NBA": "basketball/nba",
            "MLB": "baseball/mlb", "NHL": "hockey/nhl",
            "NCAA_BB": "basketball/mens-college-basketball",
            "Soccer": "soccer/eng.1",
        }

        scoreboards: Dict[str, Dict] = {}
        for sport in sport_markets:
            if sport in ESPN_SPORTS:
                try:
                    espn_sport = sport_to_espn.get(sport, sport.lower())
                    sb = await self.espn_client.get_scoreboard(sport=espn_sport)
                    if sb:
                        scoreboards[sport] = sb
                except Exception as e:
                    logger.debug(f"ESPN scoreboard fetch for {sport} failed: {e}")

        # Process each sport's markets
        for sport, mkts in sport_markets.items():
            sb = scoreboards.get(sport, {})
            games = sb.get("games", [])

            # Build team→game lookup from scoreboard
            team_games: Dict[str, Dict] = {}
            for game in games:
                for side in ("home", "away"):
                    team_name = game.get(side, {}).get("team", "").lower()
                    if team_name:
                        team_games[team_name] = game
                    # Also index by abbreviation
                    abbr = game.get(side, {}).get("abbreviation", "").lower()
                    if abbr:
                        team_games[abbr] = game

            for market in mkts[:self.MAX_MARKETS_PER_RUN]:
                title = market.get("title", "")
                title_lower = title.lower()

                # Try to match market to a game
                matched_game = None
                teams = self._extract_teams_from_title(title)
                for team in teams:
                    if team.lower() in team_games:
                        matched_game = team_games[team.lower()]
                        break

                # Even without a matched game, we can still generate signals
                # for markets with clear bracket structure

                # Detect market type from title
                is_spread = any(kw in title_lower for kw in (
                    "spread", "win by", "margin", "handicap", "+", "-",
                ))
                is_total = any(kw in title_lower for kw in (
                    "total", "over", "under", "combined", "o/u",
                ))
                is_winner = any(kw in title_lower for kw in (
                    "win", "beat", "defeat", "advance",
                )) and not is_spread

                # Extract numeric threshold from title
                threshold_match = re.search(r'(\d+\.?\d*)\s*(?:points?|goals?|runs?|\+)',
                                           title, re.IGNORECASE)
                threshold = float(threshold_match.group(1)) if threshold_match else None

                if matched_game and matched_game.get("status") in ("in_progress", "in_game"):
                    # Live game — highest value signals
                    score = matched_game.get("score", {})
                    home_score = score.get("home", 0)
                    away_score = score.get("away", 0)
                    period = matched_game.get("period", 1)
                    total_score = home_score + away_score

                    if is_total and threshold:
                        # Total points market — compare current pace to threshold
                        max_periods = {"NBA": 4, "NFL": 4, "MLB": 9, "NHL": 3,
                                      "NCAA_BB": 2, "Soccer": 2}
                        total_periods = max_periods.get(sport, 4)
                        if period > 0 and total_periods > 0:
                            projected_total = total_score * (total_periods / period)
                            if abs(projected_total - threshold) > threshold * 0.1:
                                prob = 0.75 if projected_total > threshold else 0.25
                                signals.append(PipelineSignal(
                                    source_pipeline=self.PIPELINE_NAME,
                                    category=self.CATEGORY,
                                    market_id=market.get("id"),
                                    signal_type="DATA_MOMENTUM",
                                    confidence=min(0.85, 0.6 + (period / total_periods) * 0.25),
                                    ev_estimate=0.12,
                                    direction="YES" if prob > 0.5 else "NO",
                                    reasoning=(
                                        f"Live {sport}: score {home_score}-{away_score} "
                                        f"(period {period}/{total_periods}), "
                                        f"projected total {projected_total:.0f} "
                                        f"vs threshold {threshold}"
                                    ),
                                ))

                    elif is_winner or (not is_spread and not is_total):
                        # Winner market — blowout detection for live games
                        point_diff = abs(home_score - away_score)
                        blowout_thresholds = {
                            "NBA": 15, "NFL": 14, "MLB": 6, "NHL": 3,
                            "NCAA_BB": 12, "Soccer": 2,
                        }
                        bt = blowout_thresholds.get(sport, 10)
                        max_periods = {"NBA": 4, "NFL": 4, "MLB": 9, "NHL": 3,
                                      "NCAA_BB": 2, "Soccer": 2}
                        total_periods = max_periods.get(sport, 4)

                        if point_diff > bt and period >= total_periods * 0.6:
                            leader = "home" if home_score > away_score else "away"
                            signals.append(PipelineSignal(
                                source_pipeline=self.PIPELINE_NAME,
                                category=self.CATEGORY,
                                market_id=market.get("id"),
                                signal_type="DATA_MOMENTUM",
                                confidence=min(0.90, 0.7 + (period / total_periods) * 0.2),
                                ev_estimate=0.15,
                                direction="YES",
                                reasoning=(
                                    f"Live {sport} blowout: {leader} leads "
                                    f"{home_score}-{away_score} in period "
                                    f"{period}/{total_periods}"
                                ),
                            ))

                elif matched_game:
                    # Upcoming game — use team records for basic signal
                    home_team = matched_game.get("home", {})
                    away_team = matched_game.get("away", {})
                    home_record = home_team.get("record", "")
                    away_record = away_team.get("record", "")

                    # Parse records like "45-20" into win rate
                    def _parse_record(rec: str) -> Optional[float]:
                        parts = rec.split("-")
                        if len(parts) >= 2:
                            try:
                                wins = int(parts[0])
                                losses = int(parts[1])
                                total = wins + losses
                                return wins / total if total > 0 else None
                            except ValueError:
                                pass
                        return None

                    home_wr = _parse_record(home_record)
                    away_wr = _parse_record(away_record)

                    if home_wr is not None and away_wr is not None:
                        # Simple win rate comparison + home advantage
                        home_edge = (home_wr - away_wr) + 0.03  # 3% home advantage
                        if abs(home_edge) > 0.10:
                            signals.append(PipelineSignal(
                                source_pipeline=self.PIPELINE_NAME,
                                category=self.CATEGORY,
                                market_id=market.get("id"),
                                signal_type="DATA_FUNDAMENTAL",
                                confidence=min(0.80, 0.55 + abs(home_edge)),
                                ev_estimate=abs(home_edge) * 0.1,
                                direction="YES" if home_edge > 0 else "NO",
                                reasoning=(
                                    f"{sport}: {home_team.get('team', 'Home')} "
                                    f"({home_record}) vs "
                                    f"{away_team.get('team', 'Away')} ({away_record}), "
                                    f"win rate diff {home_edge:+.1%}"
                                ),
                            ))

        return signals

    async def _analyze_upcoming_odds(self, markets: List[Dict[str, Any]]) -> List[PipelineSignal]:
        """
        Generate signals from upcoming game odds divergence.

        Compares bookmaker odds from API-Sports against Kalshi market prices.
        Sharp bookmaker money divergence from market prices signals DATA_DIVERGENCE.

        Args:
            markets: List of market dictionaries.

        Returns:
            List of PipelineSignal objects for odds divergence.
        """
        signals = []

        # API-Sports budget: max 10 calls per pipeline run (100 req/day ÷ ~10 runs/day).
        # This preserves daily quota across multiple pipeline executions.
        API_SPORTS_BUDGET_PER_RUN = 10
        api_sports_calls = 0

        try:
            if not self.api_sports_client:
                logger.warning("API-Sports client not initialized")
                return signals

            for market in markets:
                if not market.get("is_live"):
                    # Upcoming game
                    sport = self._detect_sport_from_title(market.get("title", ""))
                    if not sport:
                        continue

                    # Check API budget before making call
                    if api_sports_calls >= API_SPORTS_BUDGET_PER_RUN:
                        logger.info(
                            "API-Sports budget exhausted (%d calls), skipping remaining markets",
                            api_sports_calls,
                        )
                        break

                    try:
                        # Fetch upcoming fixtures and odds
                        api_sports_calls += 1
                        odds_data = await self.api_sports_client.get_odds(
                        )

                        if not odds_data:
                            continue

                        # Match odds data to market
                        for fixture in odds_data.get("fixtures", []):
                            matching = self._match_fixture_to_market(fixture, market)
                            if matching:
                                signal = await self._create_odds_signal(
                                    market=market,
                                    fixture=fixture,
                                    bookmaker_odds=fixture.get("odds", {})
                                )
                                if signal:
                                    signals.append(signal)

                    except Exception as e:
                        logger.debug(f"Error fetching odds for market {market.get('id')}: {e}")
                        continue

        except Exception as e:
            logger.error(f"Error in upcoming odds analysis: {e}")

        return signals

    async def _analyze_live_scores(self, markets: List[Dict[str, Any]]) -> List[PipelineSignal]:
        """
        Generate signals from live game scores and in-game states.

        Uses ESPN scoreboard to detect live games. Late-game blowouts create
        high-confidence signals by comparing actual scores to Kalshi market prices.

        Args:
            markets: List of market dictionaries.

        Returns:
            List of PipelineSignal objects for in-game situations.
        """
        signals = []

        try:
            if not self.espn_client:
                logger.warning("ESPN client not initialized")
                return signals

            for market in markets:
                if market.get("is_live"):
                    # In-game market
                    sport = self._detect_sport_from_title(market.get("title", ""))
                    if not sport:
                        continue

                    try:
                        # Get live scoreboard
                        scoreboard = await self.espn_client.get_scoreboard(sport=sport)

                        for game in scoreboard.get("games", []):
                            if game.get("status") in ["in_progress", "in_game"]:
                                # Check for blowout situations
                                signal = self._analyze_blowout(
                                    market=market,
                                    game=game
                                )
                                if signal:
                                    signals.append(signal)

                    except Exception as e:
                        logger.debug(f"Error fetching live scores for {sport}: {e}")
                        continue

        except Exception as e:
            logger.error(f"Error in live scores analysis: {e}")

        return signals

    async def _analyze_team_momentum(self, markets: List[Dict[str, Any]]) -> List[PipelineSignal]:
        """
        Generate signals from team form and momentum analysis.

        Analyzes win/loss records from ESPN standings. Teams on hot streaks
        facing weak opponents generate DATA_MOMENTUM signals.

        Args:
            markets: List of market dictionaries.

        Returns:
            List of PipelineSignal objects for momentum signals.
        """
        signals = []

        try:
            if not self.espn_client:
                logger.warning("ESPN client not initialized")
                return signals

            for market in markets:
                sport = self._detect_sport_from_title(market.get("title", ""))
                if not sport:
                    continue

                try:
                    # Get standings and team records
                    standings = await self.espn_client.get_standings(sport=sport)

                    teams_in_market = self._extract_teams_from_title(
                        market.get("title", "")
                    )

                    for team in teams_in_market:
                        team_record = self._find_team_record(standings, team)
                        if team_record:
                            # Check for hot/cold streaks
                            signal = self._evaluate_streak(
                                market=market,
                                team=team,
                                record=team_record
                            )
                            if signal:
                                signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error fetching standings for {sport}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in team momentum analysis: {e}")

        return signals

    async def _analyze_player_stats(self, markets: List[Dict[str, Any]]) -> List[PipelineSignal]:
        """
        Generate signals from player statistics impact (primarily NBA).

        Uses BallDontLie to detect star player absence/return. Key player
        availability changes shift game odds significantly.

        Args:
            markets: List of market dictionaries.

        Returns:
            List of PipelineSignal objects for player impact signals.
        """
        signals = []

        try:
            if not self.balldontlie_client:
                logger.warning("BallDontLie client not initialized")
                return signals

            # Only analyze NBA markets
            nba_markets = [
                m for m in markets
                if self._detect_sport_from_title(m.get("title", "")) == "NBA"
            ]

            for market in nba_markets:
                try:
                    # Extract team names
                    teams = self._extract_teams_from_title(market.get("title", ""))

                    for team in teams:
                        # Get player stats for team
                        stats = await self.balldontlie_client.get_stats(team=team)

                        if stats:
                            # Check for key player absences
                            signal = self._evaluate_player_impact(
                                market=market,
                                team=team,
                                stats=stats
                            )
                            if signal:
                                signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error fetching player stats: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in player stats analysis: {e}")

        return signals

    async def _analyze_cross_source_consensus(self, markets: List[Dict[str, Any]]) -> List[PipelineSignal]:
        """
        Generate high-confidence signals when multiple data sources agree.

        Cross-validates odds, team form, and player stats across all sources.
        When consensus exists but market pricing disagrees, generate DATA_FUNDAMENTAL
        signals with boosted confidence.

        Args:
            markets: List of market dictionaries.

        Returns:
            List of PipelineSignal objects with boosted confidence scores.
        """
        signals = []

        # API budget tracking for consensus analysis
        # API-Sports: max 5 calls (shared budget with odds analysis)
        # BallDontLie: max 10 calls (60 req/min limit, more generous)
        API_SPORTS_BUDGET = 5
        BALLDONTLIE_BUDGET = 10
        api_sports_calls = 0
        balldontlie_calls = 0

        try:
            for market in markets:
                sport = self._detect_sport_from_title(market.get("title", ""))
                if not sport:
                    continue

                try:
                    # Gather consensus data
                    consensus_score = 0
                    total_sources = 0

                    # Source 1: Bookmaker odds (API-Sports budget-limited)
                    if self.api_sports_client and api_sports_calls < API_SPORTS_BUDGET:
                        api_sports_calls += 1
                        odds_consensus = await self._get_odds_consensus(market, sport)
                        if odds_consensus is not None:
                            consensus_score += odds_consensus
                            total_sources += 1

                    # Source 2: Team form (ESPN - no strict rate limits)
                    if self.espn_client:
                        form_consensus = await self._get_form_consensus(market, sport)
                        if form_consensus is not None:
                            consensus_score += form_consensus
                            total_sources += 1

                    # Source 3: Player stats (NBA only, BallDontLie budget-limited)
                    if sport == "NBA" and self.balldontlie_client and balldontlie_calls < BALLDONTLIE_BUDGET:
                        balldontlie_calls += 1
                        player_consensus = self._get_player_consensus(market)
                        if player_consensus is not None:
                            consensus_score += player_consensus
                            total_sources += 1

                    # Source 4: TheSportsDB data (no strict rate limits on test key)
                    if self.thesportsdb_client:
                        db_consensus = self._get_thesportsdb_consensus(market, sport)
                        if db_consensus is not None:
                            consensus_score += db_consensus
                            total_sources += 1

                    # Generate signal if strong consensus exists
                    if total_sources >= 2:
                        avg_consensus = consensus_score / total_sources
                        if abs(avg_consensus) > 0.6:  # Strong consensus threshold
                            signal = self._create_consensus_signal(
                                market=market,
                                consensus_score=avg_consensus,
                                sources_count=total_sources
                            )
                            if signal:
                                signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error analyzing cross-source consensus for market {market.get('id')}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in cross-source consensus analysis: {e}")

        return signals

    # Helper Methods

    def _find_matching_markets(
        self,
        markets: List[Dict[str, Any]],
        keywords: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Find markets matching given keywords.

        Args:
            markets: List of market dictionaries to search.
            keywords: List of keywords to match against market titles.

        Returns:
            List of matching market dictionaries.
        """
        matching = []
        keywords_lower = [k.lower() for k in keywords]

        for market in markets:
            title_lower = market.get("title", "").lower()
            if any(keyword in title_lower for keyword in keywords_lower):
                matching.append(market)

        return matching

    def _bookmaker_implied_prob(self, odds: Dict[str, Any]) -> Optional[float]:
        """
        Convert bookmaker odds to implied probability.

        Supports decimal, American, and fractional odds formats.

        Args:
            odds: Dictionary containing odds in various formats.
                 Can have keys: 'decimal', 'american', 'fractional'

        Returns:
            Implied probability as float between 0 and 1, or None if unable to convert.
        """
        try:
            # Try decimal odds first
            if "decimal" in odds:
                decimal = float(odds["decimal"])
                return 1.0 / decimal

            # Try American odds
            if "american" in odds:
                american = float(odds["american"])
                if american > 0:
                    return 100.0 / (american + 100.0)
                else:
                    return abs(american) / (abs(american) + 100.0)

            # Try fractional odds
            if "fractional" in odds:
                parts = str(odds["fractional"]).split("/")
                if len(parts) == 2:
                    numerator = float(parts[0])
                    denominator = float(parts[1])
                    return denominator / (numerator + denominator)

            return None

        except (ValueError, ZeroDivisionError, TypeError) as e:
            logger.debug(f"Error converting odds to probability: {e}")
            return None

    def _detect_sport(self, market: Dict[str, Any]) -> Optional[str]:
        """
        Determine which sport a market is about from title AND ticker.

        Checks series ticker patterns first (most reliable for gap-fill
        discovered markets), then falls back to title keyword matching.

        Args:
            market: Market dictionary with 'title', 'ticker', '_event_ticker' keys.

        Returns:
            Sport name (e.g., "NFL", "NBA", "MLB", "NHL") or None.
        """
        # Check event ticker / series ticker first (most reliable)
        for key in ("_event_ticker", "ticker"):
            ticker = (market.get(key) or "").upper()
            for prefix, sport in self.SERIES_SPORT_MAP.items():
                if ticker.startswith(prefix):
                    return sport

        # Fallback: title keyword matching
        title_lower = market.get("title", "").lower()
        for sport, keywords in self.SPORT_KEYWORDS.items():
            if any(keyword in title_lower for keyword in keywords):
                return sport

        return None

    def _detect_sport_from_title(self, title: str) -> Optional[str]:
        """Legacy sport detection from title only (used by helper methods)."""
        title_lower = title.lower()
        for sport, keywords in self.SPORT_KEYWORDS.items():
            if any(keyword in title_lower for keyword in keywords):
                return sport
        return None

    async def _get_market_price(self, market_id: str) -> Optional[float]:
        """
        Retrieve latest market price from database.

        Args:
            market_id: Kalshi market ID.

        Returns:
            Latest market price as float, or None if unavailable.
        """
        try:
            row = await self._db.fetchone(
                """SELECT yes_price FROM prices
                   WHERE market_id = ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (market_id,)
            )
            if row and row[0] is not None:
                return float(row[0])
            return None
        except Exception as e:
            logger.debug(f"Error retrieving market price for {market_id}: {e}")
            return None

    def _extract_teams_from_title(self, title: str) -> List[str]:
        """
        Extract team names from market title.

        Args:
            title: Market title string.

        Returns:
            List of team names found in title.
        """
        teams = []
        # This would use more sophisticated NLP in production
        # For now, split on 'vs' or 'v.' patterns
        parts = title.split(" vs ")
        if len(parts) == 2:
            teams = [p.strip() for p in parts]

        return teams

    def _match_fixture_to_market(
        self,
        fixture: Dict[str, Any],
        market: Dict[str, Any]
    ) -> bool:
        """
        Check if a sports fixture matches a Kalshi market.

        Args:
            fixture: Fixture data from API-Sports.
            market: Market dictionary from Kalshi.

        Returns:
            True if fixture matches market, False otherwise.
        """
        fixture_title = f"{fixture.get('home_team')} vs {fixture.get('away_team')}"
        market_title = market.get("title", "").lower()

        return (fixture_title.lower() in market_title or
                fixture.get('home_team', '').lower() in market_title or
                fixture.get('away_team', '').lower() in market_title)

    async def _create_odds_signal(
        self,
        market: Dict[str, Any],
        fixture: Dict[str, Any],
        bookmaker_odds: Dict[str, Any]
    ) -> Optional[PipelineSignal]:
        """
        Create a signal from bookmaker odds divergence.

        Args:
            market: Kalshi market dictionary.
            fixture: Sports fixture data.
            bookmaker_odds: Bookmaker odds data.

        Returns:
            PipelineSignal object or None if no significant divergence.
        """
        try:
            market_price = await self._get_market_price(market.get("id", ""))
            if market_price is None:
                return None

            # Get implied probability from bookmaker odds
            bookmaker_prob = self._bookmaker_implied_prob(bookmaker_odds)
            if bookmaker_prob is None:
                return None

            # Check for divergence
            divergence = abs(market_price - bookmaker_prob)
            if divergence > 0.05:  # 5% threshold
                signal = PipelineSignal(
                    source_pipeline=self.PIPELINE_NAME,
                    category=self.CATEGORY,
                    market_id=market.get("id"),
                    signal_type="DATA_DIVERGENCE",
                    confidence=min(0.95, 0.5 + (divergence * 2)),
                    ev_estimate=divergence,
                    direction="YES" if bookmaker_prob > market_price else "NO",
                    reasoning=f"Bookmaker odds ({bookmaker_prob:.2%}) diverge from market price ({market_price:.2%})"
                )
                return signal

            return None

        except Exception as e:
            logger.debug(f"Error creating odds signal: {e}")
            return None

    def _analyze_blowout(
        self,
        market: Dict[str, Any],
        game: Dict[str, Any]
    ) -> Optional[PipelineSignal]:
        """
        Detect and signal on late-game blowout situations.

        Args:
            market: Kalshi market dictionary.
            game: Live game data from ESPN.

        Returns:
            PipelineSignal if blowout detected, None otherwise.
        """
        try:
            score = game.get("score", {})
            home_score = score.get("home", 0)
            away_score = score.get("away", 0)

            # Check if game is in late stages
            period = game.get("period", 0)
            sport = self._detect_sport_from_title(market.get("title", ""))

            max_periods = {
                "NBA": 4,
                "NFL": 4,
                "MLB": 9,
                "NHL": 3,
                "Soccer": 2,
            }

            max_period = max_periods.get(sport, 4)
            is_late_game = period >= max_period - 1

            if is_late_game:
                point_diff = abs(home_score - away_score)

                # Thresholds vary by sport
                thresholds = {
                    "NBA": 20,
                    "NFL": 14,
                    "MLB": 8,
                    "NHL": 3,
                    "Soccer": 2,
                }

                blowout_threshold = thresholds.get(sport, 10)

                if point_diff > blowout_threshold:
                    leader = "home" if home_score > away_score else "away"
                    signal = PipelineSignal(
                        source_pipeline=self.PIPELINE_NAME,
                        category=self.CATEGORY,
                        market_id=market.get("id"),
                        signal_type="DATA_MOMENTUM",
                        confidence=0.85,
                        ev_estimate=0.15,
                        direction="YES" if leader == "home" else "NO",
                        reasoning=f"Late-game blowout: {leader} team leads by {point_diff} points"
                    )
                    return signal

            return None

        except Exception as e:
            logger.debug(f"Error analyzing blowout: {e}")
            return None

    def _find_team_record(
        self,
        standings: Dict[str, Any],
        team: str
    ) -> Optional[Dict[str, Any]]:
        """
        Find team record in standings data.

        Args:
            standings: Standings data from ESPN.
            team: Team name to search for.

        Returns:
            Team record dictionary or None if not found.
        """
        try:
            team_lower = team.lower()
            for record in standings.get("teams", []):
                if team_lower in record.get("name", "").lower():
                    return record
            return None
        except Exception as e:
            logger.debug(f"Error finding team record: {e}")
            return None

    def _evaluate_streak(
        self,
        market: Dict[str, Any],
        team: str,
        record: Dict[str, Any]
    ) -> Optional[PipelineSignal]:
        """
        Evaluate team streak and generate momentum signal if significant.

        Args:
            market: Kalshi market dictionary.
            team: Team name.
            record: Team record data.

        Returns:
            PipelineSignal if strong streak detected, None otherwise.
        """
        try:
            wins = record.get("wins", 0)
            losses = record.get("losses", 0)
            recent_games = record.get("recent_form", [])  # e.g., ["W", "W", "L", "W"]

            if not recent_games:
                return None

            # Count recent win streak
            recent_wins = 0
            for result in recent_games:
                if result == "W":
                    recent_wins += 1
                else:
                    break

            # Significant streak threshold
            if recent_wins >= 3:
                signal = PipelineSignal(
                    source_pipeline=self.PIPELINE_NAME,
                    category=self.CATEGORY,
                    market_id=market.get("id"),
                    signal_type="DATA_MOMENTUM",
                    confidence=min(0.9, 0.6 + (recent_wins * 0.1)),
                    ev_estimate=0.10,
                    direction="YES",
                    reasoning=f"{team} on {recent_wins}-game winning streak ({wins}W-{losses}L record)"
                )
                return signal

            return None

        except Exception as e:
            logger.debug(f"Error evaluating streak: {e}")
            return None

    def _evaluate_player_impact(
        self,
        market: Dict[str, Any],
        team: str,
        stats: Dict[str, Any]
    ) -> Optional[PipelineSignal]:
        """
        Evaluate player absence/return impact on game.

        Args:
            market: Kalshi market dictionary.
            team: Team name.
            stats: Player statistics data from BallDontLie.

        Returns:
            PipelineSignal if key player impact detected, None otherwise.
        """
        try:
            injured_players = stats.get("injured_players", [])

            # Check for star player absence (would use more sophisticated ranking in production)
            key_players = ["LeBron James", "Kevin Durant", "Giannis", "Luka Doncic", "Stephen Curry"]

            for player in injured_players:
                if any(key in player.get("name", "") for key in key_players):
                    signal = PipelineSignal(
                        source_pipeline=self.PIPELINE_NAME,
                        category=self.CATEGORY,
                        market_id=market.get("id"),
                        signal_type="DATA_FUNDAMENTAL",
                        confidence=0.75,
                        ev_estimate=0.12,
                        direction="NO",
                        reasoning=f"Star player {player.get('name')} out due to injury for {team}"
                    )
                    return signal

            return None

        except Exception as e:
            logger.debug(f"Error evaluating player impact: {e}")
            return None

    async def _get_odds_consensus(
        self,
        market: Dict[str, Any],
        sport: str
    ) -> Optional[float]:
        """
        Get consensus score from bookmaker odds analysis.

        Args:
            market: Kalshi market dictionary.
            sport: Sport name.

        Returns:
            Consensus score between -1 and 1, or None.
        """
        try:
            if not self.api_sports_client:
                return None

            odds = await self.api_sports_client.get_odds()
            # Score should reflect whether odds favor home or away team
            # Placeholder returning None for now
            return None

        except Exception as e:
            logger.debug(f"Error getting odds consensus: {e}")
            return None

    async def _get_form_consensus(
        self,
        market: Dict[str, Any],
        sport: str
    ) -> Optional[float]:
        """
        Get consensus score from team form analysis.

        Args:
            market: Kalshi market dictionary.
            sport: Sport name.

        Returns:
            Consensus score between -1 and 1, or None.
        """
        try:
            if not self.espn_client:
                return None

            standings = await self.espn_client.get_standings(sport=sport)
            teams = self._extract_teams_from_title(market.get("title", ""))

            # Score based on relative team form
            # Placeholder returning None for now
            return None

        except Exception as e:
            logger.debug(f"Error getting form consensus: {e}")
            return None

    def _get_player_consensus(self, market: Dict[str, Any]) -> Optional[float]:
        """
        Get consensus score from player statistics analysis.

        Args:
            market: Kalshi market dictionary.

        Returns:
            Consensus score between -1 and 1, or None.
        """
        try:
            if not self.balldontlie_client:
                return None

            teams = self._extract_teams_from_title(market.get("title", ""))
            # Score based on player availability differences between teams
            # Placeholder returning None for now
            return None

        except Exception as e:
            logger.debug(f"Error getting player consensus: {e}")
            return None

    def _get_thesportsdb_consensus(
        self,
        market: Dict[str, Any],
        sport: str
    ) -> Optional[float]:
        """
        Get consensus score from TheSportsDB data.

        Args:
            market: Kalshi market dictionary.
            sport: Sport name.

        Returns:
            Consensus score between -1 and 1, or None.
        """
        try:
            if not self.thesportsdb_client:
                return None

            # Query TheSportsDB for historical performance
            # Score based on historical matchup data
            # Placeholder returning None for now
            return None

        except Exception as e:
            logger.debug(f"Error getting TheSportsDB consensus: {e}")
            return None

    def _create_consensus_signal(
        self,
        market: Dict[str, Any],
        consensus_score: float,
        sources_count: int
    ) -> Optional[PipelineSignal]:
        """
        Create a high-confidence signal from cross-source consensus.

        Args:
            market: Kalshi market dictionary.
            consensus_score: Average consensus score between -1 and 1.
            sources_count: Number of sources contributing to consensus.

        Returns:
            PipelineSignal with boosted confidence or None.
        """
        try:
            # Boost confidence based on number of sources
            base_confidence = 0.75
            confidence_boost = 0.05 * (sources_count - 2)  # Boost for each additional source
            final_confidence = min(0.95, base_confidence + confidence_boost)

            signal = PipelineSignal(
                source_pipeline=self.PIPELINE_NAME,
                category=self.CATEGORY,
                market_id=market.get("id"),
                signal_type="DATA_FUNDAMENTAL",
                confidence=final_confidence,
                ev_estimate=abs(consensus_score) * 0.1,
                direction="YES" if consensus_score > 0 else "NO",
                reasoning=f"Multi-source consensus ({sources_count} sources) with score {consensus_score:.2f}"
            )
            return signal

        except Exception as e:
            logger.debug(f"Error creating consensus signal: {e}")
            return None
