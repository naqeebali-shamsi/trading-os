"""Confidence factor extraction from brain/signal/guard and bus snapshots."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from bus import subscribe  # noqa: E402


def latest_payload(topic: str) -> Dict[str, Any]:
    events = subscribe(topic, limit=1)
    return events[-1].get("payload", {}) if events else {}


def _proposal_from_brain(payload: Dict[str, Any]) -> Dict[str, Any]:
    decision = payload.get("decision") or {}
    return decision.get("proposal") or {}


def _risk_from_brain(payload: Dict[str, Any]) -> Dict[str, Any]:
    decision = payload.get("decision") or {}
    return decision.get("risk") or {}


def _macro_from_brain(payload: Dict[str, Any]) -> Dict[str, Any]:
    decision = payload.get("decision") or {}
    return decision.get("macro") or {}


def _llm_from_brain(payload: Dict[str, Any]) -> Dict[str, Any]:
    return payload.get("llm") or {}


def _forecast_factor(forecast: Dict[str, Any], side: Optional[str] = None) -> Dict[str, Any]:
    nested = forecast.get("forecast") if isinstance(forecast, dict) else {}
    nested = nested or {}
    direction = nested.get("direction") or forecast.get("direction")
    confidence = nested.get("confidence") or forecast.get("confidence")
    aligned = None
    if direction in {"up", "down"} and side in {"BUY", "SELL"}:
        aligned = (direction == "up" and side == "BUY") or (direction == "down" and side == "SELL")
    return {
        "model": forecast.get("model"),
        "ok": forecast.get("ok"),
        "direction": direction,
        "confidence": confidence,
        "aligned_with_side": aligned,
    }


def confidence_factors(
    *,
    brain: Optional[Dict[str, Any]] = None,
    signal: Optional[Dict[str, Any]] = None,
    guard: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    brain = brain or {}
    signal = signal or {}
    guard = guard or {}
    proposal = _proposal_from_brain(brain) if brain else signal
    risk = _risk_from_brain(brain)
    macro = _macro_from_brain(brain)
    llm = _llm_from_brain(brain)
    side = proposal.get("side") or signal.get("side")
    symbol = proposal.get("symbol") or signal.get("symbol")
    forecast = latest_payload(f"market.forecast.{symbol}") if symbol else latest_payload("market.forecast")
    regime = latest_payload("market.regime")
    event_radar = latest_payload("macro.event_radar")

    factors = {
        "proposal_confidence": proposal.get("confidence"),
        "signal_confidence": signal.get("confidence"),
        "llm_ok": llm.get("ok"),
        "llm_provider": llm.get("provider"),
        "llm_model": llm.get("model"),
        "risk_allow_new": risk.get("allow_new_risk"),
        "risk_severity": risk.get("severity"),
        "macro_regime": macro.get("risk_regime"),
        "macro_confidence": macro.get("confidence"),
        "macro_blackout": macro.get("blackout_recommended"),
        "forecast": _forecast_factor(forecast, side=side),
        "regime": regime.get("regime"),
        "event_category": event_radar.get("category"),
        "event_severity": event_radar.get("severity"),
        "guard_ok": guard.get("ok"),
        "guard_reason": guard.get("reason"),
        "missing_stop_loss": proposal.get("action") in {"NEW_ORDER", "PROPOSE_ORDER"} and not proposal.get("sl"),
    }

    penalties = []
    boosts = []
    if factors["macro_blackout"] or factors["macro_regime"] == "risk_off":
        penalties.append("macro_risk_off_or_blackout")
    if factors["risk_allow_new"] is False:
        penalties.append("risk_assessment_blocks_new_risk")
    if factors["forecast"]["ok"] is False:
        penalties.append("forecast_unavailable")
    if factors["forecast"].get("aligned_with_side") is False:
        penalties.append("forecast_side_conflict")
    if factors["missing_stop_loss"]:
        penalties.append("missing_stop_loss")
    if factors["guard_ok"] is False:
        penalties.append(f"guard_block:{factors['guard_reason']}")
    if factors["forecast"].get("aligned_with_side") is True:
        boosts.append("forecast_side_aligned")
    if factors["regime"] in {"trending", "trend"}:
        boosts.append("trend_regime")

    factors["confidence_penalties"] = penalties
    factors["confidence_boosts"] = boosts
    return factors


def factor_snapshot(
    *,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    brain: Optional[Dict[str, Any]] = None,
    signal: Optional[Dict[str, Any]] = None,
    guard: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build confidence factors with optional explicit symbol/side hints."""
    merged_signal = dict(signal or {})
    if symbol:
        merged_signal.setdefault("symbol", symbol)
    if side:
        merged_signal.setdefault("side", side)
    return confidence_factors(brain=brain, signal=merged_signal, guard=guard)
