#!/usr/bin/env python3
"""
sensory/combined_feed.py — Tick-to-Candle Pipeline (v2.1 Citadel)
------------------------------------------------------------------
FIXES from Adversarial Review:
- [CRITICAL-9] Re-evaluates multisymbol mode every 60s (not just at startup)
- [HIGH-9] Only calls OHLC.on_tick if tick content actually changed (dedup)
- [HIGH-10] Legacy main.py import guarded; fallback readers inline
- watch: removed legacy import dependency entirely
"""
import json, time, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish

try:
    from .ohlc_engine import ENGINE as OHLC
except ImportError:  # Support direct script-style execution from sensory/.
    from ohlc_engine import ENGINE as OHLC
from ipc_path import get_ipc_dir
from ipc_text import read_ipc_text
from tick_quote import enrich_tick_payload, parse_tick_text

IPC_DIR = get_ipc_dir()
CHART_PREFIX = "chart_"
USE_MULTISYMBOL = False
LAST_MODE_CHECK = 0.0
MODE_CHECK_INTERVAL = 60.0

SNAPSHOT_DIR = ROOT / "intel" / "market_snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _read_file_utf8_or_utf16(path):
    return read_ipc_text(path)


def discover_charts():
    if not IPC_DIR.exists():
        return []
    return [e.name for e in IPC_DIR.iterdir() if e.is_dir() and e.name.startswith(CHART_PREFIX)]


def read_heartbeat_chart(chart_label: str):
    hb_path = IPC_DIR / chart_label / "heartbeat.txt"
    text = _read_file_utf8_or_utf16(hb_path)
    if text is None:
        return None
    try:
        parts = text.split("|")
        if len(parts) >= 2:
            return {"ts": float(parts[0]), "raw": text, "chart": chart_label}
        parts = text.split(",")
        if len(parts) >= 2:
            return {"ts": float(parts[0]), "raw": text, "chart": chart_label}
    except Exception:
        pass
    return None


def read_tick_chart(chart_label: str):
    tick_path = IPC_DIR / chart_label / "tick.txt"
    text = _read_file_utf8_or_utf16(tick_path)
    if text is None:
        return None
    tick = parse_tick_text(text)
    if not tick:
        return None
    tick["chart"] = chart_label
    return enrich_tick_payload(tick)


def read_heartbeat_legacy():
    hb_path = IPC_DIR / "heartbeat.txt"
    text = _read_file_utf8_or_utf16(hb_path)
    if text is None:
        return None
    try:
        parts = text.split("|") if "|" in text else text.split(",")
        if len(parts) >= 2:
            return {"ts": float(parts[0]), "raw": text}
    except Exception:
        pass
    return None


def read_tick_legacy():
    tick_path = IPC_DIR / "tick.txt"
    text = _read_file_utf8_or_utf16(tick_path)
    if text is None:
        return None
    tick = parse_tick_text(text)
    return enrich_tick_payload(tick) if tick else None


def multisymbol_forced_mode():
    force = os.getenv("TRADING_OS_MULTISYMBOL", "auto").lower()
    if force in ("1", "true", "yes", "multi", "multisymbol"):
        return True
    if force in ("0", "false", "no", "legacy", "single"):
        return False
    return None


def should_use_multisymbol(charts):
    forced = multisymbol_forced_mode()
    if forced is not None:
        return forced
    return len(charts) >= 1


STATE_PER_CHART = {}
HEARTBEAT_THRESHOLD_SEC = 90.0
LAST_TICK_HASH = {}  # chart_label -> hash of last tick content
LEGACY_STATE = {"mt5_alive": False, "last_hb_ts": 0.0, "last_hb_read": 0.0}


def init_chart(chart_label):
    if chart_label not in STATE_PER_CHART:
        STATE_PER_CHART[chart_label] = {
            "alive": False,
            "last_hb_ts_seen": 0.0,
            "last_hb_read_time": 0.0,
            "last_tick": {},
        }


def _tick_hash(tick: dict) -> str:
    """Hash tick content to detect duplication."""
    return hash(f"{tick.get('bid')}:{tick.get('ask')}:{tick.get('time')}")


def process_chart(chart_label, now):
    init_chart(chart_label)
    st = STATE_PER_CHART[chart_label]
    hb = read_heartbeat_chart(chart_label)
    sym = chart_label[len(CHART_PREFIX):]

    if hb is None:
        if st["alive"]:
            st["alive"] = False
            publish("sensory.mt5.status." + chart_label, {"status": "DOWN", "symbol": sym, "reason": "heartbeat_missing"})
    else:
        ts_advancing = hb["ts"] > st["last_hb_ts_seen"] + 1.0
        time_since = now - st["last_hb_read_time"]
        seen_recently = (st["last_hb_read_time"] > 0) and (time_since < HEARTBEAT_THRESHOLD_SEC)
        alive = ts_advancing or seen_recently
        if alive and not st["alive"]:
            st["alive"] = True
            publish("sensory.mt5.status." + chart_label, {"status": "UP", "symbol": sym})
        elif not alive and st["alive"]:
            st["alive"] = False
            publish("sensory.mt5.status." + chart_label, {"status": "STALE", "symbol": sym})
        st["last_hb_ts_seen"] = hb["ts"]
        st["last_hb_read_time"] = now

    tick = read_tick_chart(chart_label)
    if tick:
        tick = enrich_tick_payload(tick, now=now)
        # [FIX HIGH-9] Skip duplicate ticks (same bid/ask/time)
        thash = _tick_hash(tick)
        if LAST_TICK_HASH.get(chart_label) == thash:
            return  # duplicate, skip
        LAST_TICK_HASH[chart_label] = thash

        st["last_tick"] = tick
        publish("market.tick." + sym, tick)
        publish("market.tick", tick)

        completed = OHLC.on_tick(symbol=tick["symbol"], bid=tick["bid"], ask=tick["ask"], ts=now)
        for candle in completed:
            publish("candle.close", candle)
            publish("candle.close." + sym, candle)


def run_legacy_cycle(now):
    hb = read_heartbeat_legacy()
    if hb is not None:
        ts = hb.get("ts", 0)
        if ts > LEGACY_STATE["last_hb_ts"] + 1.0:
            if not LEGACY_STATE["mt5_alive"]:
                LEGACY_STATE["mt5_alive"] = True
                publish("sensory.mt5.status", {"status": "UP", "ts": ts})
            LEGACY_STATE["last_hb_ts"] = ts
            LEGACY_STATE["last_hb_read"] = now
        elif now - LEGACY_STATE["last_hb_ts"] > 90:
            if LEGACY_STATE["mt5_alive"]:
                LEGACY_STATE["mt5_alive"] = False
                publish("sensory.mt5.status", {"status": "STALE"})

    tick = read_tick_legacy()
    if tick:
        tick = enrich_tick_payload(tick, now=now)
        sym = tick.get("symbol", "")
        thash = _tick_hash(tick)
        if LAST_TICK_HASH.get("legacy") == thash:
            return
        LAST_TICK_HASH["legacy"] = thash

        LEGACY_STATE["last_tick"] = tick
        publish("market.tick", tick)
        completed = OHLC.on_tick(symbol=sym, bid=tick["bid"], ask=tick["ask"], ts=now)
        for candle in completed:
            publish("candle.close", candle)
        publish("sensory.heartbeat", {"mt5_alive": True, "candle_count": len(completed)})


def run():
    global USE_MULTISYMBOL, LAST_MODE_CHECK

    charts = discover_charts()
    USE_MULTISYMBOL = should_use_multisymbol(charts)
    print(f"[combined_feed] Mode: {'multisymbol' if USE_MULTISYMBOL else 'legacy'} (charts: {charts})")

    while True:
        now = time.time()

        # [FIX CRITICAL-9] Re-evaluate mode periodically
        if now - LAST_MODE_CHECK > MODE_CHECK_INTERVAL:
            charts = discover_charts()
            new_mode = should_use_multisymbol(charts)
            if new_mode != USE_MULTISYMBOL:
                USE_MULTISYMBOL = new_mode
                print(f"[combined_feed] Mode switched to: {'multisymbol' if USE_MULTISYMBOL else 'legacy'} (charts: {charts})")
            LAST_MODE_CHECK = now

        if USE_MULTISYMBOL:
            total_alive = 0
            for chart in charts:
                process_chart(chart, now)
                if STATE_PER_CHART.get(chart, {}).get("alive"):
                    total_alive += 1
            publish("sensory.heartbeat", {
                "mode": "multisymbol",
                "charts_discovered": len(charts),
                "charts_alive": total_alive,
                "chart_labels": charts,
            })
        else:
            run_legacy_cycle(now)

        # Snapshot
        try:
            snapshot = {}
            if USE_MULTISYMBOL:
                for chart, st in STATE_PER_CHART.items():
                    if st.get("last_tick"):
                        snapshot[chart] = st["last_tick"]
            else:
                sym = LEGACY_STATE.get("last_tick", {}).get("symbol")
                if sym:
                    snapshot = {sym: LEGACY_STATE["last_tick"]}
            snap_path = SNAPSHOT_DIR / "latest.json"
            tmp_path = snap_path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump({"ts": now, "ticks": snapshot}, f)
            os.replace(str(tmp_path), str(snap_path))
        except Exception:
            pass

        time.sleep(5)


if __name__ == "__main__":
    run()
