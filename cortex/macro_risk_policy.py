"""Convert macro event radar into risk constraints (not orders)."""
from __future__ import annotations

import time
from typing import Any, Optional

from cortex.macro_lexicon import get_symbol_relevance


def build_policy_from_radar(radar: dict[str, Any]) -> dict[str, Any]:
    """Derive sizing/blackout constraints from a radar classification payload."""
    category = str(radar.get("category") or "none")
    bias = str(radar.get("bias") or "neutral")
    severity = str(radar.get("severity") or "low").lower()
    confidence = float(radar.get("confidence") or 0.0)
    symbols = [str(s).upper() for s in (radar.get("candidate_symbols") or [])]

    blocked_symbols: list[str] = []
    size_multiplier = 1.0
    blackout_recommended = False
    allow_new_risk = True
    notes: list[str] = []

    if category == "none" or confidence < 0.35:
        return _policy_payload(
            radar,
            allow_new_risk=True,
            size_multiplier=1.0,
            blocked_symbols=[],
            blackout_recommended=False,
            notes=["no_material_macro_signal"],
        )

    if severity in {"high", "critical"} and confidence >= 0.75:
        if bias in {"risk_off", "geopolitical_escalation"}:
            blackout_recommended = True
            size_multiplier = 0.25
            notes.append("high_severity_risk_off")
        if bias == "equity_volatility":
            blocked_symbols.extend(s for s in symbols if s)
            size_multiplier = min(size_multiplier, 0.5)
            notes.append("equity_volatility_reduce_beta")
        if bias == "rates_volatility":
            size_multiplier = min(size_multiplier, 0.5)
            notes.append("rates_volatility_reduce_size")

    elif severity == "medium" and confidence >= 0.55:
        size_multiplier = 0.75
        notes.append("medium_severity_caution")

    relevance = get_symbol_relevance()
    for sym in symbols:
        if not sym or sym in blocked_symbols:
            continue
        cat_weight = float((relevance.get(sym) or {}).get(category, 0) or 0)
        if bias == "risk_off" and cat_weight >= 1.5:
            blocked_symbols.append(sym)

    if blackout_recommended:
        allow_new_risk = False

    return _policy_payload(
        radar,
        allow_new_risk=allow_new_risk,
        size_multiplier=size_multiplier,
        blocked_symbols=sorted(set(blocked_symbols)),
        blackout_recommended=blackout_recommended,
        notes=notes,
    )


def _policy_payload(
    radar: dict[str, Any],
    *,
    allow_new_risk: bool,
    size_multiplier: float,
    blocked_symbols: list[str],
    blackout_recommended: bool,
    notes: list[str],
) -> dict[str, Any]:
    return {
        "source": "macro_risk_policy",
        "advisory_only": True,
        "ts": time.time(),
        "category": radar.get("category"),
        "bias": radar.get("bias"),
        "severity": radar.get("severity"),
        "confidence": radar.get("confidence"),
        "allow_new_risk": allow_new_risk,
        "size_multiplier": round(max(0.0, min(1.0, size_multiplier)), 3),
        "blocked_symbols": blocked_symbols,
        "blackout_recommended": blackout_recommended,
        "notes": notes,
        "radar_action_hint": radar.get("action_hint"),
    }


def apply_policy_to_intent(intent: dict[str, Any], policy: dict[str, Any]) -> tuple[bool, str]:
    """Return whether an intent may proceed under macro policy."""
    symbol = str(intent.get("symbol") or "").upper()
    if policy.get("blackout_recommended"):
        return False, "macro_blackout"
    if symbol in [str(s).upper() for s in policy.get("blocked_symbols") or []]:
        return False, f"macro_blocked_symbol:{symbol}"
    if not policy.get("allow_new_risk", True):
        return False, "macro_new_risk_disabled"
    return True, "ok"


def scale_qty(intent: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Return intent copy with qty scaled by policy size_multiplier."""
    out = dict(intent)
    mult = float(policy.get("size_multiplier") or 1.0)
    if mult < 1.0 and out.get("qty") is not None:
        try:
            qty = float(out["qty"])
            out["qty"] = round(max(qty * mult, 0.01), 4)
            out["macro_size_multiplier"] = mult
        except (TypeError, ValueError):
            pass
    return out
