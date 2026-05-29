#!/usr/bin/env python3
"""
cortex/strategy_registry.py — Self-Scoring Strategy Engine
-----------------------------------------------------------
Declarative strategies loaded from JSON. Real-time performance scoring.
Auto-selects best strategy per regime + symbol.
Integrates with backtest results.
"""
import json, time, math
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict, deque

ROOT = Path(__file__).resolve().parent.parent
STRAT_FILE = ROOT / "cortex" / "strategies.json"
PERF_DIR = ROOT / "cortex" / "performance"
PERF_DIR.mkdir(parents=True, exist_ok=True)

STRATEGY_ALIASES = {
    "SMA_CROSS": "MA_CROSS_SMA9_21",
    "SMA_CROSSOVER": "MA_CROSS_SMA9_21",
    "RSI_MR": "RSI_MEAN_REVERSION",
    "RSI_BOUNCE": "RSI_MEAN_REVERSION",
}


def normalize_strategy_id(strategy_id: Optional[str]) -> Optional[str]:
    """Return the canonical registry strategy id for known legacy aliases."""
    if not strategy_id:
        return None
    sid = str(strategy_id).strip().upper()
    return STRATEGY_ALIASES.get(sid, sid)


class StrategyRegistry:
    def __init__(self):
        self.strategies: Dict[str, dict] = {}
        self.trade_log: Dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self._load()

    def _load(self):
        if STRAT_FILE.exists():
            self.strategies = json.loads(STRAT_FILE.read_text())
        else:
            self.strategies = {}
        self._apply_runtime_overlays()

    def _apply_runtime_overlays(self):
        try:
            from cortex.strategy_performance import overlay_declarative_strategies
            from cortex.live_policy import load_policy

            overlay_declarative_strategies(self.strategies)
            policy = load_policy()
            for sid, patch in (policy.get("strategies") or {}).items():
                if sid in self.strategies:
                    self.strategies[sid].update(patch)
        except ImportError:
            pass

    def reload(self):
        """Reload declarative config and approved live policy overlays."""
        self._load()

    def save(self):
        STRAT_FILE.write_text(json.dumps(self.strategies, indent=2))

    def _selection_score(self, strategy: dict) -> float:
        weight = float(strategy.get("weight", 1.0) or 1.0)
        score = float(strategy.get("score", 0) or 0)
        if score <= 0:
            score = 1.0
        return weight * score

    def get_active(self, symbol: str = "", regime: str = "") -> List[dict]:
        """Return strategies matching current conditions, sorted by live weight * score."""
        self._apply_runtime_overlays()
        active = []
        for sid, s in self.strategies.items():
            if not s.get("active", True):
                continue
            if regime and s.get("regimes") and regime not in s.get("regimes", []):
                continue
            active.append({"id": sid, **s})
        active.sort(key=lambda x: self._selection_score(x), reverse=True)
        return active

    def is_registered(self, strategy_id: Optional[str]) -> bool:
        sid = normalize_strategy_id(strategy_id)
        return bool(sid and sid in self.strategies)

    def validate_strategy_id(self, strategy_id: Optional[str]) -> Tuple[bool, Optional[str], str]:
        sid = normalize_strategy_id(strategy_id)
        if not sid:
            return False, None, "missing_strategy_id"
        if sid not in self.strategies:
            return False, sid, "strategy_not_registered"
        return True, sid, "ok"

    def score_strategy(self, sid: str) -> float:
        """
        Compute composite score from live trades:
        win_rate_weight=0.3, profit_factor_weight=0.3, sharpe_weight=0.2, frequency_weight=0.2
        """
        trades = list(self.trade_log.get(sid, deque()))
        if not trades:
            return 1.0  # default starting score

        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        losses = sum(1 for t in trades if t.get("pnl", 0) < 0)
        total = len(trades)
        win_rate = wins / total if total > 0 else 0

        gross_profit = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0)
        gross_loss = abs(sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) <= 0))
        profit_factor = gross_profit / max(gross_loss, 1e-10)

        returns = [t.get("pnl", 0) for t in trades]
        avg = sum(returns) / len(returns)
        var = sum((r - avg) ** 2 for r in returns) / len(returns)
        sharpe = avg / math.sqrt(var + 1e-10) if var > 0 else 0

        # Frequency: normalize trades per day
        days = max(1, (trades[-1]["ts"] - trades[0]["ts"]) / 86400)
        frequency = min(1.0, total / (days * 5))  # target 5 trades/day

        raw = (win_rate * 0.3 + profit_factor * 0.3 + sharpe * 0.2 + frequency * 0.2)
        # Normalize to 0-2 scale
        score = min(2.0, max(0.1, raw))
        return round(score, 3)

    def record_trade(self, sid: str, pnl: float, ts: float = None):
        """Record a closed trade for strategy scoring."""
        if ts is None:
            ts = time.time()
        self.trade_log[sid].append({"pnl": round(pnl, 4), "ts": ts})
        # Update in-memory score
        if sid in self.strategies:
            self.strategies[sid]["wins"] = sum(1 for t in self.trade_log[sid] if t["pnl"] > 0)
            self.strategies[sid]["losses"] = sum(1 for t in self.trade_log[sid] if t["pnl"] < 0)
            self.strategies[sid]["score"] = self.score_strategy(sid)

    def select_strategy(self, symbol: str, regime: str, patterns: List[dict]) -> Optional[dict]:
        """
        Multi-factor strategy selection:
        1. Regime match
        2. Pattern alignment
        3. Score
        """
        candidates = self.get_active(symbol, regime)
        if not candidates:
            return None

        # Check pattern-strategy alignment
        direction = None
        bullish_patterns = [p for p in patterns if p.get("direction") == "bullish"]
        bearish_patterns = [p for p in patterns if p.get("direction") == "bearish"]
        if len(bullish_patterns) > len(bearish_patterns):
            direction = "long"
        elif len(bearish_patterns) > len(bullish_patterns):
            direction = "short"

        for c in candidates:
            c["direction_match"] = (
                (direction == "long" and c.get("position_type") in ("long", "bi_directional", "adaptive")) or
                (direction == "short" and c.get("position_type") in ("short", "bi_directional", "adaptive")) or
                direction is None
            )

        # Score boost for direction match
        candidates.sort(key=lambda x: (
            x.get("direction_match", False),
            self._selection_score(x),
        ), reverse=True)

        return candidates[0] if candidates else None

    def get_regime_for_strategy(self, sid: str) -> List[str]:
        s = self.strategies.get(sid, {})
        return s.get("regimes", ["trending", "ranging"])  # default: all regimes

    def export_metrics(self) -> dict:
        """Export all strategy metrics for dashboard / review."""
        return {
            sid: {
                "score": self.score_strategy(sid),
                "trades": len(self.trade_log.get(sid, [])),
                "wins": sum(1 for t in self.trade_log.get(sid, []) if t["pnl"] > 0),
                "losses": sum(1 for t in self.trade_log.get(sid, []) if t["pnl"] < 0),
                **{k: v for k, v in s.items() if k != "params"},
            }
            for sid, s in self.strategies.items()
        }


# Singleton
REGISTRY = StrategyRegistry()


def record_closed_trade(sid: str, pnl: float):
    """Called from muscle when a position closes."""
    REGISTRY.record_trade(sid, pnl)
    REGISTRY.save()


def select(symbol: str, regime: str, patterns: List[dict] = None) -> Optional[dict]:
    return REGISTRY.select_strategy(symbol, regime, patterns or [])


def is_registered(strategy_id: Optional[str]) -> bool:
    return REGISTRY.is_registered(strategy_id)


def validate_strategy_id(strategy_id: Optional[str]) -> Tuple[bool, Optional[str], str]:
    return REGISTRY.validate_strategy_id(strategy_id)


if __name__ == "__main__":
    r = StrategyRegistry()
    r.record_trade("MA_CROSS_SMA9_21", 50.0, time.time() - 86400)
    r.record_trade("MA_CROSS_SMA9_21", -20.0, time.time() - 3600)
    r.record_trade("MA_CROSS_SMA9_21", 30.0, time.time() - 600)
    print(r.score_strategy("MA_CROSS_SMA9_21"))
    print(r.export_metrics())
