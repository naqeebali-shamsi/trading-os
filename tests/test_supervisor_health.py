#!/usr/bin/env python3
"""Tests for supervisor layer health persistence."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import supervisor_health as sh  # noqa: E402


class _Proc:
    def __init__(self, pid, rc=None):
        self.pid = pid
        self._rc = rc

    def poll(self):
        return self._rc


def test_layer_snapshot_running_and_down(tmp_path):
    children = [
        ("cortex.brain", _Proc(101), tmp_path / "main.py", "brain", None),
        ("immune.risk", _Proc(102, rc=1), tmp_path / "main.py", "risk", None),
    ]
    rows = sh.layer_snapshot(children)
    assert rows[0]["running"] is True
    assert rows[0]["pid"] == 101
    assert rows[1]["running"] is False
    assert rows[1]["exit_code"] == 1


def test_merge_supervisor_health_preserves_existing_fields(tmp_path):
    health_path = tmp_path / "health.json"
    health_path.write_text(json.dumps({"ok": False, "bus": {"last_seq": 9}}), encoding="utf-8")
    block = sh.build_supervisor_block([], supervisor_pid=999)
    merged = sh.merge_supervisor_health(health_path, block)
    assert merged["bus"]["last_seq"] == 9
    assert merged["supervisor"]["pid"] == 999
    on_disk = json.loads(health_path.read_text(encoding="utf-8"))
    assert "supervisor" in on_disk
