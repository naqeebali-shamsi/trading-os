"""Multi-timeframe market structure for AgentBrain context.

Reads completed candles from the bus (not the in-process OHLC singleton) so the
cortex brain process sees the same history as signal_generator_v2.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "sensory") not in sys.path:
    sys.path.insert(0, str(ROOT / "sensory"))
if str(ROOT / "nervous") not in sys.path:
    sys.path.insert(0, str(ROOT / "nervous"))

RESEARCH_SNAPSHOT = ROOT / "intel" / "stock_research_latest.json"

DEFAULT_TIMEFRAMES = ("M5", "M15", "H1")
DEFAULT_MAX_SYMBOLS = 8
DEFAULT_CANDLES_PER_TF = 30


def _describe_trend(candles: List[dict]) -> str:
    if len(candles) < 5:
        return "indeterminate"
    lows = [float(c.get("low") or 0) for c in candles[-10:]]
    highs = [float(c.get("high") or 0) for c in candles[-10:]]
    if lows[-1] > lows[0] and highs[-1] > highs[0]:
        return "uptrend"
    if lows[-1] < lows[0] and highs[-1] < highs[0]:
        return "downtrend"
    return "range/consolidation"


def _compact_candle(candle: dict) -> dict:
    return {
        "close": candle.get("close"),
        "open": candle.get("open_price"),
        "high": candle.get("high"),
        "low": candle.get("low"),
        "ts_close": candle.get("ts_close"),
    }


def resolve_context_symbols(
    *,
    health: Optional[dict] = None,
    recent_events: Optional[Iterable[dict]] = None,
    max_symbols: int = DEFAULT_MAX_SYMBOLS,
) -> List[str]:
    symbols: set[str] = set()
    if isinstance(health, dict):
        for item in health.get("enabled_stocks") or []:
            text = str(item)
            if ":READY" in text.upper():
                symbols.add(text.split(":", 1)[0].upper())
        ipc = (health.get("ipc_mode") or {}).get("fresh_charts") or []
        for chart in ipc:
            name = str(chart).replace("chart_", "").upper()
            if name:
                symbols.add(name)

    for ev in recent_events or []:
        payload = ev.get("payload") or {}
        symbol = str(payload.get("symbol") or "").upper()
        if symbol:
            symbols.add(symbol)

    if not symbols:
        try:
            from cortex.instrument_registry import load_registry

            reg = load_registry()
            for symbol, meta in (reg.symbols or {}).items():
                if meta.get("enabled", True):
                    symbols.add(str(symbol).upper())
        except Exception:
            symbols.update({"EURUSD", "GBPUSD", "USDJPY", "XAUUSD"})

    priority = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "NVDA", "MSFT", "AAPL", "TSLA"]
    ordered = [sym for sym in priority if sym in symbols]
    ordered.extend(sorted(sym for sym in symbols if sym not in ordered))
    return ordered[:max_symbols]


def load_candle_history_from_bus(
    symbols: Sequence[str],
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    *,
    per_tf_limit: int = DEFAULT_CANDLES_PER_TF,
) -> Dict[Tuple[str, str], deque]:
    from bus import subscribe

    history: Dict[Tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=per_tf_limit))
    tf_set = {str(tf).upper() for tf in timeframes}
    for symbol in symbols:
        sym = str(symbol).upper()
        events = subscribe(f"candle.close.{sym}", limit=per_tf_limit * len(timeframes) * 2)
        if not events:
            events = [
                ev
                for ev in subscribe("candle.close", limit=per_tf_limit * len(timeframes) * max(len(symbols), 1) * 2)
                if str((ev.get("payload") or {}).get("symbol") or "").upper() == sym
            ]
        for ev in events:
            payload = ev.get("payload") or {}
            tf = str(payload.get("timeframe") or "").upper()
            if tf not in tf_set:
                continue
            key = (sym, tf)
            ts_close = payload.get("ts_close")
            if history[key] and history[key][-1].get("ts_close") == ts_close:
                continue
            history[key].append(payload)
    return history


def build_symbol_structure(
    symbol: str,
    history: Dict[Tuple[str, str], deque],
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
) -> dict:
    from candle_patterns import scan as pattern_scan

    sym = str(symbol).upper()
    tf_rows: Dict[str, dict] = {}
    for tf in timeframes:
        candles = list(history.get((sym, str(tf).upper()), []))
        if not candles:
            continue
        patterns = pattern_scan(candles, sym, tf) if len(candles) >= 5 else []
        tf_rows[str(tf).upper()] = {
            "last": _compact_candle(candles[-1]),
            "trend": _describe_trend(candles),
            "patterns": [
                {
                    "name": p.get("pattern"),
                    "direction": p.get("direction"),
                    "strength": p.get("strength"),
                }
                for p in patterns[:3]
            ],
            "candles": len(candles),
        }
    return tf_rows


def load_research_context(limit: int = 10) -> dict:
    """Load latest fundamental research snapshot for AgentBrain context."""
    if not RESEARCH_SNAPSHOT.exists():
        return {"available": False}
    try:
        payload = json.loads(RESEARCH_SNAPSHOT.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"available": False}
    top = (payload.get("top_picks") or [])[:limit]
    mb = (payload.get("multibagger_candidates") or [])[:limit]
    compact = []
    for row in top:
        compact.append(
            {
                "symbol": row.get("symbol"),
                "tier": row.get("tier"),
                "confidence": row.get("confidence"),
                "composite_score": row.get("composite_score"),
                "thesis": row.get("thesis"),
                "factors": row.get("factors"),
            }
        )
    return {
        "available": True,
        "ts": payload.get("ts"),
        "top_picks": compact,
        "multibagger_candidates": [
            {"symbol": r.get("symbol"), "confidence": r.get("confidence"), "thesis": r.get("thesis")} for r in mb
        ],
    }


def build_market_structure_context(
    *,
    health: Optional[dict] = None,
    recent_events: Optional[Iterable[dict]] = None,
    symbols: Optional[Sequence[str]] = None,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    max_symbols: int = DEFAULT_MAX_SYMBOLS,
) -> dict:
    """Compact multi-TF OHLC + pattern context for the LLM."""
    symbol_list = list(symbols or resolve_context_symbols(health=health, recent_events=recent_events, max_symbols=max_symbols))
    if not symbol_list:
        return {"symbols": {}, "timeframes": list(timeframes)}

    history = load_candle_history_from_bus(symbol_list, timeframes)
    snapshot: Dict[str, dict] = {}
    for sym in symbol_list:
        rows = build_symbol_structure(sym, history, timeframes=timeframes)
        if rows:
            snapshot[sym] = rows

    return {
        "timeframes": [str(tf).upper() for tf in timeframes],
        "symbols": snapshot,
        "stock_research": load_research_context(),
        "built_ts": time.time(),
    }
