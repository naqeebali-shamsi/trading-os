"""Symbol-scoped news halt logic with TTL decay for the signal macro gate.

News orchestrator publishes advisory ``cortex.decision`` payloads. The signal
engine must not treat a FX headline halt as a global trading stop for unrelated
symbols (e.g. GOOGL). Halts also expire so stale headline caches cannot block
indefinitely.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterable, Optional, Set, Tuple

HALT_RECOMMENDATIONS = frozenset({"halt_new", "halt_symbols", "hold", "block"})
DEFAULT_HALT_TTL_SEC = int(os.getenv("TRADING_OS_NEWS_HALT_TTL_SEC", "900"))
DEFAULT_DECISION_MAX_AGE_SEC = int(os.getenv("TRADING_OS_NEWS_HALT_MAX_AGE_SEC", "900"))
HALT_SYMBOL_MIN_RELEVANCE = float(os.getenv("TRADING_OS_NEWS_HALT_MIN_SYMBOL_RELEVANCE", "0.5"))


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_symbol(symbol: Optional[str]) -> str:
    return str(symbol or "").upper().strip()


def affected_symbol_map(decision: Dict[str, Any]) -> Dict[str, float]:
    """Return {SYMBOL: relevance} from heterogeneous decision payloads."""
    raw = decision.get("affected_symbols") or {}
    out: Dict[str, float] = {}
    if isinstance(raw, dict):
        for sym, rel in raw.items():
            name = normalize_symbol(sym)
            if not name:
                continue
            score = _num(rel, 0.0) or 0.0
            if score > 0:
                out[name] = score
    elif isinstance(raw, (list, tuple, set)):
        for sym in raw:
            name = normalize_symbol(sym)
            if name:
                out[name] = 1.0
    return out


def halt_symbol_set(decision: Dict[str, Any]) -> Set[str]:
    """Explicit halt list from orchestrator, with legacy fallbacks."""
    explicit = {normalize_symbol(s) for s in (decision.get("halt_symbols") or []) if normalize_symbol(s)}
    if explicit:
        return explicit

    recommendation = str(decision.get("recommendation") or "").lower()
    if recommendation not in HALT_RECOMMENDATIONS:
        return set()

    impacted = affected_symbol_map(decision)
    if not impacted:
        return set()

    # Legacy halt_new used to imply all impacted symbols when relevance is high enough.
    threshold = _num(decision.get("halt_min_relevance"), HALT_SYMBOL_MIN_RELEVANCE) or HALT_SYMBOL_MIN_RELEVANCE
    return {sym for sym, rel in impacted.items() if rel >= threshold}


def decision_age_sec(decision: Dict[str, Any], *, now: Optional[float] = None) -> Optional[float]:
    now = time.time() if now is None else now
    ts = _num(decision.get("ts"))
    if ts is None:
        return None
    return max(0.0, now - ts)


def decision_expired(
    decision: Dict[str, Any],
    *,
    now: Optional[float] = None,
    max_age_sec: int = DEFAULT_DECISION_MAX_AGE_SEC,
) -> bool:
    now = time.time() if now is None else now
    expires = _num(decision.get("expires_ts"))
    if expires is not None:
        return now > expires
    age = decision_age_sec(decision, now=now)
    if age is None:
        return False
    ttl = int(_num(decision.get("ttl_sec"), max_age_sec) or max_age_sec)
    return age > ttl


def recommendation_is_halt(decision: Dict[str, Any]) -> bool:
    rec = str(decision.get("recommendation") or decision.get("action") or "").lower()
    return rec in HALT_RECOMMENDATIONS


def decision_blocks_symbol(
    symbol: str,
    decision: Dict[str, Any],
    *,
    now: Optional[float] = None,
    max_age_sec: int = DEFAULT_DECISION_MAX_AGE_SEC,
) -> Tuple[bool, str]:
    """Return whether ``symbol`` should be blocked by this cortex.decision payload."""
    sym = normalize_symbol(symbol)
    if not sym or not isinstance(decision, dict):
        return False, "ok"

    if decision_expired(decision, now=now, max_age_sec=max_age_sec):
        return False, "decision_expired"

    halted = halt_symbol_set(decision)
    if halted:
        if sym in halted:
            return True, "news_halt_symbol"
        return False, "ok"

    # Global legacy halt_new without symbol scope — do not block unrelated symbols.
    if recommendation_is_halt(decision):
        impacted = affected_symbol_map(decision)
        if impacted:
            return False, "ok"
        return True, "news_halt_global"

    return False, "ok"


def annotate_decision(decision: Dict[str, Any], *, now: Optional[float] = None, ttl_sec: int = DEFAULT_HALT_TTL_SEC) -> Dict[str, Any]:
    """Add TTL + symbol-scoped halt metadata to a news decision before publish."""
    now = time.time() if now is None else now
    out = dict(decision)
    out["ts"] = now
    out["ttl_sec"] = int(ttl_sec)
    out["expires_ts"] = now + int(ttl_sec)

    recommendation = str(out.get("recommendation") or "proceed").lower()
    impacted = affected_symbol_map(out)
    impact = _num(out.get("impact_score"), 0.0) or 0.0

    halt_threshold = float(os.getenv("TRADING_OS_NEWS_HALT_IMPACT_THRESHOLD", "0.92"))
    halt_min_rel = float(os.getenv("TRADING_OS_NEWS_HALT_MIN_SYMBOL_RELEVANCE", str(HALT_SYMBOL_MIN_RELEVANCE)))

    if recommendation == "halt_new" or (impact >= halt_threshold and impacted):
        halted = [sym for sym, rel in impacted.items() if rel >= halt_min_rel]
        if halted:
            out["recommendation"] = "halt_symbols"
            out["halt_symbols"] = sorted(set(halted))
            out["halt_min_relevance"] = halt_min_rel
        elif recommendation == "halt_new":
            # No symbol mapping — downgrade to reduce_size instead of global halt.
            out["recommendation"] = "reduce_size"
            out["halt_symbols"] = []

    if out.get("recommendation") == "halt_symbols" and not out.get("halt_symbols"):
        out["recommendation"] = "reduce_size"

    return out
