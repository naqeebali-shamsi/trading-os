#!/usr/bin/env python3
"""Integration/smoke checks for Trading OS observability instrumentation."""
import contextlib
import http.client
import importlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_http(port, path, timeout=30):
    deadline = time.time() + timeout
    last_exc = None
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read().decode()
            conn.close()
            return resp.status, resp.getheaders(), body
        except Exception as exc:
            last_exc = exc
            time.sleep(0.1)
    raise AssertionError(f"HTTP {path} not ready after {timeout}s: {last_exc}")


@contextlib.contextmanager
def process(*args, env=None):
    popen_kwargs = dict(cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env or os.environ.copy())
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **popen_kwargs)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            _terminate_process(proc)


def _terminate_process(proc):
    """Terminate a spawned process, using process groups on POSIX and the
    plain terminate/kill path on platforms without os.killpg (e.g. Windows)."""
    if os.name == "posix":
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


@pytest.mark.unix_only
def test_telemetry_endpoints():
    port = free_port()
    env = os.environ.copy()
    env["TRADING_OS_TELEMETRY_PORT"] = str(port)
    with process(sys.executable, "telemetry/metrics.py", env=env) as proc:
        status, _headers, body = wait_for_http(port, "/health")
        assert status == 200
        health = json.loads(body)
        assert "charts" in health and "bus" in health and "uptime_sec" in health

        status, _headers, body = wait_for_http(port, "/metrics")
        assert status == 200
        assert "trading_os_uptime_seconds" in body
        assert "trading_os_bus_events_recent" in body

        status, _headers, body = wait_for_http(port, "/debug/state")
        assert status == 200
        debug = json.loads(body)
        assert "telemetry" in debug and "charts_on_disk" in debug
        assert proc.poll() is None
    print("[test] PASS: telemetry endpoints")


def test_supervisor_staged_boot_instrumentation():
    import kernel.supervisor as supervisor

    supervisor = importlib.reload(supervisor)
    started = []
    slept = []

    class FakeProc:
        next_pid = 4000
        def __init__(self, name):
            self.name = name
            self.pid = FakeProc.next_pid
            FakeProc.next_pid += 1
        def poll(self):
            return None
        def terminate(self):
            pass
        def wait(self, timeout=None):
            return 0

    class FakeLog:
        def close(self):
            pass

    def fake_start_layer(name, script, purpose):
        started.append(name)
        print(f"FAKE_START {name}")
        return FakeProc(name), FakeLog()

    original_start = supervisor.start_layer
    original_sleep = supervisor.time.sleep
    try:
        supervisor._children = []
        supervisor.start_layer = fake_start_layer
        supervisor.time.sleep = lambda seconds: slept.append(seconds)
        supervisor.boot()
    finally:
        supervisor.start_layer = original_start
        supervisor.time.sleep = original_sleep
        supervisor._children = []

    names = [name for name, script, _purpose in supervisor.LAYERS if script.exists()]
    assert started == names
    assert started.index("telemetry.metrics") < started.index("sensory.market")
    assert 0.15 in slept or float(os.getenv("TRADING_OS_LAYER_DELAY_SEC", "0.15")) in slept
    print("[test] PASS: supervisor staged boot instrumentation")


def test_supervisor_restart_dead_layer():
    import kernel.supervisor as supervisor
    supervisor = importlib.reload(supervisor)
    restarted = []

    class DeadProc:
        pid = 111
        def poll(self):
            return 7
    class LiveProc:
        pid = 222
        def poll(self):
            return None
    class FakeLog:
        def __init__(self):
            self.closed = False
        def close(self):
            self.closed = True

    old_log = FakeLog()
    def fake_start(name, script, purpose):
        restarted.append(name)
        return LiveProc(), FakeLog()

    original_start = supervisor.start_layer
    try:
        supervisor.start_layer = fake_start
        supervisor._children = [("dead.layer", DeadProc(), ROOT / "x.py", "test", old_log)]
        supervisor.restart_dead()
        assert restarted == ["dead.layer"]
        assert old_log.closed
        assert supervisor._children[0][1].pid == 222
    finally:
        supervisor.start_layer = original_start
        supervisor._children = []
    print("[test] PASS: supervisor restart logic")


def test_emergency_stop_and_deploy_check():
    if os.getenv("TRADING_OS_DEPLOY_CHECK_RUNNING") == "1":
        print("[test] SKIP: emergency stop deploy_check recursion guard active")
        return

    stop = ROOT / "STOP_TRADING"
    previous = stop.read_text() if stop.exists() else None
    if stop.exists():
        stop.unlink()
    try:
        runner = [sys.executable, "scripts/emergency_stop.py", "instrumentation_test"]
        if sys.platform != "win32":
            runner = ["bash", "scripts/emergency_stop.sh", "instrumentation_test"]
        result = subprocess.run(runner, cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
        assert result.returncode == 0, result.stdout
        assert stop.exists()
        assert "instrumentation_test" in stop.read_text()

        deploy = subprocess.run(["bash", "scripts/deploy_check.sh"], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        assert deploy.returncode != 0
        assert "STOP_TRADING file exists" in deploy.stdout
    finally:
        if previous is None:
            stop.unlink(missing_ok=True)
        else:
            stop.write_text(previous)
    print("[test] PASS: emergency stop gates deploy check")


def test_health_monitor_alerts():
    import kernel.watchdog as watchdog

    chart = ROOT / "ipc" / "chart_OBS_TEST"
    chart.mkdir(parents=True, exist_ok=True)
    hb = chart / "heartbeat.txt"
    old_ts = time.time() - 1000
    previous_hb = hb.read_text() if hb.exists() else None
    prev_seq = watchdog.LAST_SEQ_FILE.read_text() if watchdog.LAST_SEQ_FILE.exists() else None
    prev_ts = watchdog.LAST_TS_FILE.read_text() if watchdog.LAST_TS_FILE.exists() else None
    original_tail = watchdog.tail
    original_publish = watchdog.publish
    published_alerts = []
    try:
        hb.write_text(f"{old_ts}|alive\n")
        # Isolate from the shared append-only bus. Other concurrent tests may
        # legitimately publish events, which would make bus_stale assertions
        # racy. This unit test controls the watchdog's bus view directly.
        seq = 4242
        watchdog.tail = lambda n=1: [{"seq": seq, "ts": old_ts, "topic": "ops.instrumentation.test", "payload": {}}]
        watchdog.publish = lambda topic, payload: published_alerts.append({"topic": topic, "payload": payload}) or len(published_alerts)
        watchdog.LAST_SEQ_FILE.write_text(str(seq))
        watchdog.LAST_TS_FILE.write_text(str(time.time() - 1000))
        report = watchdog.check_once(publish_alerts=True, now=time.time())
        kinds = {alert["kind"] for alert in report["alerts"]}
        assert "heartbeat_stale" in kinds
        assert "bus_stale" in kinds
        event_kinds = {ev["payload"].get("kind") for ev in published_alerts}
        assert "heartbeat_stale" in event_kinds
    finally:
        watchdog.tail = original_tail
        watchdog.publish = original_publish
        if previous_hb is None:
            hb.unlink(missing_ok=True)
            try:
                chart.rmdir()
            except OSError:
                pass
        else:
            hb.write_text(previous_hb)
        if prev_seq is None:
            watchdog.LAST_SEQ_FILE.unlink(missing_ok=True)
        else:
            watchdog.LAST_SEQ_FILE.write_text(prev_seq)
        if prev_ts is None:
            watchdog.LAST_TS_FILE.unlink(missing_ok=True)
        else:
            watchdog.LAST_TS_FILE.write_text(prev_ts)
    print("[test] PASS: health monitor alerts")


def test_all():
    print("=" * 60)
    print("  OBSERVABILITY INSTRUMENTATION TESTS")
    print("=" * 60)
    test_telemetry_endpoints()
    test_supervisor_staged_boot_instrumentation()
    test_supervisor_restart_dead_layer()
    test_emergency_stop_and_deploy_check()
    test_health_monitor_alerts()
    print("=" * 60)
    print("  ALL OBSERVABILITY TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
