#!/usr/bin/env python3
"""Dashboard promotion API smoke tests."""
import http.client
import json
import socket
import sys
import threading
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from consciousness import dashboard  # noqa: E402
from rd import promotions  # noqa: E402
from cortex import live_policy as lp  # noqa: E402


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve():
    port = _free_port()
    httpd = dashboard.HTTPServer(("127.0.0.1", port), dashboard.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, port


def _post(port, path, body):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request("POST", path, body=json.dumps(body), headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    status = response.status
    conn.close()
    return status, payload


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request("GET", path)
    response = conn.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    status = response.status
    conn.close()
    return status, payload


def test_promotion_api_approve_reject(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        queue_file = tmp_path / "promotion_queue.jsonl"
        policy_file = tmp_path / "live_policy.json"
        history_file = tmp_path / "live_policy_history.jsonl"
        monkeypatch.setattr(promotions, "QUEUE_FILE", queue_file)
        monkeypatch.setattr(lp, "LIVE_POLICY_FILE", policy_file)
        monkeypatch.setattr(lp, "POLICY_HISTORY_FILE", history_file)
        monkeypatch.setattr(dashboard, "tail", lambda _n: [])
        monkeypatch.setattr(dashboard, "_telemetry_summary", lambda: {"reachable": False})
        monkeypatch.setattr(
            dashboard,
            "_bridge_status",
            lambda max_heartbeat_age=30.0: {"available": False, "mode": "mock"},
        )

        row = promotions.propose(
            ptype="strategy_weight",
            summary="Dashboard test",
            patch={"strategy_id": "TEST", "weight": 0.5, "active": True},
            agent="test",
        )

        httpd, thread, port = _serve()
        try:
            status, payload = _get(port, "/api/promotions?status=pending")
            assert status == 200
            assert len(payload["promotions"]) == 1

            status, payload = _post(port, "/api/promotions/approve", {"id": row["id"]})
            assert status == 200
            assert payload["ok"] is True

            status, payload = _post(port, "/api/promotions/reject", {"id": row["id"]})
            assert status == 400
        finally:
            httpd.shutdown()
            thread.join(timeout=2)
