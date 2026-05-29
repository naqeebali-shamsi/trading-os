#!/usr/bin/env python3
"""
muscle/main.py -- Arms & Legs (v5)
-----------------------------------
Order execution state machine.
Publishes order intent events -> immune gate intercepts -> if passed,
muscle writes to MT5 IPC command file.
Tracks order lifecycle: intent -> pending -> filled/rejected/timeout.

CHANGELOG v5:
- Queue orders instead of overwriting cmd_in.txt (was losing unprocessed commands)
- Added 60s order timeout (was causing phantom pending orders)
- Handles EA error responses (not just fills)
- Uses atomic write (tmp + rename) for cmd_in.txt
- Single consumer of cmd_out.txt (order_router.py removed from supervisor)
- Clears ORDER_STATE of old entries to prevent memory leak
"""
import json, os, time, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, subscribe, current_seq  # noqa
from ipc_path import get_ipc_dir
from ipc_text import read_ipc_text, write_ipc_utf16
from runtime_safety import current_trading_mode, runtime_block_reasons
from muscle import order_execution as exec_core

IPC_DIR = get_ipc_dir()
CMD_FILE = IPC_DIR / "cmd_in.txt"
RESP_FILE = IPC_DIR / "cmd_out.txt"
MODE = current_trading_mode()  # compatibility constant; process path re-validates dynamically

ORDER_STATE = {}  # order_id -> {symbol, side, qty, price, status, ts}
PENDING_QUEUE = []  # unwritten intents waiting for cmd_in.txt to be consumed
ORDER_TIMEOUT_SEC = 60.0
ORDER_CLEANUP_AGE = 3600.0  # clear entries older than 1h

def _read_file_utf8_or_utf16(path):
    return read_ipc_text(path)


def _write_file(path: Path, text: str):
    write_ipc_utf16(path, text)


def _get_simulated_fill(intent):
    """Return a simulated fill response based on current market price."""
    # Try to read current tick from IPC
    tick_dir = get_ipc_dir()
    tick_file = tick_dir / "tick.txt"
    tick = _read_file_utf8_or_utf16(tick_file)
    if tick:
        parts = tick.split(",")
        if len(parts) >= 2:
            bid = float(parts[1])
            ask = float(parts[2])
            price = ask if intent["side"] == "BUY" else bid
            return {
                "type": "fill",
                "order_id": intent["order_id"],
                "retcode": 10009,
                "fill_price": price,
                "symbol": intent["symbol"],
                "side": intent["side"],
                "qty": intent["qty"],
                "simulated": True,
            }
    return {
        "type": "fill",
        "order_id": intent["order_id"],
        "retcode": 10009,
        "fill_price": intent.get("price", 0),
        "symbol": intent["symbol"],
        "side": intent["side"],
        "qty": intent["qty"],
        "tick_old": True,
        "simulated": True,
    }


def process_order_intent(intent):
    """
    intent = {
        order_id, symbol, side, qty, price,
        type="MARKET|LIMIT", tp, sl,
        mode_check=True|False
    }
    """
    runtime_reasons = runtime_block_reasons(ROOT)
    if runtime_reasons:
        order_id = intent.get("order_id", "unknown")
        ORDER_STATE[order_id] = {**intent, "status": "rejected", "ts": time.time(), "error": ";".join(runtime_reasons)}
        exec_core.publish_runtime_reject(publish, order_id, intent, runtime_reasons)
        return

    if not intent.get("mode_check"):
        # Un-vetted order -- send to immune gate first
        publish("muscle.order.intent", intent)
        return

    order_id = intent["order_id"]

    # Check if cmd_in.txt already exists (EA hasn't consumed previous order)
    if CMD_FILE.exists():
        # Queue it — don't overwrite
        PENDING_QUEUE.append(intent)
        ORDER_STATE[order_id] = {**intent, "status": "queued", "ts": time.time()}
        publish("muscle.order.queued", {"order_id": order_id, "queue_len": len(PENDING_QUEUE)})
        return

    ORDER_STATE[order_id] = {**intent, "status": "pending", "ts": time.time()}

    try:
        qty = exec_core.validate_numeric_field("qty", intent.get("qty"))
        sl = exec_core.validate_numeric_field("sl", intent.get("sl"))
        tp = exec_core.validate_numeric_field("tp", intent.get("tp"))
    except ValueError as ve:
        ORDER_STATE[order_id] = {**intent, "status": "rejected", "ts": time.time(), "error": str(ve)}
        publish("muscle.order.rejected", {
            "order_id": order_id,
            "type": "error",
            "error_type": "invalid_intent",
            "message": str(ve),
        })
        return

    cmd_line = exec_core.format_order_cmd(intent, qty, sl, tp)

    try:
        mode = current_trading_mode()
        if mode == "SIMULATION":
            # Simulate execution — do NOT write to cmd_in.txt, fake fill immediately
            ORDER_STATE[order_id]["status"] = "sent"
            publish("muscle.order.sent", {"order_id": order_id, "cmd": "SIM:" + cmd_line})
            # Generate fake fill response
            sim_resp = _get_simulated_fill(intent)
            time.sleep(0.5)  # Simulated latency
            process_fill_update(sim_resp)
        else:
            _write_file(CMD_FILE, cmd_line)
            ORDER_STATE[order_id]["status"] = "sent"
            publish("muscle.order.sent", {"order_id": order_id, "cmd": cmd_line})
    except Exception as e:
        ORDER_STATE[order_id]["status"] = "error"
        publish("muscle.order.error", {"order_id": order_id, "error": str(e)})


def process_fill_update(resp):
    exec_core.process_fill_update(resp, order_state=ORDER_STATE, publish=publish)


def process_error_response(resp):
    exec_core.process_error_response(resp, order_state=ORDER_STATE, publish=publish)


def check_timeouts_and_queue():
    now = time.time()
    # Timeout old pending orders
    timed_out = []
    for oid, state in list(ORDER_STATE.items()):
        if state.get("status") == "pending" or state.get("status") == "sent":
            if now - state["ts"] > ORDER_TIMEOUT_SEC:
                timed_out.append(oid)
                ORDER_STATE[oid]["status"] = "timeout"
                publish("muscle.order.timeout", {"order_id": oid, "elapsed": now - state["ts"]})
        # General cleanup old entries
        if now - state["ts"] > ORDER_CLEANUP_AGE:
            del ORDER_STATE[oid]

    if runtime_block_reasons(ROOT):
        return

    # Process queued orders if cmd_in.txt is gone
    if not CMD_FILE.exists() and PENDING_QUEUE:
        intent = PENDING_QUEUE.pop(0)
        process_order_intent(intent)


def check_responses():
    exec_core.consume_response_file(
        RESP_FILE,
        read_text=_read_file_utf8_or_utf16,
        order_state=ORDER_STATE,
        publish=publish,
    )


def run():
    last_seq = current_seq()
    while True:
        # Subscribe to vetted orders from immune gate
        events = subscribe("immune.pass", since_seq=last_seq)
        for ev in events:
            sequence = ev.get("seq", 0)
            if sequence > last_seq:
                last_seq = sequence
            payload = ev.get("payload", {})
            if payload.get("type") == "order_pass":
                process_order_intent(payload["intent"])

        check_responses()
        check_timeouts_and_queue()

        time.sleep(3)


if __name__ == "__main__":
    run()
