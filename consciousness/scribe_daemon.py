#!/usr/bin/env python3
"""consciousness/scribe_daemon.py -- Obsidian Scribe

Subscribes to nervous-bus events and writes structured notes into the
Obsidian vault. Runs as a supervisor-managed daemon layer.

Events handled:
  muscle.order.filled      -> 02-Trades/
  immune.block             -> 05-Immune/
  immune.pass              -> 05-Immune/ (audit)
  market.tick              -> 03-Market/ (throttled)
  sensory.mt5.status       -> 06-System/ (DOWN alerts)
  cortex.decision          -> 01-Daily/
"""
import sys, time, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from bus import subscribe
from obsidian_bridge import write_trade, write_alert, write_market_snapshot, write_system_event, append_note

# Throttle market ticks: write at most 1 snapshot per 5 minutes per symbol
_last_tick_write = {}
TICK_COOLDOWN = 300  # seconds


def handle_muscle_filled(payload):
    if not isinstance(payload, dict):
        return
    write_trade(payload)


def handle_immune(payload):
    if not isinstance(payload, dict):
        return
    level = payload.get("level", "INFO")
    write_alert(payload)


def handle_market_tick(payload):
    if not isinstance(payload, dict):
        return
    sym = payload.get("symbol")
    now = time.time()
    if sym:
        last = _last_tick_write.get(sym, 0)
        if now - last < TICK_COOLDOWN:
            return
        _last_tick_write[sym] = now
    write_market_snapshot(payload)


def handle_system(payload):
    if not isinstance(payload, dict):
        return
    status = payload.get("status")
    if status in ("DOWN", "STALE"):
        write_system_event(f"MT5 {status}: {payload}", level=status)


def handle_cortex(payload):
    if not isinstance(payload, dict):
        return
    decision = payload.get("decision", json.dumps(payload)[:200])
    append_note("daily", "Cortex Decision", f"```json\n{decision}\n```", tags=["cortex", "decision"])


def run():
    last_seq = 0
    while True:
        # Muscle fills
        for ev in subscribe("muscle.order.filled", since_seq=last_seq, limit=50):
            seq = ev.get("seq", 0)
            if seq > last_seq:
                last_seq = seq
            handle_muscle_filled(ev.get("payload", {}))

        # Immune events
        for ev in subscribe("immune.block", since_seq=last_seq, limit=10):
            seq = ev.get("seq", 0)
            if seq > last_seq:
                last_seq = seq
            handle_immune(ev.get("payload", {}))

        # Market ticks
        for ev in subscribe("market.tick", since_seq=last_seq, limit=20):
            seq = ev.get("seq", 0)
            if seq > last_seq:
                last_seq = seq
            handle_market_tick(ev.get("payload", {}))

        # System status
        for ev in subscribe("sensory.mt5.status", since_seq=last_seq, limit=5):
            seq = ev.get("seq", 0)
            if seq > last_seq:
                last_seq = seq
            handle_system(ev.get("payload", {}))

        # Cortex decisions
        for ev in subscribe("cortex.decision", since_seq=last_seq, limit=10):
            seq = ev.get("seq", 0)
            if seq > last_seq:
                last_seq = seq
            handle_cortex(ev.get("payload", {}))

        time.sleep(10)


if __name__ == "__main__":
    run()
