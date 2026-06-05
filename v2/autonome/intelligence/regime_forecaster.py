"""
autonome/intelligence/regime_forecaster.py  v1.1
Augments the static regime filter with forward-looking TimesFM forecasts.

Fixed for actual TimesFMAdapter API.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from autonome.data.bars import Bar
from autonome.strategy.regime import RegimeFilter
from autonome.intelligence.timesfm_adapter import TimesFMAdapter

log = logging.getLogger("intel.regime_forecaster")

# Thresholds
DOWN_VOLATILITY_PCT = -2.0    # forecast drop => HIGH_VOL
UP_CONVICTION_PCT = 2.0       # forecast rally => boost conviction
BOOST_AMOUNT = 0.15           # add to confidence when up > threshold


def _expected_return(forecast: dict) -> float:
    """Extract expected return % from TimesFM forecast dict."""
    point = forecast.get("point", [])
    if len(point) < 2:
        return 0.0
    start = point[0]
    end = point[-1]
    if not start:
        return 0.0
    return (end - start) / start * 100


def augment_regime(
    symbol: str,
    base_filter: RegimeFilter,
    history_bars: List[Bar],
    current_confidence: float = 0.5,
    adapter: Optional[TimesFMAdapter] = None,
) -> Dict[str, object]:
    """
    Query TimesFM forecast and produce a regime override dict.

    Returns:
        {
            "regime": str,
            "allowed": bool,
            "confidence_boost": float,
            "forecast_return_pct": float,
            "reason": str,
        }
    """
    if adapter is None:
        try:
            adapter = TimesFMAdapter()
        except Exception:
            log.warning("TimesFM unavailable; falling back to base regime")
            base_allowed, base_reason = base_filter.check(history_bars[-1].t) if history_bars else (False, "no_history")
            return {
                "regime": base_reason,
                "allowed": base_allowed,
                "confidence_boost": 0.0,
                "forecast_return_pct": 0.0,
                "reason": f"Base regime={base_reason} (TimesFM unavailable)",
            }

    forecast = adapter.forecast(symbol, history_bars, horizon=5)
    base_allowed, base_reason = base_filter.check(history_bars[-1].t) if history_bars else (False, "no_history")

    ret = _expected_return(forecast)
    confidence = forecast.get("confidence", 0.0)

    # DOWN > 2 %  ->  HIGH_VOL override (block new entries)
    if ret <= DOWN_VOLATILITY_PCT:
        log.warning(
            "REGIME OVERRIDE %s -> HIGH_VOL (forecast %.2f%%)", symbol, ret
        )
        return {
            "regime": "HIGH_VOL",
            "allowed": False,
            "confidence_boost": 0.0,
            "forecast_return_pct": ret,
            "reason": (
                f"TimesFM predicts {ret:.2f}% over next 5 bars; "
                "regime forced HIGH_VOL"
            ),
        }

    # UP > 2 %  ->  boost conviction if base regime is OK
    if ret >= UP_CONVICTION_PCT and base_allowed:
        boost = BOOST_AMOUNT * confidence
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

    # Pass-through
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
        adapter: Optional[TimesFMAdapter] = None,
    ):
        self.base = RegimeFilter(daily_bars)
        try:
            self.adapter = adapter or TimesFMAdapter()
        except Exception:
            log.warning("TimesFM init failed; regime forecaster running without forecasts")
            self.adapter = None

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
