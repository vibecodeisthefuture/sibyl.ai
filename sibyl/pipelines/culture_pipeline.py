"""
Culture category signal pipeline for Sibyl prediction market trading system.

Transforms culture data from TMDb and Wikipedia into trading signals for Kalshi
culture and entertainment prediction markets. Analyzes movie trends, awards season,
and celebrity buzz to identify mispricings.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from sibyl.pipelines.base_pipeline import BasePipeline, PipelineSignal
from sibyl.clients.tmdb_client import TmdbClient
from sibyl.clients.wikipedia_client import WikipediaClient

logger = logging.getLogger(__name__)


class CulturePipeline(BasePipeline):
    """
    Culture category signal pipeline.

    Analyzes entertainment data from TMDb and Wikipedia to generate trading signals
    for Kalshi culture prediction markets about movies, awards, and celebrity news.
    """

    CATEGORY = "Culture"
    PIPELINE_NAME = "culture"
    DEDUP_WINDOW_MINUTES = 120  # Sprint 16: entertainment events are slow-moving

    # Keywords for market matching — expanded for gap-fill discovered markets
    CULTURE_KEYWORDS = [
        # Awards (original)
        "oscar", "emmy", "grammy", "box office", "movie", "film",
        "award", "nomination", "celebrity", "trending", "netflix",
        "disney", "awards ceremony",
        # Billboard/Music (gap-fill discovered)
        "billboard", "hot 100", "number 1", "#1", "chart", "album",
        "spotify", "streaming", "song", "artist", "rapper",
        # Reality TV (gap-fill discovered)
        "american idol", "survivor", "bachelor", "bachelorette",
        "dancing with the stars", "the voice", "big brother",
        "love island", "drag race",
        # Baby names (gap-fill discovered)
        "baby name", "popular name", "name popularity",
        # Social media / Mentions (gap-fill discovered)
        "tweet", "twitter", "x post", "instagram", "tiktok",
        "mention", "follower", "subscriber", "youtube",
        # Pop culture events
        "super bowl halftime", "met gala", "coachella",
        "comic-con", "fashion week",
        # Series tickers
        "kxbillboard", "kxhot100", "kxidol", "kxbabyname",
        "kxmention", "kxtweet",
    ]

    def __init__(self, *args, **kwargs):
        """Initialize the Culture pipeline."""
        super().__init__(*args, **kwargs)

    def _create_clients(self) -> List:
        """Create and return the data clients used by this pipeline.

        Returns:
            List of data clients: TmdbClient and WikipediaClient.
        """
        return [TmdbClient(), WikipediaClient()]

    def _category_variants(self) -> List[str]:
        """Return category name variants for market matching.

        Returns:
            List of category variants including Culture and Mentions.
        """
        return ["Culture", "culture", "Mentions", "mentions"]

    async def _analyze(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Generate culture signals by analyzing entertainment data.

        Implements three analysis strategies:
        1. Trending Movies Signal - Compare TMDb popularity against market prices
        2. Wikipedia Pageview Surge - Detect spikes indicating public attention
        3. Awards Season Signal - Analyze ratings and popularity of nominees

        Args:
            markets: List of active Kalshi markets in culture category.

        Returns:
            List of PipelineSignal objects with trading recommendations.
        """
        signals = []

        # NEW: Broad culture market analysis for gap-fill discovered markets
        try:
            signals.extend(await self._analyze_entertainment_markets(markets))
        except Exception as e:
            logger.warning(f"Entertainment market analysis failed: {e}")

        try:
            signals.extend(await self._analyze_trending_movies(markets))
        except Exception as e:
            logger.warning(f"Trending movies analysis failed: {e}")

        try:
            signals.extend(await self._analyze_pageview_surge(markets))
        except Exception as e:
            logger.warning(f"Pageview surge analysis failed: {e}")

        try:
            signals.extend(await self._analyze_awards_season(markets))
        except Exception as e:
            logger.warning(f"Awards season analysis failed: {e}")

        logger.info(f"Generated {len(signals)} culture signals")
        return signals

    async def _analyze_entertainment_markets(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze broad entertainment/culture markets beyond movies and awards.

        Handles gap-fill discovered market types:
          - Billboard charts ("Will [Artist] have #1 on Hot 100?")
          - Reality TV outcomes ("Who will win American Idol?")
          - Baby name popularity ("Most popular baby name in 2026?")
          - Social media mentions ("Will [Person] tweet about [Topic]?")

        Rate-limited: max 10 Wikipedia API calls to prevent timeouts.
        """
        signals = []
        wiki_calls = 0
        MAX_WIKI_CALLS = 10

        wiki_client = self._get_client("wikipedia")

        BILLBOARD_KW = {"billboard", "hot 100", "#1", "chart", "number 1", "number one"}
        REALITY_TV_KW = {"american idol", "survivor", "bachelor", "voice",
                        "dancing with the stars", "big brother", "love island",
                        "drag race", "bachelorette"}
        SOCIAL_KW = {"tweet", "mention", "twitter", "x post", "instagram",
                    "tiktok", "follower", "subscriber"}

        for market in markets:
            title = market.get("title", "")
            title_lower = title.lower()

            is_billboard = any(kw in title_lower for kw in BILLBOARD_KW)
            is_reality = any(kw in title_lower for kw in REALITY_TV_KW)
            is_social = any(kw in title_lower for kw in SOCIAL_KW)

            if not (is_billboard or is_reality or is_social):
                continue

            if is_billboard:
                # Billboard chart markets — use Wikipedia buzz as proxy
                # Extract artist name from title
                topic = self._extract_topic_from_title(title)
                if topic and wiki_client and wiki_calls < MAX_WIKI_CALLS:
                    wiki_calls += 1
                    try:
                        end_date = datetime.utcnow()
                        start_date = end_date - timedelta(days=7)
                        pageviews_data = await wiki_client.get_pageviews(
                            topic,
                            start_date.strftime("%Y%m%d"),
                            end_date.strftime("%Y%m%d"),
                        )
                        pageviews = [item.get("views", 0) for item in pageviews_data]

                        if len(pageviews) >= 2:
                            recent = sum(pageviews[-3:]) / max(1, min(3, len(pageviews)))
                            historical = sum(pageviews[:-3]) / max(1, len(pageviews) - 3)

                            if historical > 0:
                                surge = recent / historical
                                if surge > 1.5:
                                    prob = min(0.80, 0.5 + (surge - 1.0) * 0.15)
                                    signals.append(PipelineSignal(
                                        market_id=market.get("id"),
                                        signal_type="DATA_SENTIMENT",
                                        confidence=min(0.80, 0.55 + (surge - 1.0) * 0.1),
                                        ev_estimate=abs(prob - 0.5) * 0.1,
                                        direction="YES",
                                        reasoning=(
                                            f"Billboard market: '{topic}' Wikipedia pageviews "
                                            f"surging {surge:.1f}x, suggesting rising momentum"
                                        ),
                                        source_pipeline=self.PIPELINE_NAME,
                                        category=self.CATEGORY,
                                    ))
                    except Exception as e:
                        logger.debug(f"Billboard pageview check failed: {e}")

            elif is_reality:
                # Reality TV — generate basic awareness signal
                signals.append(PipelineSignal(
                    market_id=market.get("id"),
                    signal_type="DATA_CATALYST",
                    confidence=0.55,
                    ev_estimate=0.03,
                    direction="YES",
                    reasoning=f"Reality TV market: {title[:80]}. Active season suggests engagement.",
                    source_pipeline=self.PIPELINE_NAME,
                    category=self.CATEGORY,
                ))

            elif is_social:
                # Social media mention markets — use Wikipedia as proxy
                topic = self._extract_topic_from_title(title)
                if topic and wiki_client and wiki_calls < MAX_WIKI_CALLS:
                    wiki_calls += 1
                    try:
                        end_date = datetime.utcnow()
                        start_date = end_date - timedelta(days=3)
                        pageviews_data = await wiki_client.get_pageviews(
                            topic,
                            start_date.strftime("%Y%m%d"),
                            end_date.strftime("%Y%m%d"),
                        )
                        pageviews = [item.get("views", 0) for item in pageviews_data]

                        if pageviews:
                            avg_views = sum(pageviews) / len(pageviews)
                            if avg_views > 10000:
                                signals.append(PipelineSignal(
                                    market_id=market.get("id"),
                                    signal_type="DATA_SENTIMENT",
                                    confidence=0.60,
                                    ev_estimate=0.05,
                                    direction="YES",
                                    reasoning=(
                                        f"Social media market: '{topic}' averaging "
                                        f"{avg_views:,.0f} Wikipedia pageviews/day "
                                        f"(high public attention)"
                                    ),
                                    source_pipeline=self.PIPELINE_NAME,
                                    category=self.CATEGORY,
                                ))
                    except Exception as e:
                        logger.debug(f"Social pageview check failed: {e}")

        return signals

    async def _analyze_trending_movies(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze trending movies and box office signals.

        Fetches TMDb trending movies and upcoming releases. For markets about box office
        performance or awards, compares movie popularity scores against market prices.

        Args:
            markets: List of active markets.

        Returns:
            List of PipelineSignal objects for trending movies.
        """
        signals = []

        try:
            tmdb_client = self._get_client("tmdb")
            if not tmdb_client:
                logger.debug("TMDb client not available")
                return signals

            # Find box office and movie-related markets
            matching_markets = self._find_matching_markets(
                markets,
                ["box office", "movie", "film", "box-office"]
            )

            if not matching_markets:
                return signals

            # Fetch trending movies
            trending_data = await tmdb_client.get_trending(
                media_type="movie",
                time_window="week"
            )

            if not trending_data or "results" not in trending_data:
                return signals

            for market in matching_markets:
                try:
                    market_id = market.get("id")
                    market_title = market.get("title", "").lower()
                    market_price = await self._get_market_price(market_id)

                    if market_price is None:
                        continue

                    # Search for matching movies in trending data
                    for movie in trending_data.get("results", [])[:20]:
                        movie_title = movie.get("title", "").lower()
                        popularity = movie.get("popularity", 0) / 100.0  # Normalize to 0-1

                        # Check if movie matches market
                        if self._title_match(movie_title, market_title):
                            # Compare popularity against market price
                            edge, direction, ev = self._compute_edge(
                                popularity,
                                market_price
                            )

                            if edge > 0.10:  # Minimum 10% edge threshold
                                confidence = self._edge_to_confidence(edge)
                                signal = PipelineSignal(
                                    market_id=market_id,
                                    signal_type="DATA_SENTIMENT",
                                    confidence=confidence,
                                    ev_estimate=ev,
                                    direction=direction,
                                    reasoning=(
                                        f"Trending movie '{movie_title}' has popularity score "
                                        f"({popularity:.2%}) diverging from market price ({market_price:.2%})"
                                    ),
                                    source_pipeline=self.PIPELINE_NAME,
                                    category=self.CATEGORY,
                                    data_points={
                                        "popularity_score": popularity,
                                        "tmdb_id": movie.get("id"),
                                        "vote_average": movie.get("vote_average", 0),
                                    }
                                )
                                signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error analyzing market {market.get('id')}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in trending movies analysis: {e}")

        return signals

    async def _analyze_pageview_surge(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze Wikipedia pageview surges for attention signals.

        Rate-limited to max 15 Wikipedia calls to prevent timeouts.
        """
        signals = []
        wiki_calls = 0
        MAX_WIKI_CALLS = 15

        try:
            wiki_client = self._get_client("wikipedia")
            if not wiki_client:
                logger.debug("Wikipedia client not available")
                return signals

            # Find culture-related markets
            matching_markets = self._find_matching_markets(
                markets,
                ["celebrity", "actor", "award", "trending", "oscars", "emmys"]
            )

            if not matching_markets:
                return signals

            for market in matching_markets:
                try:
                    market_id = market.get("id")
                    market_title = market.get("title", "")
                    market_price = await self._get_market_price(market_id)

                    if market_price is None:
                        continue

                    # Extract topic from market title
                    topic = self._extract_topic_from_title(market_title)
                    if not topic:
                        continue

                    # Rate limit Wikipedia calls
                    if wiki_calls >= MAX_WIKI_CALLS:
                        break
                    wiki_calls += 1

                    # Fetch pageview data for last 7 days
                    end_date = datetime.utcnow()
                    start_date = end_date - timedelta(days=7)
                    start_str = start_date.strftime("%Y%m%d")
                    end_str = end_date.strftime("%Y%m%d")

                    pageviews_data = await wiki_client.get_pageviews(topic, start_str, end_str)
                    pageviews = [item.get("views", 0) for item in pageviews_data]

                    if not pageviews or len(pageviews) < 2:
                        continue

                    # Calculate surge ratio: recent average / historical average
                    recent_avg = sum(pageviews[-3:]) / 3
                    historical_avg = sum(pageviews[:-3]) / max(1, len(pageviews) - 3)

                    if historical_avg > 0:
                        surge_ratio = recent_avg / historical_avg
                    else:
                        surge_ratio = 1.0

                    # Pageview surge → implies higher probability
                    data_implied_prob = min(0.95, 0.5 + (surge_ratio - 1.0) * 0.2)

                    edge, direction, ev = self._compute_edge(
                        data_implied_prob,
                        market_price
                    )

                    if edge > 0.08:  # 8% threshold for pageview signals
                        confidence = self._edge_to_confidence(edge, base=0.55)
                        signal = PipelineSignal(
                            market_id=market_id,
                            signal_type="DATA_SENTIMENT",
                            confidence=confidence,
                            ev_estimate=ev,
                            direction=direction,
                            reasoning=(
                                f"Wikipedia pageviews for '{topic}' surging {surge_ratio:.1f}x "
                                f"historical average, indicating rising public attention"
                            ),
                            source_pipeline=self.PIPELINE_NAME,
                            category=self.CATEGORY,
                            data_points={
                                "surge_ratio": surge_ratio,
                                "recent_pageviews": recent_avg,
                                "historical_pageviews": historical_avg,
                            }
                        )
                        signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error analyzing pageviews for market {market.get('id')}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in pageview surge analysis: {e}")

        return signals

    async def _analyze_awards_season(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze awards season signals based on movie ratings and popularity.

        During award season, compares TMDb ratings and popularity of nominees against
        market prices for award outcome markets.

        Args:
            markets: List of active markets.

        Returns:
            List of PipelineSignal objects for awards season signals.
        """
        signals = []

        try:
            tmdb_client = self._get_client("tmdb")
            if not tmdb_client:
                logger.debug("TMDb client not available")
                return signals

            # Find awards-related markets
            matching_markets = self._find_matching_markets(
                markets,
                ["oscar", "emmy", "grammy", "award", "nomination", "best picture"]
            )

            if not matching_markets:
                return signals

            for market in matching_markets:
                try:
                    market_id = market.get("id")
                    market_title = market.get("title", "").lower()
                    market_price = await self._get_market_price(market_id)

                    if market_price is None:
                        continue

                    # Extract nominee name(s) from title
                    nominees = self._extract_nominees_from_title(market_title)
                    if not nominees:
                        continue

                    # Search TMDb for movies/shows matching nominees
                    for nominee in nominees:
                        try:
                            search_results = await tmdb_client.search_movie(nominee)

                            if not search_results:
                                continue

                            top_result = search_results[0] if search_results else None
                            if not top_result:
                                continue

                            # Use vote_average as proxy for likelihood
                            vote_average = top_result.get("vote_average", 5.0)
                            data_implied_prob = (vote_average / 10.0) * 0.8 + 0.1  # Map 0-10 to 0.1-0.9

                            edge, direction, ev = self._compute_edge(
                                data_implied_prob,
                                market_price
                            )

                            if edge > 0.12:  # 12% threshold for awards signals
                                confidence = self._edge_to_confidence(edge)
                                signal = PipelineSignal(
                                    market_id=market_id,
                                    signal_type="DATA_FUNDAMENTAL",
                                    confidence=confidence,
                                    ev_estimate=ev,
                                    direction=direction,
                                    reasoning=(
                                        f"Award nominee '{nominee}' has TMDb rating "
                                        f"{vote_average:.1f}/10 suggesting "
                                        f"{data_implied_prob:.2%} award likelihood"
                                    ),
                                    source_pipeline=self.PIPELINE_NAME,
                                    category=self.CATEGORY,
                                    data_points={
                                        "tmdb_rating": vote_average,
                                        "implied_probability": data_implied_prob,
                                        "tmdb_id": top_result.get("id"),
                                    }
                                )
                                signals.append(signal)

                        except Exception as e:
                            logger.debug(f"Error analyzing nominee '{nominee}': {e}")
                            continue

                except Exception as e:
                    logger.debug(f"Error analyzing awards market {market.get('id')}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in awards season analysis: {e}")

        return signals

    # ── Helper Methods ──────────────────────────────────────────────

    def _find_matching_markets(
        self,
        markets: List[Dict],
        keywords: List[str]
    ) -> List[Dict]:
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

    async def _get_market_price(self, market_id: str) -> Optional[float]:
        """
        Retrieve latest market price from database.

        Args:
            market_id: Kalshi market ID.

        Returns:
            Latest market YES price as float between 0-1, or None if unavailable.
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

    @staticmethod
    def _title_match(movie_title: str, market_title: str) -> bool:
        """
        Check if a movie title matches a market title.

        Args:
            movie_title: Movie title from TMDb.
            market_title: Market title from Kalshi.

        Returns:
            True if titles match, False otherwise.
        """
        # Simple substring matching; production code would use fuzzy matching
        return (movie_title in market_title or
                market_title in movie_title or
                any(word in market_title for word in movie_title.split()[:2]))

    @staticmethod
    def _extract_topic_from_title(title: str) -> Optional[str]:
        """
        Extract the main topic from a market title.

        Args:
            title: Market title string.

        Returns:
            Extracted topic or None.
        """
        # Simple extraction: take first few words
        words = title.split()[:3]
        return " ".join(words) if words else None

    @staticmethod
    def _extract_nominees_from_title(title: str) -> List[str]:
        """
        Extract nominee names from an awards market title.

        Args:
            title: Market title string (e.g., "Will John Smith win Best Actor?").

        Returns:
            List of nominee names.
        """
        # Production code would use NER; this is simplified
        # Look for names between keywords
        nominees = []
        if "will" in title.lower() and "win" in title.lower():
            parts = title.split("will")
            if len(parts) > 1:
                name_part = parts[1].split("win")[0].strip()
                nominees.append(name_part)
        return nominees
