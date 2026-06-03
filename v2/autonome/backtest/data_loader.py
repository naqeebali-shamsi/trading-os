"""
autonome/backtest/data_loader.py  v1.1
Load historical bars for backtesting.
Yahoo Finance primary, Alpaca fallback, synthetic last resort.
"""
from __future__ import annotations

import os, logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from autonome.data.bars import Bar
from autonome.data.yahoo_feed import fetch_history as fetch_yahoo

log = logging.getLogger("backtest.data")


def load_from_yahoo(
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str = "1d",
) -> List[Bar]:
    """Fetch from Yahoo Finance (no API keys needed)."""
    return fetch_yahoo(symbol, start, end, timeframe)


def generate_synthetic(
    symbol: str = "SPY",
    n: int = 500,
    base_price: float = 400.0,
    trend: float = 0.0001,
    volatility: float = 0.008,
    seed: int = 42,
) -> List[Bar]:
    """Generate synthetic OHLCV for unit testing."""
    import random, math
    random.seed(seed)

    bars = []
    price = base_price
    t = datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)

    for i in range(n):
        drift = trend * price
        shock = random.gauss(0, volatility * price)
        open_p = price + random.gauss(0, volatility * price * 0.3)
        close_p = open_p + drift + shock
        high_p = max(open_p, close_p) + abs(random.gauss(0, volatility * price * 0.2))
        low_p = min(open_p, close_p) - abs(random.gauss(0, volatility * price * 0.2))
        vol = int(1_000_000 + random.random() * 5_000_000)

        bars.append(Bar(
            symbol=symbol,
            t=t,
            open=round(open_p, 2),
            high=round(high_p, 2),
            low=round(low_p, 2),
            close=round(close_p, 2),
            volume=vol,
        ))
        price = close_p
        t += timedelta(hours=1)

    return bars


def load_bars_with_regime(
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str = "1d",
) -> Tuple[List[Bar], List[Bar]]:
    """
    Load intraday bars AND daily bars for regime filtering.
    Returns (intraday_bars, daily_bars).
    """
    intraday = fetch_yahoo(symbol, start, end, timeframe)
    if not intraday:
        log.warning("No intraday bars from Yahoo for %s, trying synthetic", symbol)
        # Generate synthetic matching the date range
        days = (end - start).days
        n = max(days * 6, 200)  # rough estimate of 1H bars
        intraday = generate_synthetic(symbol, n=n)

    # Fetch daily for regime (go back further for EMA/ATR calc)
    daily_start = start - timedelta(days=100)
    daily = fetch_yahoo(symbol, daily_start, end, "1d")

    log.info("Loaded %d %s bars + %d daily bars for %s", len(intraday), timeframe, len(daily), symbol)
    return intraday, daily
