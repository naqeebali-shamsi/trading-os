# PLATFORM ENGINEERING REVIEW — NomadCrew Trading OS v2

**Reviewer:** Platform Engineering (CLI Agent)  
**Date:** 2026-06-05  
**Scope:** scripts/india_dashboard_server.py, scripts/longterm_discovery.py, scripts/run_india_discovery.py, dashboard/server.py, config files, cron/systemd setup, dependency management, process management, WSL2 compatibility, file I/O, server stability, virtualenv hygiene, log management.

---

## 1. INFRASTRUCTURE ISSUES THAT WILL BREAK IN PRODUCTION

### CRITICAL

| # | Issue | Impact | Files |
|---|-------|--------|-------|
| 1 | **requirements.txt is incomplete** — only lists `requests>=2.31.0` and `pyyaml>=6.0.1`. Missing `yfinance`, `numpy`, and any LLM/TimesFM deps. A fresh `pip install -r requirements.txt` will crash on first India or long-term discovery run. | Fresh installs / CI / new WSL instance fail immediately. | `requirements.txt`, `run.sh` |
| 2 | **Supervisor references uninitialized `st.forecaster`** — `State.__init__` never creates `self.forecaster`, but `supervisor/main.py:336` calls `st.forecaster.forecast(...)`. First signal reaching the TimesFM filter raises `AttributeError` and kills the bar cycle. | Supervisor crashes on first valid signal. | `autonome/supervisor/main.py` |
| 3 | **Health monitor reads a log file that does not exist** — `health_monitor.py` and `dashboard/server.py` read `/tmp/autonome_paper.log`, but the supervisor logs to `stdout` (StreamHandler) by default. Under systemd, output goes to the journal, not that file. Health monitoring is blind; stale-data and CRITICAL detection never fire. | False "healthy" status while system is dead or broken. | `swarm/scripts/health_monitor.py`, `dashboard/server.py` |
| 4 | **SQLite on WSL2 /mnt/e (9P mount)** — `journal.sqlite` and `bars` table live on a Windows-mounted drive. 9P is ~10-100x slower than ext4 and known to corrupt SQLite under concurrent write load. The code opens a **new connection per ingest/query**, amplifying the problem. | DB corruption, 5-30s query pauses, journal lock errors. | `autonome/journal/trade_journal.py`, `autonome/data/bars.py` |
| 5 | **Hardcoded WSL2 paths in 15+ files** — `/mnt/e/NomadCrew[GROWTH]/trading-os/v2/...` is baked into scripts, systemd units, Python modules, and the orchestrator. Moving the repo, cloning on another machine, or running in CI breaks everything. | Zero portability. Every path change is a code edit. | `scripts/*`, `systemd/*`, `autonome/*`, `run.sh`, `autonome-orchestrator.sh` |
| 6 | **Missing systemd timers for India / longterm / dark-horse discovery** — SYSTEM_BRIEF lists cron jobs for india-discovery (Mon 2 AM UTC), longterm-discovery (Mon 3 AM UTC), and dark-horse-discovery (Mon-Fri 8 AM ET). No `.service` or `.timer` units exist for any of these. They will never run. | Discovery pipelines are dead code. | `systemd/`, `scripts/run_india_discovery.py`, `scripts/longterm_discovery.py` |

### HIGH

| # | Issue | Impact | Files |
|---|-------|--------|-------|
| 7 | **Single-threaded HTTP dashboard servers** — Both `dashboard/server.py:8765` and `scripts/india_dashboard_server.py:8766` use Python's built-in `HTTPServer` with `BaseHTTPRequestHandler`. No threading, no timeouts, no request queue. A slow SQLite query or concurrent refresh blocks all requests. | Dashboard freezes under load; no production-grade serving. | `dashboard/server.py`, `scripts/india_dashboard_server.py` |
| 8 | **systemd crash loop with no rate limit** — `autonome-supervisor.service` has `Restart=always` but no `StartLimitIntervalSec` or `StartLimitBurst`. A bad secret, missing venv, or Alpaca API outage causes infinite restart loops, hammering APIs and filling the journal. | API rate-limit bans, log disk exhaustion. | `systemd/autonome-supervisor.service` |
| 9 | **Hardcoded TimesFM venv path injection** — `scripts/run_india_discovery.py:19` injects `sys.path.insert(0, ".../timesfm_env/lib/python3.11/site-packages")`. If Python is 3.12+ or the venv is missing, the import chain fails. | India discovery breaks on any Python upgrade. | `scripts/run_india_discovery.py` |
| 10 | **Unbounded log growth** — `swarm/intel/whats_broken.md` is append-only with no rotation. `nightly_reset.py` redirects `after_hours_learner.py` to `/tmp/after_hours_learner.log` with no size limit. Over months these grow without bound. | Disk exhaustion on long-running WSL instance. | `swarm/scripts/health_monitor.py`, `swarm/scripts/nightly_reset.py` |
| 11 | **No file locking on shared state** — `swarm/config/swarm_state` is read/written by `health_monitor.py`, `orchestrator_pulse.py`, `nightly_reset.py`, and `dashboard/server.py` without `fcntl` locking or atomic writes. Race conditions on concurrent access. | State corruption (e.g., EMERGENCY_HALT overwritten by ACTIVE). | `swarm/scripts/*`, `dashboard/server.py` |
| 12 | **WSL2 clock drift on Windows sleep** — If the Windows host sleeps, WSL2's clock can drift until `ntp` syncs. All timestamps, bar staleness checks (`any_stale`), market-hours gates, and cron/timer schedules become unreliable. | Trades at wrong times, false stale-data halts, missed discovery runs. | All time-dependent modules |

### MEDIUM

| # | Issue | Impact | Files |
|---|-------|--------|-------|
| 13 | **Config parsed independently by 10+ modules** — Every module that needs config opens and parses `settings.yaml` and/or `secrets.yaml` on its own. No singleton, no env-var override, no hot-reload. Wasteful and inconsistent. | 10x config I/O, risk of parsing different versions mid-run. | `autonome/supervisor/main.py`, `autonome/data/bars.py`, `autonome/risk/risk_manager.py`, `autonome/execution/engine.py`, `autonome/journal/trade_journal.py`, `autonome/alerts/telegram.py`, `autonome/intelligence/llm_gate.py`, `autonome/intelligence/dreampod.py`, `autonome/intelligence/discovery.py` |
| 14 | **Dashboard server does not serve static assets** — `dashboard/server.py` inlines the entire HTML/JS/CSS in a Python string. `scripts/india_dashboard_server.py` serves HTML files but has no static file handler for CSS/JS/images. | Unmaintainable frontend; no caching headers for real assets. | `dashboard/server.py`, `scripts/india_dashboard_server.py` |
| 15 | **No `.gitignore` for data, logs, venv** — `data/journal.sqlite`, `.venv/`, `__pycache__/`, `swarm/logs/`, and intel files are untracked but not ignored. Risk of committing secrets or multi-GB DBs. | Accidental secret leakage, bloated repo. | Repo root |

---

## 2. WHAT'S SOLID

| Area | Observation | Files |
|------|-------------|-------|
| **Halt state persistence** | `risk_manager.py` saves `halted` + `peak_equity` to `data/halted.json` and reloads on init. Survives systemd restarts correctly. | `autonome/risk/risk_manager.py` |
| **LIVE mode safety gate** | `AUTONOME_LIVE_CONFIRM=I_UNDERSTAND` env var is required. Constructor raises `RuntimeError` if missing. Good deliberate friction. | `autonome/broker/alpaca_client.py` |
| **API failure hard stop** | 5 consecutive API failures trigger `risk.halted = True`, send alert, and log `CRITICAL`. Prevents runaway error loops. | `autonome/supervisor/main.py` |
| **Journal rotation** | `TradeJournal.rotate()` archives records older than N months to a separate DB, verifies archive size, then `VACUUM`s. Safe. | `autonome/journal/trade_journal.py` |
| **systemd timer skeleton** | DreamPod, Discovery, and Review timers exist with reasonable `OnCalendar` schedules and `Persistent=true`. | `systemd/*.timer` |
| **Rate limiting** | `OrderRateLimiter` provides token-bucket throttling (6/min global, 2/min/symbol). | `autonome/execution/rate_limiter.py` |
| **Bracket orders** | `ExecutionEngine` uses Alpaca's native `order_class: bracket` with `stop_loss` + `take_profit` legs. Not manual leg management. | `autonome/execution/engine.py` |
| **venv bootstrap in run.sh** | `run.sh` auto-creates `.venv` and runs `pip install -r requirements.txt`. Good onboarding pattern. | `run.sh` |
| **Duplicate signal cooldown** | `ExecutionEngine` rejects duplicate signals for the same symbol within 60 seconds. | `autonome/execution/engine.py` |

---

## 3. TOP 3 PLATFORM FIXES

### Fix 1: Complete dependency management and remove hardcoded path hacks

- **Audit and fill `requirements.txt`** with every non-stdlib import: `yfinance`, `numpy`, and any packages used by `timesfm_adapter_production.py`, `dreampod.py`, `discovery.py`.
- **Add `pyproject.toml`** (or `setup.py`) so the project is `pip install -e .`-able. This eliminates the `sys.path.insert(0, '/mnt/e/...')` hacks in `longterm_discovery.py`, `run_india_discovery.py`, and `us_screener.py`.
- **Replace all absolute paths** with a `ROOT_DIR` resolved at runtime (e.g., `Path(__file__).resolve().parent.parent` or an `AUTONOME_ROOT` env var). Systemd units should use `Environment=AUTONOME_ROOT=%h/trading-os/v2` or similar.

### Fix 2: Move SQLite data off `/mnt/e` to native WSL2 ext4

- **Create a data directory on ext4**: `mkdir -p ~/.local/share/autonome/data` and symlink `data/journal.sqlite` there, OR add a `DATA_DIR` env var that defaults to `~/.local/share/autonome`.
- **Update `settings.yaml`** `journal.db_path` to use the ext4 path.
- **Add connection pooling or reuse** in `BarStore` and `TradeJournal`. Opening a new `sqlite3.connect()` per ingest is fine on ext4 but still wasteful. Use a persistent connection with `check_same_thread=False` if threading is added later.
- **Rationale:** This is the single biggest WSL2 reliability issue. 9P SQLite corruption is real and hard to debug.

### Fix 3: Fix the supervisor crash bug and make health monitoring log-agnostic

- **Initialize `self.forecaster`** in `State.__init__` (e.g., `self.forecaster = TimesFMAdapter()`), or guard the reference in `supervisor/main.py:335-340` with `if hasattr(st, 'forecaster') and st.forecaster:`.
- **Redirect supervisor logs to a file** that `health_monitor.py` can read, OR (better) rewrite `health_monitor.py` to query `journal.sqlite` and `data/state.json` directly instead of parsing log lines. Logs are an unreliable health source.
- **Ensure `dashboard/server.py`** also reads from `journal.sqlite` / `state.json` rather than `/tmp/autonome_paper.log`.
- **Add `ExecStartPre=/bin/test -f config/secrets.yaml`** to the supervisor systemd unit to fail fast with a clear message instead of crash-looping.
- **Add `StartLimitIntervalSec=300` and `StartLimitBurst=3`** to the supervisor service to prevent infinite restart loops.

---

## APPENDIX: Quick Reference — Files Reviewed

| File | Lines | Key Concern |
|------|-------|-------------|
| `scripts/india_dashboard_server.py` | 158 | Hardcoded paths; single-threaded server |
| `scripts/longterm_discovery.py` | 96 | Hardcoded `sys.path`; no error handling on screener import |
| `scripts/run_india_discovery.py` | 111 | Hardcoded TimesFM venv path (py3.11) |
| `dashboard/server.py` | 444 | Inlined HTML; reads non-existent `/tmp/autonome_paper.log`; SQLite on main thread |
| `autonome/supervisor/main.py` | 436 | `st.forecaster` never initialized; logs to stdout only |
| `autonome/data/bars.py` | 207 | New SQLite connection per `ingest()`; config parsed per module |
| `autonome/journal/trade_journal.py` | 186 | New SQLite connection per log call |
| `autonome/broker/alpaca_client.py` | 256 | Good LIVE gate; config parsed per module |
| `autonome/risk/risk_manager.py` | 276 | Good halt persistence; config parsed per module |
| `autonome/execution/engine.py` | 302 | Good bracket orders; config parsed per module |
| `autonome/india/fundamentals.py` | 313 | Uses `yfinance` (not in requirements.txt) |
| `autonome/india/sentinel.py` | 201 | Uses `yfinance` (not in requirements.txt) |
| `swarm/scripts/health_monitor.py` | 99 | Reads missing log file; writes unbounded `whats_broken.md` |
| `swarm/scripts/orchestrator_pulse.py` | 74 | Reads `swarm_state` without locking |
| `swarm/scripts/nightly_reset.py` | 49 | `os.system()` call without venv activation |
| `systemd/autonome-supervisor.service` | 17 | No start-limit burst; hardcoded path |
| `systemd/autonome-dreampod.timer` | 9 | Good schedule |
| `systemd/autonome-discovery.timer` | 9 | Good schedule |
| `config/settings.yaml` | 90 | Reasonable structure |
| `requirements.txt` | 3 | Critically incomplete |
| `run.sh` | 35 | Good venv bootstrap |
| `autonome-orchestrator.sh` | 87 | Hardcoded root path |
