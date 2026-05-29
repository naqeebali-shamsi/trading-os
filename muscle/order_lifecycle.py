#!/usr/bin/env python3
"""Durable order lifecycle and pre-IPC dedupe helpers.

The router uses this module before touching broker IPC. Runtime state is local and
ignored by git. It is deliberately small, JSON-based, and atomic-write so it can
survive process restarts without replaying live orders.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "muscle" / ".order_lifecycle_state.json"
# In-flight only — terminal orders must not block repeat intents with the same fingerprint.
ACTIVE_STATUSES = {"accepted", "queued", "pending_ipc_write", "sent", "unknown_broker_state"}
TERMINAL_STATUSES = {"filled", "rejected", "ipc_write_failed", "unknown_broker_state"}


def is_active_status(status: str) -> bool:
    return str(status or "") in ACTIVE_STATUSES


def is_terminal_status(status: str) -> bool:
    return str(status or "") in TERMINAL_STATUSES


def _norm_num(value: Any) -> str:
    try:
        return f"{float(value):.10g}"
    except (TypeError, ValueError):
        return ""


def _norm_text(value: Any) -> str:
    return str(value or "").strip().upper()


def stable_fingerprint(intent: Dict[str, Any]) -> str:
    """Return deterministic fingerprint for logically identical order intents."""
    payload = {
        "symbol": _norm_text(intent.get("symbol")),
        "side": _norm_text(intent.get("side")),
        "qty": _norm_num(intent.get("qty")),
        "type": _norm_text(intent.get("type") or "MARKET"),
        "sl": _norm_num(intent.get("sl")),
        "tp": _norm_num(intent.get("tp")),
        "strategy_id": _norm_text(intent.get("strategy_id")),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or STATE_FILE
    if not path.exists():
        return {"orders": {}, "fingerprints": {}, "ts": time.time()}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("orders", {})
        data.setdefault("fingerprints", {})
        return data
    except Exception:
        # Fail closed: preserve corrupt file and start empty, but caller should not crash live routing.
        return {"orders": {}, "fingerprints": {}, "load_error": True, "ts": time.time()}


def save_state(state: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    state["ts"] = time.time()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def get_order(order_id: str, path: Optional[Path] = None) -> Dict[str, Any]:
    if not order_id:
        return {}
    return load_state(path).get("orders", {}).get(str(order_id), {})


def duplicate_for(intent: Dict[str, Any], path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Return prior active order if this intent/order_id must not be resent."""
    state = load_state(path)
    order_id = str(intent.get("order_id") or "")
    if order_id:
        prior = state.get("orders", {}).get(order_id)
        if prior and is_active_status(prior.get("status")):
            return {"kind": "order_id", "order_id": order_id, "prior": prior}
    fp = stable_fingerprint(intent)
    prior_id = state.get("fingerprints", {}).get(fp)
    if prior_id:
        prior = state.get("orders", {}).get(str(prior_id), {})
        if is_active_status(prior.get("status")):
            return {"kind": "fingerprint", "fingerprint": fp, "order_id": prior_id, "prior": prior}
    return None


def record_transition(order_id: str, status: str, *, intent: Optional[Dict[str, Any]] = None, details: Optional[Dict[str, Any]] = None, path: Optional[Path] = None) -> Dict[str, Any]:
    state = load_state(path)
    oid = str(order_id or (intent or {}).get("order_id") or "unknown")
    fp = stable_fingerprint(intent or state.get("orders", {}).get(oid, {}).get("intent", {}))
    row = state.setdefault("orders", {}).setdefault(oid, {"order_id": oid, "created_ts": time.time(), "transitions": []})
    if intent:
        row["intent"] = dict(intent)
    row["status"] = status
    row["fingerprint"] = fp
    row["updated_ts"] = time.time()
    if details:
        row.setdefault("details", {}).update(details)
    transition = {"ts": time.time(), "status": status, "details": details or {}}
    row.setdefault("transitions", []).append(transition)
    state.setdefault("fingerprints", {})[fp] = oid
    save_state(state, path)
    return row


def mark_unknown_if_sent(order_id: str, *, elapsed: float, chart: Optional[str] = None, path: Optional[Path] = None) -> Dict[str, Any]:
    return record_transition(order_id, "unknown_broker_state", details={"elapsed": elapsed, "chart": chart}, path=path)


def authoritative_status(order_id: str, router_cache: Optional[Dict[str, Any]] = None, path: Optional[Path] = None) -> str:
    """Return durable lifecycle status when present, else ephemeral router cache status."""
    row = get_order(order_id, path)
    if row.get("status"):
        return str(row["status"])
    return str((router_cache or {}).get("status") or "")


def sync_router_row(
    order_id: str,
    router_cache: Dict[str, Dict[str, Any]],
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Merge durable lifecycle row into the router's in-memory cache."""
    row = get_order(order_id, path)
    if not row:
        return router_cache.get(order_id, {})
    merged = {**(row.get("intent") or {}), **router_cache.get(order_id, {})}
    merged["order_id"] = order_id
    merged["status"] = row["status"]
    merged.setdefault("ts", row.get("updated_ts") or row.get("created_ts") or time.time())
    details = row.get("details") or {}
    if "chart" in details:
        merged["chart"] = details["chart"]
    router_cache[order_id] = merged
    return merged


def iter_tracked_order_ids(router_cache: Dict[str, Dict[str, Any]], path: Optional[Path] = None) -> set:
    """Union of durable lifecycle orders and ephemeral router cache keys."""
    state = load_state(path)
    return set(router_cache) | set(state.get("orders", {}))
