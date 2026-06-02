#!/usr/bin/env python3
"""
kernel/supervisor.py -- Systemd for the Biology (v5 — Staged Boot)
-------------------------------------------------------------------
v5 changes:
- Boot is dependency-staged instead of flat: foundation -> sensory ->
  decision/risk -> execution -> memory/ops.
- The stage plan is data-driven and testable, so future layer additions have an
  explicit dependency home instead of silently joining an unsafe flat boot.
"""
import subprocess, sys, time, signal, json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_KERNEL_DIR = str(Path(__file__).resolve().parent)
# Running `python kernel/supervisor.py` puts kernel/ on sys.path[0], which resolves
# `import kernel` to kernel/kernel.py instead of this package.
if sys.path[:1] == [_KERNEL_DIR]:
    sys.path.pop(0)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish  # noqa: E402
from kernel.supervisor_health import build_supervisor_block, merge_supervisor_health  # noqa: E402

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
HEALTH_FILE = ROOT / "kernel" / "health.json"

BOOT_STAGE_DELAY_SEC = float(os.getenv("TRADING_OS_STAGE_DELAY_SEC", "1.0"))
LAYER_START_DELAY_SEC = float(os.getenv("TRADING_OS_LAYER_DELAY_SEC", "0.15"))

BOOT_STAGES = [
    {
        "name": "foundation",
        "description": "telemetry and passive observers first",
        "layers": [
            ("telemetry.metrics", ROOT / "telemetry" / "metrics.py", "Prometheus metrics + health endpoint"),
            ("consciousness.trace", ROOT / "consciousness" / "traces.py", "Event trace collector"),
            ("consciousness.alerts", ROOT / "consciousness" / "monitor.py", "Alert router"),
        ],
    },
    {
        "name": "sensory",
        "description": "market data and regime detection before decisions",
        "layers": [
            ("sensory.market", ROOT / "sensory" / "combined_feed.py", "Tick feed + OHLC aggregation (multisymbol-aware)"),
            ("sensory.regime", ROOT / "sensory" / "regime_detector.py", "Trend/range classifier"),
            ("sensory.timesfm", ROOT / "sensory" / "timesfm_forecaster.py", "Advisory-only TimesFM forecast context"),
            ("cortex.news", ROOT / "cortex" / "news_orchestrator.py", "LLM news scraper + decision (Track C)"),
            ("cortex.event_radar", ROOT / "cortex" / "event_radar.py", "Advisory macro event classifier"),
            ("research.stocks", ROOT / "research" / "stock_researcher.py", "Fundamental stock screener (advisory)"),
        ],
    },
    {
        "name": "decision_and_risk",
        "description": "signals, brain, risk, and anomaly gates before execution",
        "layers": [
            ("cortex.signals", ROOT / "cortex" / "signal_generator_v2.py", "Pattern + strategy signals"),
            ("cortex.brain", ROOT / "cortex" / "main.py", "LLM decision engine"),
            ("immune.risk", ROOT / "immune" / "main.py", "Risk gate (all orders)"),
            ("immune.anomaly", ROOT / "immune" / "anomaly.py", "Z-score anomaly detect"),
        ],
    },
    {
        "name": "execution",
        "description": "order routing starts only after risk gates are alive",
        "layers": [
            ("muscle.pnl_sync", ROOT / "muscle" / "pnl_sync_daemon.py", "MT5 position reconciliation"),
            ("muscle.position", ROOT / "muscle" / "position_commands.py", "Audited position command router"),
            ("muscle.main", ROOT / "muscle" / "muscle_main.py", "Order router (legacy/multi auto)"),
            ("muscle.positions", ROOT / "muscle" / "position_tracker.py", "Position ledger"),
        ],
    },
    {
        "name": "learning_and_ops",
        "description": "journaling, scoring, dashboards, and R&D after core loop",
        "layers": [
            ("memory.journal", ROOT / "memory" / "main.py", "Trade journal writer"),
            ("memory.learner", ROOT / "memory" / "learner.py", "Meta-learning loop"),
            ("introspect.score", ROOT / "introspect" / "score_strategies.py", "Track A: strategy scoring from bus"),
            ("introspect.decision_eval", ROOT / "introspect" / "decision_evaluator.py", "Decision confidence attribution + training data loop"),
            ("introspect.post_trade_eval", ROOT / "introspect" / "post_trade_evaluator.py", "Passive post-trade outcome reviews"),
            ("swarm.dispatcher", ROOT / "swarm" / "main.py", "R&D task dispatcher"),
            ("rd.dream", ROOT / "rd" / "dream_scheduler.py", "Dream Lab continuous R&D scheduler"),
            ("research.edge_ledger", ROOT / "research" / "edge_ledger_daemon.py", "Edge candidate ledger: ingest, label, gate report"),
            ("consciousness.dash", ROOT / "consciousness" / "dashboard.py", "HTTP dashboard :8765"),
            ("consciousness.scribe", ROOT / "consciousness" / "scribe_daemon.py", "Obsidian writer"),
        ],
    },
]


def iter_layers():
    for stage in BOOT_STAGES:
        for layer in stage["layers"]:
            yield layer


LAYERS = list(iter_layers())


def validate_boot_plan():
    """Fail fast on duplicate layer names or unsafe dependency ordering."""
    names = [name for name, _script, _purpose in LAYERS]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise RuntimeError(f"Duplicate supervisor layer(s): {', '.join(duplicates)}")

    order = {name: idx for idx, name in enumerate(names)}
    required_before = [
        ("telemetry.metrics", "sensory.market"),
        ("sensory.market", "cortex.signals"),
        ("immune.risk", "muscle.pnl_sync"),
        ("muscle.pnl_sync", "muscle.position"),
        ("muscle.position", "muscle.main"),
        ("muscle.main", "memory.journal"),
    ]
    for before, after in required_before:
        if before in order and after in order and order[before] > order[after]:
            raise RuntimeError(f"Unsafe supervisor boot order: {before} must start before {after}")

_children = []


def _log_path(name):
    return LOG_DIR / f"{name}.log"


def ensure_health_file():
    if not HEALTH_FILE.exists():
        HEALTH_FILE.write_text(json.dumps({}))


def start_layer(name, script, purpose):
    log = open(_log_path(name), "a")
    env = os.environ.copy()
    # Propagate multisymbol env if set
    if os.getenv("TRADING_OS_MULTISYMBOL"):
        env["TRADING_OS_MULTISYMBOL"] = os.getenv("TRADING_OS_MULTISYMBOL")
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=log,
        stderr=log,
        cwd=str(ROOT),
        env=env,
    )
    ts = time.strftime("%H:%M:%S")
    print(f"  [{ts}] {name:24s}  PID={proc.pid:6d}")
    return proc, log


def shutdown(sig, frame):
    print(f"\n[!] Supervisor shutting down ({'SIGINT' if sig == signal.SIGINT else 'SIGTERM'})...")
    for name, proc, script, purpose, log in _children:
        if proc and proc.poll() is None:
            print(f"    Stopping {name} (PID {proc.pid})...")
            proc.terminate()
    for name, proc, script, purpose, log in _children:
        if proc:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    print("[ok] All layers stopped.")
    sys.exit(0)


def _persist_layer_health():
    block = build_supervisor_block(_children, supervisor_pid=os.getpid())
    merge_supervisor_health(HEALTH_FILE, block)


def restart_dead():
    for i, (name, proc, script, purpose, log) in enumerate(_children):
        if proc is None:
            continue
        rc = proc.poll()
        if rc is not None:
            ts = time.strftime("%H:%M:%S")
            print(f"  [{ts}] {name} EXITED rc={rc}. Restarting...")
            try:
                log.close()
            except Exception:
                pass
            new_proc, new_log = start_layer(name, script, purpose)
            _children[i] = (name, new_proc, script, purpose, new_log)
            publish("ops.layer.restarted", {
                "layer": name,
                "exit_code": rc,
                "pid": new_proc.pid,
                "ts": time.time(),
            })


def boot():
    global _children
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    ensure_health_file()
    validate_boot_plan()

    from kernel.preflight import ensure_boot_safe  # noqa: WPS433

    preflight = ensure_boot_safe(ROOT)
    print(f"  Preflight: ok={preflight.get('ok', preflight.get('preflight_ok'))} mode={preflight.get('trading_mode')}")

    # Display mode banner
    multi_env = os.getenv("TRADING_OS_MULTISYMBOL", "auto")
    print("=" * 62)
    print("  TRADING OS v5.0 — Staged Biological Swarm Architecture")
    print(f"  Multisymbol mode: {multi_env}")
    print("=" * 62)
    print("  Boot stages:")

    for stage_idx, stage in enumerate(BOOT_STAGES, start=1):
        print(f"  Stage {stage_idx}/{len(BOOT_STAGES)}: {stage['name']} — {stage['description']}")
        for name, script, purpose in stage["layers"]:
            if not script.exists():
                print(f"  [SKIP] {name}: {script} not found")
                continue
            proc, log = start_layer(name, script, purpose)
            _children.append((name, proc, script, purpose, log))
            time.sleep(LAYER_START_DELAY_SEC)

        if stage_idx < len(BOOT_STAGES):
            time.sleep(BOOT_STAGE_DELAY_SEC)

    print("=" * 62)
    print("  Trader desk: http://127.0.0.1:8765/ui")
    from paths import logs_dir, nervous_dir

    print(f"  Logs      : {logs_dir()}/")
    print(f"  Bus       : tail -f {nervous_dir() / 'bus.jsonl'}")
    print("  Stop      : Ctrl+C")
    print("=" * 62)
    _persist_layer_health()


def run():
    boot()
    while True:
        time.sleep(5)
        restart_dead()
        _persist_layer_health()


if __name__ == "__main__":
    run()
