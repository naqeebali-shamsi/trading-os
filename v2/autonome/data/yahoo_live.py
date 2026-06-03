"""
autonome/data/yahoo_live.py  v1.0
Subscribe-style Yahoo Finance bridge for 15m bars.
Polls every 60 seconds during market hours, calls callback on new bar.
"""
from __future__ import annotations

import time, logging, threading
from datetime import datetime, timezone, timedelta
from typing import Callable, List, Optional

from autonome.data.yahoo_feed import fetch_history
from autonome.data.bars import Bar

log = logging.getLogger("data.yahoo_live")


class YahooLiveFeed:
    def __init__(self, symbols: List[str], timeframe: str = "15m", poll_sec: int = 60):
        self.symbols = symbols
        self.timeframe = timeframe
        self.poll_sec = poll_sec
        self._callbacks: List[Callable[[str, Bar], None]] = []
        self._last_bars: dict = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def on_bar(self, callback: Callable[[str, Bar], None]):
        self._callbacks.append(callback)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("YahooLiveFeed started for %s", self.symbols)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        while self._running:
            try:
                for sym in self.symbols:
                    self._poll_symbol(sym)
            except Exception as e:
                log.error("Poll error: %s", e)
            time.sleep(self.poll_sec)

    def _poll_symbol(self, symbol: str):
        # Fetch last 2 bars to catch latest
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=60)  # small window for speed
        bars = fetch_history(symbol, start, end, self.timeframe)
        if not bars:
            return
        latest = bars[-1]
        key = f"{symbol}:{latest.t.isoformat().rsplit(':', 1)[0]}"  # minute precision
        if symbol not in self._last_bars or self._last_bars[symbol] != key:
            self._last_bars[symbol] = key
            for cb in self._callbacks:
                try:
                    cb(symbol, latest)
                except Exception:
                    log.exception("Callback error")

    def latest(self, symbol: str) -> Optional[Bar]:
        """Get most recent cached bar."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=60)
        bars = fetch_history(symbol, start, end, self.timeframe)
        return bars[-1] if bars else None
