"""Shared order execution helpers for muscle routers.

Both ``muscle.main`` (root IPC) and ``muscle.multisymbol_router`` (per-chart IPC)
use this module for response handling, command formatting, and common reject paths.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

PublishFn = Callable[[str, Dict[str, Any]], None]


def format_order_cmd(intent: Dict[str, Any], qty: float, sl: float, tp: float) -> str:
    order_id = intent["order_id"]
    return "ORDER,{symbol},{side},{qty},{sl},{tp},{oid}".format(
        symbol=intent["symbol"],
        side=intent["side"],
        qty=qty,
        sl=sl or 0,
        tp=tp or 0,
        oid=order_id,
    )


def validate_numeric_field(name: str, value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Field '{name}' must be numeric, got {type(value).__name__}: {value!r}") from exc


def publish_runtime_reject(publish: PublishFn, order_id: str, intent: Dict[str, Any], reasons: list) -> None:
    publish(
        "muscle.order.rejected",
        {
            "order_id": order_id,
            "type": "error",
            "error_type": "runtime_safety_block",
            "reasons": reasons,
            "message": "Runtime safety gate blocked order execution",
            "intent": {k: intent.get(k) for k in ("symbol", "side", "qty", "strategy_id")},
        },
    )


def reject_not_vetted(
    publish: PublishFn,
    order_state: Dict[str, Dict[str, Any]],
    order_id: str,
    intent: Dict[str, Any],
) -> None:
    order_state[order_id] = {**intent, "status": "rejected", "ts": time.time(), "error": "not_vetted"}
    publish(
        "muscle.order.rejected",
        {
            "order_id": order_id,
            "type": "error",
            "error_type": "not_vetted",
            "message": "Order passed to muscle without immune vetting",
        },
    )


def process_fill_update(
    resp: Dict[str, Any],
    *,
    order_state: Dict[str, Dict[str, Any]],
    publish: PublishFn,
    lifecycle=None,
) -> None:
    order_id = resp.get("order_id")
    if not order_id:
        return
    known = order_id in order_state
    if lifecycle is not None:
        known = known or bool(lifecycle.get_order(order_id))
    if not known:
        publish("muscle.order.unknown_response", {"order_id": order_id, "response": resp, "response_type": "fill"})
        return
    order_state.setdefault(order_id, {"order_id": order_id, "status": "sent", "ts": time.time()})
    order_state[order_id]["status"] = "filled"
    order_state[order_id]["fill_price"] = resp.get("fill_price")
    order_state[order_id]["retcode"] = resp.get("retcode")
    if lifecycle is not None:
        lifecycle.record_transition(
            order_id,
            "filled",
            details={"fill_price": resp.get("fill_price"), "retcode": resp.get("retcode")},
        )
        lifecycle.sync_router_row(order_id, order_state)
    publish("muscle.order.filled", {"order_id": order_id, **resp})


def process_error_response(
    resp: Dict[str, Any],
    *,
    order_state: Dict[str, Dict[str, Any]],
    publish: PublishFn,
    lifecycle=None,
) -> None:
    order_id = resp.get("order_id")
    if not order_id:
        return
    known = order_id in order_state
    if lifecycle is not None:
        known = known or bool(lifecycle.get_order(order_id))
    if not known:
        publish("muscle.order.unknown_response", {"order_id": order_id, "response": resp, "response_type": "error"})
        return
    order_state.setdefault(order_id, {"order_id": order_id, "status": "sent", "ts": time.time()})
    order_state[order_id]["status"] = "rejected"
    order_state[order_id]["error_type"] = resp.get("error_type")
    order_state[order_id]["error_msg"] = resp.get("message")
    if lifecycle is not None:
        lifecycle.record_transition(
            order_id,
            "rejected",
            details={"error_type": resp.get("error_type"), "message": resp.get("message")},
        )
        lifecycle.sync_router_row(order_id, order_state)
    publish("muscle.order.rejected", {"order_id": order_id, **resp})


def consume_response_file(
    resp_file: Path,
    *,
    read_text,
    order_state: Dict[str, Dict[str, Any]],
    publish: PublishFn,
    chart: Optional[str] = None,
    lifecycle=None,
    on_corrupt=None,
) -> None:
    if not resp_file.exists():
        return
    try:
        text = read_text(resp_file)
        if not text:
            resp_file.unlink(missing_ok=True)
            return
        resp = json.loads(text)
        rtype = resp.get("type")
        if rtype == "fill":
            process_fill_update(resp, order_state=order_state, publish=publish, lifecycle=lifecycle)
        elif rtype == "error":
            process_error_response(resp, order_state=order_state, publish=publish, lifecycle=lifecycle)
        elif rtype == "close_all_ack":
            payload = {**resp, "chart": chart} if chart else resp
            publish("muscle.close_all_ack", payload)
        elif rtype == "modify_ok":
            payload = {**resp, "chart": chart} if chart else resp
            publish("muscle.response", payload)
        resp_file.unlink(missing_ok=True)
    except (json.JSONDecodeError, OSError) as exc:
        if on_corrupt:
            on_corrupt(chart, exc, resp_file)
        else:
            try:
                resp_file.unlink(missing_ok=True)
            except OSError:
                pass
