#!/usr/bin/env python3
"""Tests for dashboard SSE bus stream helpers."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from consciousness import event_bus_stream as stream  # noqa: E402


def test_topic_matches_glob_prefix():
    assert stream.topic_matches("market.tick.EURUSD", ["market.*"])
    assert not stream.topic_matches("muscle.order.sent", ["market.*"])


def test_read_events_since_filters_by_seq_and_topic(tmp_path, monkeypatch):
    bus_file = tmp_path / "bus.jsonl"
    rows = [
        {"seq": 1, "topic": "market.tick", "payload": {"symbol": "EURUSD"}, "ts": 1.0},
        {"seq": 2, "topic": "muscle.order.sent", "payload": {"symbol": "EURUSD"}, "ts": 2.0},
        {"seq": 3, "topic": "market.signal", "payload": {"symbol": "GBPUSD"}, "ts": 3.0},
    ]
    bus_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(stream, "_bus_file", lambda: bus_file)

    market = stream.read_events_since(1, topic_filters=["market.*"])
    assert len(market) == 1
    assert market[0]["seq"] == 3

    all_new = stream.read_events_since(0)
    assert len(all_new) == 3


def test_format_sse_event():
    frame = stream.format_sse("bus.event", {"seq": 1, "topic": "market.tick", "payload": {}})
    assert frame.startswith("event: bus.event\n")
    assert "data:" in frame
