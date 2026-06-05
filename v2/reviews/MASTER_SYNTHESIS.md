# NOMADCREW TRADING OS — MASTER REVIEW SYNTHESIS
## Multi-Agent Review v4.0  |  8 Specialists  |  June 5, 2026

---

## EXECUTIVE SCORECARD

| Dimension | Score /10 | Verdict |
|-----------|-----------|---------|
| Architecture | 4/10 | God object supervisor, sync blocking, no DI |
| Platform Engineering | 3/10 | Missing deps, WSL path hacks, SQLite on 9P |
| Financial Rigor | 4/10 | Broken D/E parsing, static thresholds, fake Kelly |
| Investor Readiness | 3/10 | Crash bugs, 2-ETF universe, no trade management |
| Trading Edge | 3/10 | Retail indicators, no alpha, market orders |
| Data Quality | 4/10 | Survivorship bias, Yahoo delays, no gap modeling |
| UI/UX | 6/10 | Polished dark theme, not mobile, fragile JS |
| QA/Test Coverage | 2/10 | 2 test files for 9K LOC, no pytest, no CI |
| **OVERALL** | **3.6/10** | **Skeleton with good ideas — un-investable as-is** |

---

## CONSENSUS CRITICAL ISSUES (All 8 Agents Agree)

### 1. FOUR RUNTIME CRASH BUGS
- `supervisor/main.py` → `st.forecaster` never initialized → crash on first signal
- `strategy/router.py` → `self._last_signal_bar` doesn't exist → crash on scan
- `risk/portfolio_heat.py` → `self.positions` doesn't exist → crash on exit
- `regime_forecaster.py` → imports non-existent module

**Impact**: The US trading system literally **cannot run** without fixing these first.

### 2. TRADING UNIVERSE IS 2 ETFs (TQQQ + SPY)
This is not a trading system. It's a leveraged beta replication toy with slippage.

### 3. KELLY SIZING USES `signal_confidence` AS `win_rate`
Mathematically invalid. Confidence is a fuzzy model score, not historical probability. Can approve dangerously oversized positions.

### 4. DEFAULT EXECUTION IS MARKET ORDERS ON BREAKOUTS
Literally buys the top of the wick. Retail execution at its worst.

### 5. NO POSITION MANAGEMENT AFTER ENTRY
No trailing stops, breakeven, time-stops, partial scale-outs. Enter and pray.

### 6. INDIA D/E PARSING IS BROKEN
Heuristic misclassifies valid ratios as percentages. Silently corrupts the most important risk metric.

### 7. STATIC SCORING THRESHOLDS
PE < 15 = +3 whether market is at 15x or 35x. No sector-relative percentiles. No adaptability.

### 8. NO TEST FRAMEWORK
2 test files for 9,100 LOC. No pytest. No CI. The "tests" that exist have broken mocks.

### 9. SYNC ARCHITECTURE BLOCKS ON LLM GATE
30-second HTTP call to OpenRouter on every signal, blocking the entire hot path.

### 10. SURVIVORSHIP BIAS IN BACKTESTS
Only tests currently-listed stocks. Inflates returns. No earnings/gap modeling.

---

## WHAT'S ACTUALLY GOOD (Don't Throw Away)

| Component | Why It Works |
|-----------|--------------|
| **Risk Framework** | Kelly sizing (concept), drawdown halt, daily loss limits, VIX-based sizing, portfolio heat, slippage buffer — correct concepts |
| **Bracket Orders** | Native Alpaca OCO with pre-flight checks and retries |
| **Live Mode Gate** | `AUTONOME_LIVE_CONFIRM` prevents accidental live trading |
| **Journal Rotation** | Paranoid archive-then-verify-then-delete pattern |
| **Health Monitor** | Deterministic state-file escalation with emergency halt |
| **TimesFM Adapter** | Graceful statistical fallback when model unavailable |
| **India Risk Module** | Wider stops, cash buffer — context-aware market adaptation |
| **Long-Term Scoring** | Q+V+G-R framework is academically correct |
| **Dashboard Design** | Consistent dark theme, readable cards, good color semantics |

---

## TOP 3 FIXES (By Priority)

### #1: FIX THE FOUR CRASH BUGS + REPLACE KELLY + ADD TESTS
Without this, nothing else matters. The system literally crashes.
- Fix `st.forecaster`, `_last_signal_bar`, `self.positions`, import error
- Replace confidence-as-win-rate Kelly with **fixed fractional 1% risk per trade**
- Install pytest, write supervisor loop tests, risk manager halt tests

### #2: EXPAND UNIVERSE + WIRE DISCOVERY + ADD TRADE MANAGEMENT
- Expand beyond TQQQ/SPY to 50+ stocks via dark-horse screener
- Add macro regime overlay (VIX + SPY 200DMA)
- Add position management: breakeven stops, time-stops, partial scale-outs, chandelier trailing
- Default to **limit orders with market fallback**

### #3: FIX DATA QUALITY + SURVIVORSHIP BIAS + BACKTEST RIGOR
- Source historical constituents for unbiased backtests
- Model earnings/gap risk in backtests
- Replace static thresholds with sector-relative z-scores/percentiles
- Decouple LLM gate from hot path (async or batch)

---

## AGENT-SPECIFIC HIGHLIGHTS

### System Architect
- "Supervisor is a God Object with 12 hardcoded dependencies"
- "Synchronous blocking architecture — no WebSocket streaming, no event bus"
- "No position lifecycle management after entry"
- Top fix: Dependency injection + async event-driven core

### Financial Analyst
- "D/E and ROE heuristic parsing is broken in fundamentals.py"
- "Static scoring thresholds ignore market regimes"
- "Kelly sizing uses signal_confidence as win_rate"
- Top fix: Compute D/E from balance sheet + percentile-based scoring

### Trader
- "Trading universe is only TQQQ/SPY — zero diversification"
- "Strategies are textbook retail indicators with no walk-forward edge"
- "Default execution is market orders on breakouts"
- Top fix: Expand universe + add macro regime + trade management

### Investor
- "Investability score: 3/10 — solid skeleton, un-investable as-is"
- "A system that crashes during order flow has unbounded loss potential"
- "No position management after entry"
- Top fix: Fix crashes + fixed fractional risk + persist risk state

### Platform Engineer
- "requirements.txt is severely incomplete (missing yfinance, numpy)"
- "SQLite on /mnt/e (9P mount) — corruption and performance risk"
- "Health monitor reads /tmp/autonome_paper.log which does not exist"
- Top fix: Complete deps + move SQLite to native ext4 + fix supervisor crash

### Market Analyst
- "Survivorship bias: backtests only test currently-listed stocks"
- "Statistical forecaster injects sine-wave noise into forecasts"
- "News sentinel has zero signal validation"
- Top fix: Historical constituents + earnings gap modeling + walk-forward validation

### UI/UX Designer
- "No viewport meta tag — broken on mobile"
- "Fragile JS relying on global event.target"
- "Misleading empty sparklines in longterm.html"
- Top fix: Mobile-first + fix interactions + robust loading/error states

### QA Specialist
- "2 test files for 9,100 LOC — 1:30 ratio"
- "No test framework installed"
- "FakeAlpaca mock lacks _get method — runtime error silently ignored"
- Top fix: Install pytest + test supervisor + test risk halt + property-based tests

---

## FILES PRODUCED

| File | Size | Author |
|------|------|--------|
| `reviews/ARCHITECT_REVIEW.md` | 16 KB | System Architect |
| `reviews/PLATFORM_REVIEW.md` | 12 KB | Platform Engineer |
| `reviews/FINANCIAL_REVIEW.md` | 10 KB | Financial Analyst |
| `reviews/INVESTOR_REVIEW.md` | 14 KB | Investor |
| `reviews/TRADER_REVIEW.md` | 11 KB | Trader |
| `reviews/MARKET_REVIEW.md` | 16 KB | Market Analyst |
| `reviews/UX_REVIEW.md` | 9 KB | UI/UX Designer |
| `reviews/QA_REVIEW.md` | 12 KB | QA Specialist |
| **This synthesis** | — | Master |

---

## NEXT STEPS

1. **Read the individual reviews** for detailed file:line citations
2. **Fix crash bugs first** (supervisor, router, heat, forecaster)
3. **Install pytest** and write 5 core tests (supervisor loop, risk halt, execution, D/E parsing, backtest engine)
4. **Replace Kelly** with fixed fractional 1% risk
5. **Expand trading universe** to 50+ stocks and wire dark-horse discovery
6. **Add trade management** (breakeven, trailing, time-stops, partials)
7. **Fix India D/E parsing** in fundamentals.py
8. **Add mobile viewport** and fix dashboard JS fragility

---
*Generated by 8-agent swarm review | NomadCrew Trading OS v4.0*
