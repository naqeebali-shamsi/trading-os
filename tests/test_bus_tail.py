#!/usr/bin/env python3
"""Bus tail backward-read behavior."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))

import bus  # noqa: E402


def test_tail_reads_last_lines_without_full_scan(monkeypatch, tmp_path):
    bus_file = tmp_path / "bus.jsonl"
    events = [{"seq": i, "topic": "market.tick", "payload": {"i": i}} for i in range(500)]
    bus_file.write_text("\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8")
    monkeypatch.setattr(bus, "BUS_FILE", bus_file)

    tail = bus.tail(5)
    assert [row["seq"] for row in tail] == [495, 496, 497, 498, 499]


def test_tail_topic_filter_still_works(monkeypatch, tmp_path):
    bus_file = tmp_path / "bus.jsonl"
    events = [
        {"seq": 1, "topic": "market.tick", "payload": {}},
        {"seq": 2, "topic": "muscle.order.sent", "payload": {}},
        {"seq": 3, "topic": "market.tick", "payload": {}},
    ]
    bus_file.write_text("\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8")
    monkeypatch.setattr(bus, "BUS_FILE", bus_file)

    tail = bus.tail(2, topics={"market.tick"})
    assert len(tail) == 2
    assert all(row["topic"] == "market.tick" for row in tail)
