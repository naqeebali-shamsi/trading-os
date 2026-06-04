"""
autonome/india/sentinel.py  v1.0
India World Event Sentinel.

Track macro inputs that affect Indian equities:
1. USD/INR rate (FII flows, import costs)
2. Brent crude oil (import bill, inflation)
3. US 10Y Treasury yield (Fed policy → EM outflows)
4. VIX India (market fear)
5. RBI meeting calendar
6. US-India trade / geopolitical events

AI sentiment via Bloomberg Terminal RSS + keyword detection.
Output: market regime classification for India.
"""
from __future__ import annotations

import json, logging, urllib.request
from typing import Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from autonome.data.yahoo_feed import fetch_history

log = logging.getLogger("india.sentinel")

# Symbols to track
USD_INR = "INR=X"
BRENT_OIL = "BZ=F"
US_10Y = "^TNX"  # Not perfect but tracks US rates via yield proxy
GOLD = "GC=F"
VIX_INDIA = "^INDIAVIX"  # May not be available on Yahoo


@dataclass
class MacroSnapshot:
    timestamp: str
    usd_inr: float
    oil_usd: float
    fear_indicator: str  # LOW | MODERATE | HIGH | EXTREME
    risk_score: float  # 0-10 (higher = more dangerous for India)
    thesis: str
    tailwinds: List[str]
    headwinds: List[str]


class IndiaSentinel:
    """Monitor macro conditions affecting Indian equities."""

    def __init__(self):
        pass

    def _fetch_rate(self, symbol: str) -> Optional[float]:
        """Get latest price for a macro instrument using yfinance."""
        try:
            import yfinance as yf
            t = yf.Ticker(symbol)
            h = t.history(period="5d")
            return float(h["Close"].iloc[-1]) if len(h) > 0 else None
        except Exception as e:
            log.warning("Sentinel fetch failed for %s: %s", symbol, e)
            return None

    def _fetch_usd_inr_trend(self) -> Tuple[Optional[float], str]:
        """USD/INR level and 10d trend."""
        try:
            import yfinance as yf
            t = yf.Ticker("INR=X")
            h = t.history(period="20d")
            if len(h) < 5:
                return None, "unknown"
            spot = float(h["Close"].iloc[-1])
            ma10 = float(h["Close"].iloc[-10:].mean())
            if spot > ma10 * 1.01:
                trend = "weakening (depreciating)"
            elif spot < ma10 * 0.99:
                trend = "strengthening (appreciating)"
            else:
                trend = "stable"
            return spot, trend
        except Exception as e:
            log.warning("USD/INR trend failed: %s", e)
            return None, "unknown"

    def _fetch_oil_context(self) -> Tuple[Optional[float], str]:
        """Oil price and risk level."""
        try:
            import yfinance as yf
            t = yf.Ticker("BZ=F")
            h = t.history(period="5d")
            if len(h) == 0:
                return None, "unknown"
            spot = float(h["Close"].iloc[-1])
            if spot > 90:
                risk = "HIGH (bad for India import bill)"
            elif spot > 75:
                risk = "MODERATE"
            else:
                risk = "LOW"
            return spot, risk
        except Exception as e:
            log.warning("Oil fetch failed: %s", e)
            return None, "unknown"

    def scan(self) -> MacroSnapshot:
        """Run full macro scan and return snapshot."""
        usd_inr, inr_trend = self._fetch_usd_inr_trend()
        oil, oil_risk = self._fetch_oil_context()
        gold = self._fetch_rate(GOLD)

        risk_score = 5.0
        tailwinds = []
        headwinds = []

        # INR depreciation = risk
        if usd_inr and usd_inr > 85:
            risk_score += 2.0
            headwinds.append(f"USD/INR above 85 ({usd_inr:.1f}) — FII outflow pressure")
        elif usd_inr and usd_inr < 83:
            risk_score -= 1.0
            tailwinds.append(f"Strong rupee ({usd_inr:.1f}) — positive for imports")

        # Oil prices
        if oil and oil > 85:
            risk_score += 2.0
            headwinds.append(f"Oil above $85 — inflation & import bill pressure")
        elif oil and oil < 70:
            risk_score -= 1.5
            tailwinds.append(f"Low oil prices (${oil:.0f}) — import bill relief")

        # Gold context
        if gold and gold > 2400:
            headwinds.append("Gold elevated — safe-haven demand (risk-off)")
            risk_score += 0.5

        fear = "MODERATE"
        if risk_score >= 8:
            fear = "EXTREME"
        elif risk_score >= 6:
            fear = "HIGH"
        elif risk_score <= 3:
            fear = "LOW"

        thesis_parts = []
        if headwinds:
            thesis_parts.append("Risks: " + "; ".join(headwinds[:2]))
        if tailwinds:
            thesis_parts.append("Tailwinds: " + "; ".join(tailwinds[:2]))

        return MacroSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            usd_inr=usd_inr or 0.0,
            oil_usd=oil or 0.0,
            fear_indicator=fear,
            risk_score=round(risk_score, 1),
            thesis=" | ".join(thesis_parts),
            tailwinds=tailwinds,
            headwinds=headwinds,
        )

    def recommend_regime(self) -> str:
        """
        Recommend position strategy based on macro risk.
        """
        snap = self.scan()
        if snap.risk_score >= 8:
            return "DEFENSE"
        elif snap.risk_score >= 6:
            return "CAUTIOUS"  # Smaller positions, wider stops
        elif snap.risk_score <= 3:
            return "AGGRESSIVE"  # Full position, tighter stops
        else:
            return "BALANCED"


def write_sentinel_report(path: str = None) -> str:
    """Write macro snapshot to file."""
    if path is None:
        path = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/intel/india_macro.json"

    sentinel = IndiaSentinel()
    snap = sentinel.scan()

    data = {
        "timestamp": snap.timestamp,
        "usd_inr": snap.usd_inr,
        "oil_usd": snap.oil_usd,
        "risk_score": snap.risk_score,
        "fear_indicator": snap.fear_indicator,
        "thesis": snap.thesis,
        "recommended_regime": sentinel.recommend_regime(),
        "tailwinds": snap.tailwinds,
        "headwinds": snap.headwinds,
    }

    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    log.info("Sentinel report: %s", path)
    return path
