#!/usr/bin/env python3
"""
sensory_multisymbol.py -- Multi-Symbol Sensory Adapter (v1.0)
-------------------------------------------------------------
Scans the IPC directory for chart_* subdirectories and reads
tick.txt / heartbeat.txt from EACH chart in parallel.

Publishes per-symbol tick events to the nervous bus using topic:
  market.tick.<SYMBOL>

Publishes per-chart heartbeat events:
  sensory.mt5.status.chart_<SYMBOL>

CHANGELOG v1.0:
- Discovers symbols dynamically by scanning ipc/chart_*/ subdirs
- Reads all chart tick files every cycle
- Filters stale charts (heartbeat older than threshold)
- Aggregates multi-symbol snapshot to sensory.heartbeat
"""
import json, os, time, sys
from pathlib import Path
from collections import deque

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish  # noqa

from ipc_path import get_ipc_dir  # shared IPC resolver
from ipc_text import read_ipc_text
from tick_quote import enrich_tick_payload, parse_tick_text

IPC_DIR = get_ipc_dir()
SNAPSHOT_DIR = ROOT / "intel" / "market_snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Scan for chart subdirectories
CHART_PREFIX = "chart_"

# heartbeats older than this are considered stale
HEARTBEAT_THRESHOLD_SEC = 90.0

# Shared state keyed by symbol
STATE_PER_CHART = {}  # chart_label -> {"alive": bool, "last_hb_ts": float,
                      #            "last_tick": dict, "tick_buffer": deque}

def _read_file_utf8_or_utf16(path: Path):
    return read_ipc_text(path)


def discover_charts():
    """Return list of chart subdirectories under IPC_DIR."""
    if not IPC_DIR.exists():
        return []
    charts = []
    for entry in IPC_DIR.iterdir():
        if entry.is_dir() and entry.name.startswith(CHART_PREFIX):
            charts.append(entry.name)
    return charts


def read_heartbeat(chart_label: str):
    """Parse heartbeat.txt inside a chart directory."""
    hb_path = IPC_DIR / chart_label / "heartbeat.txt"
    text = _read_file_utf8_or_utf16(hb_path)
    if text is None:
        return None
    try:
        parts = text.split("|")
        if len(parts) >= 2:
            ts = float(parts[0])
            return {"ts": ts, "raw": text}
        # Legacy comma fallback
        parts = text.split(",")
        if len(parts) >= 2:
            ts = float(parts[0])
            return {"ts": ts, "raw": text}
    except Exception:
        pass
    return None


def read_tick(chart_label: str):
    """Parse tick.txt inside a chart directory."""
    tick_path = IPC_DIR / chart_label / "tick.txt"
    text = _read_file_utf8_or_utf16(tick_path)
    if text is None:
        return None
    tick = parse_tick_text(text)
    if not tick:
        return None
    tick["chart"] = chart_label
    return enrich_tick_payload(tick)


def init_chart_state(chart_label: str):
    if chart_label not in STATE_PER_CHART:
        STATE_PER_CHART[chart_label] = {
            "alive": False,
            "last_hb_ts_seen": 0.0,
            "last_hb_read_time": 0.0,
            "last_tick": {},
            "tick_buffer": deque(maxlen=100),
        }


def process_chart(chart_label: str, now: float):
    init_chart_state(chart_label)
    st = STATE_PER_CHART[chart_label]

    # ---------- heartbeat ----------
    hb = read_heartbeat(chart_label)
    sym = chart_label[len(CHART_PREFIX):]  # chart_EURUSD -> EURUSD

    if hb is None:
        if st["alive"]:
            st["alive"] = False
            publish("sensory.mt5.status." + chart_label,
                    {"status": "DOWN", "symbol": sym, "reason": "heartbeat_missing"})
    else:
        ts_advancing = hb["ts"] > st["last_hb_ts_seen"] + 1.0
        time_since_read = now - st["last_hb_read_time"]
        seen_recently = (st["last_hb_read_time"] > 0) and (time_since_read < HEARTBEAT_THRESHOLD_SEC)
        alive = ts_advancing or seen_recently

        if alive and not st["alive"]:
            st["alive"] = True
            publish("sensory.mt5.status." + chart_label,
                    {"status": "UP", "symbol": sym,
                     "ts_delta": hb["ts"] - st["last_hb_ts_seen"],
                     "read_age": time_since_read})
        elif not alive and st["alive"]:
            st["alive"] = False
            publish("sensory.mt5.status." + chart_label,
                    {"status": "STALE", "symbol": sym,
                     "ts_delta": hb["ts"] - st["last_hb_ts_seen"],
                     "read_age": time_since_read})

        st["last_hb_ts_seen"] = hb["ts"]
        st["last_hb_read_time"] = now

    # ---------- tick ----------
    tick = read_tick(chart_label)
    if tick:
        tick = enrich_tick_payload(tick, now=now)
        st["last_tick"] = tick
        st["tick_buffer"].append({"ts": now, **tick})
        # Publish to per-symbol topic
        publish("market.tick." + sym, tick)
        # Also publish to generic market.tick for backward compat
        publish("market.tick", tick)


def run_cycle():
    now = time.time()
    charts = discover_charts()
    total_alive = 0
    total_ticks = 0

    for chart in charts:
        process_chart(chart, now)
        st = STATE_PER_CHART[chart]
        if st["alive"]:
            total_alive += 1
        total_ticks += len(st["tick_buffer"])

    # Aggregate heartbeat across all charts
    publish("sensory.heartbeat", {
        "charts_discovered": len(charts),
        "charts_alive": total_alive,
        "total_ticks_buffered": total_ticks,
        "chart_labels": charts,
    })

    # Write aggregated snapshot
    try:
        snapshot = {}
        for chart, st in STATE_PER_CHART.items():
            if st["last_tick"]:
                snapshot[chart] = st["last_tick"]
        snap_path = SNAPSHOT_DIR / "multisymbol_latest.json"
        tmp_path = snap_path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(snapshot, f)
        os.replace(str(tmp_path), str(snap_path))
    except Exception:
        pass


def run():
    while True:
        run_cycle()
        time.sleep(5)


if __name__ == "__main__":
    run()
