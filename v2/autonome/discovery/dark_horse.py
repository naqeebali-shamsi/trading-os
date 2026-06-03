"""
autonome/discovery/dark_horse.py  v1.0
Dark Horse Discovery Engine.

Combines screener, news, and sector rotation into ranked dark horse picks.
Produces a watchlist file the supervisor can read.
"""
from __future__ import annotations

import json, logging, os, statistics
from typing import List, Dict, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta

from .yahoo_screener import YahooScreener, ScreenerResult
from .news_sentinel import NewsSentinel, NewsItem
from .sector_rotation import SectorRotationDetector
from .yahoo_dynamic import scan_universe, DynamicResult

log = logging.getLogger("discovery.darkhorse")

# Where to write the daily watchlist
WATCHLIST_PATH = os.environ.get(
    "AUTONOME_WATCHLIST",
    "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/intel/daily_watchlist.json"
)

# Minimum quality threshold for dark horse recommendation
MIN_DARK_HORSE_SCORE = 3.0

# Hard sector-to-stock mapping for when we need individual names
SECTOR_LEADERS = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "ORCL"],
    "Financials": ["JPM", "BAC", "GS", "BLK", "AXP"],
    "Energy": ["XOM", "CVX", "COP", "EOG", "SLB"],
    "Industrials": ["CAT", "BA", "HON", "UPS", "GE"],
    "Healthcare": ["JNJ", "UNH", "LLY", "ABBV", "PFE"],
    "Consumer Staples": ["WMT", "PG", "KO", "COST", "PEP"],
    "Consumer Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE"],
    "Materials": ["LIN", "APD", "ECL", "SHW", "NEM"],
    "Utilities": ["NEE", "SO", "DUK", "AEP", "SRE"],
    "Real Estate": ["AMT", "PLD", "CCI", "PSA", "DLR"],
    "Semiconductors": ["NVDA", "AVGO", "AMD", "QCOM", "MU", "TSM"],
    "Biotech": ["LLY", "ABBV", "MRK", "AMGN", "GILD", "REGN"],
    "Regional Banks": ["PNFP", "SFNC", "TRMK", "SFST", "AMTB"],
    "Innovation/Disruptive": ["TSLA", "ROKU", "SQ", "ZM", "CRSP"],
    "Metals & Mining": ["NEM", "FCX", "GOLD", "WPM", "RGLD"],
}


@dataclass
class DarkHorsePick:
    symbol: str
    name: str
    price: float
    discovery_source: str  # screener_gainers | screener_shorted | news_catalyst | sector_rotation
    score: float
    thesis: str
    catalyst_keywords: List[str] = None
    sector: str = ""
    confidence: str = "medium"  # high | medium | low
    time_added: str = ""
    suggested_position: str = "LONG"  # LONG | SHORT | WATCH
    max_position_pct: float = 1.0

    def __post_init__(self):
        if self.catalyst_keywords is None:
            self.catalyst_keywords = []
        if not self.time_added:
            self.time_added = datetime.now(timezone.utc).isoformat()


class DarkHorseEngine:
    """Combine multiple data sources into ranked dark horse picks."""

    def __init__(self):
        self.screener = YahooScreener()
        self.news = NewsSentinel()
        self.sectors = SectorRotationDetector()

    def run(self) -> List[DarkHorsePick]:
        """Full discovery run. Returns ranked dark horse picks."""
        log.info("=== Dark Horse Discovery ===")
        picks = []

        # 1. Yahoo screeners
        picks.extend(self._scan_screeners())

        # 2. News catalysts
        picks.extend(self._scan_news())

        # 3. Sector rotation
        picks.extend(self._scan_sectors())

        # 4. Deduplicate and rank
        picks = self._deduplicate_and_rank(picks)

        # 5. Filter quality threshold
        picks = [p for p in picks if p.score >= MIN_DARK_HORSE_SCORE]

        log.info("Discovered %d dark horses (score >= %.1f)", len(picks), MIN_DARK_HORSE_SCORE)
        return picks

    def _scan_screeners(self) -> List[DarkHorsePick]:
        """Scan Yahoo screeners for unusual activity."""
        picks = []

        # Top gainers with unusual volume (fallback to dynamic if screener fails)
        try:
            gainers = self.screener.top_gainers(min_price=5.0)
            for r in gainers[:10]:
                vol_ratio = r.volume / max(r.avg_volume, 1)
                if vol_ratio > 2.0 and r.change_pct > 5.0:
                    score = min(10.0, r.change_pct * 0.3 + vol_ratio * 0.5)
                    picks.append(DarkHorsePick(
                        symbol=r.symbol,
                        name=r.name,
                        price=r.price,
                        discovery_source="screener_gainers",
                        score=round(score, 2),
                        thesis=f"+{r.change_pct:.1f}% with {vol_ratio:.1f}x avg volume — momentum breakout",
                        sector=r.sector or "",
                        confidence="high" if score > 7 else "medium",
                    ))
        except Exception as e:
            log.warning("Screener gainers failed, using dynamic fallback: %s", e)

        # High short interest
        try:
            shorted = self.screener.most_shorted()
            for r in shorted[:10]:
                vol_ratio = r.volume / max(r.avg_volume, 1)
                if vol_ratio > 1.5 and r.change_pct > 3.0:
                    score = min(10.0, r.change_pct * 0.4 + vol_ratio * 0.3 + 2.0)
                    picks.append(DarkHorsePick(
                        symbol=r.symbol,
                        name=r.name,
                        price=r.price,
                        discovery_source="screener_shorted",
                        score=round(score, 2),
                        thesis=f"Short squeeze candidate: +{r.change_pct:.1f}% on {vol_ratio:.1f}x volume, high short interest",
                        sector=r.sector or "",
                        confidence="medium",
                        suggested_position="LONG",
                    ))
        except Exception as e:
            log.warning("Screener shorted failed: %s", e)

        # Dynamic chart-based scan (always works, no screener API needed)
        log.info("Running dynamic universe scan...")
        dynamic_results = scan_universe()
        for d in dynamic_results:
            picks.append(DarkHorsePick(
                symbol=d.symbol,
                name=d.symbol,
                price=d.price,
                discovery_source="dynamic_scan",
                score=d.score,
                thesis=d.thesis,
                confidence="high" if d.score > 7 else "medium" if d.score > 5 else "low",
            ))

        return picks

    def _scan_news(self) -> List[DarkHorsePick]:
        """Scan news for catalyst-driven moves."""
        picks = []
        items = self.news.scan(max_items=30)

        for item in items:
            for ticker in item.matched_tickers:
                score = min(10.0, item.score * 1.5)
                picks.append(DarkHorsePick(
                    symbol=ticker,
                    name=ticker,  # will be filled later
                    price=0.0,
                    discovery_source="news_catalyst",
                    score=round(score, 2),
                    thesis=item.thesis,
                    catalyst_keywords=item.matched_keywords,
                    confidence="high" if item.score >= 2.5 else "medium",
                    suggested_position="LONG",
                ))

        return picks

    def _scan_sectors(self) -> List[DarkHorsePick]:
        """Detect sector rotation and pick likely beneficiaries."""
        picks = []
        sectors = self.sectors.dark_horse_sectors()

        for reason, etf, thesis in sectors:
            sector_name = self.sectors.sectors.get(etf, "Unknown")
            leaders = SECTOR_LEADERS.get(sector_name, [])

            for leader in leaders[:3]:
                score = 4.5 if reason == "sector_rotation" else 3.5
                picks.append(DarkHorsePick(
                    symbol=leader,
                    name=leader,
                    price=0.0,
                    discovery_source="sector_rotation",
                    score=round(score, 2),
                    thesis=f"Sector rotation into {sector_name}: {thesis}",
                    sector=sector_name,
                    confidence="medium",
                    suggested_position="LONG",
                ))

        return picks

    def _deduplicate_and_rank(self, picks: List[DarkHorsePick]) -> List[DarkHorsePick]:
        """Remove duplicates by symbol, keep highest score."""
        by_symbol: Dict[str, DarkHorsePick] = {}
        for p in picks:
            if p.symbol in by_symbol:
                if p.score > by_symbol[p.symbol].score:
                    by_symbol[p.symbol] = p
            else:
                by_symbol[p.symbol] = p

        ranked = sorted(by_symbol.values(), key=lambda x: x.score, reverse=True)
        return ranked

    def write_watchlist(self, picks: List[DarkHorsePick]) -> str:
        """Write picks to the daily watchlist file."""
        data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(picks),
            "picks": [asdict(p) for p in picks[:20]],  # top 20
        }
        os.makedirs(os.path.dirname(WATCHLIST_PATH), exist_ok=True)
        with open(WATCHLIST_PATH, "w") as f:
            json.dump(data, f, indent=2, default=str)
        log.info("Watchlist written: %s (%d picks)", WATCHLIST_PATH, len(picks[:20]))
        return WATCHLIST_PATH

    def write_markdown_report(self, picks: List[DarkHorsePick]) -> str:
        """Write a human-readable markdown report."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = WATCHLIST_PATH.replace(".json", f"_{date_str}.md")

        lines = [
            f"# Dark Horse Discovery Report — {date_str}",
            f"Generated: {datetime.now(timezone.utc).isoformat()} UTC",
            f"Total candidates: {len(picks)}",
            "",
            "## Top Picks (score ≥ {:.1f})".format(MIN_DARK_HORSE_SCORE),
            "",
        ]

        for i, p in enumerate(picks[:15], 1):
            emoji = "🚀" if p.confidence == "high" else "⚡" if p.confidence == "medium" else "👁"
            lines.extend([
                f"### {i}. {emoji} {p.symbol} — Score: {p.score:.1f} ({p.confidence.upper()})",
                f"- **Source**: {p.discovery_source}",
                f"- **Thesis**: {p.thesis}",
                f"- **Suggested**: {p.suggested_position}",
                f"- **Max Position**: {p.max_position_pct}% equity",
                "",
            ])

        # Source breakdown
        sources = {}
        for p in picks:
            sources[p.discovery_source] = sources.get(p.discovery_source, 0) + 1
        lines.append("## Discovery Breakdown")
        for src, count in sorted(sources.items(), key=lambda x: -x[1]):
            lines.append(f"- {src}: {count}")
        lines.append("")

        with open(path, "w") as f:
            f.write("\n".join(lines))
        log.info("Report written: %s", path)
        return path


def run_discovery() -> None:
    """Entry point for cron/daily discovery run."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )
    engine = DarkHorseEngine()
    picks = engine.run()
    if picks:
        engine.write_watchlist(picks)
        engine.write_markdown_report(picks)
    else:
        log.info("No dark horses found today")


if __name__ == "__main__":
    run_discovery()
