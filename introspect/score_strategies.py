#!/usr/bin/env python3
"""
introspect/score_strategies.py — Track A (v2 Citadel)
------------------------------------------------------
FIXES from Adversarial Review (v1):
- [CRITICAL-6] Persist last_seq to disk; only read NEW events each cycle
- [CRITICAL-7] Order_id to strategy_id mapping via registry lookup (not underscore split)
- [CRITICAL-8] Only write strategy_registry.json if scores actually changed (checksum)
- [HIGH-7] Tail journal reads via file offset persistence (not full read)
- [HIGH-8] Slippage normalized by ATR (per-instrument pip value)
- [MEDIUM-6] Alert on corrupt registry — backup + rewrite
"""
import json, time, sys, os
from pathlib import Path
from collections import defaultdict, deque

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, subscribe  # noqa

SCORE_INTERVAL = int(os.getenv("INTROSPECT_INTERVAL", "60"))
JOURNAL = ROOT / "memory" / "journal.jsonl"
STRAT_FILE = ROOT / "cortex" / "strategies.json"
sys.path.insert(0, str(ROOT))
from cortex.strategy_performance import merge_strategy_metrics  # noqa: E402

# Persistent state
STATE_FILE = ROOT / "introspect" / ".state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

METRICS = defaultdict(lambda: {
    "filled": 0, "rejected": 0, "timeout": 0, "slippage_sum": 0.0,
    "atr_sum": 0.0, "latency_sum": 0.0, "trades": deque(maxlen=200), "signals": 0,
})


def _load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_seq_filled": 0, "last_seq_rejected": 0, "last_seq_timeout": 0,
            "last_seq_signal": 0, "journal_offset": 0, "last_registry_checksum": ""}


def _save_state(state):
    STATE_FILE.write_text(json.dumps(state))


def _read_journal_tail(offset: int) -> tuple:
    """Read journal.jsonl from byte offset to end. Return (entries, new_offset)."""
    if not JOURNAL.exists():
        return [], 0
    try:
        with open(JOURNAL, "rb") as f:
            f.seek(offset)
            data = f.read().decode("utf-8", errors="ignore")
            new_offset = f.tell()
        entries = []
        for line in data.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries, new_offset
    except Exception:
        return [], offset


def _extract_strategy_id(ev_payload: dict) -> str:
    """Extract strategy_id from bus event payload."""
    # Order events have order_id like STRATEGY_12345
    oid = ev_payload.get("order_id", "")
    # Check if order_id starts with a known strategy id
    if oid:
        strats = _load_strategies_raw()
        for sid in strats:
            if oid.startswith(sid + "_") or oid.startswith(sid + "-"):
                return sid
    # Fallback: explicit strategy_id field
    sid = ev_payload.get("strategy_id", "")
    if sid:
        return sid
    return "UNKNOWN"


def _load_strategies_raw():
    if not STRAT_FILE.exists():
        return {}
    try:
        return json.loads(STRAT_FILE.read_text())
    except json.JSONDecodeError:
        # [FIX MEDIUM-6] Corrupt registry alert
        backup = STRAT_FILE.with_suffix(".json.bak")
        try:
            STRAT_FILE.rename(backup)
        except Exception:
            pass
        backup.write_text(STRAT_FILE.read_text())
        publish("alert.routed", {
            "severity": "critical",
            "source": "introspect",
            "message": "strategy_registry.json corrupt — backed up to .bak",
        })
        STRAT_FILE.write_text("{}")
        return {}


def score_from_signals(state: dict):
    """Read ONLY new bus events since last cycle."""
    # Filled
    events = subscribe("muscle.order.filled", since_seq=state["last_seq_filled"])
    for ev in events:
        p = ev.get("payload", {})
        sid = _extract_strategy_id(p)
        m = METRICS[sid]
        m["filled"] += 1
        expected = p.get("price", 0)
        actual = p.get("fill_price", expected)
        # [FIX HIGH-8] Normalize slippage by ATR
        atr = p.get("atr", 0.001) or 0.001
        if expected and actual:
            raw_slip = abs(actual - expected)
            m["slippage_sum"] += raw_slip / max(atr, 0.0001)
            m["atr_sum"] += atr
        state["last_seq_filled"] = max(state["last_seq_filled"], ev.get("seq", 0))

    # Rejected
    events = subscribe("muscle.order.rejected", since_seq=state["last_seq_rejected"])
    for ev in events:
        p = ev.get("payload", {})
        sid = _extract_strategy_id(p)
        METRICS[sid]["rejected"] += 1
        state["last_seq_rejected"] = max(state["last_seq_rejected"], ev.get("seq", 0))

    # Timeout
    events = subscribe("muscle.order.timeout", since_seq=state["last_seq_timeout"])
    for ev in events:
        p = ev.get("payload", {})
        sid = _extract_strategy_id(p)
        METRICS[sid]["timeout"] += 1
        state["last_seq_timeout"] = max(state["last_seq_timeout"], ev.get("seq", 0))

    # Signals
    events = subscribe("market.signal", since_seq=state["last_seq_signal"])
    for ev in events:
        p = ev.get("payload", {})
        sid = p.get("strategy_id", "UNKNOWN")
        METRICS[sid]["signals"] += 1
        state["last_seq_signal"] = max(state["last_seq_signal"], ev.get("seq", 0))


def score_from_journal(state: dict):
    """Read only NEW journal entries since last offset."""
    entries, new_offset = _read_journal_tail(state["journal_offset"])
    state["journal_offset"] = new_offset
    for e in entries:
        if e.get("type") != "trade_closed":
            continue
        sid = e.get("strategy_id", "UNKNOWN")
        pnl = e.get("pnl", 0)
        m = METRICS[sid]
        m["trades"].append({
            "ts": e.get("ts", time.time()),
            "pnl": pnl,
            "symbol": e.get("symbol", ""),
        })


def compute_scores() -> dict:
    report = {}
    for sid, m in METRICS.items():
        total = m["filled"] + m["rejected"] + m["timeout"]
        if total == 0:
            continue
        win_trades = [t for t in m["trades"] if t["pnl"] > 0]
        total_pnl = sum(t["pnl"] for t in m["trades"])
        win_rate = len(win_trades) / len(m["trades"]) if m["trades"] else 0
        # [FIX HIGH-8] ATR-normalized slippage
        avg_norm_slippage = (m["slippage_sum"] / m["filled"]) if m["filled"] else 0

        fill_rate = m["filled"] / total if total else 0
        report[sid] = {
            "total_signals": m["signals"],
            "filled": m["filled"],
            "rejected": m["rejected"],
            "timeout": m["timeout"],
            "fill_rate": round(fill_rate, 3),
            "win_rate": round(win_rate, 3),
            "total_pnl": round(total_pnl, 2),
            "avg_norm_slippage": round(avg_norm_slippage, 4),
            "trade_count": len(m["trades"]),
            # Composite: win_rate*0.3 + fill_rate*0.3 + (1 - norm_slip)*0.2 + freq*0.2
            "score": round(
                win_rate * 0.3 +
                fill_rate * 0.3 +
                max(0, 1 - avg_norm_slippage) * 0.2 +
                min(1, m["signals"] / 10) * 0.2, 3),
        }
    return report


def promote_demote(report: dict) -> bool:
    """Persist live strategy metrics without mutating tracked strategy config.

    `cortex/strategies.json` is declarative config and is tracked in git. Live
    fill-rate/PnL telemetry is runtime state, so write it to an ignored sidecar
    file to keep the working tree clean while preserving dashboard/introspection
    visibility.

    Returns True when the runtime metrics sidecar was updated.
    """
    strats = _load_strategies_raw()
    if not strats:
        return False

    updates = {}
    for sid, scores in report.items():
        if sid in strats:
            updates[sid] = {
                "live_score": scores["score"],
                "live_fill_rate": scores["fill_rate"],
                "live_win_rate": scores["win_rate"],
                "live_pnl": scores["total_pnl"],
            }

    return merge_strategy_metrics(updates, source="introspect.score")


def run_cycle():
    state = _load_state()
    score_from_signals(state)
    score_from_journal(state)
    report = compute_scores()
    if report:
        publish("introspect.score_update", {"strategies": report, "ts": time.time()})
        written = promote_demote(report)
        sorted_sids = sorted(report.items(), key=lambda x: x[1]["score"], reverse=True)
        publish("introspect.strategy_report", {
            "top": sorted_sids[:3],
            "bottom": sorted_sids[-3:] if len(sorted_sids) >= 3 else [],
            "registry_updated": written,
            "ts": time.time(),
        })
    _save_state(state)


def run():
    print(f"[introspect] Strategy scoring daemon started, interval={SCORE_INTERVAL}s")
    while True:
        try:
            run_cycle()
        except Exception as e:
            publish("cortex.fallback", {"layer": "introspect", "action": "crash", "error": str(e)})
        time.sleep(SCORE_INTERVAL)


if __name__ == "__main__":
    run()
