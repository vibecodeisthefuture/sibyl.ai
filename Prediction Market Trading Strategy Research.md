---
title: Prediction Market Trading Strategy Research
type: reference
date: 2026-03-28
last_updated: 2026-03-28
status: active
area: learning
tags:
  - prediction-markets
  - trading
  - quantitative-finance
  - arbitrage
  - kalshi
  - polymarket
  - research
related:
  - "[[Projects/sibyl-readme]]"
---

# Quantitative Analysis of Optimal Trading Strategies in Event Derivatives: Empirical Evidence from Kalshi and Polymarket

The global financial landscape has witnessed a transformative shift with the institutionalization of prediction markets, evolving from experimental academic tools into high-volume venues for event-driven speculation. By 2026, the monthly transaction volume across these platforms reached approximately $21 billion, a staggering increase from the $1.2 billion recorded in early 2025. This growth has been catalyzed by two primary entities: Kalshi, a federally regulated exchange overseen by the Commodity Futures Trading Commission (CFTC), and Polymarket, a decentralized, crypto-native platform. These markets operate on the fundamental principle of Arrow-Debreu securities, where contracts represent binary outcomes — paying one unit of currency if an event occurs and zero otherwise — thereby allowing the price to serve as a real-time, financially-backed proxy for the market's perceived probability of the event.

The utility of these markets extends beyond mere speculation. Research from the Federal Reserve suggests that prediction markets provide a high-frequency, continuously updated, and distributionally rich benchmark for macroeconomic expectations, often outperforming traditional surveys and financial derivatives in accuracy. For professional traders and institutional hedgers, the maturation of this asset class has introduced a suite of systematic strategies designed to exploit structural inefficiencies, behavioral biases, and microstructural signals. This report provides a comprehensive analysis of the most effective tested strategies, supported by empirical data and statistical validation.

---

## Comparative Microstructure and Regulatory Frameworks

A nuanced understanding of the divergence between Kalshi and Polymarket is essential for strategy selection. Kalshi operates as a Designated Contract Market (DCM), employing a centralized limit order book (CLOB) and settling trades in U.S. dollars via traditional banking rails such as ACH. Its integration with retail brokerages like Robinhood, which brought prediction markets to 27 million funded accounts, has significantly altered its liquidity profile, particularly in sports and entertainment sectors.

Polymarket, conversely, utilizes a decentralized structure on the Polygon blockchain, with settlement occurring in USDC. This environment fosters a different class of participants — international, crypto-savvy, and often highly aggressive speculators — which leads to distinct pricing dynamics. While Kalshi emphasizes regulatory compliance, FDIC-insured accounts, and macroeconomic stability, Polymarket prioritizes speed, global access, and cultural/news-based markets.

### Liquidity and Execution Performance Metrics

Statistical comparisons of execution quality reveal that Polymarket generally offers tighter spreads and higher market depth, though Kalshi maintains an edge in specific institutional-grade economic markets.

| Feature | Polymarket (International) | Kalshi (US) |
| :--- | :--- | :--- |
| Typical Spreads | 2–5 cents | 3–8 cents |
| Market Depth Ratio | 3.5x higher vs Kalshi | Benchmark for macro |
| Execution Latency | 62ms | 78ms (Standard) / <10ms (VIP) |
| Slippage (5k contracts) | 1–2% | 2–4% |
| Fees (Effective Taker) | 0.01%–0.10% | ~1.2% (Average) |
| Capital Efficiency | 0% APY on idle funds | 3.75%–4% APY |

The higher taker fees on Kalshi represent a significant barrier for high-frequency strategies, whereas Polymarket's fee structure encourages high-volume market making. Furthermore, Kalshi offers a 0.05% rebate to market makers, while Polymarket's U.S. platform implements a 10 basis point taker fee.

---

## Arbitrage Strategies and Systematic Extraction

Arbitrage remains the primary systematic edge in prediction markets, exploiting the fundamental identity that in a binary market, the price of "YES" plus the price of "NO" must logically equal $1.00 (excluding fees and spreads). Empirical analysis of Polymarket data from April 2024 to April 2025 identified approximately $40 million in realized profit extracted via arbitrage.

### Inter-Exchange and Intra-Exchange Arbitrage

Inter-exchange arbitrage leverages price discrepancies for identical events across different platforms. These gaps arise due to differing participant demographics, liquidity levels, and reaction speeds to news. For instance, a news shock may be reflected on Polymarket 3–7 seconds faster than on Kalshi, allowing bots to buy the lagging side and sell the leading side.

Intra-exchange arbitrage focuses on mispricing within a single market or across dependent markets on the same platform. A common form is the "Buy-All" strategy in multi-condition markets. If a market lists four mutually exclusive candidates for an election and the sum of their "YES" prices is less than $1.00, a trader can purchase all outcomes to guarantee a risk-free payout.

### Realized Arbitrage Profits by Strategy Type (2024–2025)

The distribution of arbitrage profits shows that single-market rebalancing is far more lucrative than more complex combinatorial strategies.

| Strategy Class | Realized Extraction (USD) | Relative Share |
| :--- | :--- | :--- |
| Long "NO" Positions | $17,307,114 | 43.7% |
| Long "YES" Positions | $11,092,286 | 28.0% |
| Single-Condition Rebalancing | $5,899,287 | 14.9% |
| Short YES/NO Arbitrage | $5,298,446 | 13.4% |
| Combinatorial Arbitrage | $95,157 | 0.24% |
| **Total Arbitrage Extracted** | **~$39.6 million** | **100.0%** |

Combinatorial arbitrage, which relies on logical links across different market IDs (e.g., predicting that if Party A wins the Presidency, they must have a specific probability of winning the Senate), captured only 0.24% of total profits despite its theoretical appeal. This failure is attributed to extreme liquidity asymmetry between primary and secondary markets; while a "Presidential Winner" market may have $500,000 in available liquidity, its dependent "Cabinet Appointment" market may have only $5,000, limiting the maximum extractable profit.

---

## Behavioral Finance and the Favorite-Longshot Bias

One of the most persistent and statistically significant edges in prediction markets is the exploitation of behavioral biases, most notably the favorite-longshot bias. This phenomenon occurs when traders systematically overpay for unlikely outcomes (longshots) while underpricing highly probable ones (favorites).

### Empirical Evidence from Kalshi Transaction Data

A systematic study of over 300,000 contracts on Kalshi provides the following return statistics for different price buckets:

| Price Bucket | Outcome Group | Expected Return (Pre-Fee) | Expected Return (Post-Fee) |
| :--- | :--- | :--- | :--- |
| < 10 cents | Extreme Longshots | -60.0% | Worse |
| 10–50 cents | Underdogs | Negative | Negative |
| > 50 cents | Favorites | Positive | ~2.6% for Makers |
| **Market Average** | **All Contracts** | **-20.0%** | **-22.0%** |

The negative average return across the market suggests that the "house" (in the form of exchange fees and the bid-ask spread) consumes significant capital, but the losses are disproportionately concentrated among buyers of cheap contracts. Traders can capture this bias by acting as market makers (Makers) for favorite contracts, as they earn significantly higher returns than Takers, who pay a premium to enter positions immediately. The GWU working paper interprets these patterns through a model of belief disagreement and a behavioral tendency to overstate small probabilities.

### Domain-Specific Calibration and Horizon Effects

The accuracy of pricing is not uniform across all topics. Calibration analysis of 292 million trades across Kalshi and Polymarket indicates that political markets are chronically "underconfident," meaning prices are compressed toward 50% even when the outcome is highly certain.

| Domain | Calibration Profile | Intercept/Slope Metric |
| :--- | :--- | :--- |
| Politics | Persistent Underconfidence | Intercept +0.15 |
| Weather | Overconfidence (Extreme Pricing) | Intercept -0.09 |
| Entertainment | Overconfidence | Intercept -0.09 |
| Macro/Economics | High Calibration | μ rising from 0.99 to 1.32 |

For traders, this suggests that the "smart money" play in political markets is to buy "YES" on heavy favorites, as the market systematically understates their true probability. Conversely, in weather and entertainment markets, traders should "fade" extreme prices, as participants tend to overstate their certainty.

---

## Quantitative Market Microstructure and OBI

High-frequency traders (HFT) in prediction markets utilize Order Book Imbalance (OBI) as a primary predictive signal for short-term price movements. OBI quantifies the net difference between aggressive buy and sell orders at the best bid and ask.

### Price Impact and Predictive Windows

The relationship between mid-price change (Δp) and OBI is modeled as:

$$\Delta p_{t+1} = \lambda \cdot OBI_t + \epsilon_t$$

where λ is the price impact coefficient, which is inversely proportional to market depth.

Empirical findings suggest:

- **Predictive Horizon**: OBI signals have their highest accuracy over 1–5 minute horizons, decaying into noise after 10 minutes.
- **Volatility Regimes**: The signal is most effective in moderate volatility (ATR 0.8–1.5x average); in high volatility, execution lag erases the edge.
- **Success Rates**: OBI correlates with next-tick direction at 62% accuracy for highly liquid markets like the S&P 500 contracts on Kalshi, though this drops to 48% in noisier markets.

Traders implement this by sum-weighting resting volume across the top price levels (typically 1.0, 0.5, 0.25, etc.) and treating bid depth ratios above 60% as directional indicators. This allows for "ignition" trades where the trader enters a position just as liquidity on the opposite side begins to thin but before the price move has fully materialized.

---

## Macroeconomic Forecasting and Tactical Hedging

Kalshi macroeconomic markets offer a distinct advantage for institutional and retail traders looking to hedge risk or forecast policy shifts. Federal Reserve research has confirmed that Kalshi expectations for headline CPI provide a statistically significant improvement over the Bloomberg consensus.

### Macro Indicator Accuracy Benchmarks

| Indicator | Kalshi Performance vs. Traditional | Advantage |
| :--- | :--- | :--- |
| Fed Funds Rate | Better than Fed Funds Futures | Mode hits perfect record 1 day prior |
| Headline CPI | Significant Improvement vs Bloomberg | High-frequency updating distribution |
| Core CPI | Statistically Similar to Bloomberg | Distributed forecasts for tail risks |
| Unemployment | Similar to Bloomberg Consensus | Fills gaps where options are thin |

These markets serve as a "real-time, financially-backed expectations data" source. For instance, Kalshi's mode correctly predicted the September 2024 FOMC 50 basis point cut when other benchmarks were uncertain. Traders can use these signals to position in rate-sensitive sectors before official announcements, effectively treating prediction markets as a leading indicator.

### Hedging Use Cases with Kalshi Macro Contracts

Unlike traditional derivatives which can be thinly traded or limited to specific contracts, Kalshi's event-specific structure allows for surgical risk management:

- **Currency Risk**: An import-export company can buy contracts on currency fluctuations to hedge against profit erosion.
- **Weather Risk**: Agricultural and tourism professionals use rainfall and temperature contracts to manage seasonal volatility.
- **Policy Risk**: Traders long on energy stocks may hedge with "No" positions on specific regulatory or election outcomes.

---

## Optimal Capital Management: The Kelly Criterion

Maximizing profit in prediction markets requires a rigorous approach to position sizing. The Kelly Criterion is the standard mathematical formula for maximizing long-term growth by allocating capital based on the perceived "edge."

### Mathematical Foundation for Binary Markets

For a prediction market contract where winning pays $1.00 and you pay `p_market` per share, the optimal fraction of bankroll (`f*`) is:

$$f^* = \frac{p_{model} - p_{market}}{1 - p_{market}}$$

where `p_model` is your model's estimated probability of the event and `p_market` is the market price.

However, "Full Kelly" sizing is highly sensitive to model error and can lead to extreme variance. Professional implementations, such as those used by Polymarket automated bots, employ a "Fractional Kelly" approach.

| Kelly Alpha (α) | Growth Potential | Variance/Volatility | Risk of Ruin |
| :--- | :--- | :--- | :--- |
| Full (1.0) | 100% | 100% | High (Psychologically difficult) |
| Half (0.5) | ~75% | ~25% | Low |
| Brier-Tiered | Dynamic | Low | Optimized for model accuracy |

Systems like the Polymarket Prediction System v2 use Brier-tiered alpha values (typically 0.10 to 0.40) to scale bets according to the model's historical calibration. This ensures that even if a model estimates a high probability, the actual wager is restrained by the proven reliability of the model's historical Brier score.

---

## Informed Trading and Signal Extraction

Prediction markets frequently attract participants with material non-public information, particularly in geopolitical and corporate markets. A systematic analysis of over 93,000 distinct markets on Polymarket identified $143 million in aggregate anomalous profit from informed trading.

### Analysis of Suspicious Trading Activity

The Harvard-based study "From Iran to Taylor Swift" utilized a composite score of five signals to identify informed trading: cross-sectional bet size, within-trader bet size, profitability, pre-event timing, and directional concentration.

| Case Study | Wallet Identifier | Profit (USD) | Implied Signal |
| :--- | :--- | :--- | :--- |
| Iran Strike (Feb 2026) | Magamyman | $553,000 | Traded 71 min before news |
| Maduro Capture | Burdensome-Mix | $485,000 | Traded hours before op |
| Google Search Rankings | Pseudonymous | >$1,000,000 | Precise prophetic knowledge |
| Taylor Swift Engagement | romanticpaul | Significant | Aggressive buying pre-news |

For professional traders, these "whales" serve as high-conviction signals. While individual retail traders cannot compete with insiders, monitoring wallet patterns and sudden price spikes — such as the 3.6% to 73% jump in the 2025 Nobel Peace Prize market hours before the announcement — provides an "epistemic value" that can be traded. However, this strategy carries the risk of "herd behavior" and "informational cascades," where traders follow a signal that may actually be noise, as observed in the French Whale's $45 million bet on the 2024 election.

---

## Benchmarking Accuracy and the "Volume Trap"

A critical finding in the prediction market landscape is that larger liquidity does not always correlate with higher accuracy. A landmark study by Vanderbilt University (Clinton & Huang) analyzed 2,500 markets during the 2024 election cycle and found that PredictIt, despite being the smallest exchange, was the most accurate.

| Platform | Notional Volume | Resolution Accuracy (%) | Accuracy Metric (Brier/Log-Loss) |
| :--- | :--- | :--- | :--- |
| PredictIt | Restricted ($3,500 cap) | 93% | Gold Standard |
| Kalshi | Regulated USD | 78% | Respectable |
| Polymarket | $2.4B+ Handle | 67% | Least Accurate |

Researchers define this as the "Volume Trap": massive liquidity can attract political partisans and noise traders who use the market for "cheerleading" rather than objective forecasting. This results in "mutual exclusivity errors," where the probabilities for competing outcomes (e.g., Democrat Sweep vs. Republican Sweep) move in the same direction simultaneously, indicating a fundamental lack of internal logic in high-volume markets. Systematic traders must therefore be cautious of "within-market pricing dynamics" on Polymarket and look to Kalshi or PredictIt for cleaner signal aggregation.

---

## Quantitative Model Training and Backtesting

The development of automated agents for prediction markets relies on replaying historical data through event-driven backtesting engines. Frameworks like PredictionMarketBench replay real Kalshi episodes, simulating fills based on historical order book depth and taker/maker schedules.

### Model Features and Calibration Results

Modern machine learning ensembles (e.g., Gradient Boosting, Random Forest) use upwards of 54 features to predict market resolution. A key finding from the development of the Polymarket Prediction System v2 was that price history alone is a poor predictor of final outcomes; instead, volume patterns, category-specific resolution clarity, and question structure are the most predictive features.

| Metric | Baseline (v1) | Optimized (v2) | Implications |
| :--- | :--- | :--- | :--- |
| Features | 10 (incl. price) | 54 (no price leakage) | Avoids spurious correlation |
| Brier Score | 0.237 | 0.229 | Significant calibration gain |
| Training Samples | 100 | 7,889 | Improved generalization |
| YES/NO Signal Split | Biased | 47%/47% (Balanced) | Reliable directional edge |

The most critical lesson for quantitative traders is to prioritize probability calibration over overall accuracy. A model that is 60% accurate but perfectly calibrated is more valuable than a model that is 75% accurate but poorly calibrated, as the former allows for precise position sizing via the Kelly Criterion. Furthermore, backtest results should be treated with skepticism due to multiple testing bias; a 50% "haircut" to the Sharpe ratio is standard practice to account for data mining.

---

## Strategic Conclusions

The empirical evidence from 2024–2026 suggests that maximizing profit in prediction markets is a multi-dimensional challenge requiring structural, behavioral, and quantitative expertise. Arbitrage remains the most consistent source of low-risk yield, with single-market rebalancing and "Long NO" positions in mutually exclusive sets providing the highest realized extraction rates. However, the institutionalization of the space has compressed these margins, making speed and low-latency infrastructure essential for capturing inter-exchange spreads.

Traders who move beyond arbitrage can find persistent edges in behavioral biases. The favorite-longshot bias on Kalshi allows market makers to earn consistent returns by providing liquidity for favorite outcomes while avoiding the high-loss "longshot" contracts favored by retail Takers. In political markets, the structural underconfidence identified in calibration studies suggests that buying high-probability outcomes is a statistically winning strategy.

Finally, the maturation of macroeconomic event contracts on Kalshi provides an unprecedented opportunity for high-frequency hedging and data-driven forecasting. By integrating microstructural signals like Order Book Imbalance with macro distributional forecasts, professional traders can navigate the volatility of the "Truth Market" with precision. As prediction markets continue to expand into a $600 billion sector, those who can synthesize these strategies while maintaining rigorous capital management through fractional Kelly sizing will be best positioned to extract long-term alpha.

---

## Works Cited

1. [How Prediction Markets Scaled to USD 21B in Monthly Volume in 2026 - TRM Labs](https://www.trmlabs.com/resources/blog/how-prediction-markets-scaled-to-usd-21b-in-monthly-volume-in-2026)
2. [Kalshi and the Rise of Macro Markets - Federal Reserve](https://www.federalreserve.gov/econres/feds/files/2026010pap.pdf)
3. [Kalshi vs. Polymarket: Which Prediction Market Is Best for You? - Action Network](https://www.actionnetwork.com/online-sports-betting/reviews/kalshi-vs-polymarket)
4. [Analysis of Market Depth Prediction: The Duopoly Pattern of Kalshi and Polymarket](http://www.rootdata.com/news/478393)
5. [Prediction Markets vs Traditional Markets: A Trader's Guide - Stock Alarm](https://pro.stockalarm.io/blog/prediction-markets-vs-traditional-markets)
6. [The Fed - Kalshi and the Rise of Macro Markets - Federal Reserve](https://www.federalreserve.gov/econres/feds/kalshi-and-the-rise-of-macro-markets.htm)
7. [Polymarket vs Kalshi Explained - QuantVPS](https://www.quantvps.com/blog/polymarket-vs-kalshi-explained)
8. [Are Polymarket and Kalshi as reliable as they say? - DL News](https://www.dlnews.com/articles/markets/polymarket-kalshi-prediction-markets-not-so-reliable-says-study/)
9. [What is Kalshi Prediction Market? - Ledger](https://www.ledger.com/academy/topics/economics-and-regulation/what-is-kalshi-prediction-market)
10. [Highest Volume Prediction Markets in 2026 - QuantVPS](https://www.quantvps.com/blog/prediction-markets-volume-compared)
11. [Honest Comparison of Polymarket and Kalshi - BlockEden.xyz](https://blockeden.xyz/forum/t/i-trade-both-polymarket-and-kalshi-here-is-my-honest-comparison-of-fees-liquidity-ux-and-which-one-actually-has-better-odds/418)
12. [Polymarket vs Kalshi Fee Comparison - QuantVPS](https://www.quantvps.com/blog/polymarket-vs-kalshi-explained)
13. [Systematic Edges in Prediction Markets - QuantPedia](https://quantpedia.com/systematic-edges-in-prediction-markets/)
14. [Prediction Market Arbitrage Guide: Strategies for 2026 - Forex VPS](https://newyorkcityservers.com/blog/prediction-market-arbitrage-guide)
15. [How to make $40 million with a formula - Binance Square](https://www.binance.com/en/square/post/299978441376945)
16. [Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets - arXiv](https://arxiv.org/abs/2508.03474)
17. [Arbitrage in Prediction Markets: Strategies, Impact and Open Questions - Flashbots](https://collective.flashbots.net/t/arbitrage-in-prediction-markets-strategies-impact-and-open-questions/5198)
18. [How Prediction Market Arbitrage Works - Trevor Lasn](https://www.trevorlasn.com/blog/how-prediction-market-polymarket-kalshi-arbitrage-works)
19. [Combinatorial Arbitrage in Prediction Markets - Navnoor Bawa](https://navnoorbawa.substack.com/p/combinatorial-arbitrage-in-prediction)
20. [Makers or Takers: The Economics of the Kalshi Prediction Market - GWU](https://www2.gwu.edu/~forcpgm/2026-001.pdf)
21. [Makers and Takers: Economics of Kalshi - University College Dublin](https://www.ucd.ie/economics/t4media/WP2025_19.pdf)
22. [Decomposing Crowd Wisdom: Domain-Specific Calibration Dynamics - arXiv](https://arxiv.org/html/2602.19520v1)
23. [Order Book Imbalance in High-Frequency Markets - Emergent Mind](https://www.emergentmind.com/topics/order-book-imbalance-obi)
24. [Market Microstructure: Order Flow Imbalance as a Predictive Signal - Moltbook](https://www.moltbook.com/post/45a964fc-6177-490f-b73e-3f895c178b91)
25. [How Order Book Imbalances Predict Price Moves - Medium](https://medium.com/@thewealthacademyyt/how-order-book-imbalances-predict-price-moves-before-they-happen-crystal-ball-series-part-2-fd9fc66f86a5)
26. [Federal Reserve Research: Kalshi Prediction Markets - The Motley Fool](https://www.fool.com/investing/2026/03/16/federal-reserve-research-kalshi-prediction-markets/)
27. [A Beginner's Guide to Prediction Markets and Kalshi - OSL](https://www.osl.com/en/bits/article/sequoia-backed-truth-market-a-beginners-guide-to-prediction-markets-and-kalshi)
28. [Kelly Criterion - Polymarket Bot - Mintlify](https://mintlify.com/joicodev/polymarket-bot/risk/kelly-criterion)
29. [Kelly criterion - Wikipedia](https://en.wikipedia.org/wiki/Kelly_criterion)
30. [Kelly Criterion Trading: Formula & Risk Management Guide - LiteFinance](https://www.litefinance.org/blog/for-beginners/best-technical-indicators/kelly-criterion-trading/)
31. [Polymarket Prediction System v2 - Navnoor Bawa](https://navnoorbawa.substack.com/p/polymarket-prediction-system-v2-from)
32. [Building a Quantitative Prediction System for Polymarket - Navnoor Bawa](https://navnoorbawa.substack.com/p/building-a-quantitative-prediction)
33. [Explainer: Insider Trading and Prediction Markets - NC State](https://poole.ncsu.edu/thought-leadership/article/explainer-insider-trading-and-prediction-markets/)
34. [From Iran to Taylor Swift: Informed Trading in Prediction Markets - Harvard](https://corpgov.law.harvard.edu/2026/03/25/from-iran-to-taylor-swift-informed-trading-in-prediction-markets/)
35. [Taking a gamble on prediction markets - Washington Post](https://www.washingtonpost.com/politics/2026/03/23/taking-gamble-prediction-markets/)
36. [Prediction Markets as "Truth Machines" - Michigan Journal of Economics](https://sites.lsa.umich.edu/mje/2026/03/14/prediction-markets-as-truth-machines/)
37. [Exploring Decentralized Prediction Markets: Accuracy, Skill, and Bias - ResearchGate](https://www.researchgate.net/publication/398660802_Exploring_Decentralized_Prediction_Markets_Accuracy_Skill_and_Bias_on_Polymarket)
38. [Manipulation in Prediction Markets: An Agent-based Modeling Experiment - arXiv](https://arxiv.org/html/2601.20452v1)
39. [The Volume Trap: Why Vanderbilt Researchers Say 'Bigger' Isn't 'Better'](https://markets.financialcontent.com/wral/article/predictstreet-2026-1-16-the-volume-trap-why-vanderbilt-researchers-say-bigger-isnt-better-for-prediction-markets)
40. [The "Volume Trap": Why PredictIt's 93% Accuracy Is Shaking the Foundation](http://business.times-online.com/times-online/article/predictstreet-2026-1-17-the-volume-trap-why-predictits-93-accuracy-is-shaking-the-prediction-market-foundation)
41. [Prediction Markets? Accuracy and Efficiency of $2.4 Billion in 2024 - OSF](https://osf.io/download/d5yx2/)
42. [prediction-market-backtesting - GitHub](https://github.com/evan-kolberg/prediction-market-backtesting)
43. [PredictionMarketBench - GitHub](https://github.com/Oddpool/PredictionMarketBench)
44. [Trading Probability Strategies: Master Risk & Win More](https://tradewiththepros.com/trading-probability-strategies/)
45. [Backtesting - Man Group](https://www.man.com/insights/backtesting)
46. [A Tax Geek's Guide to Prediction Markets - Cordasco & Company](https://cspcpa.com/2026/03/02/so-you-want-to-bet-on-whether-itll-snow-in-april-and-call-it-investing-a-tax-geeks-guide-to-prediction-markets/)
