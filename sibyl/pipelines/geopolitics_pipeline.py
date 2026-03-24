"""
Geopolitics category signal pipeline for Sibyl prediction market trading system.

Transforms geopolitical and legal data from CourtListener, GDELT, and Congress APIs
into trading signals for Kalshi Geopolitics & Legal prediction markets. Analyzes
court rulings, congressional bills, and news volume.

WARNING: This is a TIER 3 RESTRICTED category. Signals generated must be flagged
for override protocol and human review before execution.
"""

import logging
from typing import List, Dict, Optional

from sibyl.pipelines.base_pipeline import BasePipeline, PipelineSignal
from sibyl.clients.courtlistener_client import CourtListenerClient
from sibyl.clients.gdelt_client import GdeltClient
from sibyl.clients.congress_client import CongressClient

logger = logging.getLogger(__name__)


class GeopoliticsPipeline(BasePipeline):
    """
    Geopolitics category signal pipeline (TIER 3 RESTRICTED).

    Analyzes legal, political, and geopolitical data from CourtListener, GDELT,
    and Congress APIs to generate trading signals for Kalshi Geopolitics & Legal
    prediction markets about court rulings, legislation, and sanctions.

    IMPORTANT: All signals from this pipeline are flagged as requiring override
    protocol due to sensitivity of geopolitical predictions.
    """

    CATEGORY = "Geopolitics & Legal"
    PIPELINE_NAME = "geopolitics"
    DEDUP_WINDOW_MINUTES = 120  # Sprint 16: political events move slowly

    # Keywords for market matching
    GEOPOLITICS_KEYWORDS = [
        "court", "supreme", "ruling", "decision", "bill", "congress",
        "senate", "legislation", "law", "regulation", "sanctions",
        "trade", "tariff", "legal", "case"
    ]

    def __init__(self, *args, **kwargs):
        """Initialize the Geopolitics pipeline."""
        super().__init__(*args, **kwargs)

    def _create_clients(self) -> List:
        """Create and return the data clients used by this pipeline.

        Returns:
            List of data clients: CourtListenerClient, GdeltClient, CongressClient.
        """
        return [CourtListenerClient(), GdeltClient(), CongressClient()]

    def _category_variants(self) -> List[str]:
        """Return category name variants for market matching.

        Returns:
            List of category variants including Geopolitics & Legal and Politics.
        """
        return ["Geopolitics & Legal", "geopolitics & legal", "Politics", "politics"]

    async def _analyze(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Generate geopolitics signals by analyzing legal and political data.

        Implements three analysis strategies:
        1. Supreme Court Opinion Signal - Track recent court decisions
        2. Congressional Bill Progress - Monitor bill advancement through committee
        3. GDELT News Volume - Detect news surges indicating rising probability

        All signals are flagged with TIER_3_RESTRICTED for override protocol.

        Args:
            markets: List of active Kalshi markets in geopolitics category.

        Returns:
            List of PipelineSignal objects with trading recommendations.
        """
        signals = []

        try:
            signals.extend(await self._analyze_supreme_court(markets))
        except Exception as e:
            logger.warning(f"Supreme Court analysis failed: {e}")

        try:
            signals.extend(await self._analyze_congressional_bills(markets))
        except Exception as e:
            logger.warning(f"Congressional bill analysis failed: {e}")

        try:
            signals.extend(await self._analyze_gdelt_news(markets))
        except Exception as e:
            logger.warning(f"GDELT news analysis failed: {e}")

        logger.info(f"Generated {len(signals)} geopolitics signals (TIER 3 RESTRICTED)")
        return signals

    async def _analyze_supreme_court(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze Supreme Court opinions for resolution catalysts.

        Searches CourtListener for recent opinions on cases matching market topics.
        New opinions on relevant cases are strong resolution catalysts.

        Args:
            markets: List of active markets.

        Returns:
            List of PipelineSignal objects for Supreme Court signals.
        """
        signals = []

        try:
            court_client = self._get_client("courtlistener")
            if not court_client:
                logger.debug("CourtListener client not available")
                return signals

            # Find court/legal related markets
            matching_markets = self._find_matching_markets(
                markets,
                ["court", "supreme", "ruling", "decision", "case"]
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

                    # Extract case name from market title
                    case_name = self._extract_case_name(market_title)
                    if not case_name:
                        continue

                    # Search for recent opinions on this case
                    opinions = await court_client.search_opinions(case_name)

                    if not opinions or "opinions" not in opinions:
                        continue

                    for opinion in opinions.get("opinions", [])[:5]:  # Check top 5
                        opinion_date = opinion.get("date_filed")
                        opinion_text = opinion.get("text", "").lower()
                        judges = opinion.get("panel", [])

                        # Check opinion sentiment (simplified)
                        outcome = self._analyze_opinion_sentiment(opinion_text)

                        if outcome is not None:
                            # Opinion directly resolves market
                            data_implied_prob = 0.95 if outcome == 1 else 0.05

                            edge, direction, ev = self._compute_edge(
                                data_implied_prob,
                                market_price
                            )

                            if edge > 0.15:  # High threshold for court signals
                                confidence = self._edge_to_confidence(edge)
                                signal = PipelineSignal(
                                    market_id=market_id,
                                    signal_type="DATA_CATALYST",
                                    confidence=confidence,
                                    ev_estimate=ev,
                                    direction=direction,
                                    reasoning=(
                                        f"TIER_3_RESTRICTED: Supreme Court opinion in '{case_name}' "
                                        f"filed {opinion_date} appears to resolve market. "
                                        f"{len(judges)} judges on panel. "
                                        f"Requires override protocol review."
                                    ),
                                    source_pipeline=self.PIPELINE_NAME,
                                    category=self.CATEGORY,
                                    data_points={
                                        "case_name": case_name,
                                        "opinion_date": opinion_date,
                                        "panel_size": len(judges),
                                        "outcome_signal": outcome,
                                        "court_id": opinion.get("id"),
                                    }
                                )
                                signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error analyzing court market {market.get('id')}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in Supreme Court analysis: {e}")

        return signals

    async def _analyze_congressional_bills(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze congressional bill progress signals.

        Uses Congress API to check bill status. Bills advancing through committee
        or heading to a vote are strong catalysts for related markets.

        Args:
            markets: List of active markets.

        Returns:
            List of PipelineSignal objects for congressional signals.
        """
        signals = []

        try:
            congress_client = self._get_client("congress")
            if not congress_client:
                logger.debug("Congress client not available")
                return signals

            # Find legislation-related markets
            matching_markets = self._find_matching_markets(
                markets,
                ["bill", "congress", "senate", "legislation", "law", "act"]
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

                    # Extract bill number/name
                    bill_identifier = self._extract_bill_identifier(market_title)
                    if not bill_identifier:
                        continue

                    # Parse bill identifier into congress, type, and number
                    # e.g., "S123" → congress=119, type="s", number=123
                    parsed_bill = self._parse_bill_identifier(bill_identifier)
                    if not parsed_bill:
                        continue

                    congress_num, bill_type, bill_number = parsed_bill

                    # Fetch bill information
                    bill_data = await congress_client.get_bill(congress_num, bill_type, bill_number)

                    if not bill_data:
                        continue

                    status = bill_data.get("current_status", "").lower()
                    last_action_date = bill_data.get("latest_action_date")
                    committees = bill_data.get("committees", [])

                    # Assess likelihood based on status
                    if "passed" in status:
                        # Bill already passed relevant chamber
                        data_implied_prob = 0.85
                        signal_type = "DATA_CATALYST"
                    elif "committee" in status and any(c.get("passed", False) for c in committees):
                        # Passed committee → advancing
                        data_implied_prob = 0.70
                        signal_type = "DATA_MOMENTUM"
                    elif "committee" in status:
                        # In committee → active progression
                        data_implied_prob = 0.55
                        signal_type = "DATA_CATALYST"
                    else:
                        continue  # No clear signal

                    edge, direction, ev = self._compute_edge(
                        data_implied_prob,
                        market_price
                    )

                    if edge > 0.12:  # 12% threshold for legislative signals
                        confidence = self._edge_to_confidence(edge)
                        signal = PipelineSignal(
                            market_id=market_id,
                            signal_type=signal_type,
                            confidence=confidence,
                            ev_estimate=ev,
                            direction=direction,
                            reasoning=(
                                f"TIER_3_RESTRICTED: Bill {bill_identifier} status is '{status}' "
                                f"as of {last_action_date} with {len(committees)} active committees. "
                                f"Implied probability {data_implied_prob:.2%}. "
                                f"Requires override protocol review."
                            ),
                            source_pipeline=self.PIPELINE_NAME,
                            category=self.CATEGORY,
                            data_points={
                                "bill_identifier": bill_identifier,
                                "status": status,
                                "last_action_date": last_action_date,
                                "committee_count": len(committees),
                                "bill_id": bill_data.get("id"),
                            }
                        )
                        signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error analyzing bill market {market.get('id')}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in congressional bill analysis: {e}")

        return signals

    async def _analyze_gdelt_news(self, markets: List[Dict]) -> List[PipelineSignal]:
        """
        Analyze news volume signals using GDELT data.

        Searches GDELT for articles matching market topics. Surging news volume
        indicates something is happening and increases perceived probability.

        Args:
            markets: List of active markets.

        Returns:
            List of PipelineSignal objects for news volume signals.
        """
        signals = []

        try:
            gdelt_client = self._get_client("gdelt")
            if not gdelt_client:
                logger.debug("GDELT client not available")
                return signals

            # Find geopolitics-related markets
            matching_markets = self._find_matching_markets(
                markets,
                ["sanctions", "war", "trade", "tariff", "conflict", "treaty"]
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

                    # Extract topic keywords
                    topic = self._extract_topic_keywords(market_title)
                    if not topic:
                        continue

                    # Fetch recent and historical news volume
                    recent_news = gdelt_client.search_articles(topic, days_back=7)
                    historical_news = gdelt_client.search_articles(topic, days_back=30)

                    if not recent_news or not historical_news:
                        continue

                    recent_count = len(recent_news.get("articles", []))
                    historical_count = len(historical_news.get("articles", []))

                    if historical_count > 0:
                        news_growth_ratio = recent_count / (historical_count / 4)
                    else:
                        news_growth_ratio = 1.0

                    # News surge indicates rising probability
                    if news_growth_ratio > 1.3:  # 30% growth threshold
                        # Boost implied probability based on surge
                        data_implied_prob = min(0.85, 0.50 + (news_growth_ratio - 1.0) * 0.15)

                        edge, direction, ev = self._compute_edge(
                            data_implied_prob,
                            market_price
                        )

                        if edge > 0.10:
                            confidence = self._edge_to_confidence(edge)
                            signal = PipelineSignal(
                                market_id=market_id,
                                signal_type="DATA_SENTIMENT",
                                confidence=confidence,
                                ev_estimate=ev,
                                direction=direction,
                                reasoning=(
                                    f"TIER_3_RESTRICTED: GDELT news volume for '{topic}' surging "
                                    f"{news_growth_ratio:.1f}x ({recent_count} recent vs "
                                    f"{historical_count} historical), indicating heightened coverage "
                                    f"and rising perceived probability. Requires override protocol review."
                                ),
                                source_pipeline=self.PIPELINE_NAME,
                                category=self.CATEGORY,
                                data_points={
                                    "topic": topic,
                                    "news_growth_ratio": news_growth_ratio,
                                    "recent_count": recent_count,
                                    "historical_count": historical_count,
                                }
                            )
                            signals.append(signal)

                except Exception as e:
                    logger.debug(f"Error analyzing news market {market.get('id')}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in GDELT news analysis: {e}")

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
    def _extract_case_name(title: str) -> Optional[str]:
        """
        Extract court case name from market title.

        Args:
            title: Market title string (e.g., "Will the Supreme Court rule in Roe v. Wade?").

        Returns:
            Extracted case name or None.
        """
        # Simple extraction: look for "v." pattern
        if " v " in title or " vs " in title:
            # Extract part before "v" or "vs"
            parts = title.split(" v")
            if parts:
                # Return words around the case
                return parts[0].strip()
        return None

    @staticmethod
    def _extract_bill_identifier(title: str) -> Optional[str]:
        """
        Extract bill identifier from market title.

        Args:
            title: Market title string (e.g., "Will S. 123 pass the Senate?").

        Returns:
            Extracted bill identifier (e.g., "S123") or None.
        """
        import re
        # Look for pattern like "S. 123", "HR 456", "S123", etc.
        match = re.search(r'(S|HR|HB)\s*\.?\s*(\d+)', title, re.IGNORECASE)
        if match:
            chamber, number = match.groups()
            return f"{chamber.upper()}{number}"
        return None

    @staticmethod
    def _parse_bill_identifier(bill_id: str) -> Optional[tuple[int, str, int]]:
        """
        Parse bill identifier into congress number, bill type, and bill number.

        Args:
            bill_id: Bill identifier string (e.g., "S123", "HR456").

        Returns:
            Tuple of (congress: int, bill_type: str, bill_number: int) or None if invalid.
            Congress defaults to 119 (current congress in 2025-2027).
        """
        import re
        # Parse "S123" or "HR456" format
        match = re.match(r'(S|HR|HB)(\d+)', bill_id, re.IGNORECASE)
        if not match:
            return None

        chamber, number = match.groups()
        chamber_lower = chamber.lower()

        # Map chamber abbreviations to API bill types
        bill_type_map = {
            "s": "s",       # Senate bill
            "hr": "hr",     # House bill
            "hb": "hr",     # Alternative House bill notation
        }

        bill_type = bill_type_map.get(chamber_lower)
        if not bill_type:
            return None

        try:
            bill_number = int(number)
        except ValueError:
            return None

        # Default to current congress (119 for 2025-2027)
        congress_num = 119

        return (congress_num, bill_type, bill_number)

    @staticmethod
    def _extract_topic_keywords(title: str) -> Optional[str]:
        """
        Extract topic keywords from market title.

        Args:
            title: Market title string.

        Returns:
            Extracted topic or None.
        """
        # Take first 3-5 meaningful words
        words = title.split()[:5]
        return " ".join(words) if words else None

    @staticmethod
    def _analyze_opinion_sentiment(opinion_text: str) -> Optional[int]:
        """
        Analyze court opinion for outcome sentiment (simplified).

        Args:
            opinion_text: Opinion text (already lowercased).

        Returns:
            1 for favorable outcome, -1 for unfavorable, 0 for neutral, None if unclear.
        """
        # Very simplified sentiment analysis for demonstration
        favorable_words = ["affirmed", "upheld", "valid", "constitutional", "prevails", "wins"]
        unfavorable_words = ["reversed", "invalid", "unconstitutional", "struck down", "overturned"]

        favorable_count = sum(1 for word in favorable_words if word in opinion_text)
        unfavorable_count = sum(1 for word in unfavorable_words if word in opinion_text)

        if favorable_count > unfavorable_count:
            return 1
        elif unfavorable_count > favorable_count:
            return -1
        elif favorable_count > 0 or unfavorable_count > 0:
            return 0
        else:
            return None
