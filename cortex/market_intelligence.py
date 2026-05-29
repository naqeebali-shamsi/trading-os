#!/usr/bin/env python3
"""
cortex/market_intelligence.py — Rich LLM Context Builder
--------------------------------------------------------
Builds full market context for LLM decisions from:
- Multi-TF OHLC snapshots
- Pattern detection results
- Active positions + unrealized PnL
- Strategy performance metrics
- Economic calendar events
- Health check status
"""
import json, time, math
from typing import Dict, List, Optional
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sensory"))
sys.path.insert(0, str(ROOT / "cortex"))

from ohlc_engine import ENGINE as OHLC
from candle_patterns import scan as pattern_scan
import strategy_registry as strat_reg


def get_price_context(symbol: str) -> dict:
    """Get recent candles across TFs for one symbol."""
    snap = OHLC.get_multi_tf_snapshot(symbol, tfs=["M5", "M15", "H1", "H4"])
    results = {}
    for tf, data in snap.items():
        hist = data["history"]
        if not hist:
            continue
        cur = data["current"]
        patterns = pattern_scan(hist, symbol, tf) if len(hist) >= 5 else []
        results[tf] = {
            "last": hist[-1] if hist else None,
            "patterns": [p["pattern"] + "(" + p["direction"][0] + ")" for p in patterns[:2]],
            "trend": _describe_trend(hist),
        }
    return results


def _describe_trend(candles: List[dict]) -> str:
    """Simple trend description from last N candles."""
    if len(candles) < 5:
        return "indeterminate"
    lows = [c["low"] for c in candles[-10:]]
    highs = [c["high"] for c in candles[-10:]]
    if lows[-1] > lows[0] and highs[-1] > highs[0]:
        return "uptrend"
    if lows[-1] < lows[0] and highs[-1] < highs[0]:
        return "downtrend"
    return "range/consolidation"


def build_market_snapshot(symbols: List[str] = None) -> dict:
    symbols = symbols or ["EURUSD", "XAUUSD", "GBPUSD", "USDJPY"]
    snapshot = {}
    for sym in symbols:
        ctx = get_price_context(sym)
        if ctx:
            snapshot[sym] = ctx
    return snapshot


def build_llm_prompt(
    symbols: List[str] = None,
    open_positions: List[dict] = None,
    recent_signals: List[dict] = None,
    health_checks: dict = None,
    news_events: List[dict] = None,
) -> str:
    """Build a rich structured prompt for the LLM trading advisor."""
    market = build_market_snapshot(symbols)
    strats = strat_reg.REGISTRY.export_metrics()

    now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    prompt = f"""You are the intelligence engine of an autonomous trading system.
Current time: {now_utc}
Mode: LIVE (demo account, real market execution)

## Market Snapshot
{json.dumps(market, indent=2, default=str)}

## Active Strategies & Performance
{json.dumps(strats, indent=2, default=str)}

## Open Positions
{json.dumps(open_positions or [], indent=2, default=str)}

## Recent Trade Signals (last hour)
{json.dumps(recent_signals or [], indent=2, default=str)}

## Health Status
{json.dumps(health_checks or {}, indent=2, default=str)}

## Upcoming/Recent News Events
{json.dumps(news_events or [], indent=2, default=str)}

## YOUR TASK
Based on the market snapshot, patterns, strategy performance, and any news/events,
make a trading decision for each symbol. Consider:
1. Pattern confluence across timeframes
2. Strategy historical performance
3. Risk (recent volatility, drawdown)
4. News impact timing
5. Current open positions (don't overexpose)

Respond ONLY with a JSON object in this exact format:
{{
  "decisions": [
    {{
      "symbol": "EURUSD",
      "action": "BUY|SELL|HOLD|CLOSE",
      "qty": 0.01,
      "sl": 1.1750,
      "tp": 1.1830,
      "reasoning": "Bullish engulfing on M15 + H1 uptrend + no conflicts",
      "confidence": 0.82,
      "strategy_id": "MA_CROSS_SMA9_21",
      "urgency": "immediate|watch"
    }}
  ],
  "market_outlook": "brief 1-sentence summary",
  "warnings": ["any risk factors"]
}}
"""
    return prompt


if __name__ == "__main__":
    # Test with dummy data
    print(build_llm_prompt(
        symbols=["EURUSD"],
        open_positions=[{"symbol": "EURUSD", "side": "BUY", "qty": 0.01}],
    )[:1500])
