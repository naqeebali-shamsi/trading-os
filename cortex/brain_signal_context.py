"""Compact signal-engine context for the AgentBrain LLM path.

The pattern signal generator publishes rich audit data on the bus. This module
summarizes emitted signals, near-miss candidates, blocks, and evaluation rows
so the brain can reason about *why* trades did or did not fire.
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

SIGNAL_TOPICS = (
    "market.signal",
    "market.signal.evaluation",
    "market.signal.candidate",
    "market.signal.blocked",
)

DEFAULT_WINDOW_SEC = 3600.0


def _pattern_names(patterns: Any, limit: int = 5) -> List[str]:
    if not isinstance(patterns, list):
        return []
    names: List[str] = []
    for item in patterns[:limit]:
        if isinstance(item, dict):
            name = item.get("pattern")
            if name:
                names.append(str(name))
        elif item:
            names.append(str(item))
    return names


def compact_intent(payload: dict) -> dict:
    """Keep order-relevant fields without dumping full sizing blobs."""
    if not isinstance(payload, dict):
        return {}
    row = {
        "symbol": payload.get("symbol"),
        "side": payload.get("side"),
        "confidence": payload.get("confidence"),
        "strategy_id": payload.get("strategy_id"),
        "timeframe": payload.get("timeframe"),
        "reason": payload.get("reason") or payload.get("blocked_reason"),
        "patterns": _pattern_names(payload.get("patterns")),
        "sl": payload.get("sl"),
        "tp": payload.get("tp"),
        "qty": payload.get("qty"),
    }
    if payload.get("min_confidence") is not None:
        row["min_confidence"] = payload.get("min_confidence")
    if payload.get("direct_intents_enabled") is not None:
        row["direct_intents_enabled"] = payload.get("direct_intents_enabled")
    return {k: v for k, v in row.items() if v is not None}


def compact_evaluation(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    row = {
        "symbol": payload.get("symbol"),
        "timeframe": payload.get("timeframe"),
        "status": payload.get("status"),
        "reason": payload.get("reason"),
        "stage": payload.get("stage"),
        "confidence": payload.get("confidence"),
        "patterns": _pattern_names(payload.get("patterns")),
    }
    if payload.get("min_confidence") is not None:
        row["min_confidence"] = payload.get("min_confidence")
    return {k: v for k, v in row.items() if v is not None}


def signal_gate_snapshot(controls: Optional[dict] = None) -> dict:
    if controls is None:
        try:
            from runtime_controls import load_controls

            controls = load_controls()
        except Exception:
            controls = {}
    keys = (
        "signal_direct_intents",
        "stock_direct_intents",
        "signal_min_confidence",
        "signal_min_candles",
        "signal_timeframes",
        "signal_macro_gate",
        "signal_macro_gate_max_age_sec",
    )
    return {key: controls.get(key) for key in keys if key in controls}


def load_recent_signal_events(
    *,
    limit_evaluations: int = 40,
    limit_emitted: int = 10,
    limit_candidates: int = 10,
    limit_blocked: int = 10,
) -> List[dict]:
    from bus import subscribe

    events: List[dict] = []
    for topic, limit in (
        ("market.signal.evaluation", limit_evaluations),
        ("market.signal", limit_emitted),
        ("market.signal.candidate", limit_candidates),
        ("market.signal.blocked", limit_blocked),
    ):
        for ev in subscribe(topic, limit=limit):
            events.append(
                {
                    "topic": topic,
                    "ts": ev.get("ts"),
                    "seq": ev.get("seq"),
                    "payload": ev.get("payload") or {},
                }
            )
    return events


def _events_for_topics(recent_events: Iterable[dict], topics: set[str]) -> List[dict]:
    rows = [ev for ev in recent_events if ev.get("topic") in topics]
    rows.sort(key=lambda ev: float(ev.get("ts") or 0))
    return rows


def build_signal_context(
    recent_events: Optional[Iterable[dict]] = None,
    *,
    now: Optional[float] = None,
    window_sec: float = DEFAULT_WINDOW_SEC,
    max_emitted: int = 5,
    max_candidates: int = 5,
    max_blocked: int = 5,
    max_evaluations: int = 20,
    controls: Optional[dict] = None,
) -> dict:
    """Summarize recent signal-engine activity for LLM context."""
    now = time.time() if now is None else now
    since = now - window_sec
    events = list(recent_events or [])
    topic_set = set(SIGNAL_TOPICS)
    if not any(str(ev.get("topic") or "") in topic_set for ev in events):
        events.extend(load_recent_signal_events())

    filtered = [
        ev
        for ev in events
        if ev.get("topic") in topic_set and float(ev.get("ts") or 0) >= since
    ]
    filtered.sort(key=lambda ev: float(ev.get("ts") or 0))

    emitted: List[dict] = []
    candidates: List[dict] = []
    blocked: List[dict] = []
    evaluations: List[dict] = []
    reason_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()

    for ev in filtered:
        topic = str(ev.get("topic") or "")
        payload = ev.get("payload") or {}
        ts = float(ev.get("ts") or 0)
        if topic == "market.signal":
            row = compact_intent(payload)
            row["ts"] = ts
            emitted.append(row)
        elif topic == "market.signal.candidate":
            row = compact_intent(payload)
            row["ts"] = ts
            candidates.append(row)
        elif topic == "market.signal.blocked":
            row = compact_intent(payload)
            row["ts"] = ts
            blocked.append(row)
        elif topic == "market.signal.evaluation":
            row = compact_evaluation(payload)
            row["ts"] = ts
            evaluations.append(row)
            reason_counts[str(payload.get("reason") or "unknown")] += 1
            status_counts[str(payload.get("status") or "unknown")] += 1

    latest_by_symbol: dict[str, dict] = {}
    for row in reversed(evaluations):
        symbol = str(row.get("symbol") or "").upper()
        if symbol and symbol not in latest_by_symbol:
            latest_by_symbol[symbol] = row

    return {
        "window_sec": int(window_sec),
        "gates": signal_gate_snapshot(controls),
        "emitted": emitted[-max_emitted:],
        "candidates": candidates[-max_candidates:],
        "blocked": blocked[-max_blocked:],
        "evaluation_summary": {
            "total": len(evaluations),
            "by_reason": dict(reason_counts.most_common(8)),
            "by_status": dict(status_counts),
            "latest_per_symbol": list(latest_by_symbol.values())[:12],
            "recent": evaluations[-max_evaluations:],
        },
    }


SIGNAL_TRIGGER_MAX_AGE_SEC = 900.0
NEAR_MISS_BLOCK_REASONS = {
    "below_min_confidence",
    "macro_gate",
    "warming_up",
}


def detect_signal_brain_trigger(
    recent_events: Iterable[dict],
    *,
    now: Optional[float] = None,
    max_age_sec: float = SIGNAL_TRIGGER_MAX_AGE_SEC,
) -> tuple[bool, str]:
    """Return whether recent signal-engine activity should invoke the brain."""
    now = time.time() if now is None else now
    since = now - max_age_sec
    emitted = 0
    near_miss = 0
    pattern_blocked = 0

    for ev in recent_events:
        topic = str(ev.get("topic") or "")
        if topic not in SIGNAL_TOPICS:
            continue
        if float(ev.get("ts") or 0) < since:
            continue
        payload = ev.get("payload") or {}
        if topic == "market.signal":
            emitted += 1
        elif topic == "market.signal.candidate":
            reason = str(payload.get("blocked_reason") or payload.get("reason") or "")
            if reason in NEAR_MISS_BLOCK_REASONS:
                near_miss += 1
        elif topic == "market.signal.evaluation":
            if payload.get("patterns") and payload.get("status") in {"blocked", "passed"}:
                if payload.get("reason") not in {"timeframe_disabled", "symbol_disabled", "no_patterns"}:
                    pattern_blocked += 1

    if emitted:
        return True, "signal_emitted"
    if near_miss:
        return True, "signal_near_miss"
    if pattern_blocked:
        return True, "signal_pattern_review"
    return False, "no_signal_trigger"
