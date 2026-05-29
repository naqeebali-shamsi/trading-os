#!/usr/bin/env python3
"""StrategistAgent: propose strategy hypotheses from performance context."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from rd.agents.base import DreamAgent  # noqa: E402
from cortex.strategy_performance import load_live_metrics  # noqa: E402


class StrategistAgent(DreamAgent):
    name = "strategist"

    def run(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        task = task or {}
        reason = str(task.get("reason") or "scheduled")
        context = task.get("context") or {}
        regime = str(context.get("regime") or "ranging")

        hypotheses = self._propose(regime, context)
        return self.envelope({
            "ok": True,
            "reason": reason,
            "regime": regime,
            "hypotheses": hypotheses,
            "proposals": [],
            "backtest_tasks": [
                {"type": "backtest_request", "strategy_id": h["id"], "hypothesis": h}
                for h in hypotheses[:2]
            ],
        })

    def _propose(self, regime: str, context: dict) -> List[Dict[str, Any]]:
        live = load_live_metrics()
        weak = [sid for sid, m in live.items() if float(m.get("sharpe") or 0) < 0]
        if regime == "trending":
            base = [
                {"id": "EMA_TREND_FOLLOW_8_21", "type": "trend_following", "timeframe": "H1"},
                {"id": "BREAKOUT_PULLBACK_20", "type": "breakout", "timeframe": "H4"},
            ]
        else:
            base = [
                {"id": "RSI_MEAN_REVERSION_30_70", "type": "mean_reversion", "timeframe": "H1"},
                {"id": "BOLLINGER_SQUEEZE", "type": "volatility_expansion", "timeframe": "H1"},
            ]
        for h in base:
            h["weak_incumbents"] = weak[:5]
            h["context"] = {"regime": regime, "trigger": context.get("reason")}
        return base


if __name__ == "__main__":
    print(json.dumps(StrategistAgent().run({"reason": "test"}), indent=2))
