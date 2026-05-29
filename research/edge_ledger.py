"""Append-only ledger of edge candidates and their forward outcomes.

A *candidate* is one forecast observation (symbol, timeframe, direction, entry
price, entry timestamp). After a fixed forward horizon elapses we *label* the
candidate with the realised price move and a win/loss for its direction. Both
candidates and labels are stored as JSONL so the daemon can keep appending
without rewriting history. Everything here is idempotent: re-ingesting the same
event or re-labelling the same candidate is a no-op.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_PATH = ROOT / "intel" / "edge_candidates.jsonl"
LABEL_PATH = ROOT / "intel" / "edge_labels.jsonl"

HORIZON_SECONDS = 3600

PriceLookup = Callable[[Any, Any], Optional[float]]


def _resolve_ts(*values: Any) -> Optional[float]:
    """First value that is a real number. ``0.0`` is valid, not falsy."""
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _candidate_key(symbol: str, timeframe: str, entry_ts: float, direction: str) -> str:
    return f"{symbol}|{timeframe}|{entry_ts}|{direction}"


def _read_jsonl(path: Path) -> List[dict]:
    """Read a JSONL file, skipping blank or corrupt lines."""
    if not path.exists():
        return []
    rows: List[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append_jsonl(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def _candidate_from_event(event: dict) -> Optional[dict]:
    """Turn a supported bus event into a candidate, or ``None`` if unusable."""
    if not isinstance(event, dict):
        return None
    if event.get("topic") != "market.forecast":
        return None

    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    symbol = payload.get("symbol")
    timeframe = payload.get("timeframe")
    direction = payload.get("direction")
    entry_price = _resolve_ts(payload.get("last_close"))
    entry_ts = _resolve_ts(event.get("ts"), payload.get("ts"))

    if not symbol or not timeframe or not direction:
        return None
    if entry_price is None or entry_ts is None:
        return None

    symbol = str(symbol).upper()
    timeframe = str(timeframe)
    direction = str(direction).lower()

    return {
        "key": _candidate_key(symbol, timeframe, entry_ts, direction),
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "entry_ts": entry_ts,
        "entry_price": entry_price,
        "horizon_sec": HORIZON_SECONDS,
        "exit_ts": entry_ts + HORIZON_SECONDS,
    }


def ingest_events(events: List[dict], path: Path = CANDIDATE_PATH) -> int:
    """Append new candidates parsed from bus events. Returns count appended."""
    existing = {row.get("key") for row in _read_jsonl(path)}
    fresh: List[dict] = []
    seen: set = set()
    for event in events or []:
        candidate = _candidate_from_event(event)
        if candidate is None:
            continue
        key = candidate["key"]
        if key in existing or key in seen:
            continue
        seen.add(key)
        fresh.append(candidate)
    if fresh:
        _append_jsonl(path, fresh)
    return len(fresh)


def load_candidates(path: Path = CANDIDATE_PATH) -> List[dict]:
    return _read_jsonl(path)


def load_labels(path: Path = LABEL_PATH) -> List[dict]:
    return _read_jsonl(path)


def _label_for(candidate: dict, exit_price: float) -> dict:
    entry_price = float(candidate["entry_price"])
    direction = candidate.get("direction", "up")
    ret = (exit_price - entry_price) / entry_price if entry_price else 0.0
    signed_ret = ret if direction == "up" else -ret
    return {
        "key": candidate["key"],
        "symbol": candidate["symbol"],
        "timeframe": candidate["timeframe"],
        "direction": direction,
        "entry_ts": candidate["entry_ts"],
        "exit_ts": candidate["exit_ts"],
        "entry_price": entry_price,
        "exit_price": exit_price,
        "return": ret,
        "signed_return": signed_ret,
        "win": signed_ret > 0,
    }


def label_candidates(
    price_lookup: PriceLookup,
    candidate_path: Path = CANDIDATE_PATH,
    label_path: Path = LABEL_PATH,
    now: Optional[float] = None,
) -> int:
    """Label candidates whose forward horizon has closed. Returns count appended."""
    now = time.time() if now is None else float(now)
    labeled = {row.get("key") for row in _read_jsonl(label_path)}
    fresh: List[dict] = []
    seen: set = set()
    for candidate in _read_jsonl(candidate_path):
        key = candidate.get("key")
        if not key or key in labeled or key in seen:
            continue
        entry_ts = _resolve_ts(candidate.get("entry_ts"))
        if entry_ts is None:
            continue
        exit_ts = entry_ts + HORIZON_SECONDS
        if now < exit_ts:
            continue
        exit_price = price_lookup(candidate.get("symbol"), exit_ts)
        if exit_price is None:
            continue
        try:
            exit_price = float(exit_price)
        except (TypeError, ValueError):
            continue
        seen.add(key)
        fresh.append(_label_for(candidate, exit_price))
    if fresh:
        _append_jsonl(label_path, fresh)
    return len(fresh)
