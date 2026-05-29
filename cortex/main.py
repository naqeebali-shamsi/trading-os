#!/usr/bin/env python3
"""
cortex/main.py -- Cerebrum
--------------------------
LLM brain. Maintains working memory, detects decision thresholds,
invokes LLM reasoning, dispatches decisions, tracks outcomes.

Decision triggers:
  - Market regime change (volatility spike, trend break)
  - Signal fire + no matching strategy
  - Risk anomaly detected
  - End-of-day portfolio review
  - Swarm backtest completion

Falls back to rule-based if LLM unavailable.
"""
import json, os, time, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, subscribe  # noqa
from cortex.agent import AgentBrain  # noqa
from cortex.brain_signal_context import build_signal_context, detect_signal_brain_trigger  # noqa
from cortex.brain_market_context import build_market_structure_context  # noqa
from trading_profile import env_str  # noqa
try:
    from scripts import readiness_gate  # noqa
except Exception:  # pragma: no cover - cortex must still run if ops helper is unavailable
    readiness_gate = None

WM_FILE = ROOT / "cortex" / "working_memory.json"
STRAT_FILE = ROOT / "cortex" / "strategies.json"
SECRETS_TEMPLATE = ROOT / "config" / "secrets.yaml.template"
SECRETS_FILE = ROOT / "config" / "secrets.yaml"

# Decision thresholds
DECISION_WINDOW_SEC = 300       # Don't LLM-decide more often than every 5 min
VOLATILITY_ALERT_Z = 2.5        # ATR z-score to trigger regime review
MAX_UNSIGNALED_TRADES = 3       # After 3 unmodeled trades, ask LLM for new strategy
AGENT_DECISION_MODE = env_str("TRADING_OS_LLM_DECISION_MODE", production="LIVE", development="ADVISORY").upper()
NEWS_CONTEXT_TOPICS = {
    "cortex.news",
    "market.news",
    "news.decision",
    "macro.news.oil",
    "macro.news.tech",
    "macro.news.geopolitics",
    "macro.news.health",
    "macro.news.rates",
}


def load_working_memory():
    if not WM_FILE.exists():
        return {
            "last_llm_call": 0,
            "context": {},
            "active_strategies": {},
            "pending_decisions": [],
        }
    try:
        return json.loads(WM_FILE.read_text())
    except json.JSONDecodeError:
        return {"last_llm_call": 0, "context": {}, "active_strategies": {}, "pending_decisions": []}


def save_working_memory(wm):
    WM_FILE.write_text(json.dumps(wm, indent=2))


def load_strategies():
    if not STRAT_FILE.exists():
        default = {
            "MA_CROSS_SMA9_21": {"weight": 1.0, "wins": 0, "losses": 0, "sharpe": 0, "active": True},
            "RSI_OVERSOLD_30": {"weight": 1.0, "wins": 0, "losses": 0, "sharpe": 0, "active": True},
        }
        STRAT_FILE.write_text(json.dumps(default, indent=2))
        return default
    try:
        return json.loads(STRAT_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def load_api_key():
    """Read OpenRouter key from secrets.yaml."""
    if not SECRETS_FILE.exists():
        return None
    for line in SECRETS_FILE.read_text().splitlines():
        if "openrouter" in line.lower() and ":" in line:
            parts = line.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip().strip('"').strip("'")
    return None


def build_brain_context(wm, health, strats, recent_events, open_positions, trigger):
    """Build sanitized runtime context for the guarded AgentBrain path."""
    recent = recent_events[-20:]
    latest_tick = None
    macro_policy = {}
    for ev in reversed(recent):
        if ev.get("topic") == "market.tick" and not latest_tick:
            latest_tick = ev.get("payload", {})
        if ev.get("topic") == "risk.macro_policy" and not macro_policy:
            macro_policy = ev.get("payload", {}) or {}
    signals = build_signal_context(recent_events)
    market_structure = build_market_structure_context(health=health, recent_events=recent_events)
    return {
        "market_snapshot": latest_tick or {},
        "positions": open_positions or [],
        "signals": signals,
        "market_structure": market_structure,
        "constraints": {
            "default_action": "HOLD",
            "requires_stop_loss": True,
            "decision_mode": AGENT_DECISION_MODE,
            "trigger": trigger,
            "active_strategy_count": len(strats or {}),
            "health_ok": bool(health.get("ok", True)) if isinstance(health, dict) else True,
            "macro": macro_policy,
        },
        "news": [ev.get("payload", {}) for ev in recent if ev.get("topic") in NEWS_CONTEXT_TOPICS],
        "forecasts": [
            ev.get("payload", {})
            for ev in recent
            if ev.get("topic") == "market.forecast" or str(ev.get("topic", "")).startswith("market.forecast.")
        ][-5:],
        "macro_events": [
            ev.get("payload", {})
            for ev in recent
            if ev.get("topic") == "macro.event_radar" or ev.get("topic") == "macro.event_radar.alert"
        ][-5:],
    }


def live_runtime_health():
    """Return fresh runtime health for cortex decisions.

    `kernel/health.json` can be stale if the optional watchdog is not running.
    Cortex should not tell the LLM the system is unhealthy based on an old file
    while readiness probes show the bridge and instruments are active.
    """
    health = {"ok": True, "source": "cortex_default"}
    try:
        if readiness_gate is not None:
            charts = readiness_gate.chart_dirs()
            ipc_mode = readiness_gate.detect_ipc_mode(charts, max_heartbeat_age=30.0)
            bridge_ok = ipc_mode.get("mode") in {"root", "chart", "mixed"}
            health = {
                "ok": bridge_ok,
                "source": "readiness_gate",
                "ipc_mode": ipc_mode,
                "chart_count": len(charts),
            }
        else:
            health = json.loads((ROOT / "kernel" / "health.json").read_text())
    except Exception as exc:
        health = {"ok": True, "source": "health_fallback", "warning": str(exc)}
    return health


def publish_brain_result(result, trigger="unknown"):
    """Publish auditable brain result without bypassing guardrails."""
    payload = result.as_dict()
    payload["trigger"] = trigger
    publish("cortex.brain.result", payload)

    guard = result.guard.as_dict() if result.guard else {}
    decision = guard.get("decision") or {}
    publish("cortex.decision_guard", guard)

    llm = result.llm.as_dict() if result.llm else {}
    llm_error_code = llm.get("error_code")

    if not result.ok or not result.guard or not result.guard.ok:
        publish("cortex.decision", {
            "action": "HOLD",
            "reasoning": result.error or (result.guard.reason if result.guard else "brain_not_ok"),
            "mode": guard.get("mode"),
            "trigger": trigger,
            "blocked_by_hook": result.blocked_by_hook,
            "llm_ok": llm.get("ok"),
            "llm_error": llm.get("error"),
            "llm_error_code": llm_error_code,
        })
        return None

    action = decision.get("action", "HOLD")
    if action != "NEW_ORDER":
        publish("cortex.decision", {"action": action, "reasoning": decision.get("reasoning"), "trigger": trigger})
        return None

    # Only a guard-approved NEW_ORDER reaches the existing intent topic.
    intent = {
        "order_id": f"brain_{int(time.time())}",
        "symbol": decision.get("symbol"),
        "side": decision.get("side"),
        "qty": decision.get("qty"),
        "type": "MARKET",
        "sl": decision.get("sl"),
        "tp": decision.get("tp"),
        "mode_check": False,
        "strategy_id": decision.get("strategy_id"),
        "source": "guarded_agent_brain",
    }
    publish("muscle.order.intent", intent)
    publish("cortex.decision", {"action": "NEW_ORDER", "intent": intent, "trigger": trigger})
    return intent


def operator_human_approved():
    return os.getenv("TRADING_OS_HUMAN_APPROVED", "0").strip().lower() in {"1", "true", "yes", "approved"}


def agent_brain_decide(wm, health, strats, recent_events, open_positions, trigger, *, brain=None):
    ctx = build_brain_context(wm, health, strats, recent_events, open_positions, trigger)
    brain = brain or AgentBrain()
    result = brain.run(
        market_snapshot=ctx["market_snapshot"],
        news=ctx["news"],
        positions=ctx["positions"],
        forecasts=ctx["forecasts"],
        macro_events=ctx["macro_events"],
        signals=ctx["signals"],
        market_structure=ctx["market_structure"],
        constraints=ctx["constraints"],
        provider=os.getenv("TRADING_OS_LLM_PROVIDER"),
        model=os.getenv("TRADING_OS_LLM_MODEL"),
        decision_mode=AGENT_DECISION_MODE,
        human_approved=operator_human_approved(),
        correlation_id=f"cortex-{trigger}-{int(time.time())}",
        trigger=trigger,
    )
    publish_brain_result(result, trigger=trigger)
    return result


def detect_decision_needed(wm, recent, strats):
    now = time.time()
    if now - wm.get("last_llm_call", 0) < DECISION_WINDOW_SEC:
        return False, "cooldown"

    # Signal-engine activity (near-miss, emitted, pattern review)
    needed, trigger = detect_signal_brain_trigger(recent)
    if needed:
        return True, trigger

    # Count risk blocks
    blocks = [e for e in recent if e.get("topic") == "immune.block"]
    if len(blocks) >= 3:
        return True, "multiple_risk_blocks"

    # Unmodeled signals
    signals = [e for e in recent if e.get("topic") == "market.signal"]
    if len(signals) > MAX_UNSIGNALED_TRADES:
        return True, "unmodeled_signals"

    # Volatility spike (mock -- would come from market.tick events)
    ticks = [e for e in recent if e.get("topic") == "market.tick"]
    if len(ticks) >= 2:
        prices = [t["payload"].get("bid", 0) for t in ticks if "payload" in t]
        if len(prices) >= 2:
            volatility = abs(prices[-1] - prices[0]) / (prices[0] or 1) * 100
            if volatility > 1.5:
                return True, "volatility_spike"

    # No trades for long time but market is moving
    fills = [e for e in recent if e.get("topic") == "muscle.order.filled"]
    if not fills and len(ticks) > 20:
        return True, "stale_strategy"

    return False, "no_trigger"


def rule_fallback(trigger, wm, strats, result=None):
    """Deterministic fallback when AgentBrain is unavailable or fails."""
    llm = result.llm.as_dict() if result and result.llm else {}
    publish("cortex.fallback", {
        "trigger": trigger,
        "reason": "agent_brain_unavailable",
        "underlying_error": result.error if result else None,
        "llm_error": llm.get("error"),
        "llm_error_code": llm.get("error_code"),
        "blocked_by_hook": result.blocked_by_hook if result else None,
    })
    reasoning = llm.get("error") or (result.error if result else f"fallback: {trigger}")
    publish("cortex.decision", {
        "action": "HOLD",
        "reasoning": reasoning,
        "trigger": trigger,
        "llm_ok": llm.get("ok"),
        "llm_error": llm.get("error"),
        "llm_error_code": llm.get("error_code"),
    })


def run():
    wm = load_working_memory()
    strats = load_strategies()
    while True:
        recent = subscribe("market.tick", limit=50)
        recent += subscribe("muscle.order.filled", limit=20)
        recent += subscribe("immune.block", limit=20)
        recent += subscribe("sensory.mt5.status", limit=10)
        for topic in NEWS_CONTEXT_TOPICS:
            recent += subscribe(topic, limit=5)
        recent += subscribe("macro.event_radar", limit=5)
        recent += subscribe("macro.event_radar.alert", limit=5)
        recent += subscribe("market.forecast", limit=5)
        recent += subscribe("market.signal", limit=10)
        recent += subscribe("market.signal.evaluation", limit=40)
        recent += subscribe("market.signal.candidate", limit=10)
        recent += subscribe("market.signal.blocked", limit=10)

        needed, trigger = detect_decision_needed(wm, recent, strats)
        if needed:
            wm["last_llm_call"] = time.time()
            health = live_runtime_health()
            try:
                from muscle import pnl_sync

                open_positions = list((pnl_sync.load_state().get("positions") or {}).values())
            except Exception:
                open_positions = []
            result = agent_brain_decide(wm, health, strats, recent, open_positions, trigger)
            if not result.ok:
                rule_fallback(trigger, wm, strats, result)
            else:
                # Record in working memory
                wm["pending_decisions"].append({
                    "ts": time.time(),
                    "decision": result.as_dict(),
                    "trigger": trigger,
                })
                wm["pending_decisions"] = wm["pending_decisions"][-20:]

            save_working_memory(wm)

        # Live strategy metrics (wins/losses/weight/sharpe) owned by introspect/score_strategies.py

        time.sleep(10)


if __name__ == "__main__":
    run()
