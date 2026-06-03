"""
autonome/data/bars.py  v2.0
Historical + streaming bar manager.  Alpaca crypto/eq bars.
"""
from __future__ import annotations

import os, time, logging, sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Callable
from collections import deque

import requests
import yaml

log = logging.getLogger("data.bars")


@dataclass
class Bar:
    symbol: str
    t: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    # derived
    range: float = 0.0
    body: float = 0.0

    def __post_init__(self):
        self.range = self.high - self.low
        self.body = abs(self.close - self.open)


def _load_cfg() -> dict:
    p = os.path.join(os.path.dirname(__file__), "../../config/settings.yaml")
    with open(p) as f:
        return yaml.safe_load(f)


def _load_secrets() -> dict:
    p = os.path.join(os.path.dirname(__file__), "../../config/secrets.yaml")
    with open(p) as f:
        return yaml.safe_load(f)


class BarStore:
    """
    In-memory ring buffer per symbol + SQLite append-only log for warm restarts.
    """
    def __init__(self, symbols: List[str], maxlen: int = 500):
        self.symbols = symbols
        self.maxlen = maxlen
        self.buffers: Dict[str, deque] = {s: deque(maxlen=maxlen) for s in symbols}
        self._callbacks: List[Callable[[Bar], None]] = []

        cfg = _load_cfg()
        self.db_path = cfg.get("journal", {}).get("db_path", "data/journal.sqlite")
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._ensure_table()
        self._warm_from_db()

    def _ensure_table(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS bars (
                    symbol TEXT,
                    t TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume INTEGER,
                    PRIMARY KEY (symbol, t)
                )
            """)

    def _warm_from_db(self):
        with sqlite3.connect(self.db_path) as db:
            for sym in self.symbols:
                rows = db.execute(
                    "SELECT * FROM bars WHERE symbol=? ORDER BY t DESC LIMIT ?",
                    (sym, self.maxlen)
                ).fetchall()
                for r in reversed(rows):
                    bar = Bar(symbol=r[0], t=datetime.fromisoformat(r[1]),
                              open=r[2], high=r[3], low=r[4], close=r[5], volume=r[6])
                    self.buffers[sym].append(bar)
        log.info("Warmed %d symbols from DB", len(self.symbols))

    def on_bar(self, callback: Callable[[Bar], None]):
        self._callbacks.append(callback)

    def ingest(self, bar: Bar):
        self.buffers[bar.symbol].append(bar)
        self._persist(bar)
        for cb in self._callbacks:
            try:
                cb(bar)
            except Exception:
                log.exception("callback failed for %s", bar.symbol)

    def _persist(self, bar: Bar):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                (bar.symbol, bar.t.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume)
            )

    def history(self, symbol: str, n: int) -> List[Bar]:
        buf = self.buffers.get(symbol, deque())
        return list(buf)[-n:]

    def last(self, symbol: str) -> Optional[Bar]:
        buf = self.buffers.get(symbol)
        if not buf:
            return None
        return buf[-1]

    def is_stale(self, symbol: str, max_age_sec: float = 3600.0) -> bool:
        """Return True if the last known bar for symbol is older than max_age_sec."""
        bar = self.last(symbol)
        if bar is None:
            return True
        age = (datetime.now(timezone.utc) - bar.t).total_seconds()
        return age > max_age_sec

    def any_stale(self, max_age_sec: float = 3600.0) -> tuple[bool, list[str]]:
        """Return (True, [symbols]) if any symbol has stale data."""
        stale = [s for s in self.symbols if self.is_stale(s, max_age_sec)]
        return bool(stale), stale

    def close_prices(self, symbol: str, n: int) -> List[float]:
        return [b.close for b in self.history(symbol, n)]

    def volumes(self, symbol: str, n: int) -> List[int]:
        return [b.volume for b in self.history(symbol, n)]


class AlpacaDataFeed:
    """
    REST fallback for historical bars.  Websocket upgrade later.
    """
    def __init__(self):
        cfg = _load_cfg()
        sec = _load_secrets()
        self.data_url = cfg["broker"]["data_url"]
        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": sec["alpaca"]["api_key"],
            "APCA-API-SECRET-KEY": sec["alpaca"]["api_secret"],
        })
        self.timeframe = cfg["data"]["timeframe"]
        self.symbols = cfg["data"]["symbols"]
        self.adjustment = cfg["data"].get("bar_adjustment", "raw")

    def fetch_history(self, symbol: str, limit: int = 200) -> List[Bar]:
        url = f"{self.data_url}/v2/stocks/{symbol}/bars"
        now = datetime.now().astimezone().astimezone().replace(tzinfo=None)
        end = now + timedelta(hours=4)
        start = end - timedelta(days=limit // 6 + 2)
        params = {
            "timeframe": self.timeframe,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": min(limit, 1000),
            "feed": "iex",
            "sort": "asc",
        }
        if self.adjustment in ("split", "all"):
            params["adjustment"] = self.adjustment
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        bars = []
        for b in r.json().get("bars", []):
            bars.append(Bar(
                symbol=symbol,
                t=datetime.fromisoformat(b["t"].replace("Z", "+00:00")),
                open=float(b["o"]),
                high=float(b["h"]),
                low=float(b["l"]),
                close=float(b["c"]),
                volume=int(b["v"]),
            ))
        return bars

    def warm_store(self, store: BarStore):
        for sym in self.symbols:
            try:
                bars = self.fetch_history(sym, limit=store.maxlen)
                for b in bars:
                    store.ingest(b)
                log.info("Warmed %s with %d bars", sym, len(bars))
            except Exception:
                log.exception("Failed to warm %s", sym)
            time.sleep(0.2)  # rate limit courtesy
