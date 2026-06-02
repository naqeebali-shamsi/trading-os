#!/usr/bin/env python3
"""Dream Lab scheduler: continuous light-intensity R&D cycles."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from bus import publish, subscribe  # noqa: E402
from rd.config import load_config  # noqa: E402
from rd.agents.historian import HistorianAgent  # noqa: E402
from rd.agents.trainer import TrainerAgent  # noqa: E402
from rd.agents.news_lab import NewsLabAgent  # noqa: E402
from rd.agents.strategist import StrategistAgent  # noqa: E402
from rd.agents.backtester import BacktesterAgent  # noqa: E402
from rd.agents.auditor import AuditorAgent  # noqa: E402
from rd.agents.promoter import PromoterAgent  # noqa: E402
from rd.agents.explorer import ExplorerAgent  # noqa: E402

STATE_FILE = ROOT / "intel" / "dream_lab_state.json"


def _load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _ready_symbol_count() -> Optional[int]:
    try:
        from ops.readiness_eval import ReadinessOptions, evaluate_readiness
        from ipc_path import get_ipc_dir

        result = evaluate_readiness(ROOT, ReadinessOptions(live=False), ipc_dir=Path(get_ipc_dir()))
        instruments = result.instruments or {}
        return sum(1 for s, row in instruments.items() if row.get("enabled") and row.get("ready"))
    except Exception:
        return None


def _run_agent(agent, task: Optional[dict] = None) -> Dict[str, Any]:
    publish("rd.agent.task", {"agent": agent.name, "task": task or {}})
    result = agent.run(task)
    publish("rd.agent.result", {"agent": agent.name, "ok": result.get("ok", True)})
    for proposal in result.get("proposals") or []:
        publish("rd.promotion.proposed", {"id": proposal.get("id"), "type": proposal.get("type"), "agent": agent.name})
    return result


def _daily_due(state: dict, hour_utc: int) -> bool:
    now = datetime.now(timezone.utc)
    key = f"daily_{now.date().isoformat()}"
    if state.get("last_daily_key") == key:
        return False
    if now.hour < hour_utc:
        return False
    return True


def run_cycle(cycle: str, *, state: Optional[dict] = None) -> Dict[str, Any]:
    cfg = load_config()
    state = state if state is not None else _load_state()
    sched = cfg.get("schedule") or {}
    results: Dict[str, Any] = {"cycle": cycle, "agents": {}}

    publish("rd.dream.cycle.start", {"cycle": cycle, "ts": time.time()})

    if cycle == "hourly":
        results["agents"]["historian"] = _run_agent(HistorianAgent())
        results["agents"]["trainer"] = _run_agent(TrainerAgent())
        results["agents"]["promoter"] = _run_agent(PromoterAgent())
        state["last_hourly_ts"] = time.time()

    elif cycle == "six_hour":
        results["agents"]["news_lab"] = _run_agent(NewsLabAgent())
        results["agents"]["historian"] = _run_agent(HistorianAgent(), {"rebuild_dataset": _ready_symbol_count() == 0})
        state["last_six_hour_ts"] = time.time()

    elif cycle == "daily":
        results["agents"]["historian"] = _run_agent(HistorianAgent(), {"rebuild_dataset": True})
        results["agents"]["trainer"] = _run_agent(TrainerAgent())
        if (cfg.get("auditor") or {}).get("enabled", True):
            results["agents"]["auditor"] = _run_agent(AuditorAgent())
        try:
            from research.strategy_search.config import load_config as load_search_config

            if load_search_config().get("enabled", True):
                results["agents"]["explorer"] = _run_agent(ExplorerAgent())
        except Exception as exc:
            results["strategy_search"] = {"ok": False, "error": str(exc)}
        try:
            from research.validate_walk_forward import run_validation

            report = ROOT / "intel" / "research_validation_dream.json"
            results["walk_forward"] = run_validation("post", report, skip_pit=_ready_symbol_count() not in (0, None))
        except Exception as exc:
            results["walk_forward"] = {"ok": False, "error": str(exc)}
        state["last_daily_key"] = f"daily_{datetime.now(timezone.utc).date().isoformat()}"
        state["last_daily_ts"] = time.time()

    elif cycle == "triggered_rd":
        task = state.pop("pending_rd_task", {})
        strat = _run_agent(StrategistAgent(), task)
        results["agents"]["strategist"] = strat
        for bt in strat.get("backtest_tasks") or []:
            results.setdefault("backtests", []).append(_run_agent(BacktesterAgent(), bt))
        results["agents"]["promoter"] = _run_agent(PromoterAgent())

    publish("rd.dream.cycle.complete", {"cycle": cycle, "ts": time.time(), "agents": list(results.get("agents", {}))})
    _save_state(state)
    return results


def run():
    cfg = load_config()
    sched = cfg.get("schedule") or {}
    hourly = float(sched.get("hourly_sec", 3600))
    six_hour = float(sched.get("six_hour_sec", 21600))
    daily_hour = int(sched.get("daily_utc_hour", 3))

    last_seq = 0
    state = _load_state()
    print("[dream_lab] scheduler started", flush=True)

    while True:
        now = time.time()
        for ev in subscribe("swarm.task", since_seq=last_seq):
            last_seq = max(last_seq, ev.get("seq", 0))
            payload = ev.get("payload") or {}
            if payload.get("type") == "research_request":
                state["pending_rd_task"] = payload
                state["pending_rd_ts"] = now

        if state.get("pending_rd_task") and now - float(state.get("pending_rd_ts") or 0) < 300:
            run_cycle("triggered_rd", state=state)
            state = _load_state()

        if now - float(state.get("last_hourly_ts") or 0) >= hourly:
            run_cycle("hourly", state=state)
            state = _load_state()

        if now - float(state.get("last_six_hour_ts") or 0) >= six_hour:
            run_cycle("six_hour", state=state)
            state = _load_state()

        if _daily_due(state, daily_hour):
            run_cycle("daily", state=state)
            state = _load_state()

        time.sleep(30)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        cycle = sys.argv[2] if len(sys.argv) > 2 else "hourly"
        print(json.dumps(run_cycle(cycle), indent=2))
    else:
        run()
