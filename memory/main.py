#!/usr/bin/env python3
"""
memory/main.py -- Hippocampus
-----------------------------
Persists every trade event, maintains equity curve, strategy performance vectors.
Tracks what's working and what's broken.
Feeds historical context to cortex.
"""
import json, os, time, sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, subscribe  # noqa

JOURNAL_FILE = ROOT / "memory" / "journal.jsonl"
REFLECTION_FILE = ROOT / "memory" / "reflection.jsonl"
EQUITY_FILE = ROOT / "memory" / "equity.jsonl"


def append_event(path, event):
    with open(path, "a") as f:
        f.write(json.dumps(event) + "\n")


def compute_equity():
    if not JOURNAL_FILE.exists():
        return []
    entries = []
    with open(JOURNAL_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # Build equity curve
    balance = 10000.0  # starting demo
    curve = [{"ts": entries[0]["ts"], "balance": balance}] if entries else []
    for e in entries:
        if e.get("type") == "trade_closed":
            balance += e.get("pnl", 0)
            curve.append({"ts": e["ts"], "balance": balance})
    return curve


def record_trade_opened(fill_event):
    entry = {
        "ts": time.time(),
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "type": "trade_opened",
        "order_id": fill_event.get("order_id"),
        "symbol": fill_event.get("symbol"),
        "side": fill_event.get("side"),
        "qty": fill_event.get("qty"),
        "fill_price": fill_event.get("fill_price"),
    }
    append_event(JOURNAL_FILE, entry)
    try:
        from memory import post_trade_learning
        post_trade_learning.capture_entry_context({**fill_event, **entry})
    except Exception as exc:
        publish("memory.trade_entry_context.error", {"error": str(exc), "order_id": entry.get("order_id")})
    publish("memory.trade_opened", entry)


def close_event_from_position(payload):
    """Map a position.closed payload (from pnl_sync reconciliation) into the
    close-event shape record_trade_closed expects. pnl_sync is the only live
    publisher of closes, so this is the bridge that feeds memory.trade_outcome."""
    pnl = payload.get("pnl")
    if pnl is None:
        pnl = (
            float(payload.get("profit") or 0)
            + float(payload.get("swap") or 0)
            + float(payload.get("commission") or 0)
        )
    return {
        "order_id": payload.get("order_id") or payload.get("comment") or payload.get("ticket"),
        "ticket": payload.get("ticket"),
        "symbol": payload.get("symbol"),
        "side": payload.get("side"),
        "qty": payload.get("qty") or payload.get("volume"),
        "pnl": pnl,
        "exit_price": payload.get("exit_price") or payload.get("current_price"),
    }


def record_trade_closed(close_event):
    entry = {
        "ts": time.time(),
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "type": "trade_closed",
        "order_id": close_event.get("order_id"),
        "symbol": close_event.get("symbol"),
        "pnl": close_event.get("pnl", 0),
        "exit_price": close_event.get("exit_price"),
    }
    append_event(JOURNAL_FILE, entry)
    try:
        from memory import post_trade_learning
        post_trade_learning.record_outcome(entry, publish_event=True)
    except Exception as exc:
        publish("memory.trade_outcome.error", {"error": str(exc), "order_id": entry.get("order_id")})
    curve = compute_equity()
    if curve:
        append_event(EQUITY_FILE, curve[-1])
    publish("memory.trade_closed", entry)


def run():
    last_seq_fill = 0
    last_seq_close = 0
    last_seq_pos_close = 0
    recorded_closes = set()  # dedup order_ids across both close topics

    def _record_close(close_event):
        oid = close_event.get("order_id")
        if oid is not None and oid in recorded_closes:
            return
        if oid is not None:
            recorded_closes.add(oid)
        record_trade_closed(close_event)

    while True:
        fills = subscribe("muscle.order.filled", since_seq=last_seq_fill)
        for ev in fills:
            seq = ev.get("seq", 0)
            if seq > last_seq_fill:
                last_seq_fill = seq
            record_trade_opened(ev.get("payload", {}))

        closes = subscribe("muscle.order.closed", since_seq=last_seq_close)
        for ev in closes:
            seq = ev.get("seq", 0)
            if seq > last_seq_close:
                last_seq_close = seq
            _record_close(ev.get("payload", {}))

        # pnl_sync publishes position.closed when a reconciled position disappears;
        # this is the only live close signal, so bridge it into trade outcomes.
        pos_closes = subscribe("position.closed", since_seq=last_seq_pos_close)
        for ev in pos_closes:
            seq = ev.get("seq", 0)
            if seq > last_seq_pos_close:
                last_seq_pos_close = seq
            _record_close(close_event_from_position(ev.get("payload", {})))

        time.sleep(3)


if __name__ == "__main__":
    run()
