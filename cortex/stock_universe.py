#!/usr/bin/env python3
"""Stocks arm universe helpers — long-term selection, crowding avoidance.

Design intent (not yet full hedge-fund stack):
- Prefer fundamental, multi-month positioning over shared AI momentum herds.
- Penalize symbols flagged as crowded by popularity/agent-convergence signals.
- Keep selection logic separate from execution (immune/muscle unchanged).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


STOCKS_ARM_HORIZON = "long_term"
DEFAULT_CROWDING_PENALTY = 0.35


def india_watchlist_symbols(registry) -> List[str]:
    return registry.symbols_matching(asset_class="stock_cfd", region="IN")


def enabled_stock_symbols(registry, *, region: Optional[str] = None) -> List[str]:
    return registry.enabled_symbols(asset_class="stock_cfd", region=region)


def deprioritize_crowded(
    symbols: Sequence[str],
    popularity: Mapping[str, float],
    *,
    penalty: float = DEFAULT_CROWDING_PENALTY,
    crowd_threshold: float = 0.7,
) -> List[str]:
    """Return symbols sorted by ascending crowd risk (least crowded first)."""
    scored: List[tuple[float, str]] = []
    for symbol in symbols:
        crowd = float(popularity.get(symbol, 0.0) or 0.0)
        score = crowd + (penalty if crowd >= crowd_threshold else 0.0)
        scored.append((score, symbol))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [symbol for _, symbol in scored]


def rank_long_term_candidates(
    symbols: Sequence[str],
    *,
    fundamentals: Optional[Mapping[str, Mapping[str, Any]]] = None,
    popularity: Optional[Mapping[str, float]] = None,
    research_rows: Optional[Sequence[Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Rank symbols for the stocks arm using research scores + crowding penalty."""
    fundamentals = fundamentals or {}
    popularity = popularity or {}
    research_by_symbol = {str(r.get("symbol") or "").upper(): r for r in (research_rows or [])}

    rows: List[Dict[str, Any]] = []
    ordered = deprioritize_crowded(symbols, popularity) if popularity else list(symbols)
    for symbol in ordered:
        sym = str(symbol).upper()
        meta = fundamentals.get(sym) or fundamentals.get(symbol) or {}
        research = research_by_symbol.get(sym) or {}
        rows.append(
            {
                "symbol": sym,
                "horizon": STOCKS_ARM_HORIZON,
                "fundamental_score": research.get("composite_score") or meta.get("score"),
                "confidence": research.get("confidence"),
                "tier": research.get("tier"),
                "factors": research.get("factors"),
                "thesis": research.get("thesis") or meta.get("thesis"),
                "thesis_tags": research.get("thesis_tags"),
                "crowding": popularity.get(sym),
            }
        )

    rows.sort(
        key=lambda r: (
            {"multibagger_candidate": 0, "high_conviction": 1, "accumulate": 2, "watch": 3}.get(str(r.get("tier")), 9),
            -(float(r.get("confidence") or 0.0)),
            -(float(r.get("fundamental_score") or 0.0)),
            str(r.get("symbol") or ""),
        )
    )
    return rows
