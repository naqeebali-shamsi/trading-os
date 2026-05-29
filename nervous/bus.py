#!/usr/bin/env python3
"""
nervous/bus.py -- Spinal Cord
-----------------------------
Append-only JSONLines event stream.
Every layer publishes/subscribes through here.
No direct coupling between components.

Single source of truth: bus.jsonl
Per-topic indexes: topics/<topic>.jsonl (auto-generated)
"""
import json, os, sys, time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    try:
        from nervous.event_schemas import base_topic, validate_event
    except Exception:
        from event_schemas import base_topic, validate_event
    from data_lake import persist_candle, persist_signal_evaluation
except Exception:  # Keep the spinal cord alive even during partial deploy/import repair.
    base_topic = lambda topic: topic
    validate_event = lambda topic, payload: (True, [])
    persist_candle = None
    persist_signal_evaluation = None

from ops.file_lock import exclusive_lock  # noqa: E402

BUS_ROOT = Path(__file__).resolve().parent
_PUBLISH_LOCK = BUS_ROOT / ".publish.lock"
BUS_FILE = BUS_ROOT / "bus.jsonl"
TOPICS_DIR = BUS_ROOT / "topics"
QUARANTINE_FILE = BUS_ROOT / "quarantine.jsonl"


# ------------------------------------------------------------------
def _ensure():
    BUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)


def _quarantine(topic, payload, errors, meta=None):
    """Persist invalid critical events without recursive bus publish."""
    _ensure()
    event = {
        "ts": time.time(),
        "topic": topic,
        "payload": payload,
        "meta": meta or {},
        "errors": list(errors or []),
    }
    with exclusive_lock(QUARANTINE_FILE):
        with open(QUARANTINE_FILE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")
            handle.flush()


def _persist_derived(event):
    """Best-effort durable data lake writes for critical research streams."""
    try:
        canonical = base_topic(event.get("topic", ""))
        payload = event.get("payload") or {}
        if canonical == "candle.close" and persist_candle is not None:
            persist_candle(payload, source_topic=event.get("topic"), seq=event.get("seq"))
        elif canonical == "market.signal.evaluation" and persist_signal_evaluation is not None:
            persist_signal_evaluation(payload, source_topic=event.get("topic"), seq=event.get("seq"))
    except Exception:
        # Bus publishing must never fail because analytics persistence is down.
        return

# ------------------------------------------------------------------
def publish(topic, payload, meta=None):
    """Append event to bus + update topic index. Thread-safe via flock."""
    _ensure()
    valid, errors = validate_event(topic, payload)
    if not valid:
        _quarantine(topic, payload, errors, meta=meta)
        return None
    event = {
        "ts": time.time(),
        "topic": topic,
        "payload": payload,
        "meta": meta or {},
        "seq": None,
    }
    tfile = TOPICS_DIR / f"{topic}.jsonl"
    with exclusive_lock(_PUBLISH_LOCK):
        event["seq"] = _next_seq()
        encoded = json.dumps(event)
        with open(BUS_FILE, "a", encoding="utf-8") as f:
            f.write(encoded + "\n")
            f.flush()
        with open(tfile, "a", encoding="utf-8") as f:
            f.write(encoded + "\n")
            f.flush()
    _persist_derived(event)
    return event["seq"]

# ------------------------------------------------------------------
def subscribe(topic, since_seq=0, limit=100):
    """Read events from a topic."""
    tfile = TOPICS_DIR / f"{topic}.jsonl"
    if not tfile.exists():
        return []
    events = []
    with open(tfile, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if ev.get("seq", 0) > since_seq:
                    events.append(ev)
            except json.JSONDecodeError:
                continue
    return events[-limit:]

def _tail_lines_from_end(path: Path, n: int, *, chunk_size: int = 65536) -> list:
    """Read the last *n* non-empty lines from a file without scanning from BOF."""
    if n <= 0:
        return []
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        if end == 0:
            return []
        offset = end
        remainder = b""
        collected_rev: list = []
        while offset > 0 and len(collected_rev) < n:
            read_size = min(chunk_size, offset)
            offset -= read_size
            handle.seek(offset)
            block = handle.read(read_size) + remainder
            lines = block.split(b"\n")
            remainder = lines[0]
            for raw_line in reversed(lines[1:]):
                line = raw_line.strip()
                if not line:
                    continue
                collected_rev.append(line)
                if len(collected_rev) >= n:
                    break
        if len(collected_rev) < n and offset == 0 and remainder.strip():
            collected_rev.append(remainder.strip())
    collected_rev.reverse()
    return collected_rev[:n]


# ------------------------------------------------------------------
def tail(n=20, topics=None):
    """Tail last N events across bus, optionally filtered by topics."""
    if not BUS_FILE.exists():
        return []
    if topics is None:
        lines = _tail_lines_from_end(BUS_FILE, n)
        results = []
        for line in lines:
            try:
                text = line.decode("utf-8") if isinstance(line, bytes) else line
                results.append(json.loads(text))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return results

    results = deque(maxlen=n)
    with open(BUS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if topics is None or ev.get("topic") in topics:
                    results.append(ev)
            except json.JSONDecodeError:
                continue
    return list(results)

# ------------------------------------------------------------------
_SEQ_FILE = BUS_ROOT / ".seq"


def _next_seq():
    """Atomically allocate the next bus sequence number.

    Callers must hold ``exclusive_lock(_PUBLISH_LOCK)`` (see ``publish``).
    """
    _ensure()
    raw = _SEQ_FILE.read_text(encoding="utf-8").strip() if _SEQ_FILE.exists() else ""
    try:
        seq = int(raw) + 1 if raw else 1
    except ValueError:
        seq = 1
    _SEQ_FILE.write_text(str(seq), encoding="utf-8")
    return seq


def current_seq(default=0):
    """Return latest allocated bus sequence without incrementing it."""
    if not _SEQ_FILE.exists():
        return default
    try:
        return int(_SEQ_FILE.read_text().strip() or default)
    except ValueError:
        return default


# ------------------------------------------------------------------
def query(payload_keys=None, since=None, until=None):
    """Query bus by time range or payload key filters."""
    if not BUS_FILE.exists():
        return []
    results = []
    with open(BUS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                ts = ev.get("ts", 0)
                if since and ts < since:
                    continue
                if until and ts > until:
                    continue
                if payload_keys:
                    p = ev.get("payload", {})
                    if not any(k in p for k in payload_keys):
                        continue
                results.append(ev)
            except json.JSONDecodeError:
                continue
    return results[-500:]


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: bus.py <publish|tail|query>")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "publish" and len(sys.argv) >= 4:
        seq = publish(sys.argv[2], json.loads(sys.argv[3]))
        print(f"Published seq={seq}")
    elif cmd == "tail":
        for ev in tail(10):
            print(json.dumps(ev))
    elif cmd == "query":
        for ev in query():
            print(json.dumps(ev))
