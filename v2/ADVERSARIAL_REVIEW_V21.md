# ADVERSARIAL REVIEW: Trading OS v2.1 — CRITICAL GAPS REPORT
## Generated: 2026-06-03 by adversarial subagent

---

## GAPS TABLE
| # | Missing Feature | Impact | Where it should live |
|---|---|---|---|
| 1 | True OCO bracket order support / dangling order cleanup | Running naked legs = double exposure, entries in wrong direction | execution/engine.py, supervisor/main.py |
| 2 | Partial fill handling on entry | Bracket legs use full `sig.qty` even if entry only partially filled, creating over-hedge | execution/engine.py |
| 3 | Order lifecycle monitoring & state machine | Orders are fire-and-forget. Hung/rejected/filled orders never tracked or reconciled | execution/engine.py, supervisor/main.py |
| 4 | Slippage model / execution quality tracking | Market orders on breakout = guaranteed slippage; no measurement or protection | execution/engine.py, risk_manager.py |
| 5 | Hard stop on persistent broker API failure | If `fetch_account()` fails, supervisor logs and keeps trading blindly | supervisor/main.py |
| 6 | Correlation / sector concentration risk guard | Can hold 5 correlated tech longs simultaneously; no sector limits | risk_manager.py |
| 7 | Volatility halt (VIX or realized vol) | `vol_pause_annual` is loaded from config but NEVER checked in code | risk_manager.py |
| 8 | Pattern Day Trader (PDT) protection | `account.daytrade_count` is fetched but never used to block trades | risk_manager.py |
| 9 | Fractional share support | Rounds to `int(shares)` unconditionally; on AMZN/GOOGL, 1% risk may not buy a single share | risk_manager.py |
| 10 | Short selling checks | SHORT signals generated but no borrow availability, HTB fees, or margin requirement checks | execution/engine.py, risk_manager.py |
| 11 | Data staleness detection | No check if bars are delayed/stale; strategy fires on stale data | data/bars.py, supervisor/main.py |
| 12 | Order throttling / rate limiting | Supervisor loops over symbols and can spam orders; no rate limiting | supervisor/main.py |
| 13 | Drawdown halt state persistence | `risk.halted` is in-memory only; systemd restart clears it | risk_manager.py |
| 14 | Real-time alerts on critical failures | `telegram.enabled: false`; errors are only log lines | alerts module |
| 15 | Portfolio-level capital allocation | Each signal is "all-in" individually; no portfolio weighting / rebalancing | risk_manager.py |
| 16 | Trade/broker reconciliation | Local journal never reconciled against actual broker positions/orders | trade_journal.py |
| 17 | Earnings avoidance code enforcement | Playbook says avoid earnings week but code has zero enforcement | strategy/momentum_breakout.py |
| 18 | Limit order entry option | Always market entry = slippage in breakout scenarios | settings.yaml, execution/engine.py |
| 19 | Dividend/split adjustment on bars | Raw prices used; splits break all historical calcs | data/bars.py |
| 20 | Config mode migration guard | `mode: PAPER` can be changed to LIVE in YAML with no runtime confirmation | alpaca_client.py |
| 21 | SQLite journal rotation | Log grows unbounded; no rotation or disk space check | trade_journal.py |
| 22 | Secure secret storage | passwords/keys in plain YAML on disk | secrets.yaml |
| 23 | Comprehensive failure tests | Tests only cover happy path; 0 failure scenario tests | tests/ |
| 24 | Order timestamp validation | No clock-skew detection between system and broker | supervisor/main.py |
| 25 | Position hold-time limits | Can hold indefinitely; no max duration cap | risk_manager.py |
| 26 | Broker reconnection / exponential backoff | `requests.Session` has no retry-on-failure logic | alpaca_client.py |
| 27 | Commission / cost model | Alpaca has $0 comm but borrow fees, margin interest ignored | trade_journal.py |
| 28 | Pre-market / post-market filter | Config says `market_hours_only: true`, but strategy signals have no time-of-day aware validation | momentum_breakout.py |
| 29 | Position sizing cap per-symbol | Doesn't cap exposure at individual symbol level beyond `max_concurrent_positions` | risk_manager.py |
| 30 | Proper exception handling in flat logic | `flatten_symbol()` blindly submits market order; doesn't check for errors | execution/engine.py |

---

## DETAILED FINDINGS

### 1. BROKEN BRACKET ORDERS — THE #1 MONEY LOSER [CRITICAL]
File: autonome/execution/engine.py (lines 87-101)
Problem: System submits entry, stop, and target as THREE INDEPENDENT orders. They are NOT linked as OCO.
When stop fills, target remains open. When target fills, stop remains open. Supervisor has ZERO code to cancel dangling legs.
Impact:
  - Stop fills, position closes. Target order still active. Price reverses to target level → system ENTRY in opposite direction = accidental counter-trend trade.
  - Target fills, stop still active. Price drops to stop → exits newly gained position = locked-in unnecessary loss.
  - In volatile markets this loop can repeat, churning capital.
Fix: Use Alpaca's native `stop_loss` and `take_profit` in a single bracket order submission (Alpaca v2 API supports this). If submitting separately, build an order state machine that monitors fills and cancels paired legs.

### 2. PARTIAL FILLS LEAD TO WRONG BRACKET SIZE [CRITICAL]
File: autonome/execution/engine.py (lines 88-97)
Problem: Stop and target orders always use `qty=rd.qty` (the intended full quantity). The entry order may only partially fill. The bracket legs then over-hedge the actual position.
Impact: 
  - Entry fills 50 shares. Stop/target placed for full 100 shares. When stop hits, system sells 100 shares of a 50-share position = creates accidental 50-share short.
Fix: After confirming entry fill, check `filled_qty` on the entry order result. Use THAT quantity for stop and target legs. Implement partial-fill accumulation logic.

### 3. ORDERS ARE FIRE-AND-FORGET [CRITICAL]
File: autonome/execution/engine.py, supervisor/main.py
Problem: `enter_position()` fires orders and returns a `TradeRecord`. The supervisor loop immediately moves on. There is no background thread or coroutine monitoring:
  - Was the entry order eventually filled?
  - Did the stop or target get rejected?
  - Is an order hanging in "new" state for hours?
Impact: Silent failures. An order in "new" state may expire unfilled while the system thinks it's "OPEN". A rejected target leaves a position with no stop loss = naked risk.
Fix: Build an `OrderLifecycleManager` that polls open orders every N seconds, transitions statuses, emits alerts on anomalies, and triggers reconciliation.

### 4. NO SLIPPAGE MODEL / EXECUTION QUALITY [CRITICAL]
File: autonome/risk/risk_manager.py, execution/engine.py
Problem: All entries use market orders (`order_type: market`). On breakout signals in volatile tech names (TSLA, NVDA), slippage can be 0.2-0.5%. The position sizing calculates risk based on `entry_price` from the signal (last close), but actual fill price is ignored for risk sizing.
Impact: A trade sized for 1% risk at $500 entry fills at $502.50. The stop at $490 now represents 2.5% risk. If slippage is worse, the "1% risk" trade can become 3-4% actual loss.
Fix: 
  a. Add slippage estimation to `risk.evaluate()` based on ATR and symbol volatility.
  b. Submit entry as a limit order at signal price + small buffer, with fallback to market after timeout.
  c. Log expected vs actual fill price and alert on excessive slippage.

### 5. HARD STOP ON API FAILURE MISSING [CRITICAL]
File: autonome/supervisor/main.py (lines 106-113, 217-226)
Problem: If `fetch_account()` fails in the initial warm or periodic equity snapshot, the code logs the exception and continues. There is no emergency halt.
Impact: Trading with stale/degraded account data (wrong equity, wrong buying power, missed halt triggers = position sizes calculated incorrectly or drawn-down account traded into oblivion).
Fix: Implement an `API_HEALTH_MONITOR`. If >= N consecutive critical API calls fail (account fetch, position list, order submit), set `risk.halted = True`, send alert, and require manual intervention to resume.

### 6. CORRELATION / CONCENTRATION RISK ABSENT [CRITICAL]
File: autonome/risk/risk_manager.py
Problem: `max_positions: 5` only counts positions. It doesn't check if all 5 are tech stocks moving in lockstep. The `already_in_symbol` check prevents duplicate symbols but nothing prevents AAPL + MSFT + META + GOOGL + NVDA (all ~0.85 correlated).
Impact: In a sector rotation or macro shock (Fed announcement), all positions move against you simultaneously. The "1% risk per trade x 5 positions" becomes 5% risk in a single macro bet.
Fix: Add sector/tag exposure tracking. Enforce max exposure per sector (e.g., 30%). Maintain a simple correlation matrix from recent returns and reject signals that exceed correlation threshold with existing positions.

### 7. VOLATILITY HALT NEVER CHECKED [HIGH]
File: autonome/risk/risk_manager.py (line 35, 52-56)
Problem: `self.vol_pause_annual` is loaded from config at `__init__`. There is no method that actually computes current volatility and compares it. `evaluate()` never calls a volatility check.
Impact: System trades full speed during GME-style volatility spikes, flash crashes, or VIX > 40 events where momentum breakout signals are frequently false.
Fix: In `evaluate()`, compute realized volatility from last 20 bars. If `realized_vol_annual >= vol_pause_annual_pct`, reject all new signals with reason `volatility_halt`.

### 8. PATTERN DAY TRADER PROTECTION MISSING [HIGH]
File: autonome/broker/alpaca_client.py (line 111: `daytrade_count=int(raw.get("daytrade_count", 0))`)
File: autonome/risk/risk_manager.py
Problem: The `daytrade_count` is fetched into `Account` but `risk_manager.evaluate()` never reads it. Alpaca's free paper account has no PDT rule but live accounts under $25K do.
Impact: On a live sub-$25K account, system can execute 4+ day trades in 5 days and trigger a 90-day trading restriction.
Fix: Add `max_daytrades_remaining` check in `risk.evaluate()`. Halt if `daytrade_count >= 2` (conservative buffer).

### 9. FRACTIONAL SHARES NOT SUPPORTED [HIGH]
File: autonome/risk/risk_manager.py (lines 120, 125, 132)
Problem: Position sizing uses `int(shares)` unconditionally. If the system wants to risk $1,000 on AMZN at $220 with a $5 stop: `$1,000 / 5 = 200 shares`, `$200 x $220 = $44,000` notional vs $100K account. But for smaller accounts, `dollar_risk / risk` may compute to 0.8 shares which becomes `int` = 0, rejecting the trade. Conversely, if it computes 1.5 shares, it becomes 1 share (undersized).
Impact: Missing valid trades on high-priced names, or imprecise sizing on all names. Alpaca supports fractional shares.
Fix: Remove `int()` wrapping. Round to 3 decimal places for fractional precision, or use `round(qty, 6)`.

### 10. SHORT SELLING HAS ZERO BORROW/MARGIN CHECKS [HIGH]
File: autonome/execution/engine.py (lines 86-97), autonome/risk/risk_manager.py
Problem: Strategy generates SHORT signals. The system submits `side="sell"` orders. No check for:
  - Is the symbol shortable on Alpaca?
  - Is it Hard to Borrow (HTB) with massive borrow fees?
  - Does the account have margin enabled?
  - Is there enough buying power for short margin requirement?
Impact: Short order gets rejected, or worse, fills but with 100%+ annualized borrow fees that silently erode P&L.
Fix: Query Alpaca asset info for `shortable` and `easy_to_borrow`. Add `margin_requirement` check to risk evaluate. Consider rejecting HTB symbols.

### 11. NO DATA STALENESS DETECTION [HIGH]
File: autonome/data/bars.py (fetch_history), autonome/supervisor/main.py
Problem: `fetch_history()` pulls bars with a time window but never validates that the LAST bar returned is actually recent. If Alpaca API is stale or returns cached data, the strategy computes on old bars and fires stale signals.
Impact: Trading on yesterday's breakout = entering after the move is over = immediate losses.
Fix: In `fetch_history()`, reject results where `bars[-1].t < now - max_staleness`. In supervisor, skip the cycle if ANY symbol's last bar is stale.

### 12. ORDER THROTTLING / RATE LIMIT MISSING [MEDIUM]
File: autonome/supervisor/main.py (lines 143-212)
Problem: The main loop iterates all symbols and, on each bar, can call `execution.enter_position()` which internally makes 3 API calls (entry/stop/target). With 10 symbols and a fast signal, that's 30 orders in seconds. Alpaca has rate limits (200 requests/minute on free tier, but that's shared across all endpoints).
Impact: Rate limit hits, order rejections, cascading failures.
Fix: Add per-second order submission rate limiter. Queue orders if limit exceeded.

### 13. DRAWDOWN HALT STATE LOST ON RESTART [MEDIUM]
File: autonome/risk/risk_manager.py (line 41: `self.halted = False`)
Problem: `halted` is a Python instance variable. On systemd restart, it resets to `False`. If the system was halted due to 10% drawdown, a crash/restart immediately resumes trading.
Impact: System can trade through a drawdown event after any crash or intentional restart.
Fix: Persist halted state to SQLite or a file. Read it back on initialization.

### 14. NO REAL-TIME ALERTS SYSTEM [MEDIUM]
File: config/settings.yaml (alerts.telegram.enabled: false), autonome/supervisor/main.py
Problem: All critical events are log lines. A log line on a headless server in systemd is invisible until someone runs `journalctl`. No PagerDuty/Slack/Email/SMS for:
  - Drawdown halt triggered
  - Order rejected
  - API failure
  - Position entered/exited
Impact: You discover problems hours or days later. Real losses accumulate while you sleep.
Fix: Implement `alerts.py` with pluggable backends. Start with Telegram webhook as it's gated in config already.

### 15. PORTFOLIO ALLOCATION IS "ALL-IN PER SIGNAL" [MEDIUM]
File: autonome/risk/risk_manager.py
Problem: Each signal risks 1% of equity independently. With `max_positions: 5`, the system can deploy up to 5% total risk. But there's no logic for:
  - Equal weighting vs conviction-weighting
  - Kelly fraction per symbol
  - Total portfolio heat (sum of all position risks)
  - Rebalancing if one position swells
Impact: Concentration in winning trades + no trimming = portfolio becomes unbalanced.
Fix: Add `total_portfolio_heat` tracking. Reject new signals if total heat (sum of all active position risks) exceeds threshold (e.g., 5%).

### 16. NO BROKER RECONCILIATION [MEDIUM]
File: autonome/journal/trade_journal.py
Problem: The journal logs what it THINKS happened. It never compares against Alpaca's actual positions and orders. If an order is modified externally, or if a fill happens that the system missed (race condition), the journal is wrong.
Impact: P&L tracking is inaccurate. Supervisory decisions based on journal data are wrong.
Fix: Implement a nightly/hourly `reconcile()` method that fetches broker positions + orders and flags discrepancies.

### 17. EARNINGS AVOIDANCE IS PLAYBOOK-ONLY [MEDIUM]
File: config/playbook.md (line 33: Avoid earnings week), autonome/strategy/momentum_breakout.py
Problem: Playbook says avoid earnings week. Zero code enforces this. The strategy happily scans and signals on AAPL the day before earnings.
Impact: Breakout signals into earnings are high-risk binary events. Stop losses don't protect against overnight gap-downs.
Fix: Integrate earnings calendar API (e.g., Finnhub, Alpha Vantage). Add `earnings_date` field and reject signals within T-2 days of earnings.

### 18. LIMIT ORDER ENTRY MISSING [MEDIUM]
File: execution/engine.py (line 40: order_type=market), config/settings.yaml
Problem: Entry is always market order. In breakout scenarios with high momentum, market orders can fill far above/below signal price.
Fix: Add `entry_order_type: limit_with_market_fallback` config. Submit limit at signal price + small slippage buffer. After N seconds unfilled, cancel and resubmit as market.

### 19. DIVIDEND/SPLIT ADJUSTMENT MISSING [MEDIUM]
File: autonome/data/bars.py
Problem: Historical bars are raw (unadjusted). On a 10:1 split, all EMA/ATR calculations break because the past prices are 10x higher.
Impact: False signals after splits. Stop distances computed incorrectly.
Fix: Use Alpaca's `adjustment=split` or `adjustment=all` parameter in bars request. Store adjusted closes for calculations.

### 20. MODE MIGRATION TOO EASY [MEDIUM]
File: config/settings.yaml (line 5), alpaca_client.py
Problem: Switching from PAPER to LIVE is a single YAML edit (`mode: LIVE`). No runtime confirmation, no env-var override requiring explicit action, no audit log.
Impact: Accidental LIVE trading during testing = immediate real money loss.
Fix: Require dual confirmation (config + env var `AUTONOME_LIVE_CONFIRM=I_UNDERSTAND`). Log mode changes to journal. Warn prominently on startup.

### 21. SQLITE JOURNAL GROWS UNBOUNDED [MEDIUM]
File: autonome/journal/trade_journal.py
Problem: Append-only SQLite with no rotation, no vacuum, no archive. Over months this becomes many GB.
Impact: Disk full = system crash, corrupted DB, lost data.
Fix: Implement monthly rotation. Archive old data. Vacuum periodically.

### 22. SECRETS IN PLAINTEXT YAML [MEDIUM]
File: config/secrets.yaml
Problem: API keys and secrets stored in plain YAML on disk. No encryption at rest. If the repo is accidentally committed or the disk is compromised, keys are exposed.
Impact: API key theft leading to unauthorized trading, account drain, or data exfiltration.
Fix: Use environment variables for secrets. If file-based, use Python keyring or at minimum AES-256 encryption with a master password.

### 23. TESTS ONLY COVER HAPPY PATH [MEDIUM]
File: tests/test_pipeline.py
Problem: Tests verify successful signal, risk approval, and execution. Zero tests for:
  - API timeout/rejection
  - Drawdown circuit breaker
  - Partial fill
  - Order cancellation
  - Halt state
  - Concurrent signals
Impact: Bugs in failure paths deploy undetected.
Fix: Add pytest fixtures with mocked failures. Test every rejection path in risk manager.

### 24. CLOCK SKEW NOT CHECKED [LOW]
File: autonome/data/bars.py, supervisor/main.py
Problem: No validation that system time matches broker time. No NTP check.
Impact: Stale data, premature/late order submissions.
Fix: Compare `datetime.utcnow()` with Alpaca clock API on startup. Warn if skew > 10s.

---

## THE SINGLE BIGGEST PRODUCTION RISK

**The broken bracket order implementation (Finding #1) is the #1 money-loss risk.**

Independent stop + target orders with no OCO linkage and no dangling-order cleanup means:
1. If stop fills, the target remains as an accidental entry order.
2. If target fills, the stop becomes a new exit/entry order.
3. In a choppy market, this can cause repeated direction-flipping entries accumulating losses while the "supervisor exposure loop" (which doesn't exist in current code) was supposed to clean up.

This is a known bug acknowledged in the code comment at line 99:
> "NOTE: these are independent orders -- they don't OCO. When one fills, the other remains. We rely on the supervisor exposure loop to cancel dangling orders when position drops to zero."

But the supervisor exposure loop DOESN'T EXIST in main.py. There is no code that monitors positions and cancels orphaned stop/target orders.

---

## ADDITIONAL FEATURES NEEDED (Prioritized)

### P0 — Deploy Blockers (Real money loss expected if deployed without these)
1. True OCO bracket order or dangling-order monitor
2. Partial fill handling on entry
3. Order lifecycle monitoring loop
4. Hard stop on N consecutive API failures
5. Correlation/sector risk concentration limits

### P1 — High Risk (Moderate money loss expected)
6. VIX/realized volatility halt enforcement (vol_pause_annual used)
7. Pattern Day Trader count enforcement
8. Fractional share precision sizing
9. Short selling borrow/margin checks
10. Data staleness detection and cycle skipping
11. Order throttling / rate limiting
12. Real-time alerts on critical failures

### P2 — Operational Safety
13. Drawdown halt state persistence across restarts
14. Earnings calendar enforcement
15. Limit order entry with market fallback
16. Split/dividend adjusted bars
17. Dual-confirmation LIVE mode switch
18. SQLite journal rotation / disk space monitoring
19. Encrypted/environment-variable secret management
20. Full failure-scenario test suite

---

### SUMMARY TALLY
- Total critical gaps: 6
- Total high-risk gaps: 10
- Total medium-risk gaps: 13
- Total low-risk gaps: 1
- **Files reviewed: 16 Python + 4 service + 2 config**
- **Estimated time to production-safe: 2-3 sprint cycles with dedicated QA**
