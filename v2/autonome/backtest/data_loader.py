"""
autonome/backtest/data_loader.py  v1.0
Load historical bars for backtesting.
Alpaca REST first, synthetic fallback for tests.
"""
from __future__ import annotations

import os, logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests
import yaml

from autonome.data.bars import Bar

log = logging.getLogger("backtest.data")


def _load_cfg() -> dict:
    p = os.path.join(os.path.dirname(__file__), "../../config/settings.yaml")
    with open(p) as f:
        return yaml.safe_load(f)


def _load_secrets() -> dict:
    p = os.path.join(os.path.dirname(__file__), "../../config/secrets.yaml")
    with open(p) as f:
        return yaml.safe_load(f)


def load_from_alpaca(
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str = "1Hour",
    adjustment: str = "raw",
) -> List[Bar]:
    """Fetch historical bars from Alpaca."""
    cfg = _load_cfg()
    sec = _load_secrets()

    url = f"{cfg['broker']['data_url']}/v2/stocks/{symbol}/bars"
    params = {
        "timeframe": timeframe,
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 10000,
        "feed": "iex",
        "sort": "asc",
    }
    if adjustment in ("split", "all"):
        params["adjustment"] = adjustment

    session = requests.Session()
    session.headers.update({
        "APCA-API-KEY-ID": sec["alpaca"]["api_key"],
        "APCA-API-SECRET-KEY": sec["alpaca"]["api_secret"],
    })

    r = session.get(url, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()

    bars = []
    for b in data.get("bars", []):
        bars.append(Bar(
            symbol=symbol,
            t=datetime.fromisoformat(b["t"].replace("Z", "+00:00")),
            open=float(b["o"]),
            high=float(b["h"]),
            low=float(b["l"]),
            close=float(b["c"]),
            volume=int(b["v"]),
        ))
    log.info("Loaded %d bars for %s from Alpaca", len(bars), symbol)
    return bars


def generate_synthetic(
    symbol: str = "SPY",
    n: int = 500,
    base_price: float = 400.0,
    trend: float = 0.0001,       # per-bar drift
    volatility: float = 0.008,   # per-bar std
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
