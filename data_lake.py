#!/usr/bin/env python3
"""Durable append-only data lake helpers.

The first iteration intentionally uses JSONL plus small idempotency sidecars so it
works on Windows/WSL without extra services. Later steps can compact this into
DuckDB/Parquet once schemas stabilize.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from ops.file_lock import exclusive_lock

ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.getenv("TRADING_OS_DATA_ROOT", str(ROOT / "data")))
TRAINING_ROOT = Path(os.getenv("TRADING_OS_TRAINING_ROOT", str(ROOT / "memory" / "training")))

_SAFE = re.compile(r"[^A-Za-z0-9_.=-]+")


def safe_part(value: Any) -> str:
    text = str(value or "UNKNOWN").strip().upper()
    return _SAFE.sub("_", text) or "UNKNOWN"


def append_jsonl_dedupe(path: Path, row: dict, key: str | None = None) -> bool:
    """Append row once. Returns True when written, False on duplicate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dict(row)
    tmp.setdefault("persisted_ts", time.time())
    if key:
        ids_path = path.with_suffix(path.suffix + ".ids")
        with exclusive_lock(path):
            seen = set(ids_path.read_text(encoding="utf-8").splitlines()) if ids_path.exists() else set()
            if key in seen:
                return False
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(tmp, sort_keys=True, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            with open(ids_path, "a", encoding="utf-8") as ids:
                ids.write(key + "\n")
                ids.flush()
                os.fsync(ids.fileno())
        return True

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(tmp, sort_keys=True, default=str) + "\n")
    return True


def persist_candle(candle: dict, *, source_topic: str | None = None, seq: int | None = None) -> bool:
    symbol = safe_part(candle.get("symbol"))
    timeframe = safe_part(candle.get("timeframe"))
    ts_close = candle.get("ts_close")
    path = DATA_ROOT / "lake" / "candles" / f"symbol={symbol}" / f"timeframe={timeframe}" / "candles.jsonl"
    key = f"{symbol}|{timeframe}|{ts_close}"
    row = {**candle, "source_topic": source_topic, "bus_seq": seq}
    return append_jsonl_dedupe(path, row, key=key)


def persist_signal_evaluation(evaluation: dict, *, source_topic: str | None = None, seq: int | None = None) -> bool:
    symbol = safe_part(evaluation.get("symbol"))
    timeframe = safe_part(evaluation.get("timeframe"))
    ts_close = evaluation.get("ts_close", evaluation.get("candle_ts_close", "unknown"))
    stage = safe_part(evaluation.get("stage", evaluation.get("status", "unknown")))
    reason = safe_part(evaluation.get("reason", "unknown"))
    path = TRAINING_ROOT / "signal_evaluations.jsonl"
    key = f"{symbol}|{timeframe}|{ts_close}|{stage}|{reason}|{evaluation.get('order_id', '')}"
    row = {**evaluation, "source_topic": source_topic, "bus_seq": seq}
    return append_jsonl_dedupe(path, row, key=key)
