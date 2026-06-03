# AUTONOME TRADING OS v2 — ADVERSARIAL REVIEW V28
**Date:** 2026-06-03  
**Scope:** Full stack — data, strategy, risk, execution, journal, supervisor, backtest, swarm, dashboard  
**Reviewer:** Hermes Agent (subagent)  
**Mandate:** Brutally honest. Find bugs, design flaws, silent failures, and anything that could lose money.

---

## Executive Summary

The v2 Trading OS has a solid architectural skeleton — circuit breakers, rate limiting, paper/live gates, LLM gating, and journaling are all present. **However, there are multiple critical flaws that could cause real financial loss if promoted to LIVE trading.** The most dangerous issues are in execution safety (global order cancellation, untracked bracket children), risk accounting (non-persisted daily loss), and the LLM gate's fail-open design. Additionally, the strategy layer has significant statistical and implementation bugs that would silently degrade performance.

**Verdict: NOT READY for LIVE without fixes to all CRITICAL and HIGH items.**

---

## Findings Summary Table

| # | Severity | Component | Title | Financial Risk |
|---|----------|-----------|-------|----------------|
| 1 | **CRITICAL** | Execution | `cancel_all_orders()` on entry no-fill cancels ALL orders globally | **YES** — Unprotected positions lose stops/targets |
| 2 | **CRITICAL** | Execution | Bracket child orders (stop/target) never tracked or validated | **YES** — Naked positions after entry fill |
| 3 | **CRITICAL** | Risk | `daily_loss_accum` not persisted; resets on restart | **YES** — Can exceed daily loss limit |
| 4 | **CRITICAL** | Intelligence | LLM Gate fail-open on API errors auto-approves trades | **YES** — Bad trades enter on LLM outage |
| 5 | **CRITICAL** | Strategy | Router cooldown is GLOBAL (`_last_signal_bar`), not per-symbol | **YES** — Misses signals; forces single-symbol bias |
| 6 | **CRITICAL** | Strategy | Single symbol `TQQQ` with no diversification | **YES** — Concentration risk, no hedging |
| 7 | **HIGH** | Data | AlpacaDataFeed uses broken `datetime.now().astimezone().astimezone()` | Partial — Wrong bar windows fetched |
| 8 | **HIGH** | Data | VIX fetcher returns stale cache without freshness flag | Partial — Decisions on stale data |
| 9 | **HIGH** | Risk | No hard per-trade notional cap (% of equity) | **YES** — Kelly can approve oversized positions |
| 10 | **HIGH** | Risk | Portfolio heat registers BEFORE fill; never unregisters on failure | Partial — Heat permanently inflated |
| 11 | **HIGH** | Execution | No duplicate signal protection; can double-enter same bar | **YES** — Double position size |
| 12 | **HIGH** | Strategy | Earnings `is_earnings_week` blocks AFTER earnings too (uses `abs()`) | Partial — Misses post-earnings continuation moves |
| 13 | **HIGH** | Backtest | Sharpe ratio computed from equity snapshots, not daily returns | None — Misleading performance analysis |
| 14 | **HIGH** | Backtest | `entry_at="next_open"` skips entry on penultimate bar | None — Simulated results biased low |
| 15 | **HIGH** | Journal | Rotation DELETES before confirming archive write succeeded | **YES** — Data loss on disk-full errors |
| 16 | **HIGH** | Alerts | Telegram HTML parse_mode without content escaping | Partial — Alerts fail silently on special chars |
| 17 | **MEDIUM** | Supervisor | `limit_with_fallback` configured but engine ignores it | Partial — Always market orders |
| 18 | **MEDIUM** | Execution | `sync_orders()` calls private `_get()` directly | None — Brittle coupling |
| 19 | **MEDIUM** | Health | Hardcoded log path `/tmp/autonome_paper.log` may not exist | None — Health monitoring blind |
| 20 | **MEDIUM** | Swarm | All scripts use hardcoded absolute paths | None — Non-portable, fragile |
| 21 | **MEDIUM** | Risk | `record_win()` is empty; win tracking missing | None — Incomplete analytics |
| 22 | **MEDIUM** | Reconcile | Finds discrepancies but never auto-corrects journal | Partial — Ghost positions persist |
| 23 | **MEDIUM** | Learner | Compares live (1H) vs 15m backtest — timeframe mismatch | None — Invalid benchmarking |
| 24 | **LOW** | Data | Yahoo feed `fetch_daily` uses `__import__("datetime")` inline | None — Code smell |
| 25 | **LOW** | Dashboard | Hardcoded DB/log paths; single-threaded HTTP server | None — Ops inconvenience |

---

## Detailed Findings

---

### 1. CRITICAL — Execution: `cancel_all_orders()` is a nuclear weapon [execution/engine.py:161]

**The Bug:**
```python
if not filled_price:
    try:
        self.client.cancel_all_orders()  # <-- KILLS EVERYTHING
    except Exception:
        pass
    del self.active_orders[entry.id]
```

When a single entry order fails to fill, the engine calls `cancel_all_orders()` on the entire account. This wipes out:
- Stop-loss orders for OTHER open positions
- Take-profit orders for OTHER open positions  
- Pending entry orders for OTHER symbols

**Result:** Positions become naked. A $50k account could lose stops on 5 positions simultaneously because one illiquid entry didn't fill.

**Fix:** Cancel only the specific `entry.id`:
```python
self.client.cancel_order(entry.id)
```
Then explicitly query and cancel any known child orders linked to that entry. Do NOT use `cancel_all_orders()` anywhere except emergency flatten.

---

### 2. CRITICAL — Execution: Bracket children are never tracked [execution/engine.py:175-177]

**The Bug:**
```python
return TradeRecord(
    ...
    stop_order_id=None,   # Never populated
    target_order_id=None, # Never populated
    ...
)
```

Alpaca's bracket order API creates child stop-loss and take-profit orders automatically. The engine captures the parent `entry.id` but **never queries for or stores the child order IDs**. If the parent fills but one child fails to create (rare but real), there is ZERO detection.

**Result:** Position exists with no protective stop. A flash crash or gap-down could cause unbounded losses.

**Fix:** After entry fill, query open orders filtered by `client_order_id` prefix or nested orders endpoint. Store child IDs in `active_orders` and validate both exist within a timeout.

---

### 3. CRITICAL — Risk: `daily_loss_accum` resets on process restart [risk/risk_manager.py:47, _save_halt_state]

**The Bug:**
```python
def _save_halt_state(self):
    json.dump({"halted": self.halted, "peak_equity": self.peak_equity}, f)
    # daily_loss_accum is NOT saved
```

Only `halted` and `peak_equity` persist to disk. The daily loss accumulator is in-memory only. If the supervisor restarts (crash, deployment, system reboot), `daily_loss_accum` resets to `0.0`.

**Result:** If the system loses $2,000 of a $3,000 daily limit, then restarts, it can lose another $3,000 the same day — exceeding the configured risk by 67%.

**Fix:** Persist `daily_loss_accum` and the date it was last reset in `halted.json` (or a separate state file). Validate date on load.

---

### 4. CRITICAL — Intelligence: LLM Gate fails OPEN on errors [intelligence/llm_gate.py:252-260]

**The Bug:**
```python
except requests.exceptions.RequestException as e:
    return {"decision": "APPROVE", "confidence": 0.5, ...}
except Exception as e:
    return {"decision": "APPROVE", "confidence": 0.5, ...}
```

On API timeout, network failure, rate limit, or any unexpected error, the gate **approves the trade**. Only `JSONDecodeError` rejects. This is the opposite of safe design.

**Result:** During an LLM provider outage (OpenRouter down, rate-limited, key expired), every signal gets auto-approved. Bad signals enter during volatile times exactly when you want the gate most.

**Fix:** Default to `REJECT` on ALL errors. Only `APPROVE` on explicit success:
```python
except Exception:
    log.error(...)
    return {"decision": "REJECT", "confidence": 0.0, "reasoning": "llm_unavailable"}
```

---

### 5. CRITICAL — Strategy: Router cooldown is global, not per-symbol [strategy/router.py:42-44, 114]

**The Bug:**
```python
self._last_signal_bar: Optional[int] = None  # ONE variable for ALL symbols
...
if self._last_signal_bar is not None and global_bar_idx < self._last_signal_bar + self.min_gap:
    return None
```

The router prevents signals from firing within `min_gap` bars of the LAST signal — across ALL symbols. If TQQQ fires, SPY cannot fire for the next 3 bars.

**Result:** With only TQQQ configured this is moot, but as soon as multi-symbol is enabled, this suppresses legitimate diversification signals. It's a single global cooldown masquerading as per-strategy risk control.

**Fix:** Change to `dict[str, int]` keyed by symbol:
```python
self._last_signal_bar: dict[str, int] = {}
```

---

### 6. CRITICAL — Strategy/Config: Single symbol TQQQ, no diversification [config/settings.yaml:21]

**The Bug:**
```yaml
symbols:
  - TQQQ
```

The entire account is deployed into a single 3× leveraged Nasdaq ETF. No hedge, no uncorrelated assets, no sector rotation.

**Result:** 
- Beta ~3 to Nasdaq. A 10% Nasdaq drop = 30% account drawdown.
- No recovery mechanism if TQQQ specific events occur (splits, liquidity shocks, delisting risk).
- `max_drawdown_pct: 10.0` is almost guaranteed to trigger because TQQQ moves 3-5% daily.

**Fix:** Add minimum 3 uncorrelated symbols (e.g., SPY, GLD, TLT) or set `max_drawdown_pct` to 30%+ for leveraged ETFs. Better: forbid single-symbol configs.

---

### 7. HIGH — Data: Broken datetime math in AlpacaDataFeed [data/bars.py:170]

**The Bug:**
```python
now = datetime.now().astimezone().astimezone().replace(tzinfo=None)
end = now + timedelta(hours=4)
```

Calling `.astimezone()` twice is nonsensical. Replacing `tzinfo=None` makes the datetime naive, then adding 4 hours shifts the window in local server time unpredictably. If the server is UTC, it requests bars 4 hours in the future.

**Result:** Historical bar requests may return empty or wrong bars, especially near market open/close. Strategy operates on stale or missing data.

**Fix:**
```python
now = datetime.now(timezone.utc)
end = now
start = now - timedelta(days=limit // 6 + 2)
```

---

### 8. HIGH — Data: VIX fetcher returns stale data silently [data/vix_feed.py:34-35, 51]

**The Bug:**
```python
if _last_fetch_attempt is not None and (now - _last_fetch_attempt).total_seconds() < 300:
    return _vix_cache   # Could be hours old
```

On failure, the fetcher returns stale cache with NO indication of staleness. The risk manager then makes VIX-based sizing decisions on potentially hours-old data.

**Result:** If VIX spikes to 45 (halt threshold) but fetcher is returning yesterday's 18 due to a Yahoo block, the system trades at full size into a storm.

**Fix:** Return `(value, timestamp, is_fresh)` tuple. Or reject signals if VIX is stale >15 min.

---

### 9. HIGH — Risk: No hard per-trade notional cap [risk/risk_manager.py:207-225]

**The Bug:** The Kelly sizing formula can generate very large positions when `adjusted_risk` is small (tight stop near entry). The only guards are:
- `notional > buying_power * 0.95` → clips to 95% BP
- `shares * entry_price < 1.0` → min $1

There is NO maximum per-trade notional as % of equity. If a signal has a $0.05 stop on a $500 stock, Kelly could approve 100% of equity in one trade.

**Result:** Single trade can consume entire account. No diversification, no tail-risk protection.

**Fix:** Add `max_position_notional_pct` to settings (default 20%) and enforce before buying power check:
```python
max_notional = equity * MAX_POSITION_PCT
if proposed_notional > max_notional:
    shares = max_notional / entry_price
```

---

### 10. HIGH — Risk: Portfolio heat registers pre-fill, never unregisters [risk/risk_manager.py:248]

**The Bug:**
```python
self.heat.register_position(symbol, entry_price, stop_loss, shares, sector, signal_confiction)
```

This is called in `evaluate()`, **before** the trade is submitted to the broker. If the order is rejected, partially filled, or fails to fill, the heat remains registered permanently.

**Result:** Heat tracker becomes permanently inflated. After a few rejected signals, `can_add_position()` will reject ALL new signals due to phantom heat. The system starves itself.

**Fix:** Register heat ONLY after `TradeRecord.status == "OPEN"` (confirmed fill) in the supervisor loop. Add an `unregister_position()` call on trade failure.

---

### 11. HIGH — Execution: No duplicate signal protection [supervisor/main.py:286-288]

**The Bug:** The supervisor loop iterates symbols and scans each independently. If a symbol's bar triggers a signal and the order submission is slow (rate limited, API lag), the next bar ingestion could trigger the SAME signal again before the first order state updates.

**Result:** Double entry into the same position. Risk manager only checks `already_in_symbol` against broker positions, which won't exist yet for the pending first order.

**Fix:** Maintain a `pending_symbols` set in the supervisor. Add symbol on signal detection, remove on order confirmation or rejection.

---

### 12. HIGH — Strategy: Earnings block is symmetric [data/earnings.py:65-74]

**The Bug:**
```python
delta = abs((earnings_date - today).days)
return delta <= buffer_days
```

This blocks trades both BEFORE and AFTER earnings. The real gap risk is the announcement itself (before market open or after close). Post-earnings, the new information is priced in and continuation moves are valid signals.

**Result:** System misses valid post-earnings momentum continuation for `buffer_days` after every report.

**Fix:** Use `delta = (earnings_date - today).days` and only block when `0 <= delta <= buffer_days` (pre-earnings).

---

### 13. HIGH — Backtest: Sharpe ratio is wrong [backtest/metrics.py:59-73]

**The Bug:**
```python
daily_returns = []
for i in range(1, len(equity_values)):
    daily_returns.append((equity_values[i] - equity_values[i-1]) / equity_values[i-1])
```

The `equity_curve` in the backtest engine is appended once per bar where a trade exits. These are NOT daily returns — they are trade-interval returns (could be minutes, hours, or days apart). Computing annualized Sharpe from irregularly spaced equity points is statistically invalid.

**Result:** Sharpe ratios are meaningless. Parameter sweeps optimizing for Sharpe pick random noise.

**Fix:** Resample equity curve to daily returns before computing Sharpe, or report "trade-based Sharpe" with a disclaimer.

---

### 14. HIGH — Backtest: Penultimate bar signal never enters [backtest/engine.py:219-221]

**The Bug:**
```python
if self.entry_at == "next_open":
    if i + 1 >= len(bars):
        continue
```

If a signal fires on the second-to-last bar, the engine skips it because there's no `i+1` bar for entry. This biases backtests downward by dropping end-of-period winners.

**Fix:** Allow entry at current bar close for the final signal, or extend the data fetch by +2 bars.

---

### 15. HIGH — Journal: Rotation deletes before confirming archive [journal/trade_journal.py:154-175]

**The Bug:**
```python
for table in ("signals", "orders", "pnl", "equity"):
    src.execute(f"DELETE FROM {table} WHERE t < ?", (cutoff,))
src.execute("VACUUM")
```

The `DELETE` runs inside the same transaction as the archive copy. While SQLite atomicity protects against process crashes, if the disk fills during `VACUUM` or the archive DB is on a different filesystem that fails, data vanishes.

**Result:** Complete loss of historical trade data on disk-full errors.

**Fix:** Two-phase rotation: (1) Copy to archive, (2) Verify row counts match, (3) Then delete.

---

### 16. HIGH — Alerts: Telegram HTML injection risk [alerts/telegram.py:76-89]

**The Bug:**
```python
payload = {"chat_id": self._chat_id, "text": message, "parse_mode": "HTML", ...}
```

`message` is constructed from trade data including symbol names, error strings, and reasoning. If any field contains `<`, `>`, or `&`, Telegram rejects the message or misrenders it.

**Result:** Critical halt alerts fail to deliver because an error message contained `3 < 5`.

**Fix:** Escape HTML entities: `message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")`

---

### 17. MEDIUM — Supervisor: `limit_with_fallback` configured but ignored [config/settings.yaml:55, execution/engine.py:47-48]

**The Bug:** Settings specifies `entry_order_type: limit_with_fallback` but `ExecutionEngine` unconditionally uses `self.order_type` from config, which defaults to `market`.

**Result:** All entries are market orders despite explicit config requesting limit orders. Spread costs bleed PnL on every entry.

**Fix:** Respect `entry_order_type` in `ExecutionEngine.enter_position()`. Call `submit_limit_with_fallback()` when configured.

---

### 18. MEDIUM — Execution: sync_orders calls private method [execution/engine.py:213]

**The Bug:**
```python
open_orders = self.client._get("/v2/orders?status=open&limit=500")
```

Direct access to `_get` breaks encapsulation. If AlpacaClient refactors its URL structure or auth, order sync breaks.

**Fix:** Add a public `list_orders_raw()` method to AlpacaClient, or use the existing `list_orders()`.

---

### 19. MEDIUM — Health Monitor: Hardcoded log path [swarm/scripts/health_monitor.py:13]

**The Bug:** The health monitor reads `/tmp/autonome_paper.log`, but the supervisor logs to `stdout` by default (via `logging.basicConfig`). Unless a systemd redirect or manual redirect is active, this file does not exist.

**Fix:** Make log path configurable. Or check both `journalctl` output and `/tmp/autonome_paper.log`.

---

### 20. MEDIUM — Swarm Scripts: Hardcoded absolute paths everywhere

**Affected:** `health_monitor.py`, `nightly_reset.py`, `orchestrator_pulse.py`, `after_hours_learner.py`

All hardcode `/mnt/e/NomadCrew[GROWTH]/trading-os/v2/...`. Moving the project or running on a different machine breaks every script.

**Fix:** Derive paths from `__file__` or an environment variable `AUTONOME_ROOT`.

---

### 21. MEDIUM — Risk: record_win() is a no-op [risk/risk_manager.py:256-257]

**The Bug:**
```python
def record_win(self, win: float):
    pass
```

Wins are not tracked. Win streaks, expectancy updates, and adaptive sizing cannot function.

**Fix:** Implement win tracking, or add a comment explaining this is intentional with a TODO.

---

### 22. MEDIUM — Reconcile: Finds but never fixes discrepancies [execution/reconcile.py]

**The Bug:** `reconcile_positions()` returns a list of discrepancies (ghost positions, untracked positions, qty mismatches) but never writes corrections back to the journal.

**Result:** Ghost `OPEN` orders persist forever in the journal. Each reconciliation re-flags them. Dashboards and PnL queries are polluted.

**Fix:** Add auto-correction modes:
- Ghost position → update journal status to `CLOSED`
- Untracked position → add `MANUAL` entry to journal
- Qty mismatch → log warning + alert

---

### 23. MEDIUM — Learner: Timeframe mismatch [swarm/scripts/after_hours_learner.py:186-198]

**The Bug:** The strategy operates on 1H bars, but the learner fetches 15m bars and runs a naive backtest on them. Comparing live 1H trade outcomes against 15m simulation is apples-to-oranges.

**Fix:** Fetch bars matching the strategy timeframe (`settings.yaml:data:timeframe`).

---

### 24. LOW — Data: `__import__("datetime")` hack [data/yahoo_feed.py:119]

```python
start = end - __import__("datetime").timedelta(days=days + 30)
```

Just `from datetime import timedelta` at the top.

---

### 25. LOW — Dashboard: Hardcoded paths and thread-safety [dashboard/server.py]

- DB path is hardcoded absolute.
- Uses single-threaded `HTTPServer` — one slow client blocks all others.
- No authentication. Anyone on the network can view equity and positions.

---

## Additional Design Concerns (Non-bug)

### Concern A: No P&L attribution by strategy
The router selects between momentum, pullback, and crossover, but journal entries don't store which strategy generated the signal. You cannot tell if pullback is destroying your account while crossover is profitable.

### Concern B: `confidence` is just reward/risk ratio normalized
In `momentum_breakout.py`:
```python
confidence = min(0.95, reward / (risk + 1e-9) / 3.0)
```
This is not confidence. It's just R/R ÷ 3. A signal with R/R=3 gets 95% "confidence" regardless of base rate, sample size, or out-of-sample performance. The LLM gate then makes decisions based on this pseudo-confidence.

### Concern C: No walk-forward validation
Strategy parameters are hardcoded in `settings.yaml`. There is no periodic re-optimization or walk-forward validation. A parameter set that worked in 2023 may be toxic in 2026.

### Concern D: `market_hours_only: true` with 1H bars
If the system sleeps 60s when market is closed, it wakes every minute to check. But with 1H bars, most wake-ups are wasted. The bar-fetch loop also fetches 5 bars every cycle (not just the newest), wasting API quota.

---

## Recommended Priority Roadmap

| Phase | Items | Effort |
|-------|-------|--------|
| **STOP-LOSS** (Deploy before any real capital) | #1 (cancel_all_orders), #2 (track bracket children), #3 (persist daily_loss), #4 (LLM fail-closed) | 1-2 days |
| **RISK HARDENING** | #9 (position cap), #10 (heat post-fill), #11 (duplicate guard), #6 (multi-symbol) | 2-3 days |
| **DATA INTEGRITY** | #7 (datetime fix), #8 (VIX staleness), #12 (earnings fix), #13 (Sharpe fix) | 1-2 days |
| **OPERATIONS** | #15 (journal rotation), #16 (Telegram escape), #19/20 (paths), #17 (limit orders) | 1-2 days |

---

## Conclusion

This is a promising system with good intentions and many correct architectural choices (rate limiting, paper gates, circuit breakers, journaling). **But it currently has critical execution safety holes that make it dangerous for live deployment.** The combination of global order cancellation, untracked bracket legs, non-persisted daily loss, and an LLM gate that approves on failure creates a compounding risk profile.

**Do not go LIVE until all CRITICAL and HIGH items are resolved and re-reviewed.**

---
*Review generated by Adversarial Review Agent — Hermes Subagent*  
*File: ADVERSARIAL_REVIEW_V28.md*
