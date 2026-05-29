#!/usr/bin/env python3
"""
memory/learner.py — Meta-Learning / Self-Improvement Loop
---------------------------------------------------------
Closes the feedback loop:
  1. Reads trade outcomes from journal
  2. Computes strategy performance metrics
  3. Decides if a strategy should be promoted, demoted, retired
  4. Triggers swarm R&D when performance drops or gaps appear
  5. Updates strategy weights in cortex/working_memory.json

This is the learning organ. Without it, the system is static.
"""
import json, os, time, sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/"nervous"))
from bus import publish, subscribe

WM_FILE = ROOT / "cortex" / "working_memory.json"
STRAT_FILE = ROOT / "cortex" / "strategies.json"
JOURNAL = ROOT / "memory" / "journal.jsonl"

sys.path.insert(0, str(ROOT))
from cortex.strategy_performance import (  # noqa: E402
    merge_strategy_metrics,
    overlay_declarative_strategies,
)
from trading_profile import learner_auto_apply_enabled  # noqa: E402


def _human_approval_required() -> bool:
    try:
        from rd.config import load_config

        return bool(load_config().get("human_approval_required", True))
    except ImportError:
        return True


def _propose_learner_action(action: dict) -> None:
    try:
        from rd import promotions
    except ImportError:
        return
    sid = action.get("strategy_id")
    act = action.get("action")
    if not sid or act in {"held", None}:
        return
    if act in {"retired", "deactivated_excess", "demoted"}:
        promotions.propose(
            ptype="strategy_active",
            summary=f"Learner suggests deactivating {sid} ({act})",
            patch={"strategy_id": sid, "active": False, "weight": action.get("new_weight", 0.0)},
            evidence=action,
            risk="medium",
            agent="memory.learner",
        )
    elif act == "promoted":
        promotions.propose(
            ptype="strategy_weight",
            summary=f"Learner suggests promoting {sid}",
            patch={"strategy_id": sid, "weight": action.get("new_weight"), "active": True},
            evidence=action,
            risk="low",
            agent="memory.learner",
        )

# Runtime fields persisted to strategy_live_metrics.json (not git-tracked strategies.json)
_LIVE_FIELDS = ("wins", "losses", "sharpe", "weight", "active")

# Thresholds
SHARPE_RETIRE = -0.5          # Retire if Sharpe < -0.5 after 20+ trades
SHARPE_PROMOTE = 1.0          # Increase weight if Sharpe > 1.0
MIN_TRADES_EVAL = 10          # Minimum trades before evaluation
WEIGHT_FLOOR = 0.1            # Minimum weight
WEIGHT_CEIL = 5.0             # Maximum weight
MAX_ACTIVE = 3                # Max simultaneous active strategies
AUTO_APPLY = learner_auto_apply_enabled()


def load_journal():
    if not JOURNAL.exists():
        return []
    entries = []
    with open(JOURNAL) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def strat_performance(strategy_id, entries):
    """Compute win rate, Sharpe, total PnL for a strategy."""
    strat_trades = [e for e in entries if e.get("strategy_id") == strategy_id and e.get("type") == "trade_closed"]
    if not strat_trades:
        return None
    wins = len([t for t in strat_trades if t.get("pnl", 0) > 0])
    losses = len(strat_trades) - wins
    total_pnl = sum(t.get("pnl", 0) for t in strat_trades)
    win_rate = wins / len(strat_trades) if strat_trades else 0
    # Simplified Sharpe: (mean return / std dev) annualized-ish
    returns = [t.get("pnl", 0) for t in strat_trades]
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1) if len(returns) > 1 else 1
    std_r = variance ** 0.5
    sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r > 0 else 0
    return {
        "trades": len(strat_trades),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "sharpe": sharpe,
        "mean_return": mean_r,
    }


def load_strategies():
    if not STRAT_FILE.exists():
        return {}
    try:
        strats = json.loads(STRAT_FILE.read_text())
    except json.JSONDecodeError:
        return {}
    return overlay_declarative_strategies(strats)


def save_strategies(strats):
    """Merge learner runtime metrics into strategy_live_metrics.json by strategy_id."""
    if _human_approval_required() or not AUTO_APPLY:
        publish("memory.learner.observe_only", {
            "strategies": {sid: {k: s.get(k) for k in _LIVE_FIELDS if k in s} for sid, s in strats.items()},
            "auto_apply": False,
            "human_approval_required": _human_approval_required(),
        })
        return
    updates = {}
    for sid, s in strats.items():
        patch = {field: s[field] for field in _LIVE_FIELDS if field in s}
        if patch:
            updates[sid] = patch
    merge_strategy_metrics(updates, source="memory.learner")


def evaluate_and_update():
    strats = load_strategies()
    entries = load_journal()
    actions = []
    
    for sid, s in strats.items():
        perf = strat_performance(sid, entries)
        if not perf:
            continue
        if perf["trades"] < MIN_TRADES_EVAL:
            continue
        
        old_weight = s.get("weight", 1.0)
        old_active = s.get("active", True)
        
        # Decision logic
        if perf["sharpe"] < SHARPE_RETIRE and perf["trades"] >= 20:
            s["active"] = False
            s["weight"] = 0.0
            action = "retired"
        elif perf["sharpe"] > SHARPE_PROMOTE:
            s["weight"] = min(old_weight * 1.2, WEIGHT_CEIL)
            action = "promoted"
        elif perf["win_rate"] < 0.35 and perf["trades"] >= 20:
            s["weight"] = max(old_weight * 0.7, WEIGHT_FLOOR)
            action = "demoted"
        else:
            action = "held"
        
        s["wins"] = perf["wins"]
        s["losses"] = perf["losses"]
        s["sharpe"] = perf["sharpe"]
        
        actions.append({
            "strategy_id": sid,
            "action": action,
            "sharpe": perf["sharpe"],
            "win_rate": perf["win_rate"],
            "old_weight": old_weight,
            "new_weight": s["weight"],
            "active": s["active"],
        })
    
    # Ensure max active strategies
    active = [(sid, s) for sid, s in strats.items() if s.get("active", False)]
    active.sort(key=lambda x: x[1].get("sharpe", 0), reverse=True)
    if len(active) > MAX_ACTIVE:
        for sid, s in active[MAX_ACTIVE:]:
            s["active"] = False
            s["weight"] = 0
            actions.append({"strategy_id": sid, "action": "deactivated_excess", "reason": "max_active_exceeded"})
    
    if _human_approval_required():
        for action in actions:
            if action.get("action") not in {"held", None}:
                _propose_learner_action(action)
        return actions

    save_strategies(strats)
    return actions


def should_request_rnd(actions):
    """Decide if swarm R&D should be triggered."""
    # Trigger if all active strategies have negative Sharpe
    strats = load_strategies()
    active = [s for s in strats.values() if s.get("active", False)]
    if not active:
        return True, "no_active_strategies"
    all_negative = all(s.get("sharpe", 0) < 0 for s in active)
    if all_negative:
        return True, "all_strategies_negative_sharpe"
    
    # Trigger if win rate across all strategies is < 40%
    total_trades = sum(s.get("wins", 0) + s.get("losses", 0) for s in strats.values())
    total_wins = sum(s.get("wins", 0) for s in strats.values())
    if total_trades > 50 and total_wins / total_trades < 0.4:
        return True, "aggregate_win_rate_too_low"
    
    return False, "performance_acceptable"


def run():
    evaluation_interval = 300  # 5 minutes
    while True:
        actions = evaluate_and_update()
        for a in actions:
            publish("memory.learner.action", a)
        
        need_rnd, reason = should_request_rnd(actions)
        if need_rnd:
            publish("swarm.task", {
                "type": "research_request",
                "reason": reason,
                "context": {
                    "timestamp": time.time(),
                    "strategies": load_strategies(),
                }
            })
            publish("memory.learner.rnd_triggered", {"reason": reason})
        
        time.sleep(evaluation_interval)


if __name__ == "__main__":
    run()
