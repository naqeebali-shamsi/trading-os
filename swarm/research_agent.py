#!/usr/bin/env python3
"""
swarm/research_agent.py — Strategy Research Subagent
-----------------------------------------------------
Reads world inputs (economic calendar, sentiment, news, regime)
and proposes new strategy hypotheses.
Outputs a strategy hypothesis record to a results file.
Called by swarm/main.py with SWARM_TASK env variable.
"""
import json, os, time, random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "swarm" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

TASK = json.loads(os.environ.get("SWARM_TASK", "{}"))
OUT = os.environ.get("SWARM_OUT", str(RESULTS / f"research_{int(time.time())}.json"))

def propose_strategies(context):
    """Based on market context, propose strategy hypotheses."""
    hypotheses = []
    regime = context.get("regime", "ranging")
    
    if regime == "trending":
        hypotheses.append({
            "id": "EMA_TREND_FOLLOW_8_21",
            "type": "trend_following",
            "description": "Enter on EMA8 cross above EMA21, exit on cross below. ATR-based SL.",
            "expected_sharpe": 1.2,
            "max_dd_expect": 8,
            "timeframe": "H1",
            "indicators": ["EMA8", "EMA21", "ATR14"],
            "entry_logic": "EMA8 > EMA21 and close > EMA8",
            "exit_logic": "EMA8 < EMA21 or ATR trailing stop",
            "risk_per_trade": 1.0,
            "position_type": "single_direction",
        })
        hypotheses.append({
            "id": "BREAKOUT_PULLBACK_20",
            "type": "breakout",
            "description": "Trade pullback to 20-period Donchian mid after breakout.",
            "expected_sharpe": 1.4,
            "max_dd_expect": 10,
            "timeframe": "H4",
            "indicators": ["Donchian20", "RSI"],
            "entry_logic": "Price breaks Donchian upper, waits for retest",
            "exit_logic": "Stop below swing low or RSI > 70",
            "risk_per_trade": 1.5,
            "position_type": "single_direction",
        })
    elif regime == "ranging":
        hypotheses.append({
            "id": "RSI_MEAN_REVERSION_30_70",
            "type": "mean_reversion",
            "description": "Buy when RSI < 30 at support, sell when RSI > 70 at resistance.",
            "expected_sharpe": 0.9,
            "max_dd_expect": 5,
            "timeframe": "H1",
            "indicators": ["RSI14", "BB20", "Volume"],
            "entry_logic": "RSI < 30 and price at lower BB",
            "exit_logic": "RSI > 50 or mid BB",
            "risk_per_trade": 0.8,
            "position_type": "bi_directional",
        })
        hypotheses.append({
            "id": "BOLLINGER_SQUEEZE",
            "type": "volatility_expansion",
            "description": "Enter when BB width squeezes below 20th percentile, then expands.",
            "expected_sharpe": 1.1,
            "max_dd_expect": 6,
            "timeframe": "H1",
            "indicators": ["BB20", "ATR14", "Volume"],
            "entry_logic": "BB width < percentile(20) for 5 bars, then expansion",
            "exit_logic": "Opposite BB touch or ATR stop",
            "risk_per_trade": 1.0,
            "position_type": "single_direction",
        })
    else:
        hypotheses.append({
            "id": "ADAPTIVE_DUAL",
            "type": "regime_switching",
            "description": "Switch between trend and mean-reversion based on ADX.",
            "expected_sharpe": 1.0,
            "max_dd_expect": 7,
            "timeframe": "H1",
            "indicators": ["ADX14", "EMA20", "RSI14"],
            "entry_logic": "ADX > 25: trend mode. ADX < 20: range mode.",
            "exit_logic": "Regime change or fixed stop",
            "risk_per_trade": 1.0,
            "position_type": "adaptive",
        })
    
    # Always include a safety baseline
    hypotheses.append({
        "id": "SAFETY_BASELINE_HOLD",
        "type": "hold",
        "description": "Do nothing. Baseline for comparison.",
        "expected_sharpe": 0,
        "max_dd_expect": 0,
        "timeframe": "N/A",
        "indicators": [],
        "entry_logic": "None",
        "exit_logic": "None",
        "risk_per_trade": 0,
        "position_type": "none",
    })
    
    return hypotheses

def run():
    context = TASK.get("context", {})
    hypotheses = propose_strategies(context)
    result = {
        "agent": "research",
        "ts": time.time(),
        "input_context": context,
        "hypotheses_count": len(hypotheses),
        "hypotheses": hypotheses,
        "recommendation": random.choice([h["id"] for h in hypotheses if h["type"] != "hold"]) if [h for h in hypotheses if h["type"] != "hold"] else "SAFETY_BASELINE_HOLD",
    }
    with open(OUT, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Research complete. Wrote {OUT}")

if __name__ == "__main__":
    run()
