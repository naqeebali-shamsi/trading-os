"""
autonome/intelligence/timesfm_adapter.py  v1.0
Google TimesFM wrapper for price forecasting.

Since pip install is unavailable in this environment, we provide a
TREND-BASED MOCK that mimics the TimesFM API surface. To swap in
the real model:

    pip install timesfm==1.3.0
    from timesfm import TimesFm

Then replace TrendMock with a real TimesFm instance.
"""
from __future__ import annotations

import logging, statistics
from typing import List, Dict
from datetime import datetime

from autonome.data.bars import Bar

log = logging.getLogger("intelligence.timesfm")


# ── Mock implementation (real TimesFM not installed) ──────────────────────
class TrendMock:
    """Deterministic mock that extrapolates recent trend with noise bands."""

    def forecast(self, history: List[Bar], horizon: int = 5) -> Dict:
        if len(history) < 10:
            return self._flat_forecast(history, horizon)

        closes = [b.close for b in history[-20:]]
        sma5 = statistics.mean(closes[-5:])
        sma20 = statistics.mean(closes)
        momentum = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] else 0
        vol = statistics.stdev(closes) if len(closes) > 1 else 0

        # Trend projection
        base = closes[-1]
        projected = []
        for i in range(1, horizon + 1):
            drift = momentum * base * i * 0.5  # dampened continuation
            projected.append(base + drift)

        # Confidence bands widen with volatility
        ci = vol * 1.5
        lower = [p - ci for p in projected]
        upper = [p + ci for p in projected]

        # Regime classification
        regime = "neutral"
        if momentum > 0.01 and sma5 > sma20:
            regime = "bullish"
        elif momentum < -0.01 and sma5 < sma20:
            regime = "bearish"
        elif vol / base > 0.02:
            regime = "volatile"

        return {
            "point": projected,
            "lower": lower,
            "upper": upper,
            "regime": regime,
            "momentum_pct": round(momentum * 100, 3),
            "confidence": max(0.0, min(1.0, 1.0 - (vol / base) * 10)),
        }

    def _flat_forecast(self, history, horizon):
        last = history[-1].close if history else 100.0
        return {
            "point": [last] * horizon,
            "lower": [last * 0.98] * horizon,
            "upper": [last * 1.02] * horizon,
            "regime": "insufficient_data",
            "momentum_pct": 0.0,
            "confidence": 0.0,
        }


class TimesFMAdapter:
    """Uniform API wrapper.  Swap backend without changing callers."""

    def __init__(self, _model_path: str = ""):
        # TODO: load real TimesFm here when available
        self._model = TrendMock()

    def forecast(self, symbol: str, history: List[Bar], horizon: int = 5) -> Dict:
        """
        Return dict with keys:
            point: List[float] — predicted closes
            lower: List[float] — 80% CI lower
            upper: List[float] — 80% CI upper
            regime: str — bullish | bearish | volatile | neutral
            momentum_pct: float
            confidence: float — 0.0 to 1.0
        """
        result = self._model.forecast(history, horizon)
        result["symbol"] = symbol
        result["horizon"] = horizon
        result["generated_at"] = datetime.utcnow().isoformat()
        log.info("TimesFM | %s | regime=%s | momentum=%+.3f%% | conf=%.2f",
                 symbol, result["regime"], result["momentum_pct"], result["confidence"])
        return result

    def direction_bias(self, forecast: Dict) -> str:
        """Simplified bias for strategy consumption."""
        point = forecast.get("point", [])
        if not point:
            return "neutral"
        change = (point[-1] - point[0]) / point[0] * 100
        if change > 2.0:
            return "strong_long"
        if change > 0.5:
            return "weak_long"
        if change < -2.0:
            return "strong_short"
        if change < -0.5:
            return "weak_short"
        return "neutral"
