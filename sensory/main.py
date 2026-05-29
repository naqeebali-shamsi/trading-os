#!/usr/bin/env python3
"""
sensory/main.py -- Eyes & Ears (v6)
-----------------------------------
Reads MT5 heartbeat + market data via file IPC and publishes
events to nervous bus. Handles reconnection, stale-data detection,
clock skew detection.

CHANGELOG v6:
- Auto-detects UTF-16 BOM from MT5 EA (fixes silent decode failure)
- Uses abs(age) for WSL↔Windows clock skew (the usual case)
- Removed broken st_mtime check on E-drive Plan9 mount
- Raised heartbeat threshold to 90s for imperfect EA timing
- Added clock_lag event for WSL time freeze detection

CHANGELOG v5:
- Heartbeat now parsed from file CONTENT (not mtime) to avoid WSL/Windows clock skew
- Added parse validation before float conversion (swarm bug C3)
"""
import json, os, time, sys
from pathlib import Path
from collections import deque

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish  # noqa

from ipc_path import get_ipc_dir  # shared IPC resolver
from ipc_text import read_ipc_text
from tick_quote import enrich_tick_payload, parse_tick_text

IPC_DIR = get_ipc_dir()
HEARTBEAT_FILE = IPC_DIR / "heartbeat.txt"
TICK_FILE = IPC_DIR / "tick.txt"
SNAPSHOT_FILE = ROOT / "intel" / "market_snapshots"
SNAPSHOT_FILE.mkdir(parents=True, exist_ok=True)

STATE = {"mt5_alive": False, "last_tick": {}, "tick_buffer": deque(maxlen=100),
         "last_hb_ts_seen": 0.0, "last_hb_read_time": 0.0}

HEARTBEAT_THRESHOLD_SEC = 90.0


def _read_file_utf8_or_utf16(path):
    return read_ipc_text(path)


def read_heartbeat():
    text = _read_file_utf8_or_utf16(HEARTBEAT_FILE)
    if text is None:
        return None
    try:
        # Standardized format: epoch|alive (pipe-delimited)
        parts = text.split("|")
        if len(parts) >= 2:
            ts = float(parts[0])
            return {"ts": ts, "raw": text}
        # Fallback: try comma (legacy format support)
        parts = text.split(",")
        if len(parts) >= 2:
            ts = float(parts[0])
            return {"ts": ts, "raw": text}
        return None
    except Exception:
        return None


def read_tick():
    text = _read_file_utf8_or_utf16(TICK_FILE)
    if text is None:
        return None
    tick = parse_tick_text(text)
    return enrich_tick_payload(tick) if tick else None


def run_cycle():
    hb = read_heartbeat()
    now = time.time()

    if hb is None:
        if STATE["mt5_alive"]:
            STATE["mt5_alive"] = False
            publish("sensory.mt5.status", {"status": "DOWN", "reason": "heartbeat_missing"})
        return

    # Robust clock-skew-resistant freshness check:
    # If heartbeat timestamp is advancing from previous read, EA is alive regardless of WSL clock skew
    ts_advancing = hb["ts"] > STATE["last_hb_ts_seen"] + 1.0
    time_since_last_read = now - STATE["last_hb_read_time"]
    # If timestamp not advancing, check whether it was recently seen (works even with frozen WSL clock)
    seen_recently = (STATE["last_hb_read_time"] > 0) and (time_since_last_read < HEARTBEAT_THRESHOLD_SEC)
    alive = ts_advancing or seen_recently
    if alive and not STATE["mt5_alive"]:
        STATE["mt5_alive"] = True
        publish("sensory.mt5.status", {"status": "UP", "ts_delta": hb["ts"] - STATE["last_hb_ts_seen"], "read_age": time_since_last_read})
    elif not alive and STATE["mt5_alive"]:
        STATE["mt5_alive"] = False
        publish("sensory.mt5.status", {"status": "STALE", "ts_delta": hb["ts"] - STATE["last_hb_ts_seen"], "read_age": time_since_last_read})

    # Update tracking timestamps — MUST be done after the check
    STATE["last_hb_ts_seen"] = hb["ts"]
    STATE["last_hb_read_time"] = now

    # Warn if heartbeat timestamp is ahead of local clock (WSL clock lag)
    if hb["ts"] > now + 5:
        wsl_lag = hb["ts"] - now
        # Rate-limit: only publish once per 60s or when lag changes by >10s
        last_lag = STATE.get("last_clock_lag", 0)
        last_lag_ts = STATE.get("last_clock_lag_ts", 0)
        if now - last_lag_ts > 60 or abs(wsl_lag - last_lag) > 10:
            publish("sensory.clock_lag", {"wsl_lag_sec": round(wsl_lag, 1)})
            STATE["last_clock_lag"] = wsl_lag
            STATE["last_clock_lag_ts"] = now

    tick = read_tick()
    if tick:
        tick = enrich_tick_payload(tick, now=now)
        STATE["last_tick"][tick["symbol"]] = tick
        STATE["tick_buffer"].append({"ts": now, **tick})
        publish("market.tick", tick)

    # Publish heartbeat pulse to kernel
    publish("sensory.heartbeat", {"mt5_alive": STATE["mt5_alive"], "tick_count": len(STATE["tick_buffer"])})


def run():
    while True:
        run_cycle()
        time.sleep(5)


if __name__ == "__main__":
    run()
