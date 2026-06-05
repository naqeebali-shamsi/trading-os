# Financial Analysis Review — NomadCrew Trading OS v4.0
**Scope:** US/Canada screener, India screener & fundamentals, trading strategies (router, EMA crossover, momentum breakout), and risk layer.  
**Date:** 2025-06-05  
**Files reviewed:** `autonome/longterm/us_screener.py`, `autonome/longterm/india_screener.py`, `autonome/india/fundamentals.py`, `autonome/india/signals.py`, `autonome/india/strategy.py`, `autonome/strategy/router.py`, `autonome/strategy/ema_crossover.py`, `autonome/strategy/momentum_breakout.py`, `autonome/risk/risk_manager.py`, `autonome/risk/portfolio_heat.py`, `autonome/india/risk.py`

---

## 1. FINANCIAL METHODOLOGY FLAWS

### A. Valuation & Ratio Handling
| Issue | File | Severity | Detail |
|---|---|---|---|
| **D/E heuristic is dangerous** | `fundamentals.py:103` | HIGH | `if val > 10: return val / 100.0` assumes any D/E > 10 is a percentage. A company with D/E = 15.0 (ratio) becomes 0.15. Indian banks legitimately have D/E ~8–12 (ratio); they are mis-scored as ultra-low-debt. |
| **ROE/growth heuristics are backwards** | `fundamentals.py:116-130` | HIGH | `_roe()` and `_growth()` assume `val < 1.0` means decimal form (×100). yfinance already normalizes most ratios; this double-scales correct values and corrupts them. |
| **PEG uses trailing PE / earningsGrowth** | `fundamentals.py:175-177` | MEDIUM | yfinance `earningsGrowth` is TTM and often stale or missing. PEG is unreliable here. No forward-PE fallback. |
| **Trailing PE only** | `us_screener.py`, `india_screener.py` | MEDIUM | No forward PE, no EV/EBITDA (mentioned in docstring but missing), no sector-relative percentile scoring. |
| **Missing FCF yield safety** | `us_screener.py:115-120` | LOW | FCF / marketCap is used raw. Negative FCF yields 0 (not penalized). Should penalize negative FCF for "quality" stocks. |

### B. Scoring Methodology
| Issue | File | Severity | Detail |
|---|---|---|---|
| **Static thresholds ignore market regimes** | all screeners | HIGH | PE < 20 = +2 points whether S&P is at 15x or 35x. No market-relative z-scores or sector benchmarking. The same stock scores differently in bull vs bear markets for no fundamental reason. |
| **Arbitrary caps (`min(s, 10)`) destroy signal resolution** | all screeners | MEDIUM | A stock with ROE 50% and D/E 0.1 gets the same quality score as one with ROE 16% and D/E 0.4. No differentiation at the top. |
| **CAGR uses only 2 data points (Year 0 / Year 2)** | `us_screener.py:138-166` | MEDIUM | Extremely sensitive to one-off accounting items. Should regress on at least 4 periods or use operating earnings instead of net income. |
| **India India_screener D/E too strict for financials** | `india_screener.py:161-164` | MEDIUM | `de < 0.3` for full credit. HDFC Bank naturally runs D/E ~7 (ratio). The screener implicitly down-rates every Indian bank despite their strong ROE. |
| **Random sampling = non-deterministic results** | `us_screener.py:353-354` | MEDIUM | `random.sample()` means running the screener twice gives different top-30 lists. No seeding. |

### C. Risk / Position Sizing
| Issue | File | Severity | Detail |
|---|---|---|---|
| **Kelly formula misapplied** | `risk_manager.py:196-198` | HIGH | Uses `signal_confidence` as `win_rate`. Confidence is a model output (0–1 fuzzy score), not a historical win probability. Kelly requires edge derived from backtest/edge ratio. This can approve oversized positions for high-confidence garbage signals. |
| **Portfolio heat conflates P&L with risk** | `india/risk.py:155-157` | MEDIUM | On exit, heat is reduced by realized loss `abs(exit-entry) * shares`, not the original risk budget. A big winner frees more heat than a small loser, which is backwards. |
| **No time-decay of heat** | `portfolio_heat.py` | MEDIUM | A position held 3 months contributes the same heat as one entered today. Stale stops should decay or be reassessed. |
| **Position sizing ignores correlation** | all risk files | MEDIUM | Two positions in Nifty Bank stocks treated as independent. No sector-beta or pairwise correlation adjustment. |
| **Stop loss uses `latest.low`, not entry price** | `india/strategy.py:139` | MEDIUM | `stop = latest.low * (1 - atr_pct*2)`. On a green reversal day, `latest.low` can be well below entry, making the stop too wide and risk estimate wrong. |
| **Slippage buffer adds to risk denominator** | `risk_manager.py:186-188` | LOW | The 10% slippage adjustment is correct for reducing shares, but the comment says "assume 10% extra slippage beyond stop" — actually it reduces position by ~9%, which is sound. |

### D. Data Quality / yfinance
| Issue | File | Severity | Detail |
|---|---|---|---|
| **No caching layer** | all | HIGH | Every screener call re-fetches 5-year history + financials + info. For 100 stocks this is 300+ API calls. Yahoo will rate-limit or return stale/None data. |
| **Financial index names are brittle** | `us_screener.py:143` | MEDIUM | `"Total Revenue"` index matching relies on exact string. yfinance has changed these labels across versions (e.g., `"Total Revenue"` vs `"TotalRevenue"`). |
| **No stale-data detection** | all | MEDIUM | If yfinance returns 6-month-old financials because a company delayed filing, the CAGR is silently wrong. |
| **Duplicate tickers in universe** | `us_screener.py:34`, `india_screener.py:31` | LOW | `"GIS"` listed twice (US); `"DABUR.NS"` listed twice (India). Harmless but sloppy. |

---

## 2. WHAT IS SOUND

### Valuation Logic
- **Value + Quality + Growth - Risk composite** is a classic and robust framework (akin to Morningstar/AAII). The four-pillar structure is correct.
- **FCF yield as a value signal** (`us_screener.py:115-120`) is superior to P/E alone. Good inclusion.
- **Distance-from-52w-low as value proxy** is a valid counter-tilt against momentum bias.
- **India-specific thresholds** (PE < 12 = +3 vs US PE < 15 = +3) reflect higher Indian market multiples. This is context-aware.

### Risk Infrastructure
- **Persistent drawdown halt** (`risk_manager.py:132-136`) with disk-backed state is excellent practice for autonomous systems.
- **VIX regime rules** (≥30 halve size, ≥40 halt) are market-standard.
- **Portfolio heat tracking** by dollar-at-risk rather than notional is the correct professional approach.
- **Earnings avoidance** (`momentum_breakout.py:87-90`) is a smart tactical filter.
- **India risk module** correctly adapts for higher volatility (wider stops, smaller positions, cash buffer).

### Strategy Design
- **Regime-based router** (`router.py`) selecting trend vs mean-reversion strategies is the right high-level architecture.
- **ATR-based stops/targets** across all strategies adapt to volatility. Fixed-percentage stops would be worse.
- **EMA crossover volume confirmation** (`ema_crossover.py:84-87`) filters out low-conviction crosses.

### India Advisory
- **Buy-the-dip on fundamentals** thesis is well-suited to India’s retail-driven volatility.
- **`value_score()` with dip bonus + near-high penalty** creates intuitive entry/exit logic for manual execution.

---

## 3. TOP 3 IMPROVEMENTS

### 1. Fix D/E and Ratio Parsing (Critical Data Quality)
**Why:** Heuristic scaling in `fundamentals.py` silently corrupts the most important risk metric.  
**What:** Replace `_de_ratio()`, `_roe()`, `_growth()` with explicit unit detection:
```python
def _de_ratio(info):
    raw = info.get("debtToEquity")
    if raw is None: return None
    val = float(raw)
    # Detect percentage by magnitude (>10 and typical range 0-5000)
    # Detect ratio by magnitude (0.01 - 10 typical)
    if val > 10:
        # Could be % (e.g., 150 = 150%) or an anomaly
        # Cross-check with sector median or use both interpretations with bounds
        return val / 100.0
    return val
```
**Better:** Fetch balance sheet directly and compute `Total Debt / Total Stockholder Equity` yourself. YFinance `info` dict is too inconsistent for risk decisions.

### 2. Switch from Static Thresholds to Market/Sector-Relative Percentile Scores
**Why:** A stock with PE 22 is cheap in a sector averaging 35, but expensive in one averaging 12. Static thresholds mis-rank systematically.  
**What:**
- For each metric (PE, PB, ROE, D/E), compute the universe median and standard deviation.
- Convert raw values to z-scores or percentile ranks within sector.
- Score = `max(0, 5 - percentile_rank)` for value metrics (lower = better) and `percentile_rank` for quality.
- This automatically adapts to market conditions and removes arbitrary thresholds like `PE < 15 = +3`.

### 3. Replace Confidence-as-Win-Rate Kelly with Backtested Edge Sizing
**Why:** Kelly sizing with `win_rate = signal_confidence` is mathematically invalid and can blow up positions on overconfident bad signals.  
**What:**
- Maintain a **signal journal** (strategy, regime, entry, exit, R/R, outcome) in `trade_journal.py`.
- Compute actual win rate and average R/R per (strategy + regime) tuple monthly.
- Feed those historical stats into Kelly:
  ```python
  win_rate = historical_win_rate[strategy][regime]
  payoff = historical_avg_rr[strategy][regime]
  kelly = win_rate - ((1 - win_rate) / payoff)
  ```
- Until 30+ samples exist per bucket, default to fixed fractional sizing (e.g., 1% risk per trade) instead of Kelly.

---

## APPENDIX: Quick Bug List
1. `router.py:115` references `self._last_signal_bar` (undefined) — crashes every scan. Use `self._last_idx.get(symbol)`.
2. `portfolio_heat.py:98` references `self.positions` (doesn’t exist). Should be `self._position_heat`.
3. `india_screener.py` duplicates `"DABUR.NS"`.
4. `us_screener.py` line 42 `" GE"` has a leading space.
