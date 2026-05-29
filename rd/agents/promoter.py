#!/usr/bin/env python3
"""PromoterAgent: dedupe and attach auditor notes to pending promotions."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from rd.agents.base import DreamAgent  # noqa: E402
from rd import promotions  # noqa: E402


class PromoterAgent(DreamAgent):
    name = "promoter"

    def run(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        pending = promotions.list_promotions(status="pending", limit=100)
        return self.envelope({
            "ok": True,
            "pending_count": len(pending),
            "pending_ids": [p.get("id") for p in pending[:20]],
            "proposals": [],
        })


if __name__ == "__main__":
    print(json.dumps(PromoterAgent().run(), indent=2))
