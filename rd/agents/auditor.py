#!/usr/bin/env python3
"""AuditorAgent: capped ensemble review for promotion evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from rd.agents.base import DreamAgent  # noqa: E402
from rd.config import load_config  # noqa: E402
from rd import promotions  # noqa: E402


class AuditorAgent(DreamAgent):
    name = "auditor"

    def run(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cfg = load_config()
        cap = int((cfg.get("auditor") or {}).get("daily_cap", 10))
        use_ensemble = bool((cfg.get("auditor") or {}).get("use_ensemble", True))
        pending = promotions.list_promotions(status="pending", limit=cap)

        ensemble_reviews = self._run_ensemble_cap(cap, enabled=use_ensemble)
        reviews = []
        for promo in pending:
            review = self._review_promotion(promo, ensemble_reviews=ensemble_reviews)
            promotions.attach_auditor_notes(
                str(promo.get("id")),
                {
                    "verdict": review.get("verdict"),
                    "numeric_evidence": review.get("numeric_evidence"),
                    "ensemble_count": len(ensemble_reviews),
                },
            )
            reviews.append(review)

        return self.envelope({
            "ok": True,
            "reviewed": len(reviews),
            "ensemble_reviews": len(ensemble_reviews),
            "reviews": reviews,
            "proposals": [],
        })

    def _run_ensemble_cap(self, cap: int, *, enabled: bool) -> List[dict]:
        if not enabled or cap <= 0:
            return []
        try:
            from introspect import ensemble_reviewer as er
        except ImportError:
            return []
        try:
            return er.run_once(persist=True, max_examples=cap)[:cap]
        except Exception:
            return []

    def _review_promotion(self, promo: dict, *, ensemble_reviews: Optional[List[dict]] = None) -> dict:
        evidence = promo.get("evidence") or {}
        numeric_ok = bool(evidence.get("samples") or evidence.get("backtest") or evidence.get("keyword_stats"))
        verdict = "agree" if numeric_ok else "insufficient_evidence"
        ensemble_reviews = ensemble_reviews or []
        ensemble_agree = sum(
            1 for row in ensemble_reviews
            if (row.get("review") or {}).get("verdict") == "agree"
        )
        if ensemble_reviews and ensemble_agree == 0 and numeric_ok:
            verdict = "mixed"
        elif ensemble_reviews and ensemble_agree >= max(1, len(ensemble_reviews) // 2):
            verdict = "agree"
        return {
            "promotion_id": promo.get("id"),
            "verdict": verdict,
            "numeric_evidence": numeric_ok,
            "ensemble_agree": ensemble_agree,
            "ensemble_total": len(ensemble_reviews),
            "summary": promo.get("summary"),
        }


if __name__ == "__main__":
    print(json.dumps(AuditorAgent().run(), indent=2))
