"""Promotion queue for human-gated Dream Lab improvements."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
QUEUE_FILE = ROOT / "intel" / "promotion_queue.jsonl"


def _iter_queue() -> List[Dict[str, Any]]:
    if not QUEUE_FILE.exists():
        return []
    rows = []
    for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _write_queue(rows: List[Dict[str, Any]]) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r, sort_keys=True) for r in rows)
    if text:
        text += "\n"
    QUEUE_FILE.write_text(text, encoding="utf-8")


def propose(
    *,
    ptype: str,
    summary: str,
    patch: Dict[str, Any],
    evidence: Optional[Dict[str, Any]] = None,
    risk: str = "low",
    agent: str = "dream_lab",
    dedupe_window_sec: Optional[int] = None,
) -> Dict[str, Any]:
    """Append a pending promotion proposal."""
    if dedupe_window_sec is None:
        try:
            from rd.config import load_config

            dedupe_window_sec = int((load_config().get("promoter") or {}).get("dedupe_window_sec", 86400))
        except ImportError:
            dedupe_window_sec = 86400

    now = time.time()
    for row in _iter_queue():
        if row.get("status") != "pending":
            continue
        if row.get("type") != ptype:
            continue
        if now - float(row.get("created_ts") or 0) > dedupe_window_sec:
            continue
        if (row.get("patch") or {}).get("strategy_id") and (row.get("patch") or {}).get("strategy_id") == patch.get("strategy_id"):
            return row
        if row.get("summary") == summary:
            return row

    promo_id = f"promo_{time.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
    row = {
        "id": promo_id,
        "status": "pending",
        "type": ptype,
        "summary": summary,
        "patch": {**patch, "type": ptype},
        "evidence": evidence or {},
        "risk": risk,
        "agent": agent,
        "created_ts": time.time(),
    }
    rows = _iter_queue()
    rows.append(row)
    _write_queue(rows)
    return row


def list_promotions(*, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    rows = _iter_queue()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: float(r.get("created_ts") or 0), reverse=True)
    return rows[:limit]


def get_promotion(promo_id: str) -> Optional[Dict[str, Any]]:
    for row in _iter_queue():
        if row.get("id") == promo_id:
            return row
    return None


def _update_status(promo_id: str, status: str, **extra) -> Optional[Dict[str, Any]]:
    rows = _iter_queue()
    updated = None
    for row in rows:
        if row.get("id") != promo_id:
            continue
        row["status"] = status
        row["updated_ts"] = time.time()
        row.update(extra)
        updated = row
    if updated:
        _write_queue(rows)
    return updated


def approve(promo_id: str, *, actor: str = "human") -> Dict[str, Any]:
    from cortex.live_policy import apply_promotion_patch

    row = get_promotion(promo_id)
    if not row:
        raise ValueError(f"promotion not found: {promo_id}")
    if row.get("status") != "pending":
        raise ValueError(f"promotion not pending: {row.get('status')}")
    policy = apply_promotion_patch(row.get("patch") or {}, promotion_id=promo_id, actor=actor)
    updated = _update_status(
        promo_id,
        "approved",
        approved_by=actor,
        applied_version=policy.get("version"),
    )
    return {"promotion": updated or row, "policy": policy}


def reject(promo_id: str, *, reason: str = "", actor: str = "human") -> Optional[Dict[str, Any]]:
    row = get_promotion(promo_id)
    if not row:
        raise ValueError(f"promotion not found: {promo_id}")
    if row.get("status") != "pending":
        raise ValueError(f"promotion not pending: {row.get('status')}")
    return _update_status(promo_id, "rejected", rejected_by=actor, reject_reason=reason)


def attach_auditor_notes(promo_id: str, notes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Merge auditor / ensemble notes into a pending promotion's evidence."""
    rows = _iter_queue()
    updated = None
    for row in rows:
        if row.get("id") != promo_id or row.get("status") != "pending":
            continue
        evidence = dict(row.get("evidence") or {})
        evidence["auditor"] = notes
        row["evidence"] = evidence
        row["updated_ts"] = time.time()
        updated = row
    if updated:
        _write_queue(rows)
    return updated
