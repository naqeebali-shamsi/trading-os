"""
autonome/data/earnings.py  v2.0
Earnings calendar fetcher with 24h cache.
Skips pre-earnings trades to avoid gap-down risk.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List

import requests

log = logging.getLogger("data.earnings")


class EarningsCalendar:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._cache: Dict[str, tuple] = {}  # symbol -> (earnings_date_str, fetched_at)
        self._base_url = "https://finnhub.io/api/v1"

    def fetch_earnings(self, symbol: str) -> Optional[str]:
        """Fetch next earnings date for symbol. Returns YYYY-MM-DD or None."""
        # Check cache first (24h TTL)
        cached = self._cache.get(symbol)
        if cached:
            date_str, fetched_at = cached
            if time.monotonic() - fetched_at < 86400:
                return date_str

        if not self.api_key:
            return None

        today = datetime.now(timezone.utc).date()
        future = today + timedelta(days=90)
        url = f"{self._base_url}/calendar/earnings"
        params = {
            "symbol": symbol,
            "from": str(today),
            "to": str(future),
            "token": self.api_key,
        }
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            earnings = data.get("earningsCalendar", [])
            if not earnings:
                self._cache[symbol] = (None, time.monotonic())
                return None
            # Sort by date ascending, pick first future one
            upcoming = [e for e in earnings if e.get("date")]
            if not upcoming:
                self._cache[symbol] = (None, time.monotonic())
                return None
            next_date = min(upcoming, key=lambda x: x["date"])["date"]
            self._cache[symbol] = (next_date, time.monotonic())
            return next_date
        except Exception as e:
            log.warning("Earnings fetch failed for %s: %s", symbol, e)
            return None

    def is_earnings_week(self, symbol: str, buffer_days: int = 2) -> bool:
        """True if earnings within +/- buffer_days from today."""
        date_str = self.fetch_earnings(symbol)
        if not date_str:
            return False
        try:
            earnings_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            today = datetime.now(timezone.utc).date()
            delta = abs((earnings_date - today).days)
            return delta <= buffer_days
        except ValueError:
            return False

    def warn_if_close(self, symbol: str, buffer_days: int = 2) -> Optional[str]:
        """Return warning message if earnings is close, else None."""
        date_str = self.fetch_earnings(symbol)
        if not date_str:
            return None
        try:
            earnings_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            today = datetime.now(timezone.utc).date()
            delta = (earnings_date - today).days
            if delta <= buffer_days:
                return f"earnings_{date_str}_in_{delta}d"
            return None
        except ValueError:
            return None
