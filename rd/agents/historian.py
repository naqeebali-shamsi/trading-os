#!/usr/bin/env python3
"""HistorianAgent: join logs into labeled learning rows and dataset health."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from rd.agents.base import DreamAgent  # noqa: E402

DECISION_TRAINING = ROOT / "memory" / "training" / "decision_training.jsonl"
DATASET_QUALITY = ROOT / "memory" / "training" / "datasets" / "signal_outcomes_v0.quality.json"
TRADE_OUTCOMES = ROOT / "memory" / "trade_outcomes.jsonl"


def _row_label(row: dict) -> Optional[str]:
    if row.get("label") not in (None, ""):
        return str(row.get("label"))
    target = row.get("target") or {}
    label = target.get("label")
    return str(label) if label not in (None, "") else None


def _row_confidence(row: dict) -> Optional[float]:
    if row.get("confidence") is not None:
        try:
            return float(row.get("confidence"))
        except (TypeError, ValueError):
            return None
    inp = row.get("input") or {}
    try:
        return float(inp.get("confidence")) if inp.get("confidence") is not None else None
    except (TypeError, ValueError):
        return None


class HistorianAgent(DreamAgent):
    name = "historian"

    def run(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        task = task or {}
        labeled = self._count_labeled_rows()
        pending = self._count_pending_labels()
        quality = self._load_quality()
        result = {
            "labeled_rows": labeled,
            "pending_labels": pending,
            "dataset_quality": quality,
            "proposals": [],
        }
        joined = self._join_pending_labels()
        result["labels_joined"] = joined
        if task.get("rebuild_dataset"):
            result["dataset_rebuild"] = self._maybe_rebuild_dataset()
        return self.envelope(result)

    def _count_labeled_rows(self) -> int:
        if not DECISION_TRAINING.exists():
            return 0
        count = 0
        for line in DECISION_TRAINING.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            label = _row_label(row)
            if label and label != "pending_outcome":
                count += 1
        return count

    def _count_pending_labels(self) -> int:
        if not DECISION_TRAINING.exists():
            return 0
        count = 0
        for line in DECISION_TRAINING.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            label = _row_label(row)
            if label in (None, "pending_outcome"):
                count += 1
        return count

    def _load_quality(self) -> Dict[str, Any]:
        if not DATASET_QUALITY.exists():
            return {"available": False}
        try:
            return {"available": True, **json.loads(DATASET_QUALITY.read_text(encoding="utf-8"))}
        except json.JSONDecodeError:
            return {"available": False}

    def _maybe_rebuild_dataset(self) -> Dict[str, Any]:
        try:
            from research.dataset_builder import main as build_main
        except ImportError:
            return {"ok": False, "error": "dataset_builder unavailable"}
        try:
            code = build_main(["--allow-empty"])
            return {"ok": code == 0, "exit_code": code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _join_pending_labels(self) -> Dict[str, Any]:
        """Join closed trade outcomes onto pending decision training rows."""
        if not DECISION_TRAINING.exists():
            return {"joined": 0, "skipped": True}
        outcomes = self._load_outcomes_by_symbol()
        if not outcomes:
            return {"joined": 0, "reason": "no_trade_outcomes"}

        rows = []
        joined = 0
        for line in DECISION_TRAINING.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                rows.append(line)
                continue
            if _row_label(row) not in (None, "pending_outcome"):
                rows.append(row)
                continue
            symbol = (row.get("symbol") or (row.get("input") or {}).get("symbol") or "").upper()
            side = (row.get("side") or (row.get("input") or {}).get("side") or "").upper()
            match = outcomes.get((symbol, side))
            if not match:
                rows.append(row)
                continue
            pnl = match.get("pnl")
            if pnl is None:
                rows.append(row)
                continue
            label = 1 if float(pnl) > 0 else 0
            if "target" in row and isinstance(row["target"], dict):
                row["target"]["label"] = label
                row["target"]["outcome"] = {"pnl": pnl, "source": "trade_outcomes"}
            else:
                row["label"] = label
                row["outcome"] = {"side_signed_return": float(pnl), "source": "trade_outcomes"}
            joined += 1
            rows.append(row)

        if joined:
            text = "\n".join(json.dumps(r, sort_keys=True) if isinstance(r, dict) else r for r in rows)
            DECISION_TRAINING.write_text(text + "\n", encoding="utf-8")
        return {"joined": joined, "outcome_keys": len(outcomes)}

    def _load_outcomes_by_symbol(self) -> Dict[tuple, dict]:
        if not TRADE_OUTCOMES.exists():
            return {}
        latest: Dict[tuple, dict] = {}
        for line in TRADE_OUTCOMES.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            symbol = str(row.get("symbol") or "").upper()
            side = str(row.get("side") or "").upper()
            if not symbol:
                continue
            latest[(symbol, side)] = row
        return latest


if __name__ == "__main__":
    print(json.dumps(HistorianAgent().run(), indent=2))
