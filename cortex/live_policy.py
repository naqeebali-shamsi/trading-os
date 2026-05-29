"""Approved live trading policy overlay (human-gated Dream Lab promotions).

All auto-trading reads strategy weights, confidence calibration, and research
boosts from this file after human approval. Agents never write here directly.
"""
from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
LIVE_POLICY_FILE = ROOT / "intel" / "live_policy.json"
POLICY_HISTORY_FILE = ROOT / "intel" / "live_policy_history.jsonl"

DEFAULT_POLICY: Dict[str, Any] = {
    "version": 0,
    "updated_ts": 0.0,
    "strategies": {},
    "signal_min_confidence": None,
    "research_tier_boost": {},
    "confidence_calibration": {},
    "macro_lexicon": {},
    "approvals": [],
}


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deepcopy(default)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_policy() -> Dict[str, Any]:
    raw = _read_json(LIVE_POLICY_FILE, DEFAULT_POLICY)
    out = deepcopy(DEFAULT_POLICY)
    out.update(raw)
    out.setdefault("strategies", {})
    out.setdefault("research_tier_boost", {})
    out.setdefault("confidence_calibration", {})
    out.setdefault("macro_lexicon", {})
    out.setdefault("approvals", [])
    return out


def save_policy(policy: Dict[str, Any], *, reason: str = "update") -> Dict[str, Any]:
    policy = deepcopy(policy)
    policy["version"] = int(policy.get("version") or 0) + 1
    policy["updated_ts"] = time.time()
    _write_json(LIVE_POLICY_FILE, policy)
    _append_history({"ts": policy["updated_ts"], "version": policy["version"], "reason": reason, "policy": policy})
    return policy


def _append_history(row: dict) -> None:
    POLICY_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with POLICY_HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def apply_promotion_patch(patch: Mapping[str, Any], *, promotion_id: str, actor: str = "human") -> Dict[str, Any]:
    """Merge an approved promotion patch into live policy."""
    policy = load_policy()
    ptype = str(patch.get("type") or "")

    if ptype in {"strategy_weight", "strategy_active"}:
        sid = str(patch.get("strategy_id") or "")
        if not sid:
            raise ValueError("strategy promotion requires strategy_id")
        entry = dict(policy["strategies"].get(sid) or {})
        for key in ("weight", "active", "score"):
            if key in patch:
                entry[key] = patch[key]
        policy["strategies"][sid] = entry
    elif ptype == "signal_min_confidence":
        if "value" in patch:
            policy["signal_min_confidence"] = float(patch["value"])
    elif ptype == "research_tier_boost":
        tier = str(patch.get("tier") or "")
        if tier and "boost" in patch:
            policy["research_tier_boost"][tier] = float(patch["boost"])
    elif ptype == "confidence_calibration":
        policy["confidence_calibration"].update(patch.get("mapping") or {})
        if "signal_min_confidence" in patch:
            policy["signal_min_confidence"] = float(patch["signal_min_confidence"])
    elif ptype == "macro_lexicon_weight":
        keyword = str(patch.get("keyword") or "")
        if keyword and "weight" in patch:
            policy["macro_lexicon"][keyword] = float(patch["weight"])
    else:
        raise ValueError(f"unsupported promotion type: {ptype}")

    approvals = list(policy.get("approvals") or [])
    approvals.append({"promotion_id": promotion_id, "actor": actor, "ts": time.time(), "type": ptype})
    policy["approvals"] = approvals[-100:]
    return save_policy(policy, reason=f"approved:{promotion_id}")


def rollback(version: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Restore prior policy version from history."""
    if not POLICY_HISTORY_FILE.exists():
        return None
    rows = []
    for line in POLICY_HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return None
    target = version
    if target is None:
        current = load_policy()
        target = int(current.get("version") or 1) - 1
    match = None
    for row in reversed(rows):
        if int(row.get("version") or 0) == target:
            match = row.get("policy")
            break
    if not match:
        return None
    restored = deepcopy(match)
    restored["version"] = int(restored.get("version") or target)
    _write_json(LIVE_POLICY_FILE, restored)
    return restored


def strategy_overlay(strategy_id: str) -> Dict[str, Any]:
    return dict(load_policy().get("strategies", {}).get(strategy_id) or {})


def effective_signal_min_confidence(default: float) -> float:
    val = load_policy().get("signal_min_confidence")
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def effective_research_tier_boost(defaults: Mapping[str, float]) -> Dict[str, float]:
    out = dict(defaults)
    out.update({str(k): float(v) for k, v in (load_policy().get("research_tier_boost") or {}).items()})
    return out


def calibrate_confidence(raw_confidence: float, *, pattern_count: int = 0) -> float:
    """Apply approved calibration mapping if present."""
    mapping = load_policy().get("confidence_calibration") or {}
    if not mapping:
        return raw_confidence
    offset = float(mapping.get("confidence_offset") or 0.0)
    scale = float(mapping.get("confidence_scale") or 1.0)
    per_pattern = float(mapping.get("per_pattern_bonus") or 0.0)
    adjusted = (float(raw_confidence) * scale) + offset + (per_pattern * max(0, pattern_count))
    return round(min(1.0, max(0.0, adjusted)), 2)


def policy_summary() -> Dict[str, Any]:
    p = load_policy()
    return {
        "version": p.get("version"),
        "updated_ts": p.get("updated_ts"),
        "strategy_count": len(p.get("strategies") or {}),
        "signal_min_confidence": p.get("signal_min_confidence"),
        "research_tier_boost": p.get("research_tier_boost"),
        "confidence_calibration": p.get("confidence_calibration"),
        "macro_lexicon_count": len(p.get("macro_lexicon") or {}),
    }
