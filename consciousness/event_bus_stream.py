"""Server-Sent Events stream over the nervous system bus (pub/sub bridge).

Publishers: ``bus.publish`` appends to ``bus.jsonl``.
Subscribers: dashboard clients connect to ``/api/events/stream`` and receive
``bus.event`` SSE frames for matching topics.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional

ROOT = Path(__file__).resolve().parent.parent


def topic_matches(topic: str, filters: List[str]) -> bool:
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


def _bus_file() -> Path:
    return ROOT / "nervous" / "bus.jsonl"


def read_events_since(
    since_seq: int,
    *,
    topic_filters: Optional[List[str]] = None,
    limit: int = 200,
) -> List[dict[str, Any]]:
    """Read bus events with seq > since_seq, newest last."""
    path = _bus_file()
    if not path.exists():
        return []
    topic_filters = topic_filters or []
    rows: List[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            seq = int(event.get("seq") or 0)
            if seq <= since_seq:
                continue
            topic = str(event.get("topic") or "")
            if not topic_matches(topic, topic_filters):
                continue
            rows.append(event)
    if len(rows) > limit:
        rows = rows[-limit:]
    return rows


def current_bus_seq(default: int = 0) -> int:
    try:
        from bus import current_seq

        return int(current_seq(default=default))
    except Exception:
        seq_file = ROOT / "nervous" / ".seq"
        if not seq_file.exists():
            return default
        try:
            return int(seq_file.read_text(encoding="utf-8").strip() or default)
        except ValueError:
            return default


def format_sse(event_name: str, payload: dict) -> str:
    data = json.dumps(payload, separators=(",", ":"), default=str)
    return f"event: {event_name}\ndata: {data}\n\n"


def format_sse_comment(text: str) -> str:
    return f": {text}\n\n"


def stream_bus_events(
    *,
    since_seq: int = 0,
    topic_filters: Optional[List[str]] = None,
    poll_sec: float = 0.5,
    heartbeat_sec: float = 15.0,
    should_continue: Optional[Callable[[], bool]] = None,
) -> Iterator[str]:
    """Yield SSE frames for new bus events (blocking generator for HTTP stream)."""
    last_seq = max(0, int(since_seq))
    last_heartbeat = time.time()
    should_continue = should_continue or (lambda: True)

    backlog = read_events_since(last_seq, topic_filters=topic_filters, limit=100)
    for event in backlog:
        last_seq = max(last_seq, int(event.get("seq") or 0))
        yield format_sse("bus.event", event)

    yield format_sse("bus.connected", {"since_seq": last_seq, "topic_filters": topic_filters or []})

    while should_continue():
        events = read_events_since(last_seq, topic_filters=topic_filters, limit=100)
        for event in events:
            last_seq = max(last_seq, int(event.get("seq") or 0))
            yield format_sse("bus.event", event)

        now = time.time()
        if now - last_heartbeat >= heartbeat_sec:
            yield format_sse_comment("heartbeat")
            yield format_sse("bus.heartbeat", {"ts": now, "since_seq": last_seq})
            last_heartbeat = now

        time.sleep(poll_sec)
