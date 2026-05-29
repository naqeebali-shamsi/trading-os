#!/usr/bin/env python3
"""TrainerAgent: offline confidence calibration and promotion proposals."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from rd.agents.base import DreamAgent  # noqa: E402
from rd.config import load_config  # noqa: E402
from rd import promotions  # noqa: E402

TRAINING_FILE = ROOT / "memory" / "training" / "decision_training.jsonl"
MODEL_DIR = ROOT / "memory" / "training" / "models"
MODEL_FILE = MODEL_DIR / "confidence_v1.json"


def _row_label(row: dict):
    if row.get("label") not in (None, ""):
        return str(row.get("label"))
    target = row.get("target") or {}
    label = target.get("label")
    return str(label) if label not in (None, "") else None


def _row_confidence(row: dict):
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


class TrainerAgent(DreamAgent):
    name = "trainer"

    def run(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cfg = load_config()
        rows = self._load_rows()
        min_rows = int((cfg.get("trainer") or {}).get("min_rows", 50))
        if len(rows) < min_rows:
            return self.envelope({
                "ok": True,
                "skipped": True,
                "reason": f"insufficient_rows:{len(rows)}<{min_rows}",
                "proposals": [],
            })

        stats = self._train_stats(rows)
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        MODEL_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")

        proposals: List[Dict[str, Any]] = []
        proposal = self._maybe_propose_calibration(stats, cfg)
        if proposal:
            proposals.append(proposal)

        return self.envelope({"ok": True, "model": stats, "row_count": len(rows), "proposals": proposals})

    def _load_rows(self) -> List[dict]:
        if not TRAINING_FILE.exists():
            return []
        out = []
        for line in TRAINING_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _signed_label(self, row: dict) -> Optional[int]:
        label = _row_label(row)
        if label in ("pending_outcome", None):
            return None
        if label in (1, -1, 0, "1", "0", "-1", True, False):
            if label in (True, False):
                return 1 if label else 0
            return int(label)
        outcome = row.get("outcome") or (row.get("target") or {}).get("outcome") or {}
        ret = outcome.get("side_signed_return")
        if ret is None and outcome.get("pnl") is not None:
            try:
                return 1 if float(outcome.get("pnl")) > 0 else 0
            except (TypeError, ValueError):
                return None
        if ret is None:
            return None
        try:
            return 1 if float(ret) > 0 else 0
        except (TypeError, ValueError):
            return None

    def _train_stats(self, rows: List[dict]) -> Dict[str, Any]:
        labeled = []
        for row in rows:
            y = self._signed_label(row)
            conf = _row_confidence(row)
            if y is None or conf is None:
                continue
            try:
                labeled.append((float(conf), int(y)))
            except (TypeError, ValueError):
                continue

        if not labeled:
            return {"ok": False, "reason": "no_labeled_confidence_rows"}

        wins = [c for c, y in labeled if y == 1]
        losses = [c for c, y in labeled if y == 0]
        win_rate = len(wins) / len(labeled)
        avg_win_conf = sum(wins) / len(wins) if wins else 0.0
        avg_loss_conf = sum(losses) / len(losses) if losses else 0.0

        # Simple calibration: if losses have higher confidence than wins, apply negative offset
        offset = 0.0
        if avg_loss_conf > avg_win_conf:
            offset = round(min(0.08, (avg_loss_conf - avg_win_conf) * 0.5), 4)

        high_conf = [y for c, y in labeled if c >= 0.70]
        high_conf_hit = sum(high_conf) / len(high_conf) if high_conf else None

        suggested_min = 0.70
        if high_conf_hit is not None and high_conf_hit < 0.45:
            suggested_min = round(min(0.85, 0.70 + (0.45 - high_conf_hit) * 0.2), 2)

        return {
            "ok": True,
            "samples": len(labeled),
            "win_rate": round(win_rate, 4),
            "avg_win_confidence": round(avg_win_conf, 4),
            "avg_loss_confidence": round(avg_loss_conf, 4),
            "confidence_offset": offset,
            "confidence_scale": 1.0,
            "per_pattern_bonus": 0.0,
            "high_confidence_hit_rate": round(high_conf_hit, 4) if high_conf_hit is not None else None,
            "suggested_signal_min_confidence": suggested_min,
        }

    def _maybe_propose_calibration(self, stats: Dict[str, Any], cfg: dict) -> Optional[Dict[str, Any]]:
        if not stats.get("ok"):
            return None
        delta = float((cfg.get("trainer") or {}).get("propose_min_confidence_delta", 0.02))
        suggested = float(stats.get("suggested_signal_min_confidence") or 0.70)
        offset = float(stats.get("confidence_offset") or 0.0)
        if offset == 0 and abs(suggested - 0.70) < delta:
            return None

        patch = {
            "mapping": {
                "confidence_offset": offset,
                "confidence_scale": 1.0,
                "per_pattern_bonus": 0.0,
            },
            "signal_min_confidence": suggested,
        }
        summary = (
            f"Calibrate confidence (offset {offset:+.3f}) and review min confidence {suggested:.2f} "
            f"from {stats.get('samples')} labeled decisions"
        )
        return promotions.propose(
            ptype="confidence_calibration",
            summary=summary,
            patch=patch,
            evidence=stats,
            risk="medium",
            agent=self.name,
        )


if __name__ == "__main__":
    print(json.dumps(TrainerAgent().run(), indent=2))
