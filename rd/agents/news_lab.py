#!/usr/bin/env python3
"""NewsLabAgent: historical news impact sampling and lexicon proposals."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from rd.agents.base import DreamAgent  # noqa: E402
from rd.config import load_config  # noqa: E402
from rd import promotions  # noqa: E402

NEWS_CACHE = ROOT / "intel" / "news_cache.jsonl"


class NewsLabAgent(DreamAgent):
    name = "news_lab"

    def run(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cfg = load_config()
        batch = int((cfg.get("news_lab") or {}).get("batch_size", 25))
        rows = self._load_recent(batch)
        keyword_hits = self._keyword_stats(rows)
        proposals = self._maybe_propose_lexicon(keyword_hits)
        return self.envelope({
            "ok": True,
            "samples": len(rows),
            "keyword_stats": keyword_hits,
            "proposals": proposals,
        })

    def _load_recent(self, limit: int) -> List[dict]:
        if not NEWS_CACHE.exists():
            return []
        rows = []
        for line in NEWS_CACHE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        rows.sort(key=lambda r: float(r.get("ts") or 0), reverse=True)
        return rows[:limit]

    def _keyword_stats(self, rows: List[dict]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for row in rows:
            decision = row.get("decision") or {}
            for kw in decision.get("top_keywords") or []:
                counts[str(kw)] = counts.get(str(kw), 0) + 1
            assessment = str(decision.get("assessment") or "")
            if assessment:
                counts[f"assessment:{assessment}"] = counts.get(f"assessment:{assessment}", 0) + 1
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10])

    def _maybe_propose_lexicon(self, stats: Dict[str, int]) -> List[Dict[str, Any]]:
        if not stats:
            return []
        top_kw = next((k for k in stats if not k.startswith("assessment:")), None)
        if not top_kw or stats[top_kw] < 3:
            return []
        proposal = promotions.propose(
            ptype="macro_lexicon_weight",
            summary=f"Increase macro lexicon weight for '{top_kw}' after repeated weekend/news replay hits",
            patch={"keyword": top_kw, "weight": 1.1},
            evidence={"keyword_stats": stats, "sample_ts": time.time()},
            risk="low",
            agent=self.name,
        )
        return [proposal]


if __name__ == "__main__":
    print(json.dumps(NewsLabAgent().run(), indent=2))
