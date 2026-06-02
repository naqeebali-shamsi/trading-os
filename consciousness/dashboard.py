#!/usr/bin/env python3
"""
consciousness/dashboard.py -- Awareness Surface
-----------------------------------------------
Dashboard v0 with:
- system status (health + strategy snapshot)
- telemetry summary (best effort from telemetry service)
- recent activity stream (bus tail)
- explicit mock-vs-live safety flags
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, tail  # noqa
from cortex.llm_status import latest_llm_summary  # noqa
from runtime_controls import PRESETS, apply_preset, load_controls, write_controls  # noqa
from consciousness.trader_panels import build_trader_panels  # noqa

try:
    from ipc_path import get_ipc_dir  # noqa
    from ops import observability  # noqa
except Exception:  # pragma: no cover - handled via status payload
    get_ipc_dir = None
    observability = None

HEALTH_FILE = ROOT / "kernel" / "health.json"
RUNTIME_FILE = ROOT / "config" / "runtime_state.json"
STRAT_FILE = ROOT / "cortex" / "strategies.json"
RISK_LIMITS_FILE = ROOT / "immune" / "risk_limits.json"
STOP_TRADING_FILE = ROOT / "STOP_TRADING"
UI_DIR = ROOT / "consciousness" / "dashboard_ui"

TEXT_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}

DEFAULT_EVENT_LIMIT = 20
MAX_EVENT_LIMIT = 200
DEFAULT_MAX_HEARTBEAT_AGE_SEC = 30.0


def _dashboard_token():
    return os.getenv("TRADING_OS_DASHBOARD_TOKEN", "").strip()


def _client_address(handler):
    try:
        return (handler.client_address or [""])[0]
    except Exception:
        return ""


def _client_is_loopback(handler):
    return _client_address(handler) in {"127.0.0.1", "::1", "localhost"}


def _token_matches(handler):
    token = _dashboard_token()
    if not token:
        return False
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and auth[7:].strip() == token:
        return True
    return handler.headers.get("X-Trading-OS-Token", "").strip() == token


def _post_authorized(handler):
    if _dashboard_token():
        return _token_matches(handler)
    return _client_is_loopback(handler)


def _auth_config(handler):
    token = _dashboard_token()
    loopback = _client_is_loopback(handler)
    if token:
        return {
            "post_auth_required": True,
            "auth_method": "token",
            "loopback_client": loopback,
        }
    return {
        "post_auth_required": not loopback,
        "auth_method": "loopback" if loopback else "deny_remote",
        "loopback_client": loopback,
    }


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _read_json(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, IOError):
        return {}


def _fetch_json(url, timeout=0.35):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _fetch_text(url, timeout=0.35):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return ""


def _metric_value(metrics_text, metric_name):
    for line in metrics_text.splitlines():
        if line.startswith("#"):
            continue
        if not line.startswith(metric_name):
            continue
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        try:
            return float(parts[-1])
        except ValueError:
            return None
    return None


def _safe_flags(runtime_mode):
    env_mode = (os.getenv("TRADING_OS_MODE") or "").strip().upper()
    allow_mock_llm = os.getenv("TRADING_OS_ALLOW_MOCK_LLM", "0").strip().lower() in {"1", "true", "yes"}
    allow_mock_forecasts = os.getenv("TRADING_OS_ALLOW_MOCK_FORECASTS", "0").strip().lower() in {"1", "true", "yes"}
    llm_decision_mode = (os.getenv("TRADING_OS_LLM_DECISION_MODE") or "ADVISORY").strip().upper()
    risk_mode = str(_read_json(RISK_LIMITS_FILE).get("mode", "UNKNOWN")).upper()
    stop_trading = STOP_TRADING_FILE.exists()
    effective = runtime_mode or env_mode or risk_mode or "SIMULATION"
    is_live = effective == "LIVE"
    return {
        "effective_mode": effective,
        "runtime_mode": runtime_mode or "UNKNOWN",
        "env_mode": env_mode or "UNSET",
        "risk_limits_mode": risk_mode,
        "is_live_mode": is_live,
        "stop_trading_file": stop_trading,
        "allow_mock_llm": allow_mock_llm,
        "allow_mock_forecasts": allow_mock_forecasts,
        "llm_decision_mode": llm_decision_mode,
    }


def _telemetry_summary():
    port = int(os.getenv("TRADING_OS_TELEMETRY_PORT", "9876"))
    base = f"http://127.0.0.1:{port}"
    health = _fetch_json(f"{base}/health")
    metrics_text = _fetch_text(f"{base}/metrics")
    return {
        "endpoint": base,
        "reachable": bool(health or metrics_text),
        "health": health or {},
        "metrics": {
            "uptime_sec": _metric_value(metrics_text, "trading_os_uptime_seconds"),
            "bus_events_recent": _metric_value(metrics_text, "trading_os_bus_events_recent"),
            "orders_filled_total": _metric_value(metrics_text, "trading_os_orders_filled_total"),
            "orders_queued_total": _metric_value(metrics_text, "trading_os_orders_queued_total"),
        },
    }


def _parse_event_limit(raw_limit):
    if raw_limit is None:
        return DEFAULT_EVENT_LIMIT
    try:
        parsed = int(raw_limit)
    except (TypeError, ValueError):
        return DEFAULT_EVENT_LIMIT
    if parsed < 1:
        return DEFAULT_EVENT_LIMIT
    return min(parsed, MAX_EVENT_LIMIT)


def _parse_topic_filters(query_map):
    raw_filters = []
    for value in query_map.get("topics", []):
        raw_filters.extend(value.split(","))
    raw_filters.extend(query_map.get("topic", []))
    parsed = []
    for item in raw_filters:
        normalized = item.strip()
        if normalized:
            parsed.append(normalized)
    return parsed


def _parse_max_heartbeat_age(raw_value):
    if raw_value is None:
        return DEFAULT_MAX_HEARTBEAT_AGE_SEC
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_HEARTBEAT_AGE_SEC
    if parsed <= 0:
        return DEFAULT_MAX_HEARTBEAT_AGE_SEC
    return parsed


def _topic_matches(topic, filters):
    if not filters:
        return True
    if not topic:
        return False
    for candidate in filters:
        if candidate.endswith("*"):
            if topic.startswith(candidate[:-1]):
                return True
        elif topic == candidate:
            return True
    return False


def _latest_by_topic(events, topic):
    for event in events:
        if event.get("topic") == topic:
            return event.get("payload") or {}
    return None


def _count_reasons(events, topics):
    counts = {}
    topic_set = set(topics)
    for event in events:
        if event.get("topic") not in topic_set:
            continue
        payload = event.get("payload") or {}
        reason = payload.get("blocked_reason") or payload.get("reason") or payload.get("error_type") or payload.get("status") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _brain_summary(events):
    return latest_llm_summary(events)


def _signals_summary(events):
    return {
        "latest_emitted": _latest_by_topic(events, "market.signal"),
        "latest_candidate": _latest_by_topic(events, "market.signal.candidate"),
        "latest_blocked": _latest_by_topic(events, "market.signal.blocked"),
        "block_counts": _count_reasons(events, ["market.signal.candidate", "market.signal.blocked"]),
    }


def _orders_summary(events):
    return {
        "latest_intent": _latest_by_topic(events, "muscle.order.intent"),
        "latest_queued": _latest_by_topic(events, "muscle.order.queued"),
        "latest_sent": _latest_by_topic(events, "muscle.order.sent"),
        "latest_filled": _latest_by_topic(events, "muscle.order.filled"),
        "latest_rejected": _latest_by_topic(events, "muscle.order.rejected"),
        "latest_timeout": _latest_by_topic(events, "muscle.order.timeout"),
        "reject_counts": _count_reasons(events, ["muscle.order.rejected", "muscle.order.timeout"]),
    }


def _payload_for_stage(topic, payload):
    if topic == "immune.pass":
        return payload.get("intent") or payload
    if topic == "immune.block":
        return payload.get("intent") or payload
    return payload


def _order_id_for_event(topic, payload):
    stage_payload = _payload_for_stage(topic, payload)
    return (
        stage_payload.get("order_id")
        or payload.get("order_id")
        or payload.get("ticket")
        or payload.get("comment")
    )


def _trade_lifecycle_summary(events, limit=20):
    """Build a read-only lifecycle projection from recent bus events.

    This intentionally does not publish or persist anything. It is dashboard-only
    correlation of existing events so live routing cannot be affected.
    """
    stage_topics = {
        "market.signal": "signal",
        "muscle.order.intent": "intent",
        "immune.pass": "immune_pass",
        "immune.block": "immune_block",
        "muscle.order.queued": "queued",
        "muscle.order.sent": "sent",
        "muscle.order.filled": "filled",
        "muscle.order.rejected": "rejected",
        "muscle.order.timeout": "timeout",
        "muscle.order.error": "error",
        "position.opened": "position_opened",
        "position.closed": "position_closed",
        "memory.trade_opened": "memory_opened",
        "memory.trade_closed": "memory_closed",
        "memory.trade_outcome": "outcome",
        "introspect.post_trade_review": "post_trade_review",
    }
    final_priority = [
        ("post_trade_review", "reviewed"),
        ("position_closed", "closed"),
        ("memory_closed", "closed"),
        ("outcome", "review_pending"),
        ("rejected", "rejected"),
        ("immune_block", "blocked"),
        ("timeout", "timeout_unknown_broker_state"),
        ("error", "error"),
        ("position_opened", "opened"),
        ("memory_opened", "opened"),
        ("filled", "filled"),
        ("sent", "sent"),
        ("queued", "queued"),
        ("immune_pass", "vetted"),
        ("intent", "intent"),
        ("signal", "signal"),
    ]
    by_order = {}
    uncorrelated = 0
    for event in sorted(events, key=lambda row: row.get("seq", 0) or row.get("ts", 0)):
        topic = event.get("topic")
        stage = stage_topics.get(topic)
        if not stage:
            continue
        payload = event.get("payload") or {}
        oid = _order_id_for_event(topic, payload)
        if not oid:
            uncorrelated += 1
            continue
        stage_payload = _payload_for_stage(topic, payload)
        row = by_order.setdefault(str(oid), {"order_id": str(oid), "stages": {}, "stage_sequence": []})
        row["stages"][stage] = {"ts": event.get("ts"), "seq": event.get("seq"), "topic": topic, "payload": payload}
        row["stage_sequence"].append(stage)
        row["last_ts"] = event.get("ts") or row.get("last_ts")
        row["last_seq"] = event.get("seq") or row.get("last_seq")
        for key in ("symbol", "side", "qty", "strategy_id", "confidence", "price", "fill_price", "pnl"):
            if row.get(key) is None and stage_payload.get(key) is not None:
                row[key] = stage_payload.get(key)
        reason = payload.get("reason") or payload.get("error_type") or payload.get("message") or payload.get("blocked_reason")
        if reason:
            row["reason"] = reason
        if stage == "immune_pass":
            row["immune"] = {"decision": "pass", "reasons": []}
        elif stage == "immune_block":
            block_reasons = payload.get("reasons")
            if not isinstance(block_reasons, list):
                block_reasons = [block_reasons] if block_reasons else []
            row["immune"] = {"decision": "block", "reasons": [str(r) for r in block_reasons if r]}
        elif stage == "post_trade_review":
            row["review"] = {
                "join_status": payload.get("join_status"),
                "review_required": payload.get("review_required"),
                "verdict": payload.get("verdict") or payload.get("summary"),
                "matched": payload.get("join_status") == "order_id",
            }

    counts = {}
    rows = []
    defect_total = 0
    for row in by_order.values():
        stages = row.get("stages", {})
        state = "unknown"
        for stage, candidate in final_priority:
            if stage in stages:
                state = candidate
                break
        row["state"] = state
        counts[state] = counts.get(state, 0) + 1
        if "intent" in stages and "sent" in stages:
            row.setdefault("latency", {})["intent_to_sent_sec"] = round(float(stages["sent"].get("ts") or 0) - float(stages["intent"].get("ts") or 0), 3)
        if "sent" in stages and "filled" in stages:
            row.setdefault("latency", {})["sent_to_filled_sec"] = round(float(stages["filled"].get("ts") or 0) - float(stages["sent"].get("ts") or 0), 3)
        row["defects"] = _lifecycle_defects(stages)
        row["has_defect"] = bool(row["defects"])
        defect_total += len(row["defects"])
        # Keep API compact: callers need stage presence and payload for latest stage only.
        row["stage_names"] = list(dict.fromkeys(row.pop("stage_sequence", [])))
        row["stages"] = {name: {k: v for k, v in stage.items() if k != "payload"} for name, stage in stages.items()}
        rows.append(row)
    rows.sort(key=lambda row: row.get("last_ts") or 0, reverse=True)
    return {
        "trades": rows[:limit],
        "counts": counts,
        "uncorrelated_events": uncorrelated,
        "total_tracked": len(rows),
        "defect_count": defect_total,
    }


def _lifecycle_defects(stages):
    """Flag broken joins. The roadmap treats a missing join as an operational defect."""
    defects = []
    has_fill = "filled" in stages
    has_position = "position_opened" in stages or "position_closed" in stages
    if has_fill and not has_position:
        defects.append("fill_without_position_join")
    is_closed = "position_closed" in stages or "memory_closed" in stages
    if is_closed and "outcome" not in stages:
        defects.append("missing_trade_outcome")
    if (is_closed or "outcome" in stages) and "post_trade_review" not in stages:
        defects.append("missing_post_trade_review")
    return defects


def _status_summary(bridge_status, controls, signals, brain, orders):
    blockers = []
    ready_symbols = []
    blocked_symbols = {}
    for row in bridge_status.get("charts", []) or []:
        symbol = str(row.get("name", "")).replace("chart_", "")
        if row.get("heartbeat_fresh") and row.get("tick_ok"):
            ready_symbols.append(symbol)
        else:
            blocked_symbols[symbol] = row.get("heartbeat_detail") or "stale_or_missing_tick"
    if not controls.get("signal_direct_intents"):
        blockers.append("direct pattern intents disabled")
    elif controls.get("signal_direct_intents") and not controls.get("stock_direct_intents"):
        blockers.append("stock direct intents disabled (FX/metals only)")
    latest_candidate = signals.get("latest_candidate") or {}
    if latest_candidate.get("blocked_reason"):
        blockers.append(latest_candidate.get("blocked_reason"))
    if brain.get("llm_ok") is False:
        blockers.append(brain.get("operator_message") or f"LLM unavailable: {brain.get('error_code') or brain.get('llm_error')}")
    elif brain.get("action") == "HOLD":
        blockers.append("AI brain is HOLD")
    latest_rejected = orders.get("latest_rejected") or {}
    if latest_rejected:
        blockers.append(latest_rejected.get("error_type") or latest_rejected.get("message") or "latest order rejected")
    if not bridge_status.get("connected"):
        state = "broker_disconnected"
        headline = "Broker connection is offline"
    elif STOP_TRADING_FILE.exists():
        state = "halted"
        headline = "Emergency stop is active"
    elif controls.get("signal_direct_intents"):
        state = "trading_enabled"
        headline = "Pattern-based orders are enabled"
    else:
        state = "observing"
        headline = "Observing only. Pattern orders are disabled"
    return {
        "state": state,
        "headline": headline,
        "blockers": list(dict.fromkeys([str(b) for b in blockers if b]))[:8],
        "ready_symbols": ready_symbols,
        "blocked_symbols": blocked_symbols,
    }


def _bridge_status(max_heartbeat_age=DEFAULT_MAX_HEARTBEAT_AGE_SEC):
    if observability is None or get_ipc_dir is None:
        detail = "bridge status dependencies unavailable"
        if observability is not None:
            return observability.unavailable_bridge_health(max_heartbeat_age, detail)
        return {
            "available": False,
            "connected": False,
            "mode": "unavailable",
            "detail": detail,
            "ipc_root": None,
            "max_heartbeat_age_sec": max_heartbeat_age,
            "root": {
                "heartbeat_age_sec": None,
                "heartbeat_fresh": False,
                "heartbeat_detail": "missing",
                "tick_ok": False,
                "tick": "missing",
            },
            "charts": [],
            "fresh_chart_count": 0,
            "stale_chart_count": 0,
        }

    return observability.build_bridge_health(
        Path(get_ipc_dir()),
        max_heartbeat_age=max_heartbeat_age,
    )


def _preflight_state(max_heartbeat_age=DEFAULT_MAX_HEARTBEAT_AGE_SEC, *, strict_instruments: bool = False):
    from ops.readiness_eval import ReadinessOptions, evaluate_readiness

    root = Path(__file__).resolve().parent.parent
    opts = ReadinessOptions(live=True, strict_instruments=strict_instruments, max_heartbeat_age=max_heartbeat_age)
    result = evaluate_readiness(root, opts, ipc_dir=Path(get_ipc_dir()))
    return result.as_dict()


# Live readiness probes touch the broker bridge and can stall. The /api/state
# response must never block on them, so cap the wait and serve the last good
# preflight while a slow probe finishes in the background.
PREFLIGHT_TIMEOUT_SEC = 1.2
_PREFLIGHT_CACHE: dict = {}
_PREFLIGHT_CACHE_LOCK = threading.Lock()


def _preflight_cached(max_heartbeat_age=DEFAULT_MAX_HEARTBEAT_AGE_SEC, *, timeout=PREFLIGHT_TIMEOUT_SEC):
    """Best-effort preflight that times out instead of hanging the dashboard."""
    box: dict = {}
    done = threading.Event()

    def worker():
        try:
            value = _preflight_state(max_heartbeat_age=max_heartbeat_age)
            box["value"] = value
            with _PREFLIGHT_CACHE_LOCK:
                _PREFLIGHT_CACHE["value"] = value
        except Exception:
            box["value"] = None
        finally:
            done.set()

    threading.Thread(target=worker, name="dashboard-preflight", daemon=True).start()
    if done.wait(timeout) and box.get("value") is not None:
        return box["value"]
    with _PREFLIGHT_CACHE_LOCK:
        cached = _PREFLIGHT_CACHE.get("value")
    if cached is not None:
        out = dict(cached)
        out["stale"] = True
        return out
    return None


def _health_alerts(limit=30):
    topics = {"ops.health_alert", "ops.health.alert", "ops.layer.restarted"}
    rows = [ev for ev in tail(max(limit * 4, 40)) if ev.get("topic") in topics]
    rows.sort(key=lambda row: row.get("ts", 0), reverse=True)
    return {"alerts": rows[:limit], "count": len(rows[:limit])}


def _dashboard_state(limit=DEFAULT_EVENT_LIMIT, topic_filters=None, max_heartbeat_age=DEFAULT_MAX_HEARTBEAT_AGE_SEC):
    topic_filters = topic_filters or []
    health = _read_json(HEALTH_FILE)
    strategies = _read_json(STRAT_FILE)
    runtime_mode = str(_read_json(RUNTIME_FILE).get("mode", "")).strip().upper()
    # Pull a wider tail window so filtered feeds still have useful depth.
    read_window = max(limit * 10, 150)
    events = tail(read_window)
    events.sort(key=lambda row: row.get("ts", 0), reverse=True)
    filtered_events = [event for event in events if _topic_matches(event.get("topic", ""), topic_filters)]
    controls = load_controls()
    bridge_status = _bridge_status(max_heartbeat_age=max_heartbeat_age)
    signals = _signals_summary(events)
    brain = _brain_summary(events)
    orders = _orders_summary(events)
    lifecycle = _trade_lifecycle_summary(events, limit=limit)
    preflight = None
    try:
        if get_ipc_dir is not None:
            preflight = _preflight_cached(max_heartbeat_age=max_heartbeat_age)
    except Exception:
        preflight = None
    trader_panels = build_trader_panels(
        events,
        preflight=preflight,
        health=health,
        max_heartbeat_age=max_heartbeat_age,
    )
    return {
        "ts": time.time(),
        "health": health,
        "strategies": strategies,
        "recent_events": filtered_events[:limit],
        "event_feed": {
            "limit": limit,
            "topic_filters": topic_filters,
            "available_events": len(events),
            "matched_events": len(filtered_events),
        },
        "runtime_controls": controls,
        "control_presets": PRESETS,
        "status_summary": _status_summary(bridge_status, controls, signals, brain, orders),
        "brain_summary": brain,
        "signals_summary": signals,
        "orders_summary": orders,
        "trade_lifecycle": lifecycle,
        "safety_flags": _safe_flags(runtime_mode=runtime_mode),
        "telemetry_summary": _telemetry_summary(),
        "bridge_status": bridge_status,
        "trader_panels": trader_panels,
        "preflight": preflight,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence noisy access logs

    def _send_json(self, payload, status=200):
        encoded = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return json.loads(raw or "{}")

    def _send_text(self, text, content_type="text/html; charset=utf-8", status=200):
        encoded = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_file(self, path):
        if not path.exists():
            self._send_text("Not found", status=404)
            return
        suffix = path.suffix.lower()
        content_type = TEXT_CONTENT_TYPES.get(suffix, "text/plain; charset=utf-8")
        self._send_text(path.read_text(encoding="utf-8"), content_type=content_type)

    def _send_event_stream(self, *, since_seq=0, topic_filters=None):
        from consciousness.event_bus_stream import stream_bus_events  # noqa: WPS433

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            for chunk in stream_bus_events(
                since_seq=since_seq,
                topic_filters=topic_filters or [],
            ):
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        max_heartbeat_age = _parse_max_heartbeat_age(query.get("max_heartbeat_age", [None])[0])
        if path in {"/", "/api/state"}:
            limit = _parse_event_limit(query.get("limit", [None])[0])
            topic_filters = _parse_topic_filters(query)
            self._send_json(_dashboard_state(limit=limit, topic_filters=topic_filters, max_heartbeat_age=max_heartbeat_age))
            return
        if path == "/api/auth/config":
            self._send_json(_auth_config(self))
            return
        if path == "/api/bridge/status":
            self._send_json(_bridge_status(max_heartbeat_age=max_heartbeat_age))
            return
        if path == "/api/controls":
            self._send_json({"controls": load_controls(), "presets": PRESETS})
            return
        if path == "/api/preflight":
            self._send_json(_preflight_state(max_heartbeat_age=max_heartbeat_age, strict_instruments=True))
            return
        if path == "/api/events/health":
            self._send_json({"ok": True, "sse": True, "version": 2})
            return
        if path == "/api/events/recent":
            since_raw = query.get("since_seq", ["0"])[0]
            try:
                since_seq = int(since_raw or 0)
            except (TypeError, ValueError):
                since_seq = 0
            limit = _parse_event_limit(query.get("limit", [None])[0])
            topic_filters = _parse_topic_filters(query)
            from consciousness.event_bus_stream import read_events_since  # noqa: WPS433

            rows = read_events_since(since_seq, topic_filters=topic_filters, limit=limit)
            rows.sort(key=lambda row: row.get("ts", 0), reverse=True)
            self._send_json({"events": rows[:limit], "since_seq": since_seq})
            return
        if path == "/api/health/alerts":
            self._send_json(_health_alerts(limit=_parse_event_limit(query.get("limit", [None])[0])))
            return
        if path == "/api/agent/context":
            from ops.agent_context import build_agent_context  # noqa: WPS433

            limit = _parse_event_limit(query.get("limit", [None])[0])
            live = str(query.get("live", ["1"])[0]).strip().lower() not in {"0", "false", "no"}
            strict = str(query.get("strict", ["0"])[0]).strip().lower() in {"1", "true", "yes"}
            self._send_json(
                build_agent_context(
                    ROOT,
                    bus_limit=limit,
                    max_heartbeat_age=max_heartbeat_age,
                    live_preflight=live,
                    strict_instruments=strict,
                )
            )
            return
        if path == "/api/chart/bootstrap":
            from ops.chart_bootstrap import evaluate_bootstrap_gaps  # noqa: WPS433

            self._send_json(evaluate_bootstrap_gaps(max_heartbeat_age=max_heartbeat_age))
            return
        if path == "/api/agent/schemas":
            from cortex.agent_schemas import export_json_schemas  # noqa: WPS433

            self._send_json(export_json_schemas())
            return
        if path == "/api/promotions":
            from rd import promotions  # noqa: WPS433

            status = query.get("status", [None])[0]
            limit = int(query.get("limit", ["50"])[0] or 50)
            self._send_json({"promotions": promotions.list_promotions(status=status, limit=limit)})
            return
        if path == "/api/dream-lab/status":
            from cortex.live_policy import policy_summary  # noqa: WPS433

            state_file = ROOT / "intel" / "dream_lab_state.json"
            state = {}
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    state = {}
            self._send_json({"state": state, "live_policy": policy_summary()})
            return
        if path == "/api/events/stream":
            since_raw = query.get("since_seq", ["0"])[0]
            try:
                since_seq = int(since_raw or 0)
            except (TypeError, ValueError):
                since_seq = 0
            topic_filters = _parse_topic_filters(query)
            self._send_event_stream(since_seq=since_seq, topic_filters=topic_filters)
            return
        if path in {"/ui", "/ui/"}:
            self._send_file(UI_DIR / "index.html")
            return
        if path.startswith("/static/"):
            safe_name = path.replace("/static/", "", 1).strip("/")
            if ".." in safe_name.replace("\\", "/").split("/"):
                self._send_text("Not found", status=404)
                return
            self._send_file(UI_DIR / safe_name)
            return
        self._send_text("Not found", status=404)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if not _post_authorized(self):
            self._send_json({"ok": False, "error": "unauthorized"}, status=401)
            return
        try:
            body = self._read_json_body()
            old = load_controls()
            if path == "/api/controls/preset":
                preset = str(body.get("preset") or "").strip()
                new = apply_preset(preset)
                publish("ops.control.event", {"action": "preset", "preset": preset, "old": old, "new": new, "actor": "local_dashboard"})
                self._send_json({"ok": True, "controls": new})
                return
            if path == "/api/controls/update":
                allowed = {
                    "signal_direct_intents",
                    "stock_direct_intents",
                    "signal_min_confidence",
                    "signal_macro_gate",
                    "signal_macro_gate_max_age_sec",
                    "llm_decision_mode",
                    "preset",
                    "description",
                }
                updates = {k: v for k, v in body.items() if k in allowed}
                if not updates:
                    self._send_json({"ok": False, "error": "no_allowed_updates"}, status=400)
                    return
                updates.setdefault("preset", "custom")
                new = write_controls(updates)
                publish("ops.control.event", {"action": "update", "updates": updates, "old": old, "new": new, "actor": "local_dashboard"})
                self._send_json({"ok": True, "controls": new})
                return
            if path == "/api/promotions/approve":
                from rd import promotions  # noqa: WPS433

                promo_id = str(body.get("id") or body.get("promo_id") or "").strip()
                if not promo_id:
                    self._send_json({"ok": False, "error": "missing_promotion_id"}, status=400)
                    return
                try:
                    result = promotions.approve(promo_id, actor=str(body.get("actor") or "dashboard"))
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                publish("rd.promotion.approved", {"id": promo_id, "actor": body.get("actor") or "dashboard"})
                publish("rd.promotion.applied", {"id": promo_id, "version": result.get("policy", {}).get("version")})
                self._send_json({"ok": True, **result})
                return
            if path == "/api/promotions/reject":
                from rd import promotions  # noqa: WPS433

                promo_id = str(body.get("id") or body.get("promo_id") or "").strip()
                if not promo_id:
                    self._send_json({"ok": False, "error": "missing_promotion_id"}, status=400)
                    return
                try:
                    row = promotions.reject(
                        promo_id,
                        reason=str(body.get("reason") or ""),
                        actor=str(body.get("actor") or "dashboard"),
                    )
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                publish("rd.promotion.rejected", {"id": promo_id, "reason": body.get("reason") or ""})
                self._send_json({"ok": True, "promotion": row})
                return
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        except Exception as exc:  # pragma: no cover - defensive API guard
            self._send_json({"ok": False, "error": f"control_update_failed:{exc}"}, status=500)
            return
        self._send_text("Not found", status=404)


def run(host="127.0.0.1", port=None):
    port = int(port or os.getenv("TRADING_OS_DASHBOARD_PORT", "8765"))
    addr = (host, port)
    httpd = ThreadingHTTPServer(addr, Handler)
    print(f"[dashboard] API on http://{addr[0]}:{addr[1]}/api/state")
    print(f"[dashboard] SSE on http://{addr[0]}:{addr[1]}/api/events/stream")
    print(f"[dashboard] UI  on http://{addr[0]}:{addr[1]}/ui  (trader desk)")
    httpd.serve_forever()


if __name__ == "__main__":
    run()
