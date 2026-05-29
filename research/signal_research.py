"""Apply stock research snapshot to live signal engine intents."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

from research.config import load_config
from research.snapshot import load_snapshot, passes_research_gate, research_by_symbol

try:
    from cortex.live_policy import effective_research_tier_boost
except ImportError:
    effective_research_tier_boost = None

DEFAULT_TIER_BOOST = {
    "multibagger_candidate": 0.05,
    "high_conviction": 0.03,
    "accumulate": 0.01,
    "watch": 0.0,
}


def resolve_signal_research_settings(controls: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Merge stock_research.yaml signal_engine block with runtime_controls overrides."""
    cfg = load_config()
    base: Dict[str, Any] = dict(cfg.get("signal_engine") or {})
    controls = controls or {}

    ctrl_map = {
        "enabled": "signal_research_enabled",
        "min_tier": "signal_research_min_tier",
        "min_confidence": "signal_research_min_confidence",
        "block_below_min_tier": "signal_research_block_below_tier",
        "require_research_row": "signal_research_require_row",
    }
    for cfg_key, ctrl_key in ctrl_map.items():
        if ctrl_key in controls:
            base[cfg_key] = controls[ctrl_key]

    boosts = dict(DEFAULT_TIER_BOOST)
    boosts.update(base.get("tier_confidence_boost") or {})
    if effective_research_tier_boost is not None:
        boosts = effective_research_tier_boost(boosts)
    base["tier_confidence_boost"] = boosts
    base.setdefault("enabled", True)
    base.setdefault("min_tier", None)
    base.setdefault("min_confidence", 0.0)
    base.setdefault("block_below_min_tier", False)
    base.setdefault("require_research_row", False)
    return base


def apply_stock_research(
    intent: Dict[str, Any],
    symbol: str,
    *,
    asset_class: str,
    controls: Optional[Mapping[str, Any]] = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Boost confidence and optionally gate stock CFD signals using research snapshot."""
    settings = resolve_signal_research_settings(controls)
    if not settings.get("enabled", True):
        return intent, None
    if str(asset_class or "") != "stock_cfd":
        return intent, None

    sym = str(symbol or intent.get("symbol") or "").upper()
    snap = snapshot if snapshot is not None else load_snapshot()
    research = research_by_symbol(snap)
    row = research.get(sym)

    min_tier = settings.get("min_tier")
    min_confidence = float(settings.get("min_confidence") or 0.0)
    block = bool(settings.get("block_below_min_tier", False))
    require_row = bool(settings.get("require_research_row", False))

    if require_row and not row:
        if block:
            return None, "research_row_missing"
        return intent, None

    if min_tier or min_confidence:
        if not passes_research_gate(sym, research, min_tier=min_tier, min_confidence=min_confidence):
            if block:
                return None, "research_gate_blocked"
            return intent, None

    if not row:
        return intent, None

    boosts = settings.get("tier_confidence_boost") or DEFAULT_TIER_BOOST
    tier = str(row.get("tier") or "watch")
    boost = float(boosts.get(tier, 0.0))
    pattern_conf = float(intent.get("confidence") or 0.0)
    intent = dict(intent)
    intent["pattern_confidence"] = pattern_conf
    intent["confidence"] = round(min(1.0, pattern_conf + boost), 2)
    intent["research"] = {
        "tier": tier,
        "confidence": row.get("confidence"),
        "composite_score": row.get("composite_score"),
        "boost_applied": boost,
        "thesis_tags": row.get("thesis_tags") or [],
    }
    return intent, None
