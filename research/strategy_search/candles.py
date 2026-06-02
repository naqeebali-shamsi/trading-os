"""OHLC candle helpers for pattern-based strategy search."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from data_lake import DATA_ROOT
from research.dataset_builder import iter_jsonl


def row_to_candle(row: dict) -> Optional[dict]:
    close = row.get("close")
    if close is None:
        return None
    try:
        close_f = float(close)
    except (TypeError, ValueError):
        return None
    open_price = row.get("open_price", row.get("open", close_f))
    high = row.get("high", close_f)
    low = row.get("low", close_f)
    try:
        open_f = float(open_price)
        high_f = float(high)
        low_f = float(low)
    except (TypeError, ValueError):
        return None
    body = abs(close_f - open_f)
    rng = high_f - low_f
    if rng <= 0:
        rng = max(body, close_f * 1e-6)
    return {
        "open_price": open_f,
        "high": high_f,
        "low": low_f,
        "close": close_f,
        "body_size": body,
        "range": rng,
        "upper_shadow": high_f - max(open_f, close_f),
        "lower_shadow": min(open_f, close_f) - low_f,
        "is_bullish": close_f >= open_f,
        "ts_close": row.get("ts_close"),
    }


def synthetic_candle_from_close(close: float, *, prev_close: float | None = None) -> dict:
    """Build minimal OHLC from close-only series (fallback — weaker pattern fidelity)."""
    open_f = float(prev_close if prev_close is not None else close)
    close_f = float(close)
    spread = abs(close_f - open_f) or close_f * 0.0001
    high_f = max(open_f, close_f) + spread * 0.25
    low_f = min(open_f, close_f) - spread * 0.25
    body = abs(close_f - open_f)
    rng = high_f - low_f
    return {
        "open_price": open_f,
        "high": high_f,
        "low": low_f,
        "close": close_f,
        "body_size": body,
        "range": rng if rng > 0 else max(body, close_f * 1e-6),
        "upper_shadow": high_f - max(open_f, close_f),
        "lower_shadow": min(open_f, close_f) - low_f,
        "is_bullish": close_f >= open_f,
    }


def closes_to_candles(closes: List[float]) -> List[dict]:
    candles: List[dict] = []
    prev = None
    for close in closes:
        candles.append(synthetic_candle_from_close(close, prev_close=prev))
        prev = close
    return candles


def candles_from_rows(rows: List[dict]) -> List[dict]:
    candles: List[dict] = []
    for row in sorted(rows, key=lambda r: float(r.get("ts_close") or 0.0)):
        candle = row_to_candle(row)
        if candle:
            candles.append(candle)
    if candles:
        return candles
    closes = []
    for row in sorted(rows, key=lambda r: float(r.get("ts_close") or 0.0)):
        close = row.get("close")
        if close is not None:
            try:
                closes.append(float(close))
            except (TypeError, ValueError):
                continue
    return closes_to_candles(closes)


def _candle_lake_paths(symbol: str, timeframe: str) -> List[Path]:
    sym = symbol.upper()
    tf = timeframe.upper()
    return [
        DATA_ROOT / "lake" / "candles" / f"symbol={sym}" / f"timeframe={tf}" / "candles.jsonl",
        DATA_ROOT / f"symbol={sym}" / f"timeframe={tf}" / "candles.jsonl",
    ]


def load_candles_candle_lake(symbol: str, timeframe: str, *, limit: int = 1200) -> List[dict]:
    for path in _candle_lake_paths(symbol, timeframe):
        if not path.exists():
            continue
        candles: List[dict] = []
        for _, row, err in iter_jsonl(path):
            if err or not row:
                continue
            candle = row_to_candle(row)
            if candle:
                candles.append(candle)
        if candles:
            return candles[-limit:]
    return []
