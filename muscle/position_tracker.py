#!/usr/bin/env python3
"""Track open positions, PnL, exposure."""
import json, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"nervous"))
from bus import publish, subscribe

positions = {}  # order_id -> position
last_seq = 0


def position_from_fill(payload):
    """Build the position.opened payload, carrying order_id so the dashboard
    trade-lifecycle join can correlate it with muscle.order.filled."""
    return {
        "open": True,
        "order_id": payload.get("order_id"),
        "fill_price": payload.get("fill_price"),
        "symbol": payload.get("symbol"),
        "side": payload.get("side"),
        "qty": payload.get("qty"),
    }


def run():
    global last_seq
    while True:
        evs = subscribe("muscle.order.filled", since_seq=last_seq)
        for ev in evs:
            seq=ev.get("seq",0)
            if seq>last_seq: last_seq=seq
            p=ev.get("payload",{})
            oid = p.get("order_id")
            pos = position_from_fill(p)
            positions[oid] = pos
            publish("position.opened", pos)
        if positions:
            publish("position.heartbeat", {"count":len(positions), "symbols":[p["symbol"] for p in positions.values()]})
        time.sleep(5)
if __name__ == "__main__":
    run()
