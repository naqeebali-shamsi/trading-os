"""
autonome/data/vix_feed.py
Lightweight VIX fetcher from Yahoo Finance with 15-min cache.
Non-blocking; returns None on failure.
"""
from __future__ import annotations

import logging
import urllib.request
import json
from datetime import datetime, timezone
from typing import Optional, Tuple

log = logging.getLogger("vix_feed")

_CACHE_TTL_SEC = 15 * 60
_vix_cache: Optional[Tuple[float, datetime]] = None
_last_fetch_attempt: Optional[datetime] = None


def fetch_vix() -> Optional[Tuple[float, datetime]]:
    """Fetch latest VIX value from Yahoo Finance. Cached for 15 min."""
    global _vix_cache, _last_fetch_attempt

    now = datetime.now(timezone.utc)

    # Return cached if fresh
    if _vix_cache is not None:
        value, cached_at = _vix_cache
        if (now - cached_at).total_seconds() < _CACHE_TTL_SEC:
            return value, cached_at

    # Don't hammer on repeated failures within 5 min
    if _last_fetch_attempt is not None and (now - _last_fetch_attempt).total_seconds() < 300:
        return _vix_cache

    _last_fetch_attempt = now

    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=1d"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; trading-os/2.0)"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.warning("VIX fetch failed: %s", exc)
        return _vix_cache  # return stale cache if available, else None

    try:
        result = data["chart"]["result"][0]
        meta = result["meta"]
        timestamps = result.get("timestamp", [])
        values = result["indicators"]["quote"][0].get("close", [])

        # Prefer regularMarketPrice if available (real-time), else last close
        raw = meta.get("regularMarketPrice")
        if raw is None and values and timestamps:
            raw = values[-1]

        if raw is None:
            log.warning("VIX response contained no price data")
            return _vix_cache

        value = float(raw)
        ts = now
        if timestamps:
            ts = datetime.fromtimestamp(timestamps[-1], tz=timezone.utc)

        _vix_cache = (value, ts)
        log.info("VIX fetched: %.2f @ %s", value, ts.isoformat())
        return _vix_cache
    except Exception as exc:
        log.warning("VIX parse failed: %s", exc)
        return _vix_cache
