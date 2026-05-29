"""Quant-style factor scoring for long-term / multibagger stock selection.

Methodology (hedge-fund aligned, simplified for live screening):
- Quality: ROE, margins, balance-sheet safety (Greenblatt / quality factor literature)
- Growth: revenue + earnings expansion (GARP / Lynch runway)
- Value: avoid overpaying (PEG, FCF yield — not deep-value traps)
- Momentum: 12-1 month return (Jegadeesh-Titman intermediate horizon)
- Multibagger: small/mid runway + reinvestment + growth acceleration composite

Scores are normalized 0..1 per factor. Confidence reflects data completeness and
factor agreement (similar to meta-labeling inputs before position sizing).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _scale(value: Optional[float], *, low: float, high: float, invert: bool = False) -> Optional[float]:
    if value is None:
        return None
    if high == low:
        return 0.5
    raw = (float(value) - low) / (high - low)
    raw = _clamp(raw)
    return _clamp(1.0 - raw if invert else raw)


def score_quality(data: Mapping[str, Any]) -> Optional[float]:
    parts: List[float] = []
    roe = data.get("roe")
    if roe is not None:
        parts.append(_scale(roe, low=0.0, high=0.25) or 0.0)
    gm = data.get("gross_margin")
    if gm is not None:
        parts.append(_scale(gm, low=0.15, high=0.65) or 0.0)
    pm = data.get("profit_margin")
    if pm is not None:
        parts.append(_scale(pm, low=0.0, high=0.25) or 0.0)
    dte = data.get("debt_to_equity")
    if dte is not None:
        parts.append(_scale(dte, low=0.0, high=250.0, invert=True) or 0.0)
    if not parts:
        return None
    return round(sum(parts) / len(parts), 4)


def score_growth(data: Mapping[str, Any]) -> Optional[float]:
    parts: List[float] = []
    rg = data.get("revenue_growth")
    if rg is not None:
        parts.append(_scale(rg, low=-0.05, high=0.35) or 0.0)
    eg = data.get("earnings_growth")
    if eg is not None:
        parts.append(_scale(eg, low=-0.10, high=0.40) or 0.0)
    if not parts:
        return None
    return round(sum(parts) / len(parts), 4)


def score_value(data: Mapping[str, Any]) -> Optional[float]:
    """GARP-friendly value: penalize extreme P/E and reward FCF yield."""
    parts: List[float] = []
    peg = data.get("peg")
    if peg is not None and peg > 0:
        # Sweet spot ~0.8-1.8; penalize >3 and <0.3
        if peg < 0.3:
            parts.append(0.35)
        elif peg <= 1.8:
            parts.append(_scale(peg, low=0.3, high=1.8, invert=True) or 0.0)
        elif peg <= 3.0:
            parts.append(0.35)
        else:
            parts.append(0.15)
    pe = data.get("pe")
    if pe is not None and pe > 0:
        parts.append(_scale(pe, low=8.0, high=45.0, invert=True) or 0.0)
    fcf_yield = data.get("fcf_yield")
    if fcf_yield is not None:
        parts.append(_scale(fcf_yield, low=0.0, high=0.08) or 0.0)
    if not parts:
        return None
    return round(sum(parts) / len(parts), 4)


def score_momentum(data: Mapping[str, Any]) -> Optional[float]:
    mom = data.get("momentum_12_1")
    if mom is None:
        return None
    # Reward positive momentum but fade extreme parabolic (>150% in 12m)
    if mom <= 0:
        return round(_scale(mom, low=-0.30, high=0.0) or 0.0, 4)
    if mom <= 0.80:
        return round(_scale(mom, low=0.0, high=0.80) or 0.0, 4)
    if mom <= 1.50:
        return round(0.85, 4)
    return round(0.55, 4)


def score_multibagger_potential(data: Mapping[str, Any]) -> Optional[float]:
    """Heuristic multibagger runway score (not a guarantee — research filter)."""
    parts: List[float] = []
    mc = data.get("market_cap")
    if mc is not None and mc > 0:
        # Runway band ~300M to 50B USD (log scale)
        log_mc = math.log10(mc)
        if log_mc < 8.5:  # <~300M too micro/illiquid for CFD arm
            parts.append(0.35)
        elif log_mc <= 10.8:  # up to ~60B
            parts.append(_scale(log_mc, low=8.5, high=10.8) or 0.0)
        else:
            parts.append(0.25)

    rg = data.get("revenue_growth")
    if rg is not None:
        parts.append(_scale(rg, low=0.05, high=0.40) or 0.0)

    eg = data.get("earnings_growth")
    if eg is not None:
        parts.append(_scale(eg, low=0.05, high=0.45) or 0.0)

    roe = data.get("roe")
    if roe is not None:
        parts.append(_scale(roe, low=0.08, high=0.30) or 0.0)

    payout = data.get("payout_ratio")
    if payout is not None:
        # Reinvestment preference: lower payout
        parts.append(_scale(payout, low=0.0, high=0.70, invert=True) or 0.0)

    gm = data.get("gross_margin")
    pm = data.get("profit_margin")
    if gm is not None and pm is not None and gm > 0:
        expansion = pm / gm
        parts.append(_scale(expansion, low=0.05, high=0.35) or 0.0)

    if not parts:
        return None
    return round(sum(parts) / len(parts), 4)


def composite_score(
    factors: Mapping[str, Optional[float]],
    weights: Mapping[str, float],
) -> Optional[float]:
    num = 0.0
    den = 0.0
    for name, weight in weights.items():
        val = factors.get(name)
        if val is None or weight <= 0:
            continue
        num += float(val) * float(weight)
        den += float(weight)
    if den <= 0:
        return None
    return round(num / den, 4)


def factor_agreement(factors: Mapping[str, Optional[float]], *, min_factor: float = 0.55) -> float:
    vals = [float(v) for v in factors.values() if v is not None]
    if not vals:
        return 0.0
    strong = sum(1 for v in vals if v >= min_factor)
    return round(strong / len(vals), 4)


def research_confidence(
    *,
    factors: Mapping[str, Optional[float]],
    data_completeness: float,
    composite: Optional[float],
) -> float:
    """High confidence = complete data + factor agreement + strong composite."""
    agreement = factor_agreement(factors)
    comp = float(composite or 0.0)
    completeness = _clamp(float(data_completeness or 0.0))
    raw = 0.35 * completeness + 0.35 * agreement + 0.30 * comp
    return round(_clamp(raw), 4)


def classify_tier(composite: Optional[float], multibagger: Optional[float], confidence: float, *, cfg: Mapping[str, Any]) -> str:
    thresholds = cfg.get("thresholds") or {}
    min_conf = float(thresholds.get("min_confidence", 0.65))
    mb_tier = float(thresholds.get("multibagger_tier", 0.75))
    strong = float(thresholds.get("strong_buy_composite", 0.72))

    if confidence < min_conf:
        return "watch"
    if composite is not None and composite >= strong and (multibagger or 0) >= mb_tier:
        return "multibagger_candidate"
    if composite is not None and composite >= strong:
        return "high_conviction"
    if composite is not None and composite >= 0.62:
        return "accumulate"
    return "watch"


def build_research_row(symbol: str, data: Mapping[str, Any], *, cfg: Mapping[str, Any]) -> Dict[str, Any]:
    weights = cfg.get("weights") or {
        "quality": 0.25,
        "growth": 0.30,
        "value": 0.15,
        "momentum": 0.15,
        "multibagger": 0.15,
    }
    factors = {
        "quality": score_quality(data),
        "growth": score_growth(data),
        "value": score_value(data),
        "momentum": score_momentum(data),
        "multibagger": score_multibagger_potential(data),
    }
    composite = composite_score(factors, weights)
    confidence = research_confidence(
        factors=factors,
        data_completeness=float(data.get("data_completeness") or 0.0),
        composite=composite,
    )
    tier = classify_tier(composite, factors.get("multibagger"), confidence, cfg=cfg)
    thesis_bits = []
    if (factors.get("growth") or 0) >= 0.65:
        thesis_bits.append("superior_growth")
    if (factors.get("quality") or 0) >= 0.65:
        thesis_bits.append("quality_compounder")
    if (factors.get("multibagger") or 0) >= 0.75:
        thesis_bits.append("multibagger_runway")
    if (factors.get("momentum") or 0) >= 0.60:
        thesis_bits.append("positive_momentum")

    return {
        "symbol": symbol,
        "ok": bool(data.get("ok")),
        "tier": tier,
        "composite_score": composite,
        "confidence": confidence,
        "factors": factors,
        "fundamentals": {k: data.get(k) for k in (
            "price", "market_cap", "pe", "peg", "revenue_growth", "earnings_growth",
            "roe", "profit_margin", "gross_margin", "debt_to_equity", "fcf_yield",
            "momentum_12_1", "sector", "industry", "data_completeness",
        )},
        "thesis_tags": thesis_bits,
        "thesis": "; ".join(thesis_bits) or "insufficient_signal",
        "yfinance_ticker": data.get("yfinance_ticker"),
        "source": data.get("source"),
        "error": data.get("error"),
    }


def rank_research_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def sort_key(row: Dict[str, Any]):
        tier_order = {
            "multibagger_candidate": 0,
            "high_conviction": 1,
            "accumulate": 2,
            "watch": 3,
        }
        return (
            tier_order.get(str(row.get("tier")), 9),
            -(float(row.get("confidence") or 0.0)),
            -(float(row.get("composite_score") or 0.0)),
            str(row.get("symbol") or ""),
        )

    return sorted(rows, key=sort_key)
