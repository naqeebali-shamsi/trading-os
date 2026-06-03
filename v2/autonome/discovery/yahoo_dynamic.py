"""
autonome/discovery/yahoo_dynamic.py  v1.0
Dynamic stock scanner using Yahoo Finance chart data.
Finds momentum breakouts, volume anomalies, and range breaks.
No screener API needed — uses chart data directly.
"""
from __future__ import annotations

import logging, statistics
from typing import List, Dict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from autonome.data.yahoo_feed import fetch_history

log = logging.getLogger("discovery.dynamic")

# Candidate universe for dark horse scanning
# Mix of actionable names across sectors (not mega-caps that barely move)
UNIVERSE = [
    # Tech/Growth
    "NVDA", "AMD", "AVGO", "CRM", "SNOW", "DDOG", "NET", "PLTR", "ROKU", "SQ",
    "SHOP", "CRWD", "OKTA", "ZM", "DOCU", "UPST", "RBLX", "U", "TWLO", "FSLY",
    # Biotech
    "MRNA", "IONS", "CRSP", "NTLA", "BEAM", "ARWR", "SRPT", "VRTX", "REGN", "BIIB",
    # EV / Clean Energy
    "TSLA", "RIVN", "LCID", "NIO", "FSR", "ENPH", "SEDG", "RUN", "SPWR", "NOVA",
    # Meme / Retail
    "GME", "AMC", "BB", "BBBY", "NOK", "PLUG", "FCEL", "CLOV", "WKHS", "RIDE",
    # Crypto-adjacent
    "COIN", "MSTR", "RIOT", "MARA", "HUT", "BITF", "CLSK", "CORZ",
    # Defense / Infrastructure
    "LMT", "NOC", "RTX", "GD", "LHX", "KTOS", "BWXT", "KVYO",
    # Regional Energy / Resources
    "COP", "EOG", "DVN", "MRO", "FANG", "PXD", "MPC", "VLO", "PSX",
]


@dataclass
class DynamicResult:
    symbol: str
    price: float
    change_1d: float
    change_5d: float
    volatility_20d: float
    volume_surge: float
    range_position: float  # 0=at low, 1=at high
    score: float
    thesis: str


def scan_universe() -> List[DynamicResult]:
    """Scan candidate universe for momentum/volume anomalies."""
    results = []
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)

    for symbol in UNIVERSE:
        try:
            bars = fetch_history(symbol, start=start, end=end, timeframe="1d")
            if len(bars) < 10:
                continue

            closes = [b.close for b in bars]
            volumes = [b.volume for b in bars]
            highs = [b.high for b in bars]
            lows = [b.low for b in bars]

            price = closes[-1]
            change_1d = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0
            change_5d = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else change_1d

            vol_20d = statistics.stdev(closes[-20:]) if len(closes) >= 20 else statistics.stdev(closes)
            volatility = vol_20d / price * 100 if price else 0

            avg_vol = statistics.mean(volumes[-10:]) if len(volumes) >= 10 else volumes[-1]
            volume_surge = volumes[-1] / avg_vol if avg_vol else 1.0

            # Range position (where in 20d range)
            range_20d = max(highs[-20:]) - min(lows[-20:]) if len(highs) >= 20 else price * 0.1
            range_pos = (price - min(lows[-20:])) / range_20d if range_20d else 0.5

            # Dark horse score
            score = 0.0
            thesis_parts = []

            # Momentum scre
            if change_5d > 8.0:
                score += change_5d * 0.3
                thesis_parts.append(f"+{change_5d:.1f}% 5d")
            elif change_5d < -10.0:
                score += abs(change_5d) * 0.15
                thesis_parts.append(f"oversold {change_5d:.1f}% 5d")

            # Volume confirmation
            if volume_surge > 2.0:
                score += volume_surge * 1.0
                thesis_parts.append(f"{volume_surge:.1f}x volume")

            # Range break
            if range_pos > 0.85 and change_1d > 2.0:
                score += 3.0
                thesis_parts.append("breakout")
            elif range_pos < 0.15 and change_1d < -2.0:
                score += 1.5
                thesis_parts.append("breakdown")

            # Volatility bonus
            if volatility > 3.0:
                score += volatility * 0.2
                thesis_parts.append(f"high vol {volatility:.1f}%")

            if score >= 3.0:
                results.append(DynamicResult(
                    symbol=symbol,
                    price=price,
                    change_1d=round(change_1d, 2),
                    change_5d=round(change_5d, 2),
                    volatility_20d=round(volatility, 2),
                    volume_surge=round(volume_surge, 2),
                    range_position=round(range_pos, 2),
                    score=round(score, 1),
                    thesis="; ".join(thesis_parts),
                ))

        except Exception as e:
            log.debug("Scan failed for %s: %s", symbol, e)

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:20]
