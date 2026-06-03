"""
autonome/intelligence/timesfm_real.py  v1.0
Production forecasting adapter.

Tries to load Google TimesFM; falls back to a statistical forecaster
(EMA momentum + volatility bands + regime detection) that is significantly
more sophisticated than the simple TrendMock.

To upgrade to real TimesFM when Python <3.12 is available:
    pip install timesfm==1.3.0
    # Edit __init__ below to load TimesFm instead of StatisticalForecaster
"""
from __future__ import annotations

import logging, statistics, math
from typing import List, Dict
from datetime import datetime

from autonome.data.bars import Bar

log = logging.getLogger("intelligence.timesfm")


# ── Statistical Forecaster (production-grade mock) ──────────────────────────
class StatisticalForecaster:
    """
    Multi-factor statistical forecaster using:
    - EMA momentum with divergence detection
    - Bollinger-band mean reversion scoring
    - ATR-based volatility regime
    - Support/resistance from recent swing points
    """

    def forecast(self, history: List[Bar], horizon: int = 5) -> Dict:
        if len(history) < 20:
            return self._flat(history, horizon)

        closes = [b.close for b in history]
        highs = [b.high for b in history]
        lows = [b.low for b in history]
        volumes = [b.volume for b in history]

        # EMAs
        ema9 = self._ema(closes, 9)
        ema21 = self._ema(closes, 21)
        ema50 = self._ema(closes, 50) if len(closes) >= 50 else ema21

        # ATR
        atr = self._atr(history, 14)

        # Momentum
        mom_5 = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] else 0
        mom_10 = (closes[-1] - closes[-10]) / closes[-10] if len(closes) >= 10 and closes[-10] else 0

        # Volatility regime
        vol = statistics.stdev(closes[-20:]) if len(closes) >= 20 else 0
        vol_pct = vol / closes[-1] if closes[-1] else 0

        # Bollinger position
        bb_mid = statistics.mean(closes[-20:])
        bb_std = statistics.stdev(closes[-20:]) if len(closes) >= 20 else 0
        bb_pos = (closes[-1] - bb_mid) / (2 * bb_std) if bb_std else 0

        # Volume trend
        vol_sma = statistics.mean(volumes[-5:])
        vol_long = statistics.mean(volumes[-20:]) if len(volumes) >= 20 else vol_sma
        vol_ratio = vol_sma / vol_long if vol_long else 1.0

        # Support / resistance from recent swings
        support = min(lows[-20:]) if len(lows) >= 20 else min(lows)
        resistance = max(highs[-20:]) if len(highs) >= 20 else max(highs)

        # ── Regime classification ────────────────────────────────────────────
        regime = "neutral"
        if mom_5 > 0.008 and ema9 > ema21 and vol_ratio > 1.0:
            regime = "bullish_momentum"
        elif mom_5 < -0.008 and ema9 < ema21 and vol_ratio > 1.0:
            regime = "bearish_momentum"
        elif bb_pos > 0.8 and mom_5 < 0:
            regime = "overbought_reversion"
        elif bb_pos < -0.8 and mom_5 > 0:
            regime = "oversold_reversion"
        elif vol_pct > 0.025:
            regime = "high_volatility"
        elif abs(mom_5) < 0.003:
            regime = "ranging"

        # ── Forecast projection ──────────────────────────────────────────────
        base = closes[-1]
        projected = []

        # Blend momentum with mean-reversion toward EMA21
        for i in range(1, horizon + 1):
            # Momentum component (decays)
            mom_component = mom_5 * base * (0.7 ** i)

            # Mean-reversion component (grows with distance from EMA)
            dist_from_ema = (base - ema21) / ema21 if ema21 else 0
            reversion_component = -dist_from_ema * base * 0.3 * (1 - 0.8 ** i)

            # Volatility expansion/contraction
            vol_component = 0
            if vol_pct > 0.02:
                vol_component = (math.sin(i) * vol * 0.5)

            pred = base + mom_component + reversion_component + vol_component
            projected.append(pred)

        # Confidence bands widen with volatility and horizon
        ci = max(atr * 1.5, vol * 2.0)
        lower = [p - ci * (1 + i * 0.15) for i, p in enumerate(projected)]
        upper = [p + ci * (1 + i * 0.15) for i, p in enumerate(projected)]

        # Confidence score: higher for clear trends, lower for ranging
        confidence = 0.5
        if regime in ("bullish_momentum", "bearish_momentum"):
            confidence = 0.75
        elif regime in ("overbought_reversion", "oversold_reversion"):
            confidence = 0.65
        elif regime == "ranging":
            confidence = 0.35
        elif regime == "high_volatility":
            confidence = 0.25

        # Adjust confidence by volume confirmation
        if vol_ratio > 1.5:
            confidence = min(0.95, confidence + 0.1)
        elif vol_ratio < 0.7:
            confidence = max(0.1, confidence - 0.1)

        return {
            "point": projected,
            "lower": lower,
            "upper": upper,
            "regime": regime,
            "momentum_pct": round(mom_5 * 100, 3),
            "confidence": round(confidence, 3),
            "support": round(support, 2),
            "resistance": round(resistance, 2),
            "bb_position": round(bb_pos, 3),
            "vol_ratio": round(vol_ratio, 2),
        }

    def _flat(self, history, horizon):
        last = history[-1].close if history else 100.0
        return {
            "point": [last] * horizon,
            "lower": [last * 0.98] * horizon,
            "upper": [last * 1.02] * horizon,
            "regime": "insufficient_data",
            "momentum_pct": 0.0,
            "confidence": 0.0,
            "support": last * 0.95,
            "resistance": last * 1.05,
            "bb_position": 0.0,
            "vol_ratio": 1.0,
        }

    @staticmethod
    def _ema(values: List[float], period: int) -> float:
        if len(values) < period:
            return values[-1] if values else 0.0
        k = 2.0 / (period + 1)
        ema = statistics.mean(values[:period])
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    @staticmethod
    def _atr(bars: List[Bar], period: int) -> float:
        if len(bars) < 2:
            return 0.0
        trs = []
        for i in range(1, len(bars)):
            tr = max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i - 1].close),
                abs(bars[i].low - bars[i - 1].close),
            )
            trs.append(tr)
        if len(trs) < period:
            return statistics.mean(trs) if trs else 0.0
        return statistics.mean(trs[-period:])


# ── TimesFM Adapter (unified API) ───────────────────────────────────────────
class TimesFMAdapter:
    """
    Unified forecasting API.

    Attempts to load Google TimesFM; falls back to StatisticalForecaster.
    """

    def __init__(self, model_path: str = ""):
        self._backend = None
        self._backend_name = "unknown"

        # Try real TimesFM first
        try:
            import timesfm
            self._backend = timesfm.TimesFm.from_pretrained(
                "google/timesfm-2.0-200m"
            )
            self._backend_name = "timesfm"
            log.info("Loaded Google TimesFM backend")
        except Exception as e:
            log.info("TimesFM not available (%s) — using StatisticalForecaster", e)
            self._backend = StatisticalForecaster()
            self._backend_name = "statistical"

    def forecast(self, symbol: str, history: List[Bar], horizon: int = 5) -> Dict:
        result = self._backend.forecast(history, horizon)
        result["symbol"] = symbol
        result["horizon"] = horizon
        result["backend"] = self._backend_name
        result["generated_at"] = datetime.utcnow().isoformat()
        log.info(
            "Forecast | %s | backend=%s | regime=%s | momentum=%+.3f%% | conf=%.2f",
            symbol, self._backend_name, result["regime"],
            result["momentum_pct"], result["confidence"]
        )
        return result

    def direction_bias(self, forecast: Dict) -> str:
        """Simplified bias for strategy consumption."""
        point = forecast.get("point", [])
        regime = forecast.get("regime", "neutral")
        confidence = forecast.get("confidence", 0.5)
        momentum = forecast.get("momentum_pct", 0.0)

        if not point or confidence < 0.3:
            return "neutral"

        # Regime-driven primary classification
        if regime in ("bullish_momentum", "oversold_reversion"):
            return "strong_long" if confidence > 0.6 else "weak_long"
        if regime in ("bearish_momentum", "overbought_reversion"):
            return "strong_short" if confidence > 0.6 else "weak_short"
        if regime == "high_volatility":
            return "neutral"  # avoid trading in chop
        if regime == "ranging":
            return "neutral"

        # Fallback: use projected point change
        change = (point[-1] - point[0]) / point[0] * 100
        if change > 1.0:
            return "weak_long"
        if change < -1.0:
            return "weak_short"

        return "neutral"

    def should_trade(self, forecast: Dict, signal_direction: str) -> bool:
        """Check if forecast aligns with proposed trade direction."""
        bias = self.direction_bias(forecast)
        confidence = forecast.get("confidence", 0.5)

        if confidence < 0.3:
            return False  # Too uncertain

        aligned = (
            (signal_direction == "LONG" and bias in ("strong_long", "weak_long")) or
            (signal_direction == "SHORT" and bias in ("strong_short", "weak_short"))
        )

        contra = (
            (signal_direction == "LONG" and bias in ("strong_short", "weak_short")) or
            (signal_direction == "SHORT" and bias in ("strong_long", "weak_long"))
        )

        if contra:
            log.warning("Forecast CONTRADICTS %s signal — blocking", signal_direction)
            return False

        return aligned or confidence > 0.6  # Allow uncertain if high confidence
