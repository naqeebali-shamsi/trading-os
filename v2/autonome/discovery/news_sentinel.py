"""
autonome/discovery/news_sentinel.py  v1.0
News scanner for catalyst detection.
Uses RSS feeds + keyword scoring to find market-moving events.
"""
from __future__ import annotations

import re, logging, urllib.request, urllib.parse
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

log = logging.getLogger("discovery.news")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# Catalyst keywords and their impact scores
CATALYST_KEYWORDS = {
    # Government / Policy
    "fda approval": 3.0, "fda approves": 3.0, "fda clears": 3.0,
    "earmarks": 1.5, "defense contract": 2.5, "government contract": 2.0,
    "tariff": 2.0, "trade war": 2.0, "sanctions": 2.0, "bipartisan": 1.0,
    "infrastructure bill": 2.0, "chips act": 2.5, "ira": 1.5,
    "rate cut": 2.5, "rate hike": 2.5, "federal reserve": 1.5, "fed chair": 1.5,
    "inflation data": 1.5, "jobs report": 1.5, "nonfarm payrolls": 1.5,

    # M&A / Corporate
    "acquisition": 2.5, "merger": 2.5, "acquires": 2.5, "buyout": 2.5,
    "takeover": 2.5, "go private": 2.5, "spin-off": 1.5,
    "partnership": 1.5, "collaboration": 1.5, "licensing deal": 1.5,
    "ipo": 2.0, "direct listing": 1.5, "spac": 1.5,

    # Earnings
    "earnings beat": 2.5, "beats estimates": 2.5, "surpasses expectations": 2.5,
    "earnings miss": 2.0, "misses estimates": 2.0, "guidance raised": 2.5,
    "guidance cut": 2.0, "revenue growth": 1.0, "profit margin": 1.0,

    # Product / Innovation
    "ai model": 2.0, "llm": 1.5, "generative ai": 2.0, "chatbot": 1.5,
    "chip shortage": 1.5, "semiconductor": 1.0, "battery breakthrough": 2.0,
    "drug trial": 2.5, "phase 3": 2.5, "clinical trial": 2.0,
    "patent": 1.5, " breakthrough": 2.0,

    # Meme / Retail
    "short squeeze": 2.5, "gamma squeeze": 2.5, "wallstreetbets": 1.5,
    "retail interest": 1.5, "meme stock": 1.5, "retail frenzy": 2.0,
    "options volume": 1.5, "call buying": 1.5, "unusual options": 2.0,

    # Macro / Crisis
    "bank failure": 3.0, "bank run": 3.0, "credit crunch": 2.5,
    "recession": 2.0, "stagflation": 2.0, "black swan": 2.5,
    "cyberattack": 2.5, "data breach": 2.0, "supply chain": 1.5,
}

# RSS feed sources
RSS_FEEDS = {
    "benzinga": "https://www.benzinga.com/feed",
    "marketwatch": "https://www.marketwatch.com/rss/topstories",
    "seekingalpha": "https://seekingalpha.com/market_currents.xml",
    "cnbc": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "wsj_business": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "reuters_business": "https://www.reutersagency.com/feed/?taxonomy=markets&post_type=reuters-best",
}


@dataclass
class NewsItem:
    title: str
    source: str
    published: Optional[datetime]
    url: str
    score: float = 0.0
    matched_tickers: List[str] = field(default_factory=list)
    matched_keywords: List[str] = field(default_factory=list)
    thesis: str = ""


class NewsSentinel:
    """Scan news feeds for market-moving catalysts."""

    # Common ticker symbol pattern (rough)
    TICKER_RE = re.compile(r'\b([A-Z]{1,5})\b')

    def __init__(self):
        self.keywords = CATALYST_KEYWORDS

    def _fetch_rss(self, url: str, source: str) -> List[NewsItem]:
        """Fetch and parse an RSS feed."""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        items = []
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
            root = ET.fromstring(text)
            # Handle both rss/channel/item and feed/entry
            channel = root.find("channel") if root.tag == "rss" else root
            if channel is None:
                channel = root
            for elem in channel.iter():
                if elem.tag in ("item", "entry"):
                    title = elem.findtext("title", default="")
                    link = elem.findtext("link", default="")
                    pub = elem.findtext("pubDate") or elem.findtext("published", default="")
                    items.append(NewsItem(
                        title=title,
                        source=source,
                        published=self._parse_date(pub),
                        url=link,
                    ))
        except Exception as e:
            log.warning("RSS fetch failed: %s | %s", source, e)
        return items

    def _parse_date(self, text: str) -> Optional[datetime]:
        """Parse various RSS date formats."""
        if not text:
            return None
        formats = [
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%d %b %Y %H:%M:%S %Z",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    def score_item(self, item: NewsItem) -> None:
        """Score a news item by catalyst keywords and extract tickers."""
        title_lower = item.title.lower()
        score = 0.0
        matched = []

        for keyword, weight in self.keywords.items():
            if keyword in title_lower:
                score += weight
                matched.append(keyword)

        # Extract tickers (rough heuristic — misses some, catches false positives)
        tickers = []
        for word in title_lower.split():
            # Skip common words
            if word.upper() in ("THE", "A", "AN", "AND", "OR", "BUT", "FOR", "OF", "ON", "AT", "TO", "FROM", "BY", "IS", "ARE", "WAS", "WERE", "BE", "BEEN", "BEING", "HAVE", "HAS", "HAD", "DO", "DOES", "DID", "WILL", "WOULD", "COULD", "SHOULD", "MAY", "MIGHT", "MUST", "CAN", "USA", "CEO", "CFO", "COO", "CTO"):
                continue
            if re.fullmatch(r'[A-Z]{1,5}', word.upper()):
                tickers.append(word.upper())

        item.score = round(score, 2)
        item.matched_keywords = matched
        item.matched_tickers = list(set(tickers))
        item.thesis = f"[{item.source}] {item.title[:80]}" if matched else ""

    def scan(self, max_items: int = 50) -> List[NewsItem]:
        """Scan all feeds and return scored items."""
        all_items = []
        for source, url in RSS_FEEDS.items():
            items = self._fetch_rss(url, source)
            for item in items[:max_items]:
                self.score_item(item)
            all_items.extend(items)

        # Filter to items with actual score
        scored = [i for i in all_items if i.score >= 1.5]
        scored.sort(key=lambda x: (x.score, x.published or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        return scored[:max_items]

    def scan_for_symbol(self, symbol: str, max_items: int = 20) -> List[NewsItem]:
        """Scan feeds filtered to a specific symbol."""
        all_items = []
        for source, url in RSS_FEEDS.items():
            items = self._fetch_rss(url, source)
            for item in items:
                self.score_item(item)
                if symbol.upper() in [t.upper() for t in item.matched_tickers] or symbol.upper() in item.title.upper():
                    all_items.append(item)
        all_items.sort(key=lambda x: x.score, reverse=True)
        return all_items[:max_items]
