"""
autonome/discovery/sector_rotation.py  v1.0
Sector rotation detector via ETF divergence analysis.
Captures capital flight from one sector to another.
"""
from __future__ import annotations

import statistics, logging
from typing import List, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from autonome.data.yahoo_feed import fetch_history
from autonome.data.bars import Bar

log = logging.getLogger("discovery.sector")

# Key sector ETFs and what they represent
SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLI": "Industrials",
    "XLV": "Healthcare",
    "XLP": "Consumer Staples",
    "XLY": "Consumer Discretionary",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XME": "Metals & Mining",
    "SMH": "Semiconductors",
    "IBB": "Biotech",
    "KRE": "Regional Banks",
    "ARKK": "Innovation/Disruptive",
    "SOXX": "Semiconductors",
}

# Macro ETFs for context
MACRO_ETFS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000 (Small Cap)",
    "VTI": "Total Market",
    "GLD": "Gold",
    "TLT": "20yr Treasury",
    "VIXY": "VIX (Fear)",
    "UUP": "US Dollar",
    "DBC": "Commodities",
    "USO": "Oil",
}


@dataclass
class SectorPerformance:
    etf: str
    sector_name: str
    performance_1d: float
    performance_5d: float
    performance_20d: float
    relative_to_spy_20d: float  # vs SPY
    volatility_20d: float
    volume_surge: float  # today's vol vs 20d avg
    rank_score: float = 0.0


class SectorRotationDetector:
    """Detect sector rotation via ETF performance divergence."""

    def __init__(self):
        self.sectors = SECTOR_ETFS
        self.macro = MACRO_ETFS

    def _fetch_performance(self, symbol: str, lookback: int = 25) -> Dict:
        """Fetch recent bars and compute performance metrics."""
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=lookback + 5)
            bars = fetch_history(symbol, start=start, end=end, timeframe="1d")
            if len(bars) < 5:
                return {}

            closes = [b.close for b in bars]
            volumes = [b.volume for b in bars]

            perf_1d = ((closes[-1] - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 else 0
            perf_5d = ((closes[-1] - closes[-5]) / closes[-5] * 100) if len(closes) >= 5 else 0
            perf_20d = ((closes[-1] - closes[-20]) / closes[-20] * 100) if len(closes) >= 20 else perf_5d

            vol_20d = statistics.stdev(closes[-20:]) if len(closes) >= 20 else 0
            avg_vol = statistics.mean(volumes[-20:]) if len(volumes) >= 20 else volumes[-1]
            vol_surge = (volumes[-1] / avg_vol) if avg_vol else 1.0

            return {
                "perf_1d": perf_1d,
                "perf_5d": perf_5d,
                "perf_20d": perf_20d,
                "volatility": vol_20d / closes[-1] * 100 if closes[-1] else 0,
                "volume_surge": vol_surge,
            }
        except Exception as e:
            log.warning("Sector fetch failed for %s: %s", symbol, e)
            return {}

    def scan(self) -> Dict[str, List[SectorPerformance]]:
        """Scan all sectors and return ranked lists."""
        # Fetch SPY for relative comparison
        spy_data = self._fetch_performance("SPY", 25)
        spy_20d = spy_data.get("perf_20d", 0)

        sectors = []
        for etf, name in self.sectors.items():
            data = self._fetch_performance(etf, 25)
            if not data:
                continue

            rel = data["perf_20d"] - spy_20d
            # Rank score: momentum + relative strength + volume confirmation
            score = (
                data["perf_5d"] * 0.3 +
                rel * 0.4 +
                (data["volume_surge"] - 1.0) * 5.0 +  # volume surge bonus
                data["volatility"] * 0.1  # slight vol preference
            )

            sectors.append(SectorPerformance(
                etf=etf,
                sector_name=name,
                performance_1d=data["perf_1d"],
                performance_5d=data["perf_5d"],
                performance_20d=data["perf_20d"],
                relative_to_spy_20d=rel,
                volatility_20d=data["volatility"],
                volume_surge=data["volume_surge"],
                rank_score=round(score, 2),
            ))

        # Sort by rank score
        sectors.sort(key=lambda x: x.rank_score, reverse=True)

        # Classify rotation
        strong = [s for s in sectors if s.rank_score > 1.0][:5]
        weak = [s for s in sectors if s.rank_score < -1.0][:5]
        # Find rotation pairs (money flowing from weak to strong)
        rotations = []
        if strong and weak:
            for s in strong[:3]:
                for w in weak[:3]:
                    rotations.append((w.sector_name, s.sector_name, s.rank_score - w.rank_score))

        return {
            "strong": strong,
            "weak": weak,
            "rotations": rotations,
            "all": sectors,
        }

    def dark_horse_sectors(self) -> List[Tuple[str, str, str]]:
        """
        Identify sectors showing early rotation signs.
        Returns list of (reason, sector_etf, thesis).
        """
        result = self.scan()
        dark_horses = []

        for s in result["strong"]:
            if s.volume_surge > 1.5 and s.relative_to_spy_20d > 2.0:
                dark_horses.append((
                    "sector_rotation",
                    s.etf,
                    f"{s.sector_name} breaking out: +{s.performance_5d:.1f}% 5d vs SPY, vol surge {s.volume_surge:.1f}x"
                ))

        for s in result["weak"]:
            if s.volume_surge > 2.0 and s.performance_20d < -5.0:
                dark_horses.append((
                    "oversold_bounce",
                    s.etf,
                    f"{s.sector_name} deeply oversold: {s.performance_20d:.1f}% 20d, high volume may signal capitulation"
                ))

        return dark_horses
