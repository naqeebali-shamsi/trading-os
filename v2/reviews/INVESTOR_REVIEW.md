# INVESTOR REVIEW — NomadCrew Trading OS v4.0
**Reviewer:** Investor Risk Agent  
**Question:** Would I trust this with real money?  
**Verdict:** NOT YET. The plumbing is thoughtful, but the alpha generators are broken and several code paths will crash on first contact with markets. With capital at risk, "promising architecture" is not enough.

---

## 1. WHY I WOULD NOT INVEST

### 1.1 The System Cannot Run Without Patching (Capital at Risk of Software Error)
Four confirmed runtime crash bugs exist in core trading paths:
- **`supervisor/main.py:336`** references `st.forecaster` which is never initialized. The TimesFM forecast filter will crash the supervisor on the first signal.
- **`strategy/router.py:115`** references `self._last_signal_bar` but the attribute is `self._last_idx`. Every scan call throws `AttributeError`.
- **`risk/portfolio_heat.py:98`** references `self.positions` which does not exist; should be `self._position_heat`. Failed-fill cleanup crashes.
- **`intelligence/regime_forecaster.py`** imports a non-existent module (`timesfm_adapter` instead of `timesfm_adapter_production`).

A system that crashes during order submission has unbounded loss potential — orders may be left orphaned, stops uncancelled, or positions doubled.

### 1.2 Trading Universe = Two Correlated Beta Proxies
`config/settings.yaml` defines the entire trading universe as `TQQQ` and `SPY`. The dark-horse discovery engine produces watchlists, but **they are never fed into the trading loop**. You are paying slippage and commission to replicate QQQ/SPY factor exposure with 3x leverage on one leg. This is guaranteed to underperform buy-and-hold after costs.

### 1.3 Kelly Sizing Uses Fabricated Win Rates
```python
win_rate = signal_confidence  # synthetic slope ratio, not empirical
kelly = win_rate - ((1 - win_rate) / payoff)
```
Kelly criterion with fictional inputs is not conservative — it is **mathematically invalid**. It will produce erratic position sizes based on hand-waved "confidence" scores from 1980s-era EMA crossovers. Position sizing is the dominant factor in drawdowns; getting it wrong is how accounts blow up.

### 1.4 Market-Order Execution on Breakout Signals
Default `order_type: market`. On a momentum breakout, a market order fills at the top of the wick, instantly destroying the risk/reward assumptions baked into the signal. The `limit_entry.py` module exists but is not wired as default. This single configuration choice turns marginal alpha into guaranteed negative edge.

### 1.5 No Overnight/Gap Risk Modeling — TQQQ Can Gap 10%+
The backtest engine assumes stops execute inside the bar OHLC (`if bar.low <= sl: exit_price = min(bar.open, sl)`). For overnight holds on TQQQ, this is fantasy. A single Fed announcement or geopolitical event can gap TQQQ 8–15% through a stop. There is no pre-event flattening, no hedge, and no VIX term-structure monitoring.

### 1.6 No Position Management After Entry
Once a bracket order is placed, the system goes silent:
- No trailing stop recalculation as ATR expands/contracts.
- No breakeven-stop activation after +1R profit.
- No partial take-profit scaling (e.g., 50% at 2R, runner to 4R).
- No time-stop (exit if not profitable after N bars).

Trade management is where professional traders capture edge. This system is one-legged — good at entries, incompetent at exits.

### 1.7 LLM Gate Blocks the Critical Path with 30s Latency
Every signal triggers a synchronous HTTP POST to OpenRouter with a 30-second timeout. In a momentum breakout, 30 seconds of latency means the move is over before the order hits. The LLM reviews OHLC bars and a static playbook with no order book, no options flow, no dark pool data. When the API fails, signals are auto-rejected — so API downtime causes missed opportunities. This is latency theater, not edge.

### 1.8 Risk State Is Partially Ephemeral
- `daily_loss_accum` is **in-memory only**. If the process crashes at 2 PM and restarts, the counter resets to zero. Trades that should be blocked will go through.
- `PortfolioHeat._position_heat` is never persisted. After a restart, the system has no memory of its risk exposure.
- A crash during a drawdown episode is the exact moment you need protection most.

### 1.9 Synchronous Polling = Always Stale Data
The system uses Alpaca REST API polling (`fetch_history()` every cycle, `feed=iex`). There is no WebSocket streaming. On a 1-hour timeframe, you are looking at data that may be 10–60 minutes stale. For a momentum breakout strategy that relies on volume surges, missing the first 30–120 seconds is fatal.

### 1.10 Backtest Is Fantasy
The backtest engine:
- Only simulates **single-symbol** at a time (not a portfolio).
- Uses 0.05% commission and 0.05% slippage — optimistic for market orders on volatile ETFs.
- Models no overnight gaps.
- Has no out-of-sample validation or walk-forward analysis.

There is **zero empirical evidence** that the active strategies have edge.

### 1.11 Long-Term Gems Have No Execution Path
The US/Canada and India long-term screeners produce quality scores but output JSON dashboards only. There is no rebalancing logic, no portfolio construction, no position sizing, and no execution pipeline for these names. They are research reports, not a strategy.

---

## 2. WHAT GIVES CONFIDENCE

### 2.1 Risk Framework Has the Right Concepts
Despite calibration and wiring issues, the risk layer demonstrates real trading experience:
- **Drawdown circuit breaker** (10% max) with persistent halt state and manual resume.
- **Daily loss limit** (3% of equity) — though in-memory only.
- **Volatility halt** (realized vol ≥ 50% annualized).
- **VIX-based position reduction** (halve size at VIX ≥ 30; block at VIX ≥ 40).
- **Portfolio heat tracking** (5% total, 3% per sector) with conviction weighting.
- **PDT guard** and **buying power guard**.
- **Slippage-adjusted risk** (10% buffer beyond stop).

These are the controls that keep traders in business. They just need to be fully wired and crash-recoverable.

### 2.2 Live Mode Safety Gate
The `AUTONOME_LIVE_CONFIRM=I_UNDERSTAND` environment variable requirement is excellent safety engineering. It prevents accidental live trading from config typos. Every broker integration should have this.

### 2.3 Bracket Order Execution via Native Alpaca API
The execution engine uses Alpaca's `order_class: bracket`, atomically linking entry, stop, and target. This avoids the orphan-order problem that destroys many homegrown systems. Short pre-flight checks (shortable, easy_to_borrow, margin enabled) are also present.

### 2.4 Append-Only Journal with Audit Trail
SQLite logging of signals, orders, PnL, and equity snapshots enables real performance attribution. The rotation logic archives before deleting and verifies archive integrity before vacuuming. This is production-grade data safety.

### 2.5 Broker Reconciliation Catches Drift
`Reconciler` compares broker positions to journal OPEN orders, flags ghost positions, untracked positions, and orphaned stop/target orders. This catches the "system thinks it has a position but the broker doesn't" class of bugs.

### 2.6 India Risk Module Is Thoughtfully Adaptive
`india/risk.py` adapts for Indian market characteristics: wider stops (8–12%), lower per-stock limits (2.5%), higher cash buffer (20%), sector concentration caps (20%), and circuit-breaker awareness. Whoever wrote this understands that risk is market-specific.

### 2.7 Health Monitor with Emergency Halt
`health_monitor.py` detects stale heartbeats, drawdown breaches, and idle periods during market hours. It escalates to `EMERGENCY_HALT` state file. This is the right severity model.

---

## 3. TOP 3 INVESTOR-CRITICAL CHANGES

### CHANGE #1: Fix Runtime Bugs + Replace Kelly with Empirical Fixed Fractional + Add Position Management
**Priority: P0 — Capital Preservation**

1. **Fix the four confirmed crash bugs** (forecaster init, router `_last_signal_bar`, heat `unregister()`, regime_forecaster import). A system that crashes during order flow is un-investable.

2. **Replace Kelly sizing with fixed fractional risk** sized off actual stop distance:
   ```python
   dollar_risk = equity * 0.01  # 1% fixed per trade
   shares = dollar_risk / (entry_price - stop_loss)
   ```
   Kelly requires known edge. You do not have known edge. Using synthetic confidence as win_rate will over-bet on noise and under-bet on edge.

3. **Persist all risk state** (`daily_loss_accum`, `portfolio_heat`, `peak_equity`, `halted`) to SQLite on every mutation. After a crash, reload from disk before processing the next bar.

4. **Add active trade management**:
   - Breakeven stop after +1R profit.
   - Time-stop: exit at next open if not profitable after 5 bars.
   - Partial scale-out: close 50% at +2R, trail remainder with chandelier ATR stop.
   - Daily reconciliation of realized P&L to update `daily_loss_accum`.

**Investor impact:** Eliminates catastrophic loss from software error and from invalid sizing. Transforms the system from a crash-prone script into a capital-preservation-first engine.

### CHANGE #2: Expand Trading Universe + Wire Discovery + Add Macro Regime Overlay
**Priority: P1 — Alpha Generation**

1. **Connect `discovery/dark_horse.py` to `supervisor/symbol_rotator.py`**. The daily watchlist should dynamically populate the supervisor's symbol list every morning.

2. **Expand to 20–30 liquid mid-to-large caps** with options markets (for future hedging). Filter by average daily dollar volume > $50M.

3. **Implement a `MacroOverlay`**:
   - When VIX < 20 and SPY > 200-day MA: trade individual names from discovery (aggressive mode).
   - When VIX > 25 or SPY < 200-day MA: shift to cash/short-duration bonds, reduce to SPY-only, or halt new entries (defensive mode).
   - Use yield curve and USD/INR (for India) as secondary regime inputs.

4. **Build a walk-forward backtest** across the expanded universe with realistic slippage (0.1% for market, 0.03% for limit) and overnight gap sampling. Do not deploy capital without out-of-sample validation.

**Investor impact:** Converts a beta-minus-slippage replicator into a system that can actually discover and capture idiosyncratic alpha. Macro overlay prevents the system from fighting the tide.

### CHANGE #3: Fix Execution Quality + Model Gap Risk + Decouple LLM Gate
**Priority: P1 — Edge Preservation**

1. **Change default execution to `limit_with_fallback`**:
   - Breakout signals: limit at `min(entry_price, last_close * 1.001)` with 30s fallback to market.
   - Pullback signals: limit at signal price or VWAP if below.
   - Never submit market orders on momentum signals.

2. **Add pre-event risk reduction**:
   - Query earnings calendar at 3:30 PM ET.
   - If FOMC, NFP, or major earnings scheduled for next session, flatten non-core positions or reduce exposure by 50%.
   - For TQQQ specifically, apply a pre-event circuit: any position in a 3x leveraged ETF should be exited before high-volatility events.

3. **Model gap risk in backtest and live**:
   - In backtest: sample overnight gaps from historical distribution and apply them before checking stops on the first bar of each session.
   - In live: monitor pre-market futures and VIX futures. If implied gap > 2× ATR, flatten before open or reduce size.

4. **Decouple LLM gate from critical path**:
   - Run LLM review asynchronously or cache scores by symbol+regime (recomputed hourly).
   - Default to a rules-based pre-filter (regime alignment, R/R check, earnings proximity) for latency-sensitive signals.
   - Use LLM only for discovery/qualitative thesis validation, not hot-path signal approval.

**Investor impact:** Stops giving away the spread on every entry. Prevents catastrophic gap-through-stop losses on leveraged ETFs. Removes latency-induced missed opportunities.

---

## 4. INVESTOR SCORECARD

| Dimension | Score (1-10) | Investor Note |
|-----------|-------------|---------------|
| **Capital Preservation** | 4/10 | Risk concepts are right, but crashes, ephemeral state, and invalid Kelly sizing create real blow-up risk. |
| **Alpha Generation** | 2/10 | Two correlated ETFs with no statistical edge. No walk-forward validation. |
| **Position Sizing** | 3/10 | 1% fixed-risk intent is good, but Kelly implementation is mathematically invalid. |
| **Execution Quality** | 3/10 | Bracket orders are correct, but market-order default is toxic. No limit-entry wiring. |
| **Drawdown Control** | 5/10 | 10% max DD halt is reasonable. Daily loss limit is good but not persisted. |
| **Diversification** | 1/10 | Two symbols, same sector (tech/beta), same country. Long-term gems have no execution. |
| **Fee/Slippage Awareness** | 3/10 | 10% slippage buffer on risk is good, but backtest uses 0.05% and ignores market impact. |
| **Long-Term Gem Quality** | 6/10 | Screeners are thoughtful (Graham-Buffett criteria), but random sampling and no execution path limit value. |
| **Rebalancing** | 1/10 | No position rebalancing logic exists. India is manual-only. |
| **Governance & Safety** | 6/10 | Live gate and reconciliation are strong. Health monitor shows maturity. |
| **OVERALL INVESTABILITY** | 3/10 | Cannot deploy capital to a system that crashes, trades two ETFs with market orders, and has no gap-risk awareness. |

---

## 5. BOTTOM LINE

**This system has the skeleton of a professional-grade trading OS.** The risk framework, journal safety, bracket execution, broker reconciliation, and health monitoring all show real trading wisdom. I am genuinely impressed by the India risk adaptations and the live-mode safety gate.

**But I would not invest a dollar until:**
1. The runtime crash bugs are fixed and risk state is fully persisted.
2. Kelly sizing is replaced with empirically validated fixed fractional risk.
3. The trading universe expands beyond TQQQ/SPY, discovery is wired in, and a macro overlay protects against fighting the tide.
4. Execution defaults to limit orders with pre-event gap-risk management.
5. A walk-forward backtest across 20+ symbols proves edge before a single live order.

Without these changes, the most likely outcome is not catastrophic loss (thanks to the 10% drawdown halt), but slow, steady bleed from slippage, correlation, and missed opportunity cost. "Lower than mediocre and not making money" will persist because the architecture prevents anything else.

*Review completed for NomadCrew Trading OS v4.0.*
