# QA REVIEW — NomadCrew Trading OS v2
**Date:** 2026-06-05
**Scope:** /mnt/e/NomadCrew[GROWTH]/trading-os/v2/autonome/ + tests/
**Codebase:** ~9,100 LOC Python (autonome/)
**Test Files:** 2 (tests/test_backtest.py, tests/test_pipeline.py)
**Test LOC:** ~306 lines total
**Coverage Estimate:** <5% of production code paths

---

## EXECUTIVE SUMMARY

The testing posture is dangerously thin for a system that manages real money (even in paper mode today). Only 2 ad-hoc test files exist, no test framework is installed, no CI/CD pipeline validates changes, and the most critical paths — execution, risk circuit breakers, broker reconciliation, and the supervisor loop — are completely untested. The existing tests are basic smoke tests that do not exercise edge cases, failure modes, or concurrent behavior.

**Verdict:** This codebase will fail in production. The gaps are not incremental; they are structural.

---

## 1. TESTING GAPS THAT WILL CAUSE PRODUCTION FAILURES

### A. ZERO TESTS FOR THE SUPERVISOR LOOP (autonome/supervisor/main.py — 436 LOC)
The 24x7 orchestration loop has no tests. This is the heart of the system. Untested paths include:
- Daily reset logic and date-boundary bugs (line 191-210)
- API failure cascade and halt trigger (lines 138-148, 411-413)
- Data staleness soft-halt (lines 264-275)
- Market-hours gate with timezone edge cases (lines 213-225)
- Order lifecycle sync every 120s (lines 228-237)
- Broker reconciliation hourly (lines 240-252)
- Journal rotation at 500MB threshold (lines 255-260)
- Position exit detection and alert firing (lines 398-410)
- **Critical:** The loop calls `st.forecaster.forecast()` (line 336) but `State.__init__` never initializes `self.forecaster` — this will raise `AttributeError` on the first signal that passes the LLM gate.

### B. EXECUTION ENGINE UNTESTED FOR FAILURE MODES (autonome/execution/engine.py — 302 LOC)
The pipeline test uses `FakeAlpaca` which is incomplete (runtime error: `'FakeAlpaca' object has no attribute '_get'`). Untested:
- Bracket order child fetching (`_fetch_bracket_children`) — relies on raw `_get()` which the fake lacks
- Partial fill handling and 10-second wait loop (lines 147-157)
- Entry cancellation when no fill price received (lines 170-178)
- Short pre-flight guards: HTB, margin enabled, buying power (lines 85-118)
- Rate limiter queuing and duplicate cooldown (lines 59-79)
- `flatten_symbol()` and `_cancel_bracket_legs()` — orphan cleanup
- `sync_orders()` and `reconcile_position()`

### C. RISK MANAGER HALT STATE UNTESTED (autonome/risk/risk_manager.py — 276 LOC)
Only basic approve/reject logic is tested. Missing:
- Halt state persistence to `data/halted.json` and crash recovery (`_load_halt_state`, `_save_halt_state`)
- Drawdown halt trigger and re-entry behavior
- Daily loss accumulation and reset boundary
- Volatility halt with realized vol calculation
- VIX-based size reduction (halving at VIX >= 30)
- Kelly sizing edge cases: negative Kelly, zero payoff, division by zero
- Portfolio heat integration with sector limits
- `commit_trade` / `unregister_trade` heat registration lifecycle
- **Bug risk:** `realized_vol_annual` uses population variance denominator `n-1` but only 2+ returns required; with exactly 2 returns, stdev is well-defined but with tiny samples vol estimates will be extremely noisy and trigger false halts.

### D. BROKER CLIENT UNTESTED (autonome/broker/alpaca_client.py — 256 LOC)
No unit tests for:
- LIVE mode safety gate (`AUTONOME_LIVE_CONFIRM` env var check)
- Error handling for 403/422 responses from Alpaca
- `get_asset()` and `is_tradable()` failure paths
- `is_margin_enabled()` fallback on HTTPError
- Order submission with bracket payload serialization
- `list_orders()` and `get_order()` HTTP error handling
- **Risk:** All network paths are untested. A single API schema change or rate limit response will crash the supervisor.

### E. DATA LAYER UNTESTED (autonome/data/bars.py — 207 LOC)
- SQLite warm-restart logic (`_warm_from_db`)
- Concurrent ingest + persist (SQLite write lock contention under multi-symbol load)
- Staleness detection with timezone-aware vs naive datetime comparisons
- `AlpacaDataFeed.fetch_history()` — no tests for Yahoo data parsing, empty responses, or rate limiting
- `Bar.__post_init__` edge cases (zero-range bars)

### F. INDIA MARKET COMPLETELY UNTESTED (autonome/india/ — 1,350+ LOC)
All India advisory components have zero tests:
- `signals.py` — candle pattern detection, signal generation
- `fundamentals.py` — Graham-Buffett screening logic
- `strategy.py` — portfolio construction
- `risk.py` — India-specific risk rules
- `sentinel.py` — macro regime (USD/INR, oil, gold)
- `broker.py` — manual execution helper

### G. INTELLIGENCE LAYER UNTESTED (autonome/intelligence/ — 2,200+ LOC)
- `llm_gate.py` — No tests for JSON parsing from markdown blocks, API timeout handling, fallback decisions, or modification application
- `discovery.py` — No tests for RSS parsing, supply-chain mapping, or candidate scoring
- `dreampod.py` — No tests
- `regime_forecaster.py` — No tests
- `timesfm_adapter*.py` — No tests (only referenced in supervisor, but `State` never initializes the forecaster)

### H. RECONCILIATION UNTESTED (autonome/execution/reconcile.py — 101 LOC)
- Ghost position detection
- Untracked position detection
- Quantity mismatch logic
- Orphaned order detection
- All error paths (broker fetch failure, journal query failure)

### I. JOURNAL UNTESTED (autonome/journal/trade_journal.py — 186 LOC)
- Schema creation and migration
- `log_signal` with dict vs string meta handling
- `today_pnl` and `today_signals_count` date-boundary queries (uses `LIKE 'YYYY-MM-DD%'` which is fragile for ISO format strings)
- `rotate()` — archive write confirmation, VACUUM, cutoff logic
- **Bug risk:** `rotate()` uses `datetime.utcnow()` for cutoff but `log_signal` uses `datetime.utcnow()` — consistent but naive to timezone. If the system moves hosts, timestamps may drift.

### J. NO CONCURRENCY / RACE CONDITION TESTS
The supervisor loop is single-threaded but interacts with:
- SQLite (journal + bars) from multiple logical paths
- Alpaca API with async order state changes
- File-system state writes (`state.json`, `halted.json`)
No tests verify behavior under:
- Simultaneous bar ingest + journal query
- Order fill during `sync_orders()` poll
- Journal rotation while logging a signal

### K. NO PERFORMANCE BENCHMARKS
- Backtest engine throughput (bars/sec)
- Strategy scan latency per symbol
- SQLite query performance at 500MB+ journal size
- LLM gate latency and token cost estimation accuracy

### L. NO PROPERTY-BASED TESTS
No use of Hypothesis or similar. Critical invariants that should be property-tested:
- `RiskDecision.qty` is always positive when approved
- `PortfolioHeat.total_heat()` is monotonically increasing with position count
- `BacktestResult.equity_curve` never goes negative with default params
- `EMA` values are always between min and max of input series

---

## 2. WHAT IS TESTED WELL

### A. BACKTEST SMOKE TEST (tests/test_backtest.py)
- Verifies synthetic data backtest runs without crashing
- Verifies empty bar list returns zero trades
- Verifies metrics computation with hand-crafted trades (3 trades: 2 wins, 1 loss)
- **Limitation:** Only tests `MomentumBreakout` + `BacktestEngine`. Does not test `StrategyRouter`, `RegimeFilter`, or other strategies.

### B. PIPELINE INTEGRATION SMOKE TEST (tests/test_pipeline.py)
- `test_momentum_detects_breakout`: Verifies `MomentumBreakout.scan()` produces a LONG signal on synthetic uptrend + volume breakout data
- `test_risk_approves_and_rejects`: Tests 4 risk scenarios (normal, bad stop/target, zero confidence, max positions)
- `test_full_pipeline_mock`: End-to-end from signal -> risk -> execution -> journal logging
- `test_portfolio_heat`: Tests heat calculation, sector limits, total limits, and conviction weighting
- **Limitation:** Uses an incomplete `FakeAlpaca` mock. The execution engine's `_fetch_bracket_children` call fails at runtime because the fake lacks `_get`. This means the "OPEN" status in tests is misleading — the engine never actually verified bracket children.

### C. POSITIVE QUALITIES
- Tests use `tempfile.TemporaryDirectory()` for DB isolation — good hygiene
- `FakeAccount` and `FakeAlpaca` show intent to mock external dependencies
- `generate_synthetic` in backtest data_loader is a useful test utility

---

## 3. TOP 3 TESTING PRIORITIES

### PRIORITY 1: INSTALL A TEST FRAMEWORK AND TEST THE SUPERVISOR LOOP
**Why:** The supervisor is the runtime engine. A single unhandled exception kills the entire trading system.
**Actions:**
1. `pip install pytest pytest-asyncio pytest-mock freezegun responses`
2. Extract the supervisor loop body into testable functions (currently everything is nested inside `loop()`)
3. Write tests for:
   - Daily reset at date boundary
   - API failure cascade (1 failure -> 5 failures -> halt)
   - Data staleness soft-halt (3 consecutive stale cycles)
   - Fix the missing `self.forecaster` initialization bug and test TimesFM forecast integration
   - Market open/closed gate with mocked clock

### PRIORITY 2: TEST RISK MANAGER HALT STATES AND EXECUTION FAILURE MODES
**Why:** These are the safety systems. If they fail, the system loses money.
**Actions:**
1. Test halt state persistence: simulate drawdown -> halt -> restart -> verify still halted
2. Test all `RiskDecision` rejection reasons with parameterized tests
3. Test execution engine with a proper `AlpacaClient` mock (using `responses` library or `unittest.mock`)
4. Test partial fill handling, retry exhaustion, and bracket child orphan cleanup
5. Test `flatten_symbol()` and `_cancel_bracket_legs()` with mocked positions

### PRIORITY 3: ADD PROPERTY-BASED TESTS FOR INVARIANTS AND BACKTEST VALIDATION
**Why:** The current tests only verify "happy path" smoke. Property tests find edge cases humans miss.
**Actions:**
1. `pip install hypothesis`
2. Property tests for:
   - `RiskManager.evaluate()` never approves zero or negative qty
   - `PortfolioHeat.total_heat()` is bounded by `max_heat_pct`
   - `BacktestEngine.run()` equity curve is non-decreasing when all trades are winners
   - `EMA` series is always within [min(close), max(close)]
3. Backtest validation against known results: run a fixed seed synthetic dataset, assert exact metrics match a golden snapshot
4. Add a regression test suite that runs on every commit (GitHub Actions or local pre-commit hook)

---

## 4. ADDITIONAL RECOMMENDATIONS

| Area | Recommendation |
|------|----------------|
| **CI/CD** | Add `.github/workflows/test.yml` or local pre-commit hook running `pytest` |
| **Coverage** | Add `pytest-cov`; set minimum threshold at 60% for autonome/ |
| **Mocking** | Replace ad-hoc `FakeAlpaca` with `responses` library for HTTP mocking or `unittest.mock.Mock` with spec |
| **India** | Add at least smoke tests for `india/signals.py` and `india/fundamentals.py` |
| **LLM Gate** | Add tests for JSON extraction from markdown blocks, timeout handling, and cost estimation |
| **Database** | Use `pytest` fixtures with `:memory:` SQLite DBs for all journal/bar store tests |
| **Race Conditions** | Add threaded tests for `BarStore.ingest()` + `TradeJournal.log_signal()` concurrent access |
| **Benchmarks** | Add `pytest-benchmark` tests for strategy scan latency and backtest throughput |

---

## 5. METRICS

| Metric | Value |
|--------|-------|
| Production Python LOC | ~9,100 |
| Test Python LOC | ~306 |
| Test-to-Code Ratio | 1:30 |
| Modules with zero tests | 35+ |
| Framework installed | None |
| CI/CD pipeline | None |
| Property-based tests | 0 |
| Performance benchmarks | 0 |
| Race condition tests | 0 |
| Backtest golden snapshots | 0 |

---

*Review completed by QA subagent.*
