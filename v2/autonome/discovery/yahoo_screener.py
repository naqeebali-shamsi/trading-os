"""
autonome/discovery/yahoo_screener.py  v1.0
Yahoo Finance screener for pre-market discovery.
Uses Yahoo's hidden screener API (no auth required).
"""
from __future__ import annotations

import json, logging, urllib.request, urllib.parse
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger("discovery.yahoo")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


@dataclass
class ScreenerResult:
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    avg_volume: int
    market_cap: Optional[float]
    sector: Optional[str]
    industry: Optional[str]
    score: float = 0.0
    thesis: str = ""


class YahooScreener:
    """Screen Yahoo Finance for momentum/volume anomalies."""

    BASE = "https://query1.finance.yahoo.com/v1/finance/screener"

    # Predefined screeners Yahoo exposes
    SCREENERS = {
        "most_active": {"sortField": "dayvolume", "sortType": "DESC", "offset": 0, "count": 50},
        "day_gainers": {"sortField": "percentchange", "sortType": "DESC", "offset": 0, "count": 50},
        "day_losers": {"sortField": "percentchange", "sortType": "ASC", "offset": 0, "count": 50},
        "most_shorted": {"sortField": "shortratiototal", "sortType": "DESC", "offset": 0, "count": 50},
    }

    def _fetch(self, screener_id: str) -> List[Dict]:
        """Fetch a predefined screener from Yahoo."""
        url = f"{self.BASE}/predefined/{screener_id}?count=50&offset=0"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            return data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
        except Exception as e:
            log.error("Yahoo screener %s failed: %s", screener_id, e)
            return []

    def most_active(self) -> List[ScreenerResult]:
        """Most actively traded stocks today."""
        quotes = self._fetch("most_active")
        return [self._parse(q) for q in quotes if q]

    def top_gainers(self, min_price: float = 5.0) -> List[ScreenerResult]:
        """Biggest % gainers with price filter."""
        quotes = self._fetch("day_gainers")
        results = [self._parse(q) for q in quotes if q]
        return [r for r in results if r.price >= min_price]

    def top_losers(self, min_price: float = 5.0) -> List[ScreenerResult]:
        """Biggest % losers — potential bounce plays."""
        quotes = self._fetch("day_losers")
        results = [self._parse(q) for q in quotes if q]
        return [r for r in results if r.price >= min_price]

    def most_shorted(self) -> List[ScreenerResult]:
        """High short interest — squeeze candidates."""
        quotes = self._fetch("most_shorted")
        return [self._parse(q) for q in quotes if q]

    def _parse(self, q: Dict) -> ScreenerResult:
        return ScreenerResult(
            symbol=q.get("symbol", "UNKNOWN"),
            name=q.get("shortName", q.get("longName", "")),
            price=q.get("regularMarketPrice", 0.0) or 0.0,
            change_pct=q.get("regularMarketChangePercent", 0.0) or 0.0,
            volume=q.get("regularMarketVolume", 0) or 0,
            avg_volume=q.get("averageDailyVolume3Month", 0) or 0,
            market_cap=q.get("marketCap"),
            sector=q.get("sector"),
            industry=q.get("industry"),
        )

    def scan_all(self) -> Dict[str, List[ScreenerResult]]:
        """Run all screeners and return categorized results."""
        return {
            "most_active": self.most_active(),
            "top_gainers": self.top_gainers(),
            "top_losers": self.top_losers(),
            "most_shorted": self.most_shorted(),
        }
