"""
autonome/intelligence/regime_forecaster.py  v1.0
Augments the static regime filter with forward-looking TimesFM forecasts.

Rules:
  * Forecast down > 2 %  → override regime to HIGH_VOL (avoid new risk)
  * Forecast up   > 2 %  → boost conviction on existing OK regime
  * Otherwise            → pass-through from base RegimeFilter
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from autonome.data.bars import Bar
from autonome.strategy.regime import RegimeFilter
from autonome.intelligence.timesfm_adapter import (
    TimesFmAdapter,
    ForecastResult,
    get_adapter,
)

log = logging.getLogger("intel.regime_forecaster")

# Thresholds
DOWN_VOLATILITY_PCT = -2.0    # forecast drop => HIGH_VOL
UP_CONVICTION_PCT = 2.0       # forecast rally => boost conviction
BOOST_AMOUNT = 0.15           # add to confidence when up > threshold


def augment_regime(
    symbol: str,
    base_filter: RegimeFilter,
    history_bars: List[Bar],
    current_confidence: float = 0.5,
    adapter: Optional[TimesFmAdapter] = None,
) -> Dict[str, object]:
    """
    Query TimesFM forecast and produce a regime override dict.

    Returns:
        {
            "regime": str,               # e.g. "ok", "HIGH_VOL", "below_ema50", ...
            "allowed": bool,             # final trading permission
            "confidence_boost": float,   # delta added to signal confidence
            "forecast_return_pct": float,# raw expected return from forecast
            "reason": str,               # human-readable rationale
        }
    """
    if adapter is None:
        adapter = get_adapter()

    forecast = adapter.forecast(symbol, history_bars, horizon=5)
    base_allowed, base_reason = base_filter.check(history_bars[-1].t) if history_bars else (False, "no_history")

    ret = forecast.expected_return_pct

    # --------------------------------------------------------------
    # DOWN > 2 %  →  HIGH_VOL override (block new entries)
    # --------------------------------------------------------------
    if ret <= DOWN_VOLATILITY_PCT:
        log.warning(
            "REGIME OVERRIDE %s → HIGH_VOL (forecast %.2f%%)", symbol, ret
        )
        return {
            "regime": "HIGH_VOL",
            "allowed": False,
            "confidence_boost": 0.0,
            "forecast_return_pct": ret,
            "reason": (
                f"TimesFM predicts -{abs(ret):.2f}% over next 5 bars; "
                "regime forced HIGH_VOL"
            ),
        }

    # --------------------------------------------------------------
    # UP > 2 %  →  boost conviction if base regime is OK
    # --------------------------------------------------------------
    if ret >= UP_CONVICTION_PCT and base_allowed:
        boost = BOOST_AMOUNT * forecast.trend_strength
        log.info(
            "REGIME BOOST %s | +%.2f confidence (forecast +%.2f%%)",
            symbol, boost, ret,
        )
        return {
            "regime": base_reason,
            "allowed": True,
            "confidence_boost": round(boost, 4),
            "forecast_return_pct": ret,
            "reason": (
                f"Base regime OK + TimesFM predicts +{ret:.2f}% | "
                f"conviction boost +{boost:.2f}"
            ),
        }

    # --------------------------------------------------------------
    # Pass-through
    # --------------------------------------------------------------
    return {
        "regime": base_reason,
        "allowed": base_allowed,
        "confidence_boost": 0.0,
        "forecast_return_pct": ret,
        "reason": f"Base regime={base_reason} (forecast {ret:+.2f}%)",
    }


class RegimeForecaster:
    """
    Wrapper that owns both the historical RegimeFilter and the
    forward-looking TimesFM adapter.
    """

    def __init__(
        self,
        daily_bars: List[Bar],
        adapter: Optional[TimesFmAdapter] = None,
    ):
        self.base = RegimeFilter(daily_bars)
        self.adapter = adapter or get_adapter()

    def check(
        self,
        symbol: str,
        history_bars: List[Bar],
        current_confidence: float = 0.5,
    ) -> Dict[str, object]:
        return augment_regime(
            symbol=symbol,
            base_filter=self.base,
            history_bars=history_bars,
            current_confidence=current_confidence,
            adapter=self.adapter,
        )
