#!/usr/bin/env python3
"""
sensory/news_feed.py — Economic Calendar + News Sentiment
-----------------------------------------------------------
Fetches economic calendar events from forex calendar API.
Warns about high-impact events (NFP, interest rates, etc).
Publishes calendar.alert events to nervous bus.

APIs:
- forex factory / investing.com (web scraping fallback)
- newsdata.io or similar for news sentiment
"""
import json, time, urllib.request, sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish

STATE = {"last_check": 0, "events_cached": []}


# High-impact events that trigger trading halt or caution
HIGH_IMPACT_KEYWORDS = [
    "nfp", "non-farm", "interest rate", "fed", "fomc", "cpi",
    "inflation", "gdp", "central bank", "boe", "ecb", "boj",
    "unemployment", "retail sales", "war", "invasion", "sanctions",
]


def fetch_forex_factory() -> List[dict]:
    """Scrape investing.com or forex-factory.com calendar."""
    # This is a stub — real implementation would parse the HTML or use a paid API.
    # For now, return known placeholder events.
    return []


def get_cached_events() -> List[dict]:
    """Return recently cached events from file."""
    cache = ROOT / "intel" / "calendar_cache.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
            return data.get("events", [])
        except Exception:
            pass
    return []


def check_calendar():
    """Check for upcoming high-impact events."""
    now = time.time()
    if now - STATE["last_check"] < 3600:  # check once per hour
        return

    events = get_cached_events()
    upcoming = []
    for ev in events:
        ev_time = ev.get("time_unix", 0)
        if 0 < ev_time - now < 7200:  # within 2 hours
            impact = ev.get("impact", "low")
            if impact in ("high", "medium"):
                upcoming.append(ev)

    if upcoming:
        publish("calendar.alert", {
            "events": upcoming,
            "severity": "high" if any(e["impact"] == "high" for e in upcoming) else "medium",
            "advice": "Consider reducing position size or avoiding new entries 15min before/after high-impact events",
        })

    STATE["last_check"] = now


def ingest_news_headlines(country: str = "us") -> List[dict]:
    """
    Fetch latest financial headlines for sentiment analysis.
    Uses RSS feeds as free source. Returns headline list.
    """
    feeds = [
        "https://www.forexlive.com/feed/news",
        "https://feeds.marketwatch.com/marketwatch/topstories/",
    ]
    headlines = []
    # Stub: would parse RSS XML with feedparser or requests+BeautifulSoup
    return headlines


def run():
    while True:
        check_calendar()
        time.sleep(300)  # 5 min loop


if __name__ == "__main__":
    run()
