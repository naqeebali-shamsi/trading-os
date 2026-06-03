"""
autonome/data/yahoo_feed.py  v1.0
Lightweight Yahoo Finance chart API fetcher.
No external deps — uses requests + standard library.
"""
from __future__ import annotations

import time, logging
from datetime import datetime, timezone
from typing import List, Optional

import requests

from autonome.data.bars import Bar

log = logging.getLogger("data.yahoo")

# Yahoo chart endpoint (undocumented but stable)
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _yahoo_interval(tf: str) -> str:
    """Map our timeframe strings to Yahoo intervals."""
    mapping = {
        "1m": "1m", "2m": "2m", "5m": "5m", "15m": "15m", "30m": "30m",
        "60m": "1h", "1h": "1h", "1Hour": "1h",
        "90m": "90m",
        "1d": "1d", "1D": "1d", "daily": "1d",
        "5d": "5d", "1wk": "1wk", "1mo": "1mo",
    }
    return mapping.get(tf, tf)


def fetch_history(
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str = "1d",
    prepost: bool = False,
    retries: int = 3,
) -> List[Bar]:
    """
    Fetch historical bars from Yahoo Finance.
    Returns empty list on failure (never raises).
    """
    interval = _yahoo_interval(timeframe)
    period1 = int(start.timestamp())
    period2 = int(end.timestamp())

    params = {
        "period1": period1,
        "period2": period2,
        "interval": interval,
        "events": "history",
        "includeAdjustedClose": "true",
    }
    if prepost:
        params["prepost"] = "true"

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    url = YAHOO_CHART.format(symbol=symbol)

    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()

            result = data.get("chart", {}).get("result", [None])[0]
            if result is None:
                meta = data.get("chart", {}).get("error", {})
                log.warning("Yahoo error for %s: %s", symbol, meta)
                return []

            timestamps = result["timestamp"]
            ohlcv = result["indicators"]["quote"][0]
            adjclose = result["indicators"].get("adjclose", [{}])[0].get("adjclose")

            bars = []
            for i, ts in enumerate(timestamps):
                if ohlcv["open"][i] is None:
                    continue
                t = datetime.fromtimestamp(ts, tz=timezone.utc)
                # Use adjusted close if available
                close = adjclose[i] if adjclose and adjclose[i] is not None else ohlcv["close"][i]
                # Adjust open/high/low proportionally
                adj_ratio = close / ohlcv["close"][i] if ohlcv["close"][i] else 1.0
                bars.append(Bar(
                    symbol=symbol,
                    t=t,
                    open=round(ohlcv["open"][i] * adj_ratio, 4) if ohlcv["open"][i] else ohlcv["close"][i],
                    high=round(ohlcv["high"][i] * adj_ratio, 4) if ohlcv["high"][i] else ohlcv["close"][i],
                    low=round(ohlcv["low"][i] * adj_ratio, 4) if ohlcv["low"][i] else ohlcv["close"][i],
                    close=round(close, 4),
                    volume=int(ohlcv["volume"][i] or 0),
                ))

            log.info("Yahoo: loaded %d %s bars for %s", len(bars), interval, symbol)
            return bars

        except Exception as e:
            log.warning("Yahoo fetch attempt %d/%d failed for %s: %s", attempt + 1, retries, symbol, e)
            if attempt < retries - 1:
                time.sleep(1)

    return []


def fetch_daily(
    symbol: str,
    days: int = 252,
) -> List[Bar]:
    """Convenience: fetch last N days of daily bars."""
    end = datetime.now(timezone.utc)
    start = end - __import__("datetime").timedelta(days=days + 30)  # buffer
    return fetch_history(symbol, start, end, timeframe="1d")
