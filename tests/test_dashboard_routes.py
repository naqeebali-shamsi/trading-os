#!/usr/bin/env python3
"""Dashboard route smoke tests and API query behavior checks."""
import http.client
import json
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from consciousness import dashboard


def _request(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read().decode("utf-8", errors="replace")
    status = response.status
    headers = dict(response.getheaders())
    conn.close()
    return status, headers, body


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve():
    port = _free_port()
    httpd = dashboard.ThreadingHTTPServer(("127.0.0.1", port), dashboard.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, port


def test_dashboard_api_limit_and_topics(monkeypatch):
    events = [
        {"ts": 7, "topic": "market.tick", "payload": {"symbol": "EURUSD"}},
        {"ts": 6, "topic": "muscle.order", "payload": {"state": "queued"}},
        {"ts": 5, "topic": "immune.alert", "payload": {"kind": "risk"}},
        {"ts": 4, "topic": "cortex.signal", "payload": {"signal": "buy"}},
        {"ts": 3, "topic": "market.depth", "payload": {"levels": 5}},
    ]
    monkeypatch.setattr(dashboard, "tail", lambda _n: list(events))
    monkeypatch.setattr(dashboard, "_telemetry_summary", lambda: {"endpoint": "mock", "reachable": False, "health": {}, "metrics": {}})
    monkeypatch.setattr(
        dashboard,
        "_bridge_status",
        lambda max_heartbeat_age=30.0: {
            "available": True,
            "connected": True,
            "mode": "root",
            "detail": "root bridge active",
            "ipc_root": "/tmp/ipc",
            "max_heartbeat_age_sec": max_heartbeat_age,
            "root": {"heartbeat_age_sec": 1.0, "heartbeat_fresh": True, "heartbeat_detail": "ok", "tick_ok": True, "tick": "EURUSD,1,2,0"},
            "charts": [],
            "fresh_chart_count": 0,
            "stale_chart_count": 0,
        },
    )

    httpd, thread, port = _serve()
    try:
        status, _headers, body = _request(port, "/api/state?limit=2")
        assert status == 200
        payload = json.loads(body)
        assert len(payload["recent_events"]) == 2
        assert payload["event_feed"]["limit"] == 2
        assert payload["bridge_status"]["mode"] == "root"

        status, _headers, body = _request(port, "/api/state?limit=10&topics=market.*,immune.*")
        assert status == 200
        payload = json.loads(body)
        assert payload["event_feed"]["topic_filters"] == ["market.*", "immune.*"]
        assert len(payload["recent_events"]) == 3
        assert all(
            event["topic"].startswith("market.") or event["topic"].startswith("immune.")
            for event in payload["recent_events"]
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_trade_lifecycle_summary_correlates_order_stages():
    events = [
        {"ts": 1.0, "seq": 1, "topic": "muscle.order.intent", "payload": {"order_id": "o1", "symbol": "EURUSD", "side": "BUY", "qty": 0.01}},
        {"ts": 2.0, "seq": 2, "topic": "immune.pass", "payload": {"type": "order_pass", "intent": {"order_id": "o1", "symbol": "EURUSD", "side": "BUY"}}},
        {"ts": 3.0, "seq": 3, "topic": "muscle.order.sent", "payload": {"order_id": "o1", "cmd": "ORDER,..."}},
        {"ts": 5.0, "seq": 4, "topic": "muscle.order.filled", "payload": {"order_id": "o1", "fill_price": 1.1, "symbol": "EURUSD"}},
        {"ts": 6.0, "seq": 5, "topic": "position.opened", "payload": {"order_id": "o1", "ticket": 7}},
    ]
    summary = dashboard._trade_lifecycle_summary(events)
    trade = summary["trades"][0]
    assert trade["order_id"] == "o1"
    assert trade["state"] == "opened"
    assert trade["stage_names"] == ["intent", "immune_pass", "sent", "filled", "position_opened"]
    assert trade["latency"]["intent_to_sent_sec"] == 2.0
    assert trade["latency"]["sent_to_filled_sec"] == 2.0


def test_trade_lifecycle_summary_handles_rejection_and_missing_order_id():
    events = [
        {"ts": 1.0, "seq": 1, "topic": "immune.block", "payload": {"intent": {"order_id": "o2", "symbol": "GBPUSD"}, "reasons": ["position_size_too_large"]}},
        {"ts": 2.0, "seq": 2, "topic": "muscle.order.rejected", "payload": {"order_id": "o2", "error_type": "invalid_lot"}},
        {"ts": 3.0, "seq": 3, "topic": "muscle.order.filled", "payload": {"symbol": "XAUUSD"}},
    ]
    summary = dashboard._trade_lifecycle_summary(events)
    assert summary["uncorrelated_events"] == 1
    assert summary["trades"][0]["state"] == "rejected"
    assert summary["trades"][0]["reason"] == "invalid_lot"


def test_trade_lifecycle_joins_review_and_immune_reasons():
    events = [
        {"ts": 1.0, "seq": 1, "topic": "muscle.order.intent", "payload": {"order_id": "o1", "symbol": "EURUSD", "side": "BUY", "qty": 0.01}},
        {"ts": 2.0, "seq": 2, "topic": "immune.pass", "payload": {"type": "order_pass", "intent": {"order_id": "o1"}}},
        {"ts": 3.0, "seq": 3, "topic": "muscle.order.sent", "payload": {"order_id": "o1"}},
        {"ts": 4.0, "seq": 4, "topic": "muscle.order.filled", "payload": {"order_id": "o1", "fill_price": 1.1}},
        {"ts": 5.0, "seq": 5, "topic": "position.opened", "payload": {"order_id": "o1", "ticket": 7}},
        {"ts": 6.0, "seq": 6, "topic": "position.closed", "payload": {"order_id": "o1", "ticket": 7}},
        {"ts": 7.0, "seq": 7, "topic": "memory.trade_outcome", "payload": {"order_id": "o1", "pnl": 4.2}},
        {"ts": 8.0, "seq": 8, "topic": "introspect.post_trade_review", "payload": {"order_id": "o1", "join_status": "order_id", "review_required": False}},
    ]
    summary = dashboard._trade_lifecycle_summary(events)
    trade = summary["trades"][0]
    assert trade["state"] == "reviewed"
    assert trade["review"]["join_status"] == "order_id"
    assert trade["review"]["matched"] is True
    assert trade["defects"] == []
    assert summary["defect_count"] == 0


def test_trade_lifecycle_flags_missing_post_trade_review():
    events = [
        {"ts": 1.0, "seq": 1, "topic": "muscle.order.intent", "payload": {"order_id": "o9", "symbol": "GBPUSD"}},
        {"ts": 2.0, "seq": 2, "topic": "muscle.order.filled", "payload": {"order_id": "o9", "fill_price": 1.25}},
        {"ts": 3.0, "seq": 3, "topic": "position.opened", "payload": {"order_id": "o9", "ticket": 11}},
        {"ts": 4.0, "seq": 4, "topic": "position.closed", "payload": {"order_id": "o9", "ticket": 11}},
        {"ts": 5.0, "seq": 5, "topic": "memory.trade_outcome", "payload": {"order_id": "o9", "pnl": -2.0}},
    ]
    summary = dashboard._trade_lifecycle_summary(events)
    trade = summary["trades"][0]
    assert "missing_post_trade_review" in trade["defects"]
    assert trade["has_defect"] is True
    assert summary["defect_count"] == 1


def test_trade_lifecycle_captures_immune_block_reasons():
    events = [
        {"ts": 1.0, "seq": 1, "topic": "muscle.order.intent", "payload": {"order_id": "o3", "symbol": "XAUUSD"}},
        {"ts": 2.0, "seq": 2, "topic": "immune.block", "payload": {"intent": {"order_id": "o3"}, "reasons": ["position_size_too_large", "daily_loss_limit"]}},
    ]
    summary = dashboard._trade_lifecycle_summary(events)
    trade = summary["trades"][0]
    assert trade["state"] == "blocked"
    assert trade["immune"]["decision"] == "block"
    assert trade["immune"]["reasons"] == ["position_size_too_large", "daily_loss_limit"]


def test_dashboard_routes_smoke(monkeypatch):
    monkeypatch.setattr(dashboard, "tail", lambda _n: [])
    monkeypatch.setattr(dashboard, "_telemetry_summary", lambda: {"endpoint": "mock", "reachable": False, "health": {}, "metrics": {}})
    monkeypatch.setattr(
        dashboard,
        "_bridge_status",
        lambda max_heartbeat_age=30.0: {
            "available": True,
            "connected": False,
            "mode": "offline",
            "detail": "no fresh root/chart heartbeat",
            "ipc_root": "/tmp/ipc",
            "max_heartbeat_age_sec": max_heartbeat_age,
            "root": {"heartbeat_age_sec": None, "heartbeat_fresh": False, "heartbeat_detail": "missing", "tick_ok": False, "tick": "missing"},
            "charts": [],
            "fresh_chart_count": 0,
            "stale_chart_count": 0,
        },
    )

    httpd, thread, port = _serve()
    try:
        status, headers, body = _request(port, "/ui")
        assert status == 200
        assert "text/html" in headers.get("Content-Type", "")
        assert "Trading OS" in body
        assert "Trader Desk" in body or "Overview" in body

        status, headers, body = _request(port, "/static/app.js")
        assert status == 200
        assert "application/javascript" in headers.get("Content-Type", "")
        assert "refresh();" in body
        assert "renderTradeLifecycle" in body
        assert "renderResearchWatchlist" in body
        assert "renderForecastThesisPanel" in body
        assert "renderEdgeValidationPanel" in body

        status, headers, body = _request(port, "/api/state")
        assert status == 200
        assert "application/json" in headers.get("Content-Type", "")
        payload = json.loads(body)
        assert "recent_events" in payload
        assert "bridge_status" in payload
        assert "trade_lifecycle" in payload
        assert "trader_panels" in payload
        assert "research_watchlist" in payload["trader_panels"]
        assert "forecast_thesis" in payload["trader_panels"]
        assert "edge_validation" in payload["trader_panels"]

        status, headers, body = _request(port, "/api/bridge/status?max_heartbeat_age=45")
        assert status == 200
        assert "application/json" in headers.get("Content-Type", "")
        payload = json.loads(body)
        assert payload["mode"] == "offline"
        assert payload["max_heartbeat_age_sec"] == 45.0

        status, _headers, _body = _request(port, "/static/../dashboard.py")
        assert status == 404

        status, headers, body = _request(port, "/api/chart/bootstrap")
        assert status == 200
        assert "application/json" in headers.get("Content-Type", "")
        bootstrap = json.loads(body)
        assert "summary" in bootstrap
        assert "charts" in bootstrap

        status, headers, body = _request(port, "/api/agent/schemas")
        assert status == 200
        assert "application/json" in headers.get("Content-Type", "")
        schemas = json.loads(body)
        assert schemas["agent_card"]["name"] == "trading-os-brain"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
