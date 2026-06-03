"""
autonome/intelligence/timesfm_adapter_production.py  v2.1
Production TimesFM adapter with REAL model support + graceful fallback.

Uses Google TimesFM 2.5 (200M params) loaded from HuggingFace.
Falls back to StatisticalForecaster if real model unavailable.

Install real model:
    uv pip install torch transformers --index-url https://download.pytorch.org/whl/cpu
    uv pip install "git+https://github.com/google-research/timesfm.git"
"""
from __future__ import annotations

import logging, statistics, math, os, sys, warnings
from typing import List, Dict, Optional
from datetime import datetime

from autonome.data.bars import Bar

log = logging.getLogger("intelligence.timesfm")

# Path to venv site-packages if using isolated env
_TIMESFM_VENV = os.environ.get(
    "AUTONOME_TIMESFM_VENV",
    "/mnt/e/NomadCrew[GROWTH]/trading-os/timesfm_env/lib/python3.11/site-packages"
)
if os.path.isdir(_TIMESFM_VENV) and _TIMESFM_VENV not in sys.path:
    sys.path.insert(0, _TIMESFM_VENV)


def _try_import_timesfm():
    """Try to import real TimesFM v2.5 from the timesfm package."""
    try:
        import timesfm
        if hasattr(timesfm, "TimesFM_2p5_200M_torch"):
            return timesfm, "v2.5_torch"
        return None, None
    except Exception as e:
        log.debug("TimesFM import failed: %s", e)
        return None, None


# ── Real TimesFM Backend ──────────────────────────────────────────────────
class RealTimesFMBackend:
    """Production backend using Google's real TimesFM 2.5 model."""

    def __init__(self, model_id: str = "google/timesfm-2.5-200m-pytorch"):
        self.model_id = model_id
        self._model = None
        self._load()

    def _load(self):
        tfm, variant = _try_import_timesfm()
        if tfm is None:
            raise ImportError("timesfm package not available")

        log.info("Loading TimesFM | variant=%s | model=%s", variant, self.model_id)

        # Load model from HuggingFace
        self._model = tfm.TimesFM_2p5_200M_torch.from_pretrained(
            self.model_id, torch_compile=False
        )

        # Compile with production config
        self._model.compile(
            tfm.ForecastConfig(
                max_context=1024,
                max_horizon=256,
                normalize_inputs=True,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=True,
                fix_quantile_crossing=True,
            )
        )
        log.info("TimesFM loaded and compiled successfully")

    def forecast(self, history: List[Bar], horizon: int = 5) -> Dict:
        import numpy as np

        if len(history) < 10:
            return _flat(history, horizon)

        closes = np.array([b.close for b in history], dtype=np.float64)

        try:
            point, quantiles = self._model.forecast(
                horizon=horizon,
                inputs=[closes],
            )

            # point shape: (1, horizon)
            # quantiles shape: (1, horizon, 10) — 10th to 90th percentiles
            pred = point[0]
            lower = quantiles[0, :, 0]   # 10th percentile
            upper = quantiles[0, :, -1]  # 90th percentile

            # Regime detection
            projected_change = (pred[-1] - closes[-1]) / closes[-1]
            vol = np.std(closes[-20:]) / np.mean(closes[-20:]) if len(closes) >= 20 else 0.01

            regime = "neutral"
            if projected_change > 0.01:
                regime = "bullish" if vol < 0.02 else "bullish_volatile"
            elif projected_change < -0.01:
                regime = "bearish" if vol < 0.02 else "bearish_volatile"
            elif vol > 0.025:
                regime = "high_volatility"

            return {
                "point": pred.tolist(),
                "lower": lower.tolist(),
                "upper": upper.tolist(),
                "regime": regime,
                "momentum_pct": round(float(projected_change) * 100, 3),
                "confidence": round(max(0.0, min(1.0, 1.0 - vol * 20)), 3),
            }

        except Exception as e:
            log.error("TimesFM forecast error: %s | falling back", e)
            return _flat(history, horizon)


# ── Statistical Forecaster (deterministic fallback) ────────────────────────
class StatisticalForecaster:
    def forecast(self, history: List[Bar], horizon: int = 5) -> Dict:
        return _statistical_forecast(history, horizon)


def _flat(history: List[Bar], horizon: int) -> Dict:
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


def _statistical_forecast(history: List[Bar], horizon: int) -> Dict:
    if len(history) < 20:
        return _flat(history, horizon)

    closes = [b.close for b in history]
    highs = [b.high for b in history]
    lows = [b.low for b in history]
    volumes = [b.volume for b in history]

    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    atr = _atr(history, 14)
    mom_5 = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] else 0
    vol = statistics.stdev(closes[-20:]) if len(closes) >= 20 else 0
    vol_pct = vol / closes[-1] if closes[-1] else 0
    bb_mid = statistics.mean(closes[-20:])
    bb_std = statistics.stdev(closes[-20:]) if len(closes) >= 20 else 0
    bb_pos = (closes[-1] - bb_mid) / (2 * bb_std) if bb_std else 0
    vol_sma = statistics.mean(volumes[-5:])
    vol_long = statistics.mean(volumes[-20:]) if len(volumes) >= 20 else vol_sma
    vol_ratio = vol_sma / vol_long if vol_long else 1.0
    support = min(lows[-20:]) if len(lows) >= 20 else min(lows)
    resistance = max(highs[-20:]) if len(highs) >= 20 else max(highs)

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

    base = closes[-1]
    projected = []
    for i in range(1, horizon + 1):
        mom_component = mom_5 * base * (0.7 ** i)
        dist_from_ema = (base - ema21) / ema21 if ema21 else 0
        reversion_component = -dist_from_ema * base * 0.3 * (1 - 0.8 ** i)
        vol_component = math.sin(i) * vol * 0.5 if vol_pct > 0.02 else 0
        projected.append(base + mom_component + reversion_component + vol_component)

    ci = max(atr * 1.5, vol * 2.0)
    lower = [p - ci * (1 + i * 0.15) for i, p in enumerate(projected)]
    upper = [p + ci * (1 + i * 0.15) for i, p in enumerate(projected)]

    confidence = 0.5
    if regime in ("bullish_momentum", "bearish_momentum"):
        confidence = 0.75
    elif regime in ("overbought_reversion", "oversold_reversion"):
        confidence = 0.65
    elif regime == "ranging":
        confidence = 0.35
    elif regime == "high_volatility":
        confidence = 0.25

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


def _ema(values: List[float], period: int) -> float:
    if len(values) < period:
        return values[-1] if values else 0.0
    k = 2.0 / (period + 1)
    ema_val = statistics.mean(values[:period])
    for v in values[period:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val


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


# ── Unified Production Adapter ────────────────────────────────────────────
class TimesFMAdapter:
    """
    Production forecasting adapter.

    Auto-detects real TimesFM availability, loads it if possible,
    falls back to statistical forecaster if unavailable.
    """

    def __init__(self, model_id: str = "google/timesfm-2.5-200m-pytorch"):
        self._backend = None
        self._backend_name = "unknown"
        self.model_id = model_id

        # Try real model first
        try:
            self._backend = RealTimesFMBackend(model_id)
            self._backend_name = "timesfm_real"
            log.info("TimesFM | REAL backend loaded | model=%s", model_id)
        except Exception as e:
            log.info("TimesFM real unavailable (%s) — using statistical", e)
            self._backend = StatisticalForecaster()
            self._backend_name = "statistical"

    def forecast(self, symbol: str, history: List[Bar], horizon: int = 5) -> Dict:
        result = self._backend.forecast(history, horizon)
        result["symbol"] = symbol
        result["horizon"] = horizon
        result["backend"] = self._backend_name
        result["model_id"] = self.model_id
        result["generated_at"] = datetime.utcnow().isoformat()
        log.info(
            "Forecast | %s | backend=%s | regime=%s | momentum=%+.3f%% | conf=%.2f",
            symbol, self._backend_name, result["regime"],
            result["momentum_pct"], result["confidence"]
        )
        return result

    def direction_bias(self, forecast: Dict) -> str:
        point = forecast.get("point", [])
        regime = forecast.get("regime", "neutral")
        confidence = forecast.get("confidence", 0.5)

        if not point or confidence < 0.3:
            return "neutral"

        if regime in ("bullish_momentum", "oversold_reversion", "bullish"):
            return "strong_long" if confidence > 0.6 else "weak_long"
        if regime in ("bearish_momentum", "overbought_reversion", "bearish"):
            return "strong_short" if confidence > 0.6 else "weak_short"
        if regime in ("high_volatility", "ranging"):
            return "neutral"

        change = (point[-1] - point[0]) / point[0] * 100
        if change > 1.0:
            return "weak_long"
        if change < -1.0:
            return "weak_short"
        return "neutral"

    def should_trade(self, forecast: Dict, signal_direction: str) -> bool:
        bias = self.direction_bias(forecast)
        confidence = forecast.get("confidence", 0.5)

        if confidence < 0.3:
            return False

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

        return aligned or confidence > 0.6

    @property
    def is_real(self) -> bool:
        return self._backend_name == "timesfm_real"
