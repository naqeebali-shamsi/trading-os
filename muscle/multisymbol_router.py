#!/usr/bin/env python3
"""
muscle/multisymbol_router.py -- Per-Chart Order Router (v2 Citadel)
--------------------------------------------------------------------
FIXES from Adversarial Review (v1):
- [CRITICAL-1] PENDING_QUEUE uses deque with safe pop_left instead of remove-in-loop
- [CRITICAL-2] Per-chart threading.Lock for cmd_in.txt atomicity (check+write is locked)
- [CRITICAL-3] Max queue depth (100), rejects with queue_overflow
- [HIGH-1] Chart list cached, refreshed every 30s only
- [HIGH-2] Symbol normalization strips MT5 suffixes (m, .micro, #, ., etc.)
- [HIGH-3] SIMULATION mode: no sleep, adds random simulated slippage variance
- [MEDIUM-1] Filled orders cleaned after 60s; rejected/timeout kept for 1h
- [MEDIUM-2] Removed dangerous re-publish on !mode_check
- [MEDIUM-3] Validates numeric fields (qty, sl, tp) — rejects non-numeric intent
"""
import json, os, time, sys, threading, re
from pathlib import Path
from collections import deque

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, subscribe, current_seq  # noqa
from ipc_path import get_ipc_dir
from ipc_text import read_ipc_text, write_ipc_utf16
from runtime_safety import current_trading_mode, runtime_block_reasons
from immune.provenance import verify_proof  # noqa
from muscle import order_lifecycle
from muscle import order_execution as exec_core
try:
    from cortex.instrument_registry import load_registry  # noqa
except Exception:  # Keep router operational if registry import fails during emergency debugging.
    load_registry = None

IPC_DIR = get_ipc_dir()
MODE = current_trading_mode()  # compatibility constant; process path re-validates dynamically

# Ephemeral router cache (chart, ts, fill fields). Durable status: order_lifecycle.
ORDER_STATE = {}
PENDING_QUEUE = deque(maxlen=100)   # bounded queue
ORDER_TIMEOUT_SEC = 60.0
ORDER_CLEANUP_FILLED_AGE = 60.0     # filled orders cleaned quickly
ORDER_CLEANUP_ERROR_AGE = 3600.0    # rejected/timeout kept for debug
MAX_QUEUE_DEPTH = 100

CHART_PREFIX = "chart_"
_chart_cache = []       # cached chart list
_chart_cache_ts = 0.0
CHART_CACHE_TTL = 30.0
_chart_locks = {}       # chart_label -> threading.Lock()

def _chart_lock(chart: str) -> threading.Lock:
    if chart not in _chart_locks:
        _chart_locks[chart] = threading.Lock()
    return _chart_locks[chart]


def _read_file_utf8_or_utf16(path):
    return read_ipc_text(path)


def _write_file(path: Path, text: str):
    write_ipc_utf16(path, text)


def discover_charts():
    global _chart_cache, _chart_cache_ts
    now = time.time()
    if _chart_cache and (now - _chart_cache_ts) < CHART_CACHE_TTL:
        return list(_chart_cache)
    if not IPC_DIR.exists():
        return []
    _chart_cache = [e.name for e in IPC_DIR.iterdir() if e.is_dir() and e.name.startswith(CHART_PREFIX)]
    _chart_cache_ts = now
    return list(_chart_cache)


def _normalize_symbol(symbol: str) -> str:
    """Strip MT5 suffixes: EURUSDm -> EURUSD, XAUUSD. -> XAUUSD, EURUSD# -> EURUSD"""
    # Remove known suffixes
    s = re.sub(r'[mM]$', '', symbol)          # micro suffix
    s = re.sub(r'[\.#]$', '', s)              # trailing dot or hash
    s = re.sub(r'\.(micro|mini|pro)$', '', s, flags=re.I)
    return s


def chart_dir_for_symbol(symbol: str):
    """Return chart label for a symbol. Handles normalization + case variants."""
    broker_symbol = None
    if load_registry is not None:
        try:
            broker_symbol = load_registry().resolve_broker_symbol(symbol)
        except Exception:
            broker_symbol = None
    normalized = _normalize_symbol(broker_symbol or symbol)
    charts = discover_charts()

    # Try normalized as-is
    label = CHART_PREFIX + normalized
    if label in charts:
        return label

    # Try uppercase
    label_u = CHART_PREFIX + normalized.upper()
    if label_u in charts:
        return label_u

    # Try lowercase
    label_l = CHART_PREFIX + normalized.lower()
    if label_l in charts:
        return label_l

    # Fuzzy: find chart whose suffix matches the base symbol
    # EURUSD matches chart_EURUSDm if m is the only variant
    for c in charts:
        suffix = c[len(CHART_PREFIX):]
        if suffix.upper() == normalized.upper():
            return c
        if _normalize_symbol(suffix).upper() == normalized.upper():
            return c

    return None


def _get_simulated_fill(intent):
    import random
    sym = intent["symbol"]
    chart = chart_dir_for_symbol(sym)
    tick_file = IPC_DIR / chart / "tick.txt" if chart else IPC_DIR / "tick.txt"
    tick = _read_file_utf8_or_utf16(tick_file)
    base_price = 0.0
    if tick:
        parts = tick.split(",")
        if len(parts) >= 3:
            bid = float(parts[1])
            ask = float(parts[2])
            base_price = ask if intent["side"] == "BUY" else bid
    if base_price == 0.0:
        base_price = intent.get("price", 0.0) or 0.0

    # Simulated slippage: ±1 pip equivalent (very rough)
    slippage = (random.random() - 0.5) * 0.0002
    fill_price = round(base_price + slippage, 5)

    return {
        "type": "fill",
        "order_id": intent["order_id"],
        "retcode": 10009,
        "fill_price": fill_price,
        "symbol": sym,
        "side": intent["side"],
        "qty": intent["qty"],
        "simulated": True,
        "tick_source": str(tick_file) if tick else "fallback",
    }


def _publish_lifecycle_duplicate(order_id, duplicate):
    publish("muscle.order.rejected", {
        "order_id": order_id,
        "type": "error",
        "error_type": "duplicate_order_lifecycle",
        "reason": "duplicate_order_lifecycle",
        "message": f"Duplicate active order blocked by {duplicate.get('kind')}",
        "duplicate": {k: v for k, v in duplicate.items() if k != "prior"},
    })
    publish("muscle.order.duplicate_dropped", {"order_id": order_id, **{k: v for k, v in duplicate.items() if k != "prior"}})


def process_order_intent(intent, *, from_queue=False):
    order_id = intent.get("order_id", "unknown")
    runtime_reasons = runtime_block_reasons(ROOT)
    if runtime_reasons:
        ORDER_STATE[order_id] = {**intent, "status": "rejected", "ts": time.time(), "error": ";".join(runtime_reasons)}
        exec_core.publish_runtime_reject(publish, order_id, intent, runtime_reasons)
        return

    # [FIX MEDIUM-2] No re-publish on !mode_check — immune failed to vet, reject immediately
    if not intent.get("mode_check"):
        exec_core.reject_not_vetted(publish, ORDER_STATE, order_id, intent)
        return

    proof_ok, proof_reason = verify_proof(intent)
    if not proof_ok:
        ORDER_STATE[order_id] = {**intent, "status": "rejected", "ts": time.time(),
                                 "error": proof_reason}
        publish("muscle.order.rejected", {
            "order_id": order_id,
            "type": "error",
            "error_type": "invalid_immune_provenance",
            "reason": proof_reason,
            "message": f"Order immune provenance rejected: {proof_reason}",
        })
        return

    # Deny-by-default instrument validation. Tests/offline operation still work
    # for enabled configured symbols, while unknown/disabled symbols are blocked
    # before they can touch IPC command files.
    if load_registry is not None:
        try:
            registry = load_registry()
            inst_result = registry.validate_order(intent, require_enabled=True)
            if not inst_result.ok:
                ORDER_STATE[order_id] = {**intent, "status": "rejected", "ts": time.time(), "error": inst_result.reason}
                publish("muscle.order.rejected", {
                    "order_id": order_id,
                    "type": "error",
                    "error_type": inst_result.reason,
                    "message": f"Instrument validation failed: {inst_result.reason}",
                    **inst_result.as_dict(),
                })
                return
            intent["symbol"] = inst_result.symbol or intent.get("symbol")
            if inst_result.details.get("rounded_qty") is not None:
                intent["qty"] = inst_result.details["rounded_qty"]
        except Exception as exc:
            ORDER_STATE[order_id] = {**intent, "status": "rejected", "ts": time.time(), "error": f"instrument_registry_error:{exc}"}
            publish("muscle.order.rejected", {
                "order_id": order_id,
                "type": "error",
                "error_type": "instrument_registry_error",
                "message": str(exc),
            })
            return

    # [FIX MEDIUM-3] Validate all numeric fields
    try:
        qty = exec_core.validate_numeric_field("qty", intent.get("qty"))
        sl = exec_core.validate_numeric_field("sl", intent.get("sl"))
        tp = exec_core.validate_numeric_field("tp", intent.get("tp"))
        intent["qty"] = qty
        intent["sl"] = sl
        intent["tp"] = tp
    except ValueError as ve:
        ORDER_STATE[order_id] = {**intent, "status": "rejected", "ts": time.time(), "error": str(ve)}
        publish("muscle.order.rejected", {
            "order_id": order_id,
            "type": "error",
            "error_type": "invalid_intent",
            "message": str(ve),
        })
        return
    symbol = intent["symbol"]
    chart = chart_dir_for_symbol(symbol)

    if not from_queue:
        duplicate = order_lifecycle.duplicate_for(intent)
        if duplicate:
            ORDER_STATE[order_id] = {**intent, "status": "rejected", "ts": time.time(), "error": "duplicate_order_lifecycle"}
            _publish_lifecycle_duplicate(order_id, duplicate)
            return
        order_lifecycle.record_transition(order_id, "accepted", intent=intent)

    if not chart:
        ORDER_STATE[order_id] = {
            **intent, "status": "error", "ts": time.time(),
            "error": f"no_chart_for_{symbol}",
        }
        publish("muscle.order.rejected", {
            "order_id": order_id,
            "type": "error",
            "error_type": "chart_not_found",
            "message": f"No chart directory found for {symbol} (normalized: {_normalize_symbol(symbol)})",
        })
        order_lifecycle.record_transition(order_id, "rejected", intent=intent, details={"reason": "chart_not_found", "chart": chart})
        return

    cmd_file = IPC_DIR / chart / "cmd_in.txt"

    # [FIX CRITICAL-2] Lock per chart for check-and-write atomicity
    lock = _chart_lock(chart)
    with lock:
        if cmd_file.exists():
            # [FIX CRITICAL-3] Bounded queue — reject if full
            if len(PENDING_QUEUE) >= MAX_QUEUE_DEPTH:
                ORDER_STATE[order_id] = {**intent, "status": "rejected", "ts": time.time(),
                                         "chart": chart, "error": "queue_overflow"}
                publish("muscle.order.rejected", {
                    "order_id": order_id,
                    "type": "error",
                    "error_type": "queue_overflow",
                    "message": f"Queue depth={len(PENDING_QUEUE)} exceeds max={MAX_QUEUE_DEPTH}",
                    "chart": chart,
                })
                return
            PENDING_QUEUE.append({"intent": intent, "chart": chart})
            ORDER_STATE[order_id] = {**intent, "status": "queued", "ts": time.time(), "chart": chart}
            order_lifecycle.record_transition(order_id, "queued", intent=intent, details={"chart": chart, "queue_len": len(PENDING_QUEUE)})
            publish("muscle.order.queued", {"order_id": order_id, "queue_len": len(PENDING_QUEUE), "chart": chart})
            return

        ORDER_STATE[order_id] = {**intent, "status": "pending", "ts": time.time(), "chart": chart}
        order_lifecycle.record_transition(order_id, "pending_ipc_write", intent=intent, details={"chart": chart})

        cmd_line = exec_core.format_order_cmd(intent, qty, sl, tp)

        try:
            mode = current_trading_mode()
            if mode == "SIMULATION":
                ORDER_STATE[order_id]["status"] = "sent"
                order_lifecycle.record_transition(order_id, "sent", intent=intent, details={"chart": chart, "mode": mode, "simulated": True})
                publish("muscle.order.sent", {"order_id": order_id, "cmd": "SIM:" + cmd_line, "chart": chart})
                # [FIX HIGH-3] No sleep in SIM mode
                sim_resp = _get_simulated_fill(intent)
                process_fill_update(sim_resp)
            else:
                _write_file(cmd_file, cmd_line)
                ORDER_STATE[order_id]["status"] = "sent"
                order_lifecycle.record_transition(order_id, "sent", intent=intent, details={"chart": chart, "mode": mode})
                publish("muscle.order.sent", {"order_id": order_id, "cmd": cmd_line, "chart": chart})
        except Exception as e:
            ORDER_STATE[order_id]["status"] = "error"
            order_lifecycle.record_transition(order_id, "ipc_write_failed", intent=intent, details={"chart": chart, "error": str(e)})
            publish("muscle.order.error", {"order_id": order_id, "error": str(e), "chart": chart})


def process_fill_update(resp):
    exec_core.process_fill_update(resp, order_state=ORDER_STATE, publish=publish, lifecycle=order_lifecycle)


def process_error_response(resp):
    exec_core.process_error_response(resp, order_state=ORDER_STATE, publish=publish, lifecycle=order_lifecycle)


def check_timeouts_and_queue():
    now = time.time()
    for oid in order_lifecycle.iter_tracked_order_ids(ORDER_STATE):
        state = ORDER_STATE.get(oid, {})
        status = order_lifecycle.authoritative_status(oid, state)
        ts = state.get("ts") or order_lifecycle.get_order(oid).get("updated_ts") or now
        if status in ("pending", "sent", "pending_ipc_write", "accepted", "queued"):
            if now - ts > ORDER_TIMEOUT_SEC:
                elapsed = now - ts
                chart = state.get("chart") or (order_lifecycle.get_order(oid).get("details") or {}).get("chart")
                if status in ("sent", "pending_ipc_write"):
                    order_lifecycle.mark_unknown_if_sent(oid, elapsed=elapsed, chart=chart)
                    order_lifecycle.sync_router_row(oid, ORDER_STATE)
                    publish("muscle.order.timeout", {"order_id": oid, "elapsed": elapsed, "chart": chart, "status": "unknown_broker_state"})
                else:
                    order_lifecycle.record_transition(oid, "rejected", details={"reason": "pending_timeout", "elapsed": elapsed, "chart": chart})
                    order_lifecycle.sync_router_row(oid, ORDER_STATE)
                    publish("muscle.order.timeout", {"order_id": oid, "elapsed": elapsed, "chart": chart, "status": "pending_timeout"})

    for oid in list(ORDER_STATE.keys()):
        state = ORDER_STATE[oid]
        status = order_lifecycle.authoritative_status(oid, state)
        age = now - state.get("ts", now)
        if status == "filled" and age > ORDER_CLEANUP_FILLED_AGE:
            del ORDER_STATE[oid]
        elif status in ("error", "timeout", "rejected", "unknown_broker_state", "ipc_write_failed") and age > ORDER_CLEANUP_ERROR_AGE:
            del ORDER_STATE[oid]

    if runtime_block_reasons(ROOT):
        return

    # Drain queue — one per chart per cycle
    if PENDING_QUEUE:
        chart_blocked = set()
        # [FIX CRITICAL-1] Iterate over a copy, pop from left safely
        for _ in range(len(PENDING_QUEUE)):
            if not PENDING_QUEUE:
                break
            item = PENDING_QUEUE[0]
            chart = item["chart"]
            cmd_file = IPC_DIR / chart / "cmd_in.txt"
            if not cmd_file.exists() and chart not in chart_blocked:
                PENDING_QUEUE.popleft()
                process_order_intent(item["intent"], from_queue=True)
                chart_blocked.add(chart)
                # Only process one per chart per cycle
                continue
            elif cmd_file.exists():
                # This chart is blocked — skip all subsequent items for it
                chart_blocked.add(chart)
            # If chart not blocked but cmd exists now, move to back of queue for retry
            PENDING_QUEUE.rotate(-1)


def check_responses():
    charts = discover_charts()

    def _on_corrupt(chart_name, exc, resp_file):
        publish("muscle.order.error", {"chart": chart_name, "error": f"corrupt_resp: {exc}"})
        try:
            resp_file.unlink(missing_ok=True)
        except OSError:
            pass

    for chart in charts:
        exec_core.consume_response_file(
            IPC_DIR / chart / "cmd_out.txt",
            read_text=_read_file_utf8_or_utf16,
            order_state=ORDER_STATE,
            publish=publish,
            chart=chart,
            lifecycle=order_lifecycle,
            on_corrupt=_on_corrupt,
        )


def bootstrap_lifecycle_from_bus(limit=500):
    """Seed durable lifecycle from recent bus events after first deployment/restart.

    This avoids starting with an empty dedupe ledger while positions/orders already
    exist from before the lifecycle module was introduced.
    """
    for ev in subscribe("immune.pass", limit=limit):
        payload = ev.get("payload", {}) or {}
        if payload.get("type") != "order_pass":
            continue
        intent = payload.get("intent") or {}
        oid = intent.get("order_id")
        if oid and not order_lifecycle.get_order(oid):
            order_lifecycle.record_transition(oid, "accepted", intent=intent, details={"bootstrap_seq": ev.get("seq")})
    for topic, status in (
        ("muscle.order.sent", "sent"),
        ("muscle.order.filled", "filled"),
        ("muscle.order.rejected", "rejected"),
        ("muscle.order.timeout", "unknown_broker_state"),
    ):
        for ev in subscribe(topic, limit=limit):
            payload = ev.get("payload", {}) or {}
            oid = payload.get("order_id")
            if not oid:
                continue
            details = {"bootstrap_seq": ev.get("seq"), "topic": topic}
            if status == "unknown_broker_state" and payload.get("status") != "unknown_broker_state":
                details["timeout_status"] = payload.get("status")
            order_lifecycle.record_transition(oid, status, details=details)


def run():
    bootstrap_lifecycle_from_bus()
    # Start at the current bus tail so a router restart does not replay old
    # immune.pass events and duplicate already-sent broker orders.
    last_seq = current_seq()
    while True:
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
