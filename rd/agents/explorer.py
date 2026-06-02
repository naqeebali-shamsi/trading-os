#!/usr/bin/env python3
"""ExplorerAgent: bounded strategy search with anti-overfit gates."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from bus import publish  # noqa: E402
from rd.agents.base import DreamAgent  # noqa: E402
from rd import promotions  # noqa: E402
from research.strategy_search.config import load_config  # noqa: E402
from research.strategy_search.engine import run_strategy_search  # noqa: E402


class ExplorerAgent(DreamAgent):
    name = "explorer"

    def run(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        task = task or {}
        cfg = load_config()
        if not cfg.get("enabled", True):
            return self.envelope({"ok": True, "skipped": True, "reason": "strategy_search_disabled", "proposals": []})

        defaults = cfg.get("defaults") or {}
        symbol = str(task.get("symbol") or defaults.get("symbol") or "EURUSD")
        timeframe = str(task.get("timeframe") or defaults.get("timeframe") or "M15")

        report = run_strategy_search(symbol=symbol, timeframe=timeframe, config=cfg)
        publish(
            "rd.strategy_search.complete",
            {
                "ok": report.get("ok"),
                "symbol": symbol,
                "timeframe": timeframe,
                "trials_run": report.get("trials_run"),
                "survivor_count": report.get("survivor_count"),
                "report_path": report.get("report_path"),
            },
        )
        if not report.get("ok"):
            return self.envelope({"ok": False, "error": report.get("error"), "proposals": []})

        proposals: List[Dict[str, Any]] = []
        best = report.get("best_survivor")
        if best:
            spec = best["spec"]
            sid = spec["strategy_id"]
            val_sharpe = float((best.get("validation") or {}).get("sharpe_proxy") or 0)
            test_sharpe = float((best.get("test") or {}).get("sharpe_proxy") or 0)
            summary = (
                f"Strategy search survivor {sid} — val Sharpe {val_sharpe:.2f}, "
                f"test Sharpe {test_sharpe:.2f} ({report.get('trials_run')} trials, purged splits)"
            )
            proposals.append(
                promotions.propose(
                    ptype="strategy_active",
                    summary=summary,
                    patch={"strategy_id": sid, "active": True, "weight": 1.0, "source": "strategy_search"},
                    evidence={
                        "strategy_search": {
                            "report_path": report.get("report_path"),
                            "protocol": report.get("protocol"),
                            "best": best,
                        }
                    },
                    risk="high",
                    agent=self.name,
                )
            )

        return self.envelope(
            {
                "ok": True,
                "symbol": symbol,
                "timeframe": timeframe,
                "trials_run": report.get("trials_run"),
                "survivor_count": report.get("survivor_count"),
                "validation_passed_count": report.get("validation_passed_count"),
                "report_path": report.get("report_path"),
                "best_survivor_id": (best or {}).get("spec", {}).get("strategy_id"),
                "proposals": proposals,
            }
        )


if __name__ == "__main__":
    print(json.dumps(ExplorerAgent().run(), indent=2))
