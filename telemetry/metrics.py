#!/usr/bin/env python3
"""
telemetry/metrics.py — Citadel Telemetry Layer (v1)
----------------------------------------------------
Prometheus-style metrics endpoint + structured health checks.
Replaces consciousness/dashboard.py with actual system observability.

Exposes on :9876:
  /metrics      — Prometheus text format (for scraping)
  /health       — JSON health report with per-layer status
  /debug/state  — Full ORDER_STATE, PENDING_QUEUE, last events

Also publishes ops.telemetry events to bus for consciousness dashboard
"""
import json, os, sys, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, tail, TOPICS_DIR  # noqa
from ipc_path import get_ipc_dir  # noqa
from ops import bridge_status, observability  # noqa
try:
    from cortex.instrument_registry import load_registry  # noqa
except Exception:  # pragma: no cover - telemetry must stay up if registry config is bad
    load_registry = None

IPC_DIR = get_ipc_dir()
BUS_FILE = ROOT / "nervous" / "bus.jsonl"

telemetry_state = {
    "start_time": time.time(),
    "requests_served": 0,
    "last_scrape": 0,
}


def _read_json(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, IOError):
        return {}


def _list_charts():
    return [p.name for p in bridge_status.chart_dirs(IPC_DIR)]


def _chart_health():
    """Per-chart heartbeat freshness."""
    snapshot = observability.build_bridge_health(IPC_DIR)
    return observability.chart_health_rows(snapshot)


def _bus_stats():
    """Bus growth rate, last N events."""
    events = _tail_bus_fast(100)
    topics = {}
    for ev in events:
        t = ev.get("topic", "unknown")
        topics[t] = topics.get(t, 0) + 1
    return {
        "total_recent": len(events),
        "topics": topics,
    }


def _tail_bus_fast(n=100, max_bytes: int = 512 * 1024):
    """Tail recent bus events without scanning an unbounded append-only file.

    The generic nervous.bus.tail() walks the full bus file. That is fine for
    small test runs, but live/demo sessions can accumulate enough events that
    telemetry /health exceeds the test client's 1s socket timeout. Telemetry is
    intentionally observational, so a bounded tail is the safer behavior here.
    """
    if not BUS_FILE.exists():
        return []
    try:
        size = BUS_FILE.stat().st_size
        with BUS_FILE.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # discard partial line
            lines = f.readlines()[-n:]
        events = []
        for raw in lines:
            try:
                events.append(json.loads(raw.decode("utf-8", errors="ignore")))
            except json.JSONDecodeError:
                continue
        return events
    except Exception:
        return tail(n)


def _count_topic_lines_fast(topic: str, max_bytes: int = 256 * 1024):
    """Approximate topic event count without scanning unbounded files.

    Telemetry must never stall the single-threaded HTTP server. The old metrics
    path called subscribe(..., since_seq=0), which reads whole topic indexes and
    can make /metrics time out when tests or live runs create large topic files.
    """
    path = TOPICS_DIR / f"{topic}.jsonl"
    if not path.exists():
        return 0
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # discard partial line
            data = f.read()
        return data.count(b"\n")
    except Exception:
        return 0


def _tick_map():
    ticks = {}
    for chart in _list_charts():
        tick = bridge_status.read_tick(IPC_DIR / chart / "tick.txt")
        if tick and tick.get("symbol"):
            ticks[tick["symbol"]] = {
                "symbol": tick["symbol"],
                "bid": tick["bid"],
                "ask": tick["ask"],
            }
    return ticks


def _instrument_readiness():
    if load_registry is None:
        return {}
    try:
        registry = load_registry(force=True)
        return registry.readiness_snapshot(_list_charts(), _tick_map())
    except Exception as exc:
        return {"error": str(exc)}


def _prometheus_metrics():
    """Generate Prometheus text format metrics."""
    lines = []
    lines.append("# HELP trading_os_uptime_seconds System uptime")
    lines.append("# TYPE trading_os_uptime_seconds gauge")
    lines.append(f"trading_os_uptime_seconds {time.time() - telemetry_state['start_time']:.1f}")

    lines.append("# HELP trading_os_charts_discovered Number of chart_ directories found")
    lines.append("# TYPE trading_os_charts_discovered gauge")
    charts = _list_charts()
    lines.append(f"trading_os_charts_discovered {len(charts)}")

    lines.append("# HELP trading_os_chart_alive Whether each chart heartbeat is fresh")
    lines.append("# TYPE trading_os_chart_alive gauge")
    for chart, status in _chart_health().items():
        alive = 1 if status.get("alive") else 0
        lines.append(f'trading_os_chart_alive{{chart="{chart}"}} {alive}')

    lines.append("# HELP trading_os_bus_events_recent Recent bus event count (last 100)")
    lines.append("# TYPE trading_os_bus_events_recent gauge")
    stats = _bus_stats()
    lines.append(f"trading_os_bus_events_recent {stats['total_recent']}")

    readiness = _instrument_readiness()
    if readiness and "error" not in readiness:
        lines.append("# HELP trading_os_instrument_enabled Whether an instrument is enabled in config")
        lines.append("# TYPE trading_os_instrument_enabled gauge")
        lines.append("# HELP trading_os_instrument_ready Whether an instrument passes readiness checks")
        lines.append("# TYPE trading_os_instrument_ready gauge")
        lines.append("# HELP trading_os_instrument_chart_present Whether an instrument chart IPC dir exists")
        lines.append("# TYPE trading_os_instrument_chart_present gauge")
        lines.append("# HELP trading_os_instrument_spread_ok Whether current spread is within config")
        lines.append("# TYPE trading_os_instrument_spread_ok gauge")
        lines.append("# HELP trading_os_instrument_session_ok Whether instrument session is currently open")
        lines.append("# TYPE trading_os_instrument_session_ok gauge")
        for symbol, status in readiness.items():
            labels = f'symbol="{symbol}",asset_class="{status.get("asset_class", "unknown")}"'
            lines.append(f'trading_os_instrument_enabled{{{labels}}} {1 if status.get("enabled") else 0}')
            lines.append(f'trading_os_instrument_ready{{{labels}}} {1 if status.get("ready") else 0}')
            lines.append(f'trading_os_instrument_chart_present{{{labels}}} {1 if status.get("chart_present") else 0}')
            lines.append(f'trading_os_instrument_spread_ok{{{labels}}} {1 if status.get("spread_ok") else 0}')
            lines.append(f'trading_os_instrument_session_ok{{{labels}}} {1 if status.get("session_ok") else 0}')

    # Order state from multisymbol_router (hack — read from shared state impossible,
    # so we count from bus events instead)
    filled = _count_topic_lines_fast("muscle.order.filled")
    queued = _count_topic_lines_fast("muscle.order.queued")
    lines.append("# HELP trading_os_orders_filled_total Total filled orders")
    lines.append("# TYPE trading_os_orders_filled_total counter")
    lines.append(f"trading_os_orders_filled_total {filled}")

    lines.append("# HELP trading_os_orders_queued_total Total queued orders")
    lines.append("# TYPE trading_os_orders_queued_total counter")
    lines.append(f"trading_os_orders_queued_total {queued}")

    return "\n".join(lines)


def _health_json():
    bridge = observability.build_bridge_health(IPC_DIR)
    return observability.merge_health_report(
        bridge_snapshot=bridge,
        bus_stats=_bus_stats(),
        instrument_readiness=_instrument_readiness(),
        uptime_sec=time.time() - telemetry_state["start_time"],
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        telemetry_state["requests_served"] += 1
        path = self.path

        if path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(_prometheus_metrics().encode())

        elif path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(_health_json(), indent=2).encode())

        elif path == "/debug/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            # Try to read router state file if it exists
            state_file = ROOT / "muscle" / ".router_state.json"
            router_state = _read_json(state_file) if state_file.exists() else {}
            self.wfile.write(json.dumps({
                "telemetry": telemetry_state,
                "router_state_present": state_file.exists(),
                "router_state": router_state,
                "charts_on_disk": _list_charts(),
            }, indent=2).encode())

        else:
            self.send_response(404)
            self.end_headers()


def run(host="127.0.0.1", port=None):
    port = int(port or os.getenv("TRADING_OS_TELEMETRY_PORT", "9876"))
    addr = (host, port)
    httpd = HTTPServer(addr, Handler)
    print(f"[telemetry] Metrics on http://{addr[0]}:{addr[1]}/metrics")
    print(f"[telemetry] Health  on http://{addr[0]}:{addr[1]}/health")
    print(f"[telemetry] Debug   on http://{addr[0]}:{addr[1]}/debug/state")
    httpd.serve_forever()


if __name__ == "__main__":
    run()
