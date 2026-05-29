#!/usr/bin/env python3
"""Route MCP trade tools through the audited bus → immune → muscle pipeline."""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "nervous") not in sys.path:
    sys.path.insert(0, str(ROOT / "nervous"))


def _ensure_paths() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(ROOT / "nervous") not in sys.path:
        sys.path.insert(0, str(ROOT / "nervous"))


def _normalize_side(raw: str) -> str:
    side = str(raw or "").strip().upper()
    if side in {"BUY", "SELL"}:
        return side
    if side in {"B", "LONG"}:
        return "BUY"
    if side in {"S", "SHORT"}:
        return "SELL"
    raise ValueError(f"invalid_side:{raw}")


def _read_tick(symbol: str) -> dict[str, Any]:
    from ipc_path import get_ipc_dir

    ipc_dir = Path(get_ipc_dir())
    candidates = [
        ipc_dir / f"chart_{symbol}" / "tick.txt",
        ipc_dir / "tick.txt",
    ]
    try:
        from cortex.instrument_registry import InstrumentRegistry

        meta = InstrumentRegistry().get(symbol) or {}
        chart = meta.get("chart")
        if chart:
            candidates.insert(0, ipc_dir / str(chart) / "tick.txt")
    except Exception:
        pass

    from ops.bridge_status import read_tick as read_tick_file

    for path in candidates:
        tick = read_tick_file(path)
        if tick:
            tick.setdefault("symbol", symbol)
            return tick
    return {}


def _default_sl_tp(symbol: str, side: str, price: float, sl: Optional[float], tp: Optional[float]) -> tuple[float, float]:
    if sl not in (None, 0, 0.0) and tp not in (None, 0, 0.0):
        return float(sl), float(tp)

    from cortex.instrument_registry import InstrumentRegistry

    cfg = InstrumentRegistry().get(symbol) or {}
    unit = float(cfg.get("pip_size") or cfg.get("point_size") or 0.0001)
    min_units = float(cfg.get("min_stop_distance_pips") or cfg.get("min_stop_distance_points") or 10)
    sl_dist = max(unit * min_units, unit * 10)
    tp_dist = sl_dist * 2
    digits = int(cfg.get("digits") or 5)

    if side == "BUY":
        computed_sl = round(price - sl_dist, digits) if sl in (None, 0, 0.0) else float(sl)
        computed_tp = round(price + tp_dist, digits) if tp in (None, 0, 0.0) else float(tp)
    else:
        computed_sl = round(price + sl_dist, digits) if sl in (None, 0, 0.0) else float(sl)
        computed_tp = round(price - tp_dist, digits) if tp in (None, 0, 0.0) else float(tp)
    return computed_sl, computed_tp


def build_place_order_intent(args: Dict[str, Any], *, source: str = "mcp.mt5") -> Dict[str, Any]:
    """Convert MCP place_order args into a muscle.order.intent payload."""
    symbol = str(args.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("symbol_required")

    side = _normalize_side(args.get("side"))
    qty = float(args.get("volume"))
    if qty <= 0:
        raise ValueError("invalid_volume")

    tick = _read_tick(symbol)
    bid = float(tick.get("bid") or 0)
    ask = float(tick.get("ask") or 0)
    price = ask if side == "BUY" else bid
    if price <= 0:
        raise ValueError("no_market_price")

    sl, tp = _default_sl_tp(symbol, side, price, args.get("sl"), args.get("tp"))
    order_id = str(args.get("order_id") or f"mcp_{symbol}_{int(time.time())}_{uuid.uuid4().hex[:6]}")

    from cortex.instrument_registry import InstrumentRegistry

    registry = InstrumentRegistry()
    cfg = registry.get(symbol) or {}
    strategies = list(cfg.get("strategies") or [])
    strategy_id = str(args.get("strategy_id") or (strategies[0] if strategies else "MA_CROSS_SMA9_21"))
    strategy_result = registry.strategy_allowed(symbol, strategy_id)
    if not strategy_result.ok and strategies:
        strategy_id = str(strategies[0])
        strategy_result = registry.strategy_allowed(symbol, strategy_id)
    if not strategy_result.ok:
        raise ValueError(strategy_result.reason or "strategy_not_allowed")

    return {
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "sl": sl,
        "tp": tp,
        "type": "MARKET",
        "strategy_id": strategy_id,
        "source": source,
        "comment": str(args.get("comment") or "mcp_order"),
        "confidence": 1.0,
        "reason": "MCP place_order routed via bus",
        "mode_check": False,
        "human_approved": bool(args.get("human_approved")),
    }


def build_position_intent(action: str, args: Dict[str, Any], *, source: str = "mcp.mt5") -> Dict[str, Any]:
    command_id = str(args.get("command_id") or f"mcp_{action}_{int(time.time())}_{uuid.uuid4().hex[:6]}")
    payload: Dict[str, Any] = {
        "command_id": command_id,
        "action": action,
        "source": source,
        "human_approved": bool(args.get("human_approved")),
        "mode_check": False,
    }
    if action == "close_position":
        payload["ticket"] = int(args["ticket"])
    elif action == "modify":
        payload["ticket"] = int(args["ticket"])
        payload["sl"] = float(args.get("sl") or 0.0)
        payload["tp"] = float(args.get("tp") or 0.0)
    elif action != "close_all":
        raise ValueError(f"unknown_position_action:{action}")
    return payload


def publish_order_intent(intent: Dict[str, Any]) -> Optional[int]:
    from bus import publish

    return publish(
        "muscle.order.intent",
        intent,
        meta={"source": intent.get("source", "mcp.mt5"), "actor": "mcp.mt5"},
    )


def publish_position_intent(intent: Dict[str, Any]) -> Optional[int]:
    from bus import publish

    return publish(
        "muscle.position.intent",
        intent,
        meta={"source": intent.get("source", "mcp.mt5"), "actor": "mcp.mt5"},
    )


def _events_since(since_ts: float, topics: Iterable[str], *, limit: int = 400) -> list[dict[str, Any]]:
    from bus import tail

    topic_set = set(topics)
    return [
        event
        for event in tail(limit)
        if float(event.get("ts") or 0) >= since_ts and event.get("topic") in topic_set
    ]


def _intent_order_id(payload: dict[str, Any]) -> Optional[str]:
    intent = payload.get("intent") or {}
    return intent.get("order_id") or payload.get("order_id")


def _command_id(payload: dict[str, Any]) -> Optional[str]:
    cmd = payload.get("command") or payload.get("intent") or payload
    return cmd.get("command_id")


def wait_for_order_pipeline(
    order_id: str,
    since_ts: float,
    *,
    immune_timeout_sec: float | None = None,
    execution_timeout_sec: float | None = None,
    poll_sec: float = 0.25,
) -> dict[str, Any]:
    immune_timeout_sec = float(immune_timeout_sec or os.getenv("TRADING_OS_MCP_IMMUNE_TIMEOUT_SEC", "20"))
    execution_timeout_sec = float(execution_timeout_sec or os.getenv("TRADING_OS_MCP_EXEC_TIMEOUT_SEC", "90"))

    immune_deadline = time.time() + immune_timeout_sec
    approved = False
    while time.time() < immune_deadline:
        for event in _events_since(since_ts, {"immune.pass", "immune.block"}):
            payload = event.get("payload") or {}
            if _intent_order_id(payload) != order_id:
                continue
            if event.get("topic") == "immune.block":
                return {
                    "ok": False,
                    "stage": "immune",
                    "order_id": order_id,
                    "reasons": payload.get("reasons") or [],
                    "intent": payload.get("intent") or {},
                }
            approved = True
            break
        if approved:
            break
        time.sleep(poll_sec)
    else:
        return {"ok": False, "stage": "immune", "order_id": order_id, "error": "immune_timeout"}

    exec_deadline = time.time() + execution_timeout_sec
    sent_seen = False
    while time.time() < exec_deadline:
        topics = {
            "muscle.order.sent",
            "muscle.order.filled",
            "muscle.order.rejected",
            "muscle.order.timeout",
            "muscle.order.queued",
            "muscle.order.error",
        }
        for event in _events_since(since_ts, topics):
            payload = event.get("payload") or {}
            if payload.get("order_id") != order_id:
                continue
            topic = event.get("topic")
            if topic == "muscle.order.sent":
                sent_seen = True
                continue
            final_ok = topic == "muscle.order.filled"
            return {
                "ok": final_ok,
                "stage": "execution",
                "order_id": order_id,
                "topic": topic,
                "sent_seen": sent_seen,
                "payload": payload,
            }
        time.sleep(poll_sec)
    return {
        "ok": False,
        "stage": "execution",
        "order_id": order_id,
        "error": "execution_timeout",
        "sent_seen": sent_seen,
    }


def wait_for_position_pipeline(
    command_id: str,
    since_ts: float,
    *,
    immune_timeout_sec: float | None = None,
    execution_timeout_sec: float | None = None,
    poll_sec: float = 0.25,
) -> dict[str, Any]:
    immune_timeout_sec = float(immune_timeout_sec or os.getenv("TRADING_OS_MCP_IMMUNE_TIMEOUT_SEC", "20"))
    execution_timeout_sec = float(execution_timeout_sec or os.getenv("TRADING_OS_MCP_EXEC_TIMEOUT_SEC", "60"))

    immune_deadline = time.time() + immune_timeout_sec
    approved = False
    while time.time() < immune_deadline:
        for event in _events_since(since_ts, {"immune.position.pass", "immune.position.block"}):
            payload = event.get("payload") or {}
            if _command_id(payload) != command_id:
                continue
            if event.get("topic") == "immune.position.block":
                return {
                    "ok": False,
                    "stage": "immune",
                    "command_id": command_id,
                    "reasons": payload.get("reasons") or [],
                }
            approved = True
            break
        if approved:
            break
        time.sleep(poll_sec)
    else:
        return {"ok": False, "stage": "immune", "command_id": command_id, "error": "immune_timeout"}

    exec_deadline = time.time() + execution_timeout_sec
    while time.time() < exec_deadline:
        for event in _events_since(
            since_ts,
            {"muscle.position.sent", "muscle.position.ack", "muscle.position.rejected", "muscle.position.error"},
        ):
            payload = event.get("payload") or {}
            if payload.get("command_id") != command_id:
                continue
            topic = event.get("topic")
            ok = topic in {"muscle.position.sent", "muscle.position.ack"}
            if topic in {"muscle.position.rejected", "muscle.position.error"}:
                ok = False
            return {"ok": ok, "stage": "execution", "command_id": command_id, "topic": topic, "payload": payload}
        time.sleep(poll_sec)
    return {"ok": False, "stage": "execution", "command_id": command_id, "error": "execution_timeout"}


def route_place_order(args: Dict[str, Any]) -> dict[str, Any]:
    since_ts = time.time()
    intent = build_place_order_intent(args)
    seq = publish_order_intent(intent)
    outcome = wait_for_order_pipeline(intent["order_id"], since_ts)
    return {
        "route": "bus.order_intent",
        "order_id": intent["order_id"],
        "bus_seq": seq,
        "intent": intent,
        "pipeline": outcome,
        "status": "ok" if outcome.get("ok") else "error",
    }


def route_position_command(action: str, args: Dict[str, Any]) -> dict[str, Any]:
    since_ts = time.time()
    intent = build_position_intent(action, args)
    seq = publish_position_intent(intent)
    outcome = wait_for_position_pipeline(intent["command_id"], since_ts)
    return {
        "route": "bus.position_intent",
        "command_id": intent["command_id"],
        "action": action,
        "bus_seq": seq,
        "intent": intent,
        "pipeline": outcome,
        "status": "ok" if outcome.get("ok") else "error",
    }


def process_order_intent_inline(intent: Dict[str, Any]) -> dict[str, Any]:
    """Test/offline helper: run immune synchronously then execute on muscle."""
    _ensure_paths()
    from immune.main import check_order, load_limits
    from immune.provenance import attach_proof
    from bus import publish

    limits = load_limits()
    limits.update({"trade_window_start_utc": 0, "trade_window_end_utc": 23})
    limits["loss_streak_cooldown"] = {"enabled": False}
    symbol = intent.get("symbol")
    if symbol:
        allowed = set(limits.get("allowed_symbols") or [])
        allowed.add(str(symbol).upper())
        limits["allowed_symbols"] = sorted(allowed)

    passed, reasons, scaled = check_order(dict(intent), limits, journal=[])
    if not passed:
        publish("immune.block", {"type": "order_block", "intent": intent, "reasons": reasons})
        return {"ok": False, "stage": "immune", "reasons": reasons}

    event = {"topic": "muscle.order.intent", "seq": 1}
    approved = attach_proof(scaled, event)
    publish("immune.pass", {"type": "order_pass", "intent": approved, "provenance": approved.get("immune_proof")})

    from muscle.muscle_main import process_order_intent

    process_order_intent(approved)
    return {"ok": True, "stage": "inline_execution", "order_id": approved.get("order_id"), "intent": approved}
