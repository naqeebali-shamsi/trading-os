"""Load latest stock research snapshot for scanner/brain consumers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT = ROOT / "intel" / "stock_research_latest.json"

TIER_ORDER = {
    "multibagger_candidate": 0,
    "high_conviction": 1,
    "accumulate": 2,
    "watch": 3,
}


def load_snapshot(path: Path = DEFAULT_SNAPSHOT) -> Dict[str, Any]:
    if not path.exists():
        return {"available": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"available": False}
    payload["available"] = True
    return payload


def research_by_symbol(snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    snapshot = snapshot or load_snapshot()
    if not snapshot.get("available"):
        return {}
    rows: List[Dict[str, Any]] = []
    rows.extend(snapshot.get("top_picks") or [])
    rows.extend(snapshot.get("multibagger_candidates") or [])
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        if sym:
            out[sym] = row
    return out


def tier_rank(tier: Optional[str]) -> int:
    return TIER_ORDER.get(str(tier or "watch"), 99)


def passes_research_gate(
    symbol: str,
    research: Dict[str, Dict[str, Any]],
    *,
    min_tier: Optional[str] = None,
    min_confidence: float = 0.0,
) -> bool:
    row = research.get(str(symbol).upper())
    if not row:
        return min_tier is None and min_confidence <= 0
    if min_confidence and float(row.get("confidence") or 0) < min_confidence:
        return False
    if min_tier:
        required = tier_rank(min_tier)
        actual = tier_rank(row.get("tier"))
        if actual > required:
            return False
    return True


def sort_symbols_by_research(symbols: List[str], research: Dict[str, Dict[str, Any]]) -> List[str]:
    def key(sym: str):
        row = research.get(str(sym).upper()) or {}
        return (
            tier_rank(row.get("tier")),
            -(float(row.get("confidence") or 0)),
            -(float(row.get("composite_score") or 0)),
            sym,
        )

    return sorted(symbols, key=key)


def pick_research_candidates(
    symbols: List[str],
    *,
    min_tier: Optional[str] = None,
    min_confidence: float = 0.0,
    limit: Optional[int] = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> List[str]:
    research = research_by_symbol(snapshot)
    filtered = [
        s for s in symbols if passes_research_gate(s, research, min_tier=min_tier, min_confidence=min_confidence)
    ]
    ordered = sort_symbols_by_research(filtered, research)
    if limit is not None and limit > 0:
        return ordered[:limit]
    return ordered
