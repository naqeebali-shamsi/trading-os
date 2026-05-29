"""Parse MT5 IPC tick lines and derive broker quote age (vs wall clock).

EA format: SYMBOL,bid,ask,quote_epoch
"""
from __future__ import annotations

import time
from typing import Any, Optional


def parse_tick_text(text: str, *, now: Optional[float] = None) -> Optional[dict[str, Any]]:
    """Parse a tick line from tick.txt into a normalized dict."""
    if not text or not str(text).strip():
        return None
    parts = [p.strip() for p in str(text).split(",")]
    if len(parts) < 3:
        return None
    try:
        bid = float(parts[1])
        ask = float(parts[2])
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None

    wall = time.time() if now is None else float(now)
    quote_ts: Optional[float] = None
    quote_age_sec: Optional[float] = None
    if len(parts) > 3 and parts[3]:
        try:
            quote_ts = float(parts[3])
            quote_age_sec = max(0.0, wall - quote_ts)
        except (TypeError, ValueError):
            quote_ts = None
            quote_age_sec = None

    return {
        "symbol": parts[0],
        "bid": bid,
        "ask": ask,
        "quote_ts": quote_ts,
        "quote_age_sec": quote_age_sec,
        "raw": str(text)[:120],
    }


def enrich_tick_payload(tick: dict[str, Any], *, now: Optional[float] = None) -> dict[str, Any]:
    """Ensure quote_ts / quote_age_sec exist on a tick dict (mutates copy)."""
    out = dict(tick)
    wall = time.time() if now is None else float(now)
    quote_ts = out.get("quote_ts")
    if quote_ts is None and out.get("time") is not None:
        try:
            quote_ts = float(out["time"])
            out["quote_ts"] = quote_ts
        except (TypeError, ValueError):
            quote_ts = None
    if out.get("quote_age_sec") is None and quote_ts is not None:
        out["quote_age_sec"] = max(0.0, wall - float(quote_ts))
    return out
