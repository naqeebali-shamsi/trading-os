# NOMADCREW TRADING OS v2 — SYSTEM ARCHITECTURE REVIEW

**Reviewer:** Architecture Review Agent  
**Scope:** Coupling, cohesion, abstraction layers, data flow, state management, error handling, fault tolerance, module boundaries, autonomous operation readiness  
**Verdict:** The foundation shows thoughtfulness, but critical bugs, tight coupling, and missing async infrastructure make this a "deterministic toy" rather than an autonomous trading system. It will not make money in its current state.

---

## 1. CRITICAL ARCHITECTURAL FLAWS

### 1.1 The Supervisor is a God Object — Untestable, Unswappable
**File:** `autonome/supervisor/main.py`  
The `State` class directly instantiates ~12 hard dependencies: `AlpacaClient`, `BarStore`, `AlpacaDataFeed`, `StrategyRouter`, `RiskManager`, `ExecutionEngine`, `TradeJournal`, `LLMGate`, `TelegramAlertSender`, `Reconciler`, `EarningsCalendar`, and a missing `TimesFMAdapter` (see 1.2). There is no dependency injection, no interface contracts, and no factory pattern. You cannot unit-test the supervisor loop, mock the broker, or swap Alpaca for another broker without rewriting main.py. This is the #1 blocker to maintainability.

### 1.2 Runtime Crash Bugs in Core Path
**Files:** `supervisor/main.py`, `strategy/router.py`, `risk/portfolio_heat.py`, `intelligence/regime_forecaster.py`  
A code review reveals multiple show-stopper bugs that would cause `AttributeError` or `ImportError` on first run:
- **`supervisor/main.py:336`** calls `st.forecaster.forecast()` but `State.__init__` never sets `self.forecaster`. **The system will crash on the first signal that reaches the TimesFM filter.**
- **`strategy/router.py:115`** references `self._last_signal_bar`, but the actual attribute is `self._last_idx` (a `dict[str, int]`). **Every scan call crashes.**
- **`risk/portfolio_heat.py:98`** references `self.positions`, which does not exist in the class. Should be `self._position_heat`. **Unregistering a failed trade crashes.**
- **`intelligence/regime_forecaster.py`** imports `autonome.intelligence.timesfm_adapter`, but the actual module is `timesfm_adapter_production.py`. **The import fails outright.**
- **`supervisor/main.py:303`** hardcodes `strategy="momentum_breakout"` in the LLM gate context regardless of which strategy actually generated the signal.

These are not edge cases. These are core-path failures that mean the codebase **cannot run** in production without patching.

### 1.3 Synchronous Blocking Architecture on the Critical Path
The entire trading loop is single-threaded, blocking, and poll-based:
- `time.sleep(heartbeat_sec)` between cycles.
- Alpaca REST API calls for every bar, every account fetch, every order sync.
- **The LLM gate performs a synchronous HTTP POST to OpenRouter (30s timeout) inside the hot loop.** Every signal blocks the entire system. If the LLM takes 5 seconds, the next symbol's bar is stale.
- There is no event bus, no message queue, no async I/O, no WebSocket streaming.

A real trading system does not sleep. It reacts to events. This architecture cannot support low-latency decisions or handle burst market activity.

### 1.4 No Unified Position Lifecycle Management
**File:** `supervisor/main.py`  
The supervisor handles **entries** comprehensively (signal → LLM gate → risk → execute → journal). But **exits are entirely delegated to Alpaca bracket orders**. There is no code that:
- Monitors open positions after entry.
- Implements trailing stops.
- Exits based on time-in-market, deteriorating fundamentals, or regime shifts.
- Tracks realized P&L to update the daily loss accumulator.
- Frees portfolio heat when a position closes.

The risk manager's `record_loss()` and `record_win()` are never called by the supervisor. **Daily loss tracking is effectively dead code.**

### 1.5 Configuration Loading is Scattered and Duplicated
At least 8 modules (`bars.py`, `risk_manager.py`, `execution/engine.py`, `llm_gate.py`, `alpaca_client.py`, `trade_journal.py`, etc.) independently open and parse `config/settings.yaml` and/or `config/secrets.yaml`. This is:
- Wasteful (dozens of disk reads).
- Brittle (path resolution relies on `__file__` and could break if moved).
- Insecure (secrets are loaded into memory multiple times).
- Untestable (you cannot inject a fake config without monkey-patching filesystem calls).

### 1.6 BarStore Conflates Two Concerns
**File:** `autonome/data/bars.py`  
`BarStore` is both an **in-memory ring buffer for strategies** and an **SQLite persistence layer for warm restarts**. It persists bars to the same SQLite file as the trade journal (`data/journal.sqlite`). This creates:
- Lock contention (journal writes + bar INSERTs on the same DB).
- Schema confusion (bars table in journal DB).
- Operational fragility (journal rotation could corrupt bar history).
- Test pollution (tests must use temp dirs to avoid side effects).

### 1.7 REST-Polling Data Feed (No Real-Time Streaming)
**File:** `autonome/data/bars.py`  
`AlpacaDataFeed.fetch_history()` queries Alpaca REST API every cycle with `feed=iex` (free tier, delayed). There is no WebSocket connection to Alpaca's streaming API. The system is always looking at stale data. For a momentum breakout strategy that relies on volume surges, missing the first 30-120 seconds of a breakout is fatal to profitability.

### 1.8 India/US Markets are Duplicate Silos with No Shared Core
The India module (`autonome/india/`) re-invents signals, risk, strategy, and fundamentals from scratch with India-specific dataclasses (`IndiaSignal`, `IndianStock`). These share **zero** interfaces with the US trading core. There is no `BaseSignal`, `BaseStrategy`, or `BaseRiskEvaluator`. If a bug is fixed in US risk, the India code doesn't benefit. If India logic improves, it doesn't flow back.

### 1.9 Risk Manager State is Not Fully Crash-Recoverable
**File:** `autonome/risk/risk_manager.py`  
`peak_equity` and `halted` are persisted to `data/halted.json`. But `daily_loss_accum` is **purely in-memory**. If the process crashes at 2 PM and restarts, the daily loss counter resets to 0, potentially allowing trades that should be blocked. Similarly, portfolio heat (`PortfolioHeat._position_heat`) is never persisted. After a restart, the system has no memory of its risk exposure.

### 1.10 Strategy Router Recomputes EMAs From Scratch on Every Scan
**File:** `autonome/strategy/router.py`  
`_regime_score()` recalculates EMA(9), EMA(21), EMA(50), plus two full EMA series for trend counting, for every symbol on every bar. This is O(n * m) where n = bars and m = symbols. With 3 symbols and 500 bars, it's tolerable. With 50 symbols, it's CPU-intensive. There is no incremental update or caching.

### 1.11 Backtest Engine Cannot Handle Multi-Symbol
**File:** `autonome/backtest/engine.py`  
`open_trade` is a single `Optional[dict]`. The engine assumes only one position can be open at a time. It cannot simulate a portfolio with multiple concurrent positions, which is the primary use case for the strategy router.

---

## 2. WHAT'S ACTUALLY WELL-DESIGNED

### 2.1 Risk Management Layer
**File:** `autonome/risk/risk_manager.py`  
Despite the crash-recovery gap, the risk evaluation logic is the strongest part of the system:
- Kelly criterion sizing with fractional Kelly (`kelly_fraction = 0.25`).
- Persistent drawdown halt with manual resume requirement.
- Volatility halt (realized vol + VIX extremes).
- Slippage-adjusted risk (`risk * 1.10`).
- PDT guard, buying power guard, max positions, position-per-symbol dedup.
- VIX-based position size reduction (halve at VIX ≥ 30).

This shows real trading experience. The problem is it's **disconnected from the exit path**, so it only protects on entry.

### 2.2 Bracket Order Execution with Alpaca Native API
**File:** `autonome/execution/engine.py`  
Using Alpaca's `order_class: bracket` is the correct architectural choice. The engine correctly:
- Handles duplicate signal cooldown (60s).
- Implements rate limiting.
- Performs short pre-flight checks (shortable, easy_to_borrow, margin enabled, buying power).
- Retries rejected entries (3 attempts).
- Tracks active orders for lifecycle monitoring.
- Queries and tracks bracket children (stop + target).

### 2.3 Append-Only Journal with Rotation Safety
**File:** `autonome/journal/trade_journal.py`  
The SQLite schema separates `signals`, `orders`, `pnl`, and `equity`. The rotation logic is admirably paranoid:
- Archives old data to a new DB first.
- Verifies the archive file exists and is non-empty.
- Only then deletes from source and runs `VACUUM`.

This is production-grade data safety.

### 2.4 TimesFM Adapter with Graceful Fallback
**File:** `autonome/intelligence/timesfm_adapter_production.py`  
The adapter pattern here is architecturally sound:
- Attempts to load Google's real TimesFM 2.5 model.
- Falls back to a `StatisticalForecaster` using EMA/ATR/momentum/volume ratio.
- Exposes `forecast()`, `direction_bias()`, and `should_trade()` with clear interfaces.
- Logs which backend is active.

If the broken imports and missing initialization were fixed, this would be a solid ML integration.

### 2.5 Live Mode Safety Gate
**File:** `autonome/broker/alpaca_client.py`  
The `AUTONOME_LIVE_CONFIRM=I_UNDERSTAND` environment variable requirement is excellent safety engineering. It prevents accidental live trading from config typos.

### 2.6 Dark Horse Discovery Pipeline
**File:** `autonome/discovery/dark_horse.py`  
The concept of multi-source signal aggregation (screeners + news + sector rotation) with scoring, deduplication, and ranking is the right way to do systematic discovery. The `SECTOR_LEADERS` mapping is a pragmatic shortcut.

### 2.7 Health Monitoring Scripts
**Files:** `swarm/scripts/health_monitor.py`, `swarm/scripts/orchestrator_pulse.py`  
These provide deterministic, state-file-based health checks without dependencies. They detect stale heartbeats, drawdowns, and idle periods. The escalation to `EMERGENCY_HALT` is the correct severity model.

---

## 3. TOP 3 CHANGES NEEDED

### CHANGE #1: FIX CRITICAL RUNTIME BUGS AND INTRODUCE DEPENDENCY INJECTION
**Priority: P0 — System will not run without this.**

1. Fix the four crash bugs identified in §1.2:
   - Add `self.forecaster = TimesFMAdapter()` to `State.__init__`.
   - Change `self._last_signal_bar` to `self._last_idx.get(symbol, -999)` in `router.py`.
   - Fix `self.positions` → `self._position_heat` in `portfolio_heat.py`.
   - Fix the regime_forecaster import path.

2. Replace the `State` God object with a **composition root** (e.g., `Container` class or a DI framework like `dependency-injector`). Each component should receive its dependencies via constructor injection, not reach into the filesystem or instantiate hard-coded classes.

3. Define **interface contracts** (Python protocols/abstract base classes) for:
   - `BrokerClient` (so Alpaca can be swapped for Interactive Brokers, etc.)
   - `DataFeed` (so REST can be swapped for WebSocket)
   - `Strategy` (so backtests can use the same interface as live)
   - `RiskEvaluator`, `ExecutionEngine`, `Journal`

**Impact:** Transforms an untestable monolith into a modular, testable system.

---

### CHANGE #2: BUILD AN ASYNC EVENT-DRIVEN CORE WITH WEBSOCKET STREAMING
**Priority: P1 — Cannot achieve autonomy or profitability with polling.**

The current architecture:  
`Sleep → Poll REST → Process sequentially → Sleep`  

Required architecture:  
`Stream bars + trades via WebSocket → Publish to event bus → Async workers (signal, risk, LLM, execute) consume events → Non-blocking`

1. **Use asyncio + Alpaca WebSockets** (`wss://stream.data.alpaca.markets`) for real-time bar and trade updates. Rest holds, order fills, and bar closures should be events, not poll results.

2. **Decouple the LLM gate from the hot path.** Run the LLM review as an **async background task** or a **pre-computed scoring cache**. A signal should not wait 5-30 seconds for an HTTP round-trip. Consider:
   - Caching LLM scores by symbol+regime (recomputed hourly).
   - Using a local lightweight model (e.g., Llama 3.1 8B via Ollama) to eliminate network latency.
   - Making the LLM gate optional/configurable per strategy.

3. **Add a Position Lifecycle Manager** as a first-class citizen. It should:
   - Subscribe to fill events.
   -Track realized P&L and call `risk.record_loss()` / `risk.record_win()`.
   - Update `PortfolioHeat` on exits.
   - Implement trailing stop logic (not just static bracket orders).
   - Handle time-based exits (e.g., "exit if not profitable after N bars").

4. **Move daily_loss_accum and portfolio_heat to persistent state** (SQLite or Redis) so they survive restarts.

**Impact:** Transforms a deterministic script into a reactive, resilient trading system that can actually respond to market events in real time.

---

### CHANGE #3: UNIFY THE STRATEGY/RISK/EXECUTION INTERFACE ACROSS ALL MARKETS
**Priority: P1 — Prevents divergence and duplication.**

Currently there are three separate trading systems with no shared vocabulary:
- US: `Signal` (momentum_breakout) → `RiskManager.evaluate()` → `ExecutionEngine.enter_position()`
- India: `IndiaSignal` → inline risk logic → manual execution
- Long-term: Screener outputs JSON, no execution path

1. **Define a unified domain model:**
   ```python
   class TradeSignal: symbol, direction, entry, stop, target, confidence, strategy_name, market, timeframe
   class RiskDecision: approved, qty, reason, adjusted_stop, adjusted_target
   class TradeExecution: order_id, status, fill_price, fill_qty, timestamp
   class Position: symbol, qty, entry_price, current_stop, unrealized_pnl, opened_at
   ```

2. **Refactor India and Long-term modules to implement the same `Strategy` and `RiskEvaluator` interfaces.** India's "buy the dip on fundamentals" should be just another strategy plugin that the router can call. The difference between US automated and India manual should be **only at the execution layer** (a `ManualExecutionEngine` that writes to a dashboard instead of calling Alpaca).

3. **Extract configuration into a single typed config object** loaded once at startup and passed to all components. Use Pydantic models for validation. Stop parsing YAML in every module.

4. **Separate BarStore from JournalStore.** Bars should live in a dedicated time-series store (even just a separate SQLite file, or ideally `pyarrow`/`parquet` for performance). Journal should remain append-only SQLite for audit.

**Impact:** Eliminates code duplication, enables cross-market analytics, makes the system actually maintainable as it grows.

---

## 4. HONEST SUMMARY

| Dimension | Score (1-10) | Notes |
|-----------|-------------|-------|
| **Modularity** | 4/10 | Files are organized, but components are tightly coupled via direct instantiation. |
| **Testability** | 3/10 | No DI means heavy mocking needed. Backtest engine is single-symbol only. |
| **Data Flow** | 3/10 | Synchronous polling. No event bus. No streaming. LLM blocks critical path. |
| **State Management** | 4/10 | Good SQLite journal, but risk state is partially ephemeral. No position lifecycle. |
| **Error Handling** | 5/10 | Good try/except coverage, but halt state doesn't cover all failure modes. |
| **Fault Tolerance** | 4/10 | API failure counter → halt is good. But no graceful degradation for data source failure. |
| **Autonomous Readiness** | 2/10 | A system that cannot run without crashing, cannot track its own positions through exits, and blocks on external LLM calls is not autonomous. It is a scheduled script. |
| **Risk Engineering** | 7/10 | The risk layer itself is the most mature part. It's just not wired to the full lifecycle. |

**Bottom line:** The NomadCrew Trading OS v2 has the *pieces* of a good system — solid risk logic, bracket order execution, journal rotation, and a thoughtful discovery pipeline. But it is currently **a collection of decent modules bolted together into a brittle, synchronous script with multiple runtime crashes.** With the three changes above (bug fixes + DI, async event core, unified interfaces), this could become a genuinely autonomous trading system. Without them, it will remain "lower than mediocre and not making money" — because the architecture itself prevents it from being anything else.
