"""Promotion gate report over labelled edge candidates.

Groups labels by (symbol, timeframe), measures realised edge for each group, and
applies a few simple, centralised promotion gates. This is measurement only: it
reports whether a group *would* clear the bar, it never promotes anything.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

MIN_SAMPLES = 20
MIN_PROFIT_FACTOR = 1.2
MIN_EDGE_AFTER_COST = 0.0


def _group_key(label: dict) -> str:
    symbol = str(label.get("symbol") or "?").upper()
    timeframe = str(label.get("timeframe") or "?")
    return f"{symbol}|{timeframe}"


def _evaluate_group(symbol: str, timeframe: str, rows: List[dict], cost_per_trade: float) -> dict:
    samples = len(rows)
    signed_returns = [float(r.get("signed_return") or 0.0) for r in rows]
    wins = sum(1 for r in rows if r.get("win"))

    win_rate = wins / samples if samples else 0.0
    avg_return = sum(signed_returns) / samples if samples else 0.0
    edge = avg_return - cost_per_trade

    gross_wins = sum(r for r in signed_returns if r > 0)
    gross_losses = sum(-r for r in signed_returns if r < 0)
    if gross_losses > 0:
        profit_factor = gross_wins / gross_losses
    else:
        profit_factor = float("inf") if gross_wins > 0 else 0.0

    reasons: List[str] = []
    if samples < MIN_SAMPLES:
        reasons.append(f"samples<{MIN_SAMPLES}")
    if edge <= MIN_EDGE_AFTER_COST:
        reasons.append("edge<=0")
    if profit_factor < MIN_PROFIT_FACTOR:
        reasons.append(f"profit_factor<{MIN_PROFIT_FACTOR}")

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "samples": samples,
        "win_rate": win_rate,
        "edge": edge,
        "avg_return": avg_return,
        "profit_factor": profit_factor,
        "promotable": not reasons,
        "reasons": reasons,
    }


def gate_report(
    candidates: List[dict],
    labels: List[dict],
    now: Optional[float] = None,
    cost_per_trade: float = 0.0,
) -> Dict[str, Any]:
    """Build a per-group promotion gate report from labelled candidates."""
    grouped: Dict[str, List[dict]] = {}
    for label in labels or []:
        grouped.setdefault(_group_key(label), []).append(label)

    groups: List[dict] = []
    for key in sorted(grouped):
        symbol, timeframe = key.split("|", 1)
        groups.append(_evaluate_group(symbol, timeframe, grouped[key], cost_per_trade))

    return {
        "now": now,
        "cost_per_trade": cost_per_trade,
        "candidate_count": len(candidates or []),
        "label_count": len(labels or []),
        "group_count": len(groups),
        "promotable_count": sum(1 for g in groups if g["promotable"]),
        "groups": groups,
    }
