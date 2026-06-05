# NOMADCREW TRADING OS — ACTIVE TRADER REVIEW

**Scope:** autonome/ execution, strategies, risk, backtest, supervisor loop.
**Focus:** Why this won't make money in live trading, what's salvageable, and the top 3 changes that matter.

---

## 1. WHY THIS WON'T MAKE MONEY

### A. Trading universe is effectively two correlated ETFs
`config/settings.yaml` defines only `TQQQ` and `SPY` as symbols. The dark-horse discovery engine (`discovery/dark_horse.py`) runs daily and produces a scored watchlist, but that watchlist is **never fed back into the supervisor's trading universe**. The symbol rotator exists but is not wired to ingest discovery output. Result: you are swing-trading two highly correlated beta proxies with zero idiosyncratic alpha. You will earn QQQ/SPY factor returns minus slippage and lose to buy-and-hold after costs.

### B. The strategies are textbook retail indicators with no statistical edge
All three active strategies are 1980s-era technical overlays:
- **EMA crossover (9/21)** with volume confirmation
- **Momentum breakout** (close > prev high + EMA filter + volume z-score)
- **Pullback to EMA** (mean-reversion within ATR tolerance)

There is **no walk-forward backtest** showing these edges persist. The backtest engine exists, but there is no evidence it has been run across a broad universe, no out-of-sample validation, and no regime-conditioned performance attribution. The "confidence" scores are hand-waved from slope differentials, not empirical win rates.

### C. Risk/reward structure is poor
- Momentum breakout: 2×ATR stop, 3×ATR target = 1:1.5 R:R net of costs. You need >40% win rate just to break even. The volume-z filter is lagging, not leading.
- EMA crossover: 2×ATR trailing stop, 6×ATR target. With 1-hour bars, most trades will be stopped out before reaching target because trailing stops on volatile ETFs get hit during normal oscillation.
- No **breakeven stop** after 1R profit, no **time-based exits** for non-working trades. Positions sit until binary stop/target hits.

### D. Execution defaults to market orders on breakouts
`config/settings.yaml`: `order_type: market`. The `limit_entry.py` module exists but the `ExecutionEngine` defaults to the config `order_type`, which is `market`. Market-order entry on a breakout signal is literally buying the top of the wick. For mean-reversion entries it's less toxic, but still cedes the spread.

### E. Kelly sizing uses fabricated inputs
In `risk/risk_manager.py`:
```python
win_rate = signal_confidence
payoff = reward / adjusted_risk
kelly = win_rate - ((1 - win_rate) / payoff)
```
`signal_confidence` is a synthetic number baked in strategy code (e.g., EMA slope ratio). It is **not** an empirical win rate. Kelly sizing with fictional inputs produces fictional position sizes. This is dangerous, not clever.

### F. No overnight/gap risk modeling
The backtest engine (`backtest/engine.py`) assumes exits occur within the bar OHLC:
```python
if bar.low <= sl:
    exit_price = min(bar.open, sl)
```
This is fantasy for 1-hour bars and overnight holds. A catalyst (earnings, Fed, geopolitical) can gap TQQQ 5–8% through your stop. The live system has **no pre-event flattening logic**, no options-market hedge, and no VIX term-structure monitoring beyond a single VIX level.

### G. LLM Gate adds latency theater, not edge
Every signal triggers an OpenRouter API call with a 30-second timeout. In fast markets, this makes the system unusable. The LLM has no real-time order book, no options flow, no dark-pool prints — it reviews OHLC bars and a static playbook. When the API fails, the signal is auto-rejected. You are paying API latency and token costs to replicate what a simple rules filter could do.

### H. TimesFM forecast filter is determinism dressed as AI
The statistical forecaster (`intelligence/timesfm_adapter_production.py`) uses:
```python
vol_component = math.sin(i) * vol * 0.5
```
That is literally injecting sine-wave noise into price projections. The `should_trade` method blocks signals when the forecast "contradicts" direction, but the forecast is derived from the same EMA/ATR/momentum the strategy already uses. It adds zero orthogonal information and will frequently block good entries while allowing bad ones.

### I. India module is pure advisory with no execution edge
The India system (`india/signals.py`, `india/strategy.py`) generates manual signals with 10% hard stops and 18% targets. There is no broker API integration, no position tracking, and the "value score" is a naive weighted heuristic. Manual execution introduces slippage, delay, and emotional override. The macro sentinel (`india/sentinel.py`) monitors USD/INR and oil but doesn't dynamically adjust position sizing based on regime.

### J. Broken code paths in risk/heat tracking
`PortfolioHeat.unregister()` references `self.positions`, which does not exist in the class — it should be `self._position_heat`. This will throw `AttributeError` on failed-fill cleanup. The supervisor calls `st.risk.unregister_trade(tr.symbol)` on failed entries, so this path is hit regularly.

### K. No position management after entry
Once a bracket order is placed, there is:
- No trailing-stop recalculation as ATR expands/contracts
- No breakeven-stop activation after a profit threshold
- No partial-take-profit scaling (e.g., 50% at 2R, runner to 4R)
- No time-stop ("if not in profit after N bars, exit")

You are one-legged: good at entry signals, incompetent at trade management.

---

## 2. WHAT'S PROMISING

### A. Solid architectural skeleton
The supervisor loop, journal, risk manager, execution engine, and strategy router are cleanly separated. This modularity makes it easy to swap out bad components without rewriting the whole system.

### B. Risk framework has the right concepts
Drawdown halt, daily loss limit, volatility pause, VIX-based sizing reduction, PDT guard, portfolio heat, and sector concentration limits are all present. They are poorly calibrated, but the plumbing is there.

### C. Bracket order execution via Alpaca native OCO
The execution engine uses Alpaca's bracket order API, which atomically links entry, stop, and target. This avoids the orphan-order problem that plagues homegrown OCO systems.

### D. Discovery layer exists and is extensible
Dark horse discovery, sector rotation, and news sentinel are functional. They just need to be **connected** to the trading loop and used to drive symbol selection.

### E. Earnings avoidance is built in
`MomentumBreakout` checks an earnings calendar and skips symbols within the buffer window. Most retail systems ignore this entirely.

### F. Trade journal enables post-hoc analysis
SQLite logging of signals, orders, PnL, and equity snapshots means you can actually compute Sharpe, drawdown, and per-strategy expectancy if you collect enough data.

---

## 3. TOP 3 TRADER-CRITICAL CHANGES

### CHANGE 1: Expand the trading universe and wire discovery into the supervisor
**Priority: CRITICAL**

Trading only TQQQ/SPY guarantees you are a expensive beta replicator. Connect `discovery/dark_horse.py` output to `supervisor/symbol_rotator.py` so the daily watchlist dynamically populates the supervisor's symbol list. Implement a **macro regime overlay**: when VIX < 20 and SPY > 200DMA, trade individual names from discovery. When VIX > 25 or SPY < 200DMA, shift to cash/short-duration bonds or reduce to SPY-only. Add 20–30 liquid mid-to-large caps with options markets (for hedging). Currently the rotator exists but does nothing.

**Action items:**
- Modify `supervisor/main.py` to read `swarm/intel/daily_watchlist.json` at market open and rotate symbols.
- Add a `MacroOverlay` class that uses VIX, yield curve, and SPY 200DMA to set aggression level.
- Filter discovery picks by average daily volume > $50M to ensure liquidity for exits.

### CHANGE 2: Replace Kelly sizing with empirical fixed-fractional risk, and add trade management
**Priority: CRITICAL**

Kelly sizing with fabricated `win_rate` is mathematically nonsense and will blow up. Replace with **fixed fractional risk** (e.g., 1% of equity per trade) sized off the actual stop distance. More importantly, add **active trade management**:
- Breakeven stop after +1R profit.
- Time-stop: if not in profit after N bars (N = 5 for hourly), exit at next open.
- Partial scale-out: close 50% at +2R, move stop to breakeven on remainder, let remainder run to 4R+ with trailing ATR.
- Trailing stop should be **chandelier** (highest high/lowest low minus N×ATR), not static EMA distance.

**Action items:**
- In `risk/risk_manager.py`, replace Kelly block with:
  ```python
  dollar_risk = equity * 0.01  # 1% fixed
  shares = dollar_risk / (entry_price - stop_loss)
  ```
- In `execution/engine.py`, add a `manage_open_positions()` method called every bar that checks breakeven, time-stop, and trailing-stop conditions.
- Add `trailing_stop_type: chandelier` to strategy params.

### CHANGE 3: Fix execution quality and add overnight/gap risk management
**Priority: CRITICAL**

Market orders on breakout signals are execution suicide. Wire `limit_entry.py` into the main execution path by default:
- For breakout longs: limit at `min(signal.entry_price, last_bar.close + 0.1%)` with 30s fallback.
- For pullback longs: limit at signal price or VWAP if below.
- Add **pre-event risk reduction**: query `data/earnings.py` for next-day events in the universe; flatten or reduce exposure by 50% before close if event is high-impact (FOMC, NFP, major earnings).
- In the backtest engine, model gap risk: sample overnight gaps from historical distribution and apply them between bars. Do not assume stops execute inside the bar.

**Action items:**
- Change `config/settings.yaml` defaults: `order_type: limit_with_fallback`, `limit_fallback_timeout_sec: 30`.
- In `supervisor/main.py`, add an `overnight_risk_check()` that runs at 3:30 PM ET. If FOMC/NFP/earnings tomorrow, flatten non-core positions.
- In `backtest/engine.py`, replace the naive bar-range exit logic with a gap model: on each new day's first bar, apply a random gap sampled from historical overnight return distribution before checking stop/target.

---

## SUMMARY VERDICT

This is a well-architected system built around broken alpha generators. The strategies have no detectable edge, the sizing formula is mathematically invalid, the execution defaults to the worst possible order type, and the trading universe is two correlated ETFs. The good news: the scaffolding (risk framework, journal, execution plumbing, discovery) is solid. If you implement the Top 3 changes — **dynamic universe + macro overlay, fixed fractional sizing + trade management, and limit-order execution + gap risk modeling** — you transform this from a beta-minus-slippage machine into something that could actually capture idiosyncratic alpha. Without those changes, this system will continue to be "lower than mediocre and not making money," exactly as the user described.

*Review completed for NomadCrew Trading OS v4.0*
