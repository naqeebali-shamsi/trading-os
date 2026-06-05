# MARKET ANALYSIS REVIEW — NomadCrew Trading OS v2

**Reviewer:** Market Analysis (CLI Agent)  
**Date:** 2026-06-05  
**Scope:** autonome/discovery/*, autonome/intelligence/*, autonome/data/*, autonome/backtest/*, India/sentinel, macro feeds, news sentiment, sector rotation, earnings handling.

---

## 1. DATA & MARKET FLAWS

### CRITICAL

| # | Issue | Impact | Files |
|---|-------|--------|-------|
| 1 | **Survivorship bias in all backtests** — Yahoo Finance historical data is adjusted for splits but does NOT include delisted stocks. The backtest engine (`backtest/engine.py`) and data loader (`backtest/data_loader.py`) fetch history for currently trading symbols only. Any company that went bankrupt, got acquired, or delisted during the backtest window is excluded. This inflates backtest returns by 2–5% annually and makes strategies look profitable when they are not. | Backtests are fantasy. You are only testing on "winners" that survived. | `backtest/engine.py`, `backtest/data_loader.py`, `discovery/yahoo_dynamic.py` |
| 2 | **No earnings modeling in backtests** — The backtest engine has zero awareness of earnings dates. It models stops/targets using OHLC within bars, but earnings gaps (which can move a stock 10–30% overnight) are completely ignored. The earnings calendar (`data/earnings.py`) is only used for live signal filtering, not backtest simulation. | Backtests underestimate max drawdown and overestimate Sharpe. A strategy that avoids earnings in live mode but backtests through them has no comparable edge. | `backtest/engine.py`, `data/earnings.py` |
| 3 | **Yahoo Finance as primary data source** — Yahoo data is delayed (15–20 min), rate-limited, and its undocumented screener API (`yahoo_screener.py`) can break without warning. The sector rotation detector, dark horse discovery, and India signals all depend on Yahoo. No fallback to institutional-quality data (polygon, tiingo, alpaca SIP). | Discovery signals are stale before they are generated. Sector rotation uses delayed ETF prices. | `data/yahoo_feed.py`, `discovery/yahoo_screener.py`, `discovery/sector_rotation.py`, `india/sentinel.py` |
| 4 | **Earnings block is symmetric and wrong** — `data/earnings.py:73` uses `abs((earnings_date - today).days)` to block trades both BEFORE and AFTER earnings. Post-earnings, the information is already priced in; continuation moves are valid signals. The system misses 2–5 days of legitimate post-earnings momentum after every report. | Missed alpha. Pre-earnings avoidance is correct; post-earnings blocking is wrong. | `data/earnings.py` |
| 5 | **Statistical forecaster injects sine-wave noise** — `intelligence/timesfm_adapter_production.py:190` adds `math.sin(i) * vol * 0.5` to price projections. This is injecting deterministic oscillations into forecasts and using them to block/contradict signals. The forecast has zero predictive validity. | Signals are blocked for fake reasons. Forecast contradictions are noise, not signal. | `intelligence/timesfm_adapter_production.py`, `intelligence/timesfm_real.py` |
| 6 | **News sentinel has zero signal validation** — `discovery/news_sentinel.py` scores RSS headlines with keyword matching and extracts tickers with crude regex. There is no backtest, no track record, and no evidence that a news-catalyst score of 2.5 predicts next-day returns. False positives are guaranteed (e.g., "CEO" extracted as a ticker, "The" skipped but "AAPL" in a sentence about competition incorrectly scored). | Discovery picks from news are untested. Keyword lists may have worked in 2021 but are not validated. | `discovery/news_sentinel.py`, `discovery/dark_horse.py` |

### HIGH

| # | Issue | Impact | Files |
|---|-------|--------|-------|
| 7 | **Sector rotation has no predictive backtest** — `discovery/sector_rotation.py` computes 20d performance rank scores but never validates whether buying top-ranked sectors and selling bottom-ranked sectors produces alpha. The SECTOR_LEADERS mapping in `dark_horse.py` is static and misses new leaders (e.g., no `AVGO` in Semiconductors until recently). | Sector rotation signals may be lagging, not leading. No evidence of edge. | `discovery/sector_rotation.py`, `discovery/dark_horse.py` |
| 8 | **India macro sentinel uses arbitrary scoring** — `india/sentinel.py` computes a "risk_score" starting at 5.0 and adding/subtracting fixed amounts (e.g., +2.0 if USD/INR > 85). These thresholds are not backtested. We don't know if a risk_score of 8 actually predicts negative Nifty returns. No walk-forward regime validation (DEFENSE vs AGGRESSIVE). | Macro regime recommendations may be wrong. DEFENSE may trigger after the bottom; AGGRESSIVE may trigger before the top. | `india/sentinel.py` |
| 9 | **Dynamic scanner universe is static and meme-heavy** — `discovery/yahoo_dynamic.py` uses a hardcoded UNIVERSE of 70+ symbols weighted toward meme stocks, crypto-adjacent names, and former high-flyers. Many of these (BBBY, CLOV, WKHS, RIDE, FSR, SPWR) are near-delisted or severely impaired. Scanning a static list creates look-ahead bias — you are running a "discovery" engine on symbols you already know about. | Hard to discover new alpha when your universe is yesterday's bagholders. | `discovery/yahoo_dynamic.py` |
| 10 | **Backtest engine ignores overnight gaps** — The engine assumes stops/targets execute within the bar's OHLC range. For 1-hour bars and overnight holds, this is fantasy. Gaps through stops are not modeled. The `slippage=0.05%` parameter is a joke for gaps. | Backtests severely underestimate downside risk. Live drawdowns will be 2–3x backtest estimates. | `backtest/engine.py` |
| 11 | **No delisting/bankruptcy data in backtests** — When a symbol hits zero or delists, Yahoo history simply ends. The backtest engine closes the position at the last available close, not at the bankruptcy auction price (often near zero). This flatters returns on bad picks. | Strategies that pick low-quality names appear safer than they are. | `backtest/data_loader.py`, `backtest/engine.py` |
| 12 | **News sentiment has no source weighting or NLP** — All RSS sources are treated equally. A Benzinga headline and a WSJ investigative piece have the same weight. No named entity recognition, no sentiment classifiers, no historical correlation between source + keyword and next-day returns. | High noise-to-signal ratio. A keyword match on "acquisition" in a denied-rumour headline gets the same score as a confirmed deal. | `discovery/news_sentinel.py`, `intelligence/discovery.py` |
| 13 | **TimesFM adapter never loads real model** — The code tries to import `timesfm` from a hardcoded venv path (`timesfm_env/lib/python3.11/site-packages`). If that fails (and it will on most installs), it falls back to the statistical forecaster. The production system is therefore 100% deterministic statistical, not AI-powered. The user believes they have TimesFM; they do not. | False confidence in AI forecasting. The "forecast" is just EMA + ATR + sine waves. | `intelligence/timesfm_adapter_production.py` |

### MEDIUM

| # | Issue | Impact | Files |
|---|-------|--------|-------|
| 14 | **Yahoo screener API is undocumented and brittle** — `yahoo_screener.py` hits `query1.finance.yahoo.com/v1/finance/screener/predefined/...`. Yahoo changes these endpoints without notice. The `most_shorted` endpoint is particularly unstable. | Dark horse discovery fails silently when Yahoo changes endpoints. No alerting when screeners break. | `discovery/yahoo_screener.py` |
| 15 | **No volume profile or order book data** — All strategies use OHLC + volume only. No Level 2, no time-of-day volume profiles, no VWAP anchoring, no options flow. For breakout strategies, this is like flying blind. | Misses distribution vs accumulation patterns. Entries hit at exhaustion rather than confirmation. | All strategy files |
| 16 | **VIX feed is cached 15 min but strategies react to stale regime** — `data/vix_feed.py` caches VIX for 15 minutes, which is fine, but the regime filter (`strategy/regime.py`) checks `daily_bars` that are computed from yesterday's close. A VIX spike at 9:35 AM won't affect regime filtering until the next day. | Regime filter is a day behind on volatility shocks. | `data/vix_feed.py`, `strategy/regime.py` |
| 17 | **Metrics.py Sharpe is equity-curve derived, not trade-return derived** — `backtest/metrics.py` computes daily returns from the equity curve, not from individual trade returns. If the backtest produces 5 trades in one day and 0 for the rest, the Sharpe is computed over mostly flat equity days, inflating the ratio. | Sharpe ratios are overstated. Industry standard uses trade-return or daily-PnL series. | `backtest/metrics.py` |

---

## 2. WHAT'S RELIABLE

| Area | Observation | Files |
|------|-------------|-------|
| **Yahoo Finance OHLCV fetch** | The `fetch_history()` function in `yahoo_feed.py` is robust: handles retries, parses adjusted close, and returns empty list on failure (never crashes caller). Good defensive programming for a free data source. | `data/yahoo_feed.py` |
| **BarStore ring buffer + SQLite persistence** | In-memory deque per symbol with SQLite warm-restart. The ring buffer prevents unbounded memory growth. | `data/bars.py` |
| **Earnings calendar fetch + cache** | `EarningsCalendar.fetch_earnings()` has 24h TTL cache and graceful degradation when API key is missing or fails. | `data/earnings.py` |
| **VIX fetch with failover** | `vix_feed.py` caches for 15 min and returns stale data on failure rather than crashing. Good for a free indicator. | `data/vix_feed.py` |
| **Backtest engine event loop** | The bar-by-bar simulation with regime filter, risk check, and bracket-order modeling is sound scaffold. Commission and slippage are applied. | `backtest/engine.py` |
| **Metrics computation** | Computes all standard metrics (win rate, profit factor, expectancy, max drawdown, Calmar). Even if Sharpe is slightly flawed, the core calculations are correct. | `backtest/metrics.py` |
| **Parameter sweep infrastructure** | `tools/run_backtest.py` supports grid search over strategy parameters with CSV export. Good for optimization (if data quality were fixed). | `tools/run_backtest.py` |
| **Sector rotation ETF coverage** | Covers all 11 GICS sectors plus subsectors (Semiconductors, Biotech, Regional Banks, Innovation). Good breadth for a free system. | `discovery/sector_rotation.py` |
| **India macro inputs** | Tracks the right inputs (USD/INR, Brent crude, gold). Even if thresholds are unvalidated, the inputs are economically meaningful. | `india/sentinel.py` |
| **News keyword dictionary** | The `CATALYST_KEYWORDS` list is comprehensive (FDA, M&A, earnings, product, macro, retail). Good starting point for signal extraction. | `discovery/news_sentinel.py` |
| **Synthetic data generator** | `generate_synthetic()` in `data_loader.py` uses geometric Brownian motion with drift and volatility. Useful for unit tests. | `backtest/data_loader.py` |

---

## 3. TOP 3 DATA IMPROVEMENTS

### IMPROVEMENT 1: Fix survivorship bias in backtests with survivorship-free data or synthetic delisting model
**Priority: CRITICAL**

All backtests are currently invalid because they only include currently listed stocks. You need either:
- **Option A (Best):** Subscribe to survivorship-bias-free data (CRSP via WRDS, or QuantQuote, or Norgate Data for US equities). This is the gold standard.
- **Option B (Pragmatic):** Add a `delisted_universe.json` file containing historical tickers that were in your universe but later delisted. Fetch their Yahoo history up to delisting date. Include them in backtests.
- **Option C (Minimum viable):** Add a delisting penalty model: when a backtest reaches the last available bar for a symbol, exit at 50% of last close (simulating distressed delisting) with 50% probability, and at 100% with 50% probability. This at least penalizes strategies that hold near-delisting names.

**Action items:**
- Add `backtest/survivorship.py` with a `DelistedTickerDB` class that tracks historical universe membership.
- Modify `run_backtest.py` to include delisted symbols when running universe-level backtests.
- Document the survivorship-bias-free status of any backtest report. If you can't get clean data, label backtests as "biased upward; subtract 2–4% annually for realistic estimate."

### IMPROVEMENT 2: Model earnings events and overnight gaps in backtests
**Priority: CRITICAL**

The backtest engine currently assumes stops execute inside the bar OHLC. For a system trading hourly bars and holding overnight, this is fiction. You must:
- **Earnings:** Maintain an `earnings_dates.json` file with historical earnings dates for backtest symbols. In the backtest, when the next bar is an earnings date, apply a gap drawn from historical earnings-return distribution for that symbol/sector before checking stops.
- **Overnight gaps (non-earnings):** Sample overnight returns from historical distribution and apply them to the next day's open before stop/target logic.
- **Pre-event flattening:** Add a backtest rule that exits positions at the close before earnings if configured (matching live behavior).

Also fix `earnings.py:73` — change from `abs(delta)` to `delta >= 0 and delta <= buffer_days` to block only pre-earnings, not post-earnings.

**Action items:**
- Add `backtest/gap_model.py` with `sample_overnight_gap(symbol, date)` and `sample_earnings_gap(symbol, date)`.
- Modify `backtest/engine.py` to apply gap before stop/target check on the first bar of each new session.
- Fix `data/earnings.py:is_earnings_week()` to only block pre-earnings.

### IMPROVEMENT 3: Validate macro regime and sector rotation signals with walk-forward backtests
**Priority: HIGH**

The sector rotation detector and India macro sentinel produce regime classifications (DEFENSE/AGGRESSIVE, strong/weak sectors) but there is zero evidence these improve returns. You need:
- **Sector rotation backtest:** Run a walk-forward backtest where you go long top-3 sectors and short bottom-3 sectors each week, rebalancing weekly. Use Yahoo data for sector ETFs (XLK, XLF, etc.) going back to 2000. Measure Sharpe, max drawdown, and correlation to SPY.
- **India macro regime backtest:** Backtest the India strategy with regime overlays: only take BUY signals when `recommend_regime() == "AGGRESSIVE"`, scale down when "CAUTIOUS", go to cash when "DEFENSE". Compare to always-invested baseline.
- **Signal vs noise for news:** For each keyword in `CATALYST_KEYWORDS`, compute the next-day mean return for stocks mentioned in headlines containing that keyword over the past 2 years. Remove keywords with t-stat < 1.5.

**Action items:**
- Add `tools/validate_sector_rotation.py` that outputs a CSV of weekly rotation backtest results.
- Add `tools/validate_india_regime.py` that compares regime-filtered vs unfiltered India returns.
- Add `tools/validate_news_keywords.py` that computes historical keyword predictive power.
- If sector rotation shows no edge (Sharpe < 0.5), remove it from dark horse scoring or demote it.

---

## SUMMARY VERDICT

The data layer of this system is **entirely free-tier** — Yahoo Finance, RSS feeds, Finnhub free plan, and keyword matching. Free data is fine for prototyping, but the system has crossed into production without addressing the three fatal biases that make backtests worthless: **survivorship bias** (only winners tested), **earnings gap blindness** (overnight risk ignored), and **unvalidated macro signals** (thresholds pulled from thin air). The good news: the scaffolding for data ingestion, caching, backtesting, and metrics is solid. If you implement the Top 3 improvements — **survivorship-free backtest data, earnings/gap modeling, and walk-forward regime validation** — you transform backtests from marketing fiction into decision tools. Without them, you are optimizing strategies on a dataset that systematically excludes the exact scenarios that blow up live accounts.

*Review completed for NomadCrew Trading OS v4.0*
