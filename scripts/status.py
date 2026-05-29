#!/usr/bin/env python3
"""Read-only Trading OS operational status snapshot.

Summarizes IPC/MT5 bridge health, stale chart folders, positions, immune
cooldown state, STOP_TRADING, recent order responses, and latest advisory
forecasts. This script never writes commands and never places trades.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from ipc_path import get_ipc_dir  # noqa: E402
from ipc_text import read_ipc_text  # noqa: E402
from immune import main as immune_main  # noqa: E402
from muscle import pnl_sync  # noqa: E402
from ops import bridge_status  # noqa: E402
from scripts import readiness_gate  # noqa: E402
from bus import subscribe  # noqa: E402

IPC = get_ipc_dir()


def _age(path: Path):
    if not path.exists():
        return None
    return max(0.0, time.time() - path.stat().st_mtime)


def _latest_topic_payload(topic: str):
    events = subscribe(topic, limit=1)
    return events[-1].get("payload") if events else None


def bridge_snapshot(max_heartbeat_age=30.0):
    charts = bridge_status.chart_dirs(IPC)
    root_age, root_detail = bridge_status.heartbeat_age(IPC / "heartbeat.txt")
    root_tick_ok, root_tick_detail = bridge_status.tick_ok(IPC / "tick.txt")
    chart_rows = []
    fresh_charts = 0
    for chart in charts:
        age, detail = bridge_status.heartbeat_age(chart / "heartbeat.txt")
        tick_ok, tick_detail = bridge_status.tick_ok(chart / "tick.txt")
        fresh = age is not None and age <= max_heartbeat_age
        fresh_charts += 1 if fresh else 0
        chart_rows.append({
            "name": chart.name,
            "heartbeat_age_sec": age,
            "heartbeat_fresh": fresh,
            "heartbeat_detail": detail,
            "tick_ok": tick_ok,
            "tick": tick_detail,
        })
    mode = readiness_gate.detect_ipc_mode(charts, max_heartbeat_age=max_heartbeat_age)
    return {
        "ipc": str(IPC),
        "mode": mode,
        "root": {
            "heartbeat_age_sec": root_age,
            "heartbeat_fresh": root_age is not None and root_age <= max_heartbeat_age,
            "heartbeat_detail": root_detail,
            "tick_ok": root_tick_ok,
            "tick": root_tick_detail,
        },
        "charts": chart_rows,
        "fresh_chart_count": fresh_charts,
    }


def immune_snapshot(symbol=None):
    journal = immune_main.load_journal()
    limits = immune_main.load_limits()
    intent = {"symbol": symbol} if symbol else {}
    streak, last_loss_ts = immune_main.recent_loss_streak(journal, symbol=symbol)
    reason = immune_main.loss_streak_block_reason(intent, limits, journal)
    return {
        "mode": limits.get("mode"),
        "loss_streak": streak,
        "last_loss_ts": last_loss_ts,
        "cooldown_block": reason,
        "stop_trading": (ROOT / "STOP_TRADING").exists(),
    }


def position_snapshot():
    state = pnl_sync.load_state().get("positions", {})
    positions = list(state.values()) if isinstance(state, dict) else []
    report = pnl_sync.reconcile_positions(positions, previous=state if isinstance(state, dict) else {}, publish_events=False)
    return {
        "open_count": len(positions),
        "floating_pnl": report.get("floating_pnl", 0.0),
        "positions": positions,
    }


def build_status(max_heartbeat_age=30.0, symbol=None):
    return {
        "ts": time.time(),
        "bridge": bridge_snapshot(max_heartbeat_age=max_heartbeat_age),
        "positions": position_snapshot(),
        "immune": immune_snapshot(symbol=symbol),
        "recent_order_response": read_ipc_text(IPC / "cmd_out.txt"),
        "latest_forecast": _latest_topic_payload(f"market.forecast.{symbol}") if symbol else _latest_topic_payload("market.forecast"),
        "latest_event_radar": _latest_topic_payload("macro.event_radar"),
    }


def print_text(status):
    bridge = status["bridge"]
    root = bridge["root"]
    immune = status["immune"]
    positions = status["positions"]
    print("=" * 60)
    print("  Trading OS Status")
    print("=" * 60)
    print(f"IPC: {bridge['ipc']}")
    print(f"Bridge mode: {bridge['mode']['mode']} ({bridge['mode']['detail']})")
    print(f"Root heartbeat: {root['heartbeat_age_sec']:.1f}s" if root["heartbeat_age_sec"] is not None else "Root heartbeat: missing")
    print(f"Root tick: {'ok' if root['tick_ok'] else 'bad'} {root['tick']}")
    print(f"Charts: {bridge['fresh_chart_count']} fresh / {len(bridge['charts'])} present")
    for chart in bridge["charts"]:
        age = chart["heartbeat_age_sec"]
        age_text = f"{age:.1f}s" if age is not None else "missing"
        print(f"  {chart['name']}: hb={age_text} tick={'ok' if chart['tick_ok'] else 'bad'}")
    print(f"Positions: {positions['open_count']} open floating_pnl={positions['floating_pnl']}")
    print(f"Immune: mode={immune['mode']} stop_trading={immune['stop_trading']} loss_streak={immune['loss_streak']} cooldown={immune['cooldown_block'] or 'none'}")
    if status.get("latest_forecast"):
        print(f"Latest forecast: {json.dumps(status['latest_forecast'], sort_keys=True)[:220]}")
    if status.get("latest_event_radar"):
        print(f"Event radar: {json.dumps(status['latest_event_radar'], sort_keys=True)[:220]}")
    if status.get("recent_order_response"):
        print(f"Recent response: {status['recent_order_response'][:220]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Trading OS status")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--symbol", default=None, help="Symbol for forecast/cooldown focus")
    parser.add_argument("--max-heartbeat-age", type=float, default=30.0)
    args = parser.parse_args(argv)
    status = build_status(max_heartbeat_age=args.max_heartbeat_age, symbol=args.symbol)
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print_text(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
