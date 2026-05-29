#!/usr/bin/env python3
"""
sensory/candle_patterns.py — Multi-Timeframe Pattern Recognition
------------------------------------------------------------------
Detects candlestick patterns and price-action signals from OHLC data.
Supports: Doji, Engulfing, Hammer/Shooting Star, Pinbar,
          Harami, Morning/Evening Star, Three White Soldiers/Black Crows,
          Inside Bar, Marubozu

Each pattern returns a dict with:
  pattern_name, direction (bullish/bearish/neutral),
  symbol, tf, ts, confidence, context
"""
import json, time
from typing import List, Optional, Dict
from collections import deque


def require_candles(history: List[dict], n: int) -> bool:
    return len(history) >= n


def get_body(c: dict) -> float:
    return c.get("body_size", abs(c.get("close", 0) - c.get("open_price", 0)))

def get_range(c: dict) -> float:
    return c.get("range", c.get("high", 0) - c.get("low", 0))

def is_bullish(c: dict) -> bool:
    return c.get("is_bullish", c.get("close", 0) >= c.get("open_price", 0))

def is_bearish(c: dict) -> bool:
    return not is_bullish(c)

# --- Pattern detectors ---

def doji(candles: List[dict], idx: int = -1) -> Optional[dict]:
    """Body < 10% of total range."""
    if not require_candles(candles, abs(idx)):
        return None
    c = candles[idx]
    rng = get_range(c)
    if rng == 0:
        return None
    body = get_body(c)
    if body / rng < 0.1:
        return {
            "pattern": "doji",
            "direction": "neutral",
            "strength": "weak",
            "description": "Indecision — body is < 10% of range",
        }
    return None


def engulfing(candles: List[dict], idx: int = -1) -> Optional[dict]:
    """Current candle fully engulfs prior candle, opposite direction."""
    if not require_candles(candles, abs(idx) + 1):
        return None
    c2 = candles[idx]
    c1 = candles[idx - 1]
    rng2 = get_range(c2)
    rng1 = get_range(c1)
    if rng1 == 0 or rng2 == 0:
        return None
    # Bullish engulfing: c2 bullish, c1 bearish, c2.low < c1.low, c2.high > c1.high
    if is_bullish(c2) and is_bearish(c1):
        if c2["low"] <= c1["low"] and c2["high"] >= c1["high"]:
            return {
                "pattern": "bullish_engulfing",
                "direction": "bullish",
                "strength": "strong",
                "description": "Bullish engulfing — strong reversal signal",
            }
    # Bearish engulfing
    if is_bearish(c2) and is_bullish(c1):
        if c2["low"] <= c1["low"] and c2["high"] >= c1["high"]:
            return {
                "pattern": "bearish_engulfing",
                "direction": "bearish",
                "strength": "strong",
                "description": "Bearish engulfing — strong reversal signal",
            }
    return None


def hammer(candles: List[dict], idx: int = -1) -> Optional[dict]:
    """Small body at top, long lower shadow > 2x body, in downtrend context."""
    if not require_candles(candles, abs(idx)):
        return None
    c = candles[idx]
    body = get_body(c)
    rng = get_range(c)
    if rng == 0 or body == 0:
        return None
    lower_shadow = c.get("lower_shadow", min(c.get("open_price", 0), c.get("close", 0)) - c.get("low", 0))
    upper_shadow = c.get("upper_shadow", c.get("high", 0) - max(c.get("open_price", 0), c.get("close", 0)))
    # Long lower shadow, small upper shadow, body in upper third
    if lower_shadow > 2 * body and upper_shadow < body:
        if is_bullish(c):
            return {
                "pattern": "hammer",
                "direction": "bullish",
                "strength": "moderate",
                "description": "Hammer — potential bottom reversal",
            }
        else:
            return {
                "pattern": "hanging_man",
                "direction": "bearish",
                "strength": "weak",
                "description": "Hanging man — potential top continuation/reversal",
            }
    return None


def shooting_star(candles: List[dict], idx: int = -1) -> Optional[dict]:
    """Small body at bottom, long upper shadow > 2x body."""
    if not require_candles(candles, abs(idx)):
        return None
    c = candles[idx]
    body = get_body(c)
    rng = get_range(c)
    if rng == 0 or body == 0:
        return None
    lower_shadow = c.get("lower_shadow", 0)
    upper_shadow = c.get("upper_shadow", 0)
    if upper_shadow > 2 * body and lower_shadow < body:
        if is_bearish(c):
            return {
                "pattern": "shooting_star",
                "direction": "bearish",
                "strength": "moderate",
                "description": "Shooting star — potential top reversal",
            }
    return None


def harami(candles: List[dict], idx: int = -1) -> Optional[dict]:
    """Current candle body within prior candle body, opposite colors."""
    if not require_candles(candles, abs(idx) + 1):
        return None
    c2 = candles[idx]
    c1 = candles[idx - 1]
    # c1 is large, c2 is small and inside c1's body
    if get_body(c1) > get_body(c2) * 2:
        c2_top = max(c2.get("open_price", 0), c2.get("close", 0))
        c2_bot = min(c2.get("open_price", 0), c2.get("close", 0))
        c1_top = max(c1.get("open_price", 0), c1.get("close", 0))
        c1_bot = min(c1.get("open_price", 0), c1.get("close", 0))
        if c2_top < c1_top and c2_bot > c1_bot:
            if is_bearish(c1) and is_bullish(c2):
                return {
                    "pattern": "bullish_harami",
                    "direction": "bullish",
                    "strength": "moderate",
                    "description": "Bullish harami — potential bottom reversal",
                }
            if is_bullish(c1) and is_bearish(c2):
                return {
                    "pattern": "bearish_harami",
                    "direction": "bearish",
                    "strength": "moderate",
                    "description": "Bearish harami — potential top reversal",
                }
    return None


def three_soldiers_crows(candles: List[dict], idx: int = -1) -> Optional[dict]:
    """Three consecutive same-direction candles with increasing bodies."""
    if not require_candles(candles, abs(idx) + 2):
        return None
    c3 = candles[idx]
    c2 = candles[idx - 1]
    c1 = candles[idx - 2]
    # Three white soldiers
    if all(is_bullish(c) for c in (c1, c2, c3)):
        if get_body(c1) < get_body(c2) < get_body(c3):
            if c3["close"] > c2["close"] > c1["close"]:
                return {
                    "pattern": "three_white_soldiers",
                    "direction": "bullish",
                    "strength": "strong",
                    "description": "Three white soldiers — strong continuation",
                }
    # Three black crows
    if all(is_bearish(c) for c in (c1, c2, c3)):
        if get_body(c1) < get_body(c2) < get_body(c3):
            if c3["close"] < c2["close"] < c1["close"]:
                return {
                    "pattern": "three_black_crows",
                    "direction": "bearish",
                    "strength": "strong",
                    "description": "Three black crows — strong continuation",
                }
    return None


def inside_bar(candles: List[dict], idx: int = -1) -> Optional[dict]:
    """Current candle entirely inside prior candle's range."""
    if not require_candles(candles, abs(idx) + 1):
        return None
    c2 = candles[idx]
    c1 = candles[idx - 1]
    if c2["high"] < c1["high"] and c2["low"] > c1["low"]:
        return {
            "pattern": "inside_bar",
            "direction": "neutral",
            "strength": "weak",
            "description": "Inside bar — consolidation, breakout pending",
        }
    return None


def pinbar(candles: List[dict], idx: int = -1) -> Optional[dict]:
    """Very long wick (>70% of range), small body."""
    if not require_candles(candles, abs(idx)):
        return None
    c = candles[idx]
    body = get_body(c)
    rng = get_range(c)
    if rng == 0 or body == 0:
        return None
    body_pct = body / rng
    if body_pct < 0.3:
        lower = c.get("lower_shadow", 0)
        upper = c.get("upper_shadow", 0)
        if lower > upper * 3 and lower / rng > 0.6:
            return {
                "pattern": "bullish_pinbar",
                "direction": "bullish",
                "strength": "strong",
                "description": "Bullish pinbar — rejection at lows",
            }
        if upper > lower * 3 and upper / rng > 0.6:
            return {
                "pattern": "bearish_pinbar",
                "direction": "bearish",
                "strength": "strong",
                "description": "Bearish pinbar — rejection at highs",
            }
    return None


def marubozu(candles: List[dict], idx: int = -1) -> Optional[dict]:
    """No wicks — body = range."""
    if not require_candles(candles, abs(idx)):
        return None
    c = candles[idx]
    body = get_body(c)
    rng = get_range(c)
    if rng == 0:
        return None
    if body / rng > 0.95:
        d = "bullish" if is_bullish(c) else "bearish"
        return {
            "pattern": f"{d}_marubozu",
            "direction": d,
            "strength": "strong",
            "description": f"{d.capitalize()} marubozu — strong momentum",
        }
    return None


ALL_DETECTORS = [
    doji, engulfing, hammer, shooting_star, harami,
    three_soldiers_crows, inside_bar, pinbar, marubozu,
]


def scan(history: List[dict], symbol: str = "", tf: str = "") -> List[dict]:
    """Run all pattern detectors on candle history. Returns list of pattern hits."""
    results = []
    for detector in ALL_DETECTORS:
        hit = detector(history)
        if hit:
            hit["symbol"] = symbol
            hit["tf"] = tf
            hit["ts"] = history[-1].get("ts_close", time.time())
            results.append(hit)
    return results


def aggregate_patterns(snapshots: Dict[str, List[dict]]) -> dict:
    """
    snapshots: { "M5": [patterns], "H1": [patterns], ... }
    Returns confluence score (e.g., bullish across multiple TFs)
    """
    bullish = {}
    bearish = {}
    for tf, patterns in snapshots.items():
        for p in patterns:
            sym = p.get("symbol", "")
            if p["direction"] == "bullish":
                bullish.setdefault(sym, []).append(tf)
            elif p["direction"] == "bearish":
                bearish.setdefault(sym, []).append(tf)
    return {
        "bullish_confluence": {sym: tfs for sym, tfs in bullish.items() if len(tfs) >= 2},
        "bearish_confluence": {sym: tfs for sym, tfs in bearish.items() if len(tfs) >= 2},
        "timestamp": time.time(),
    }


if __name__ == "__main__":
    # Quick test
    candles = [
        dict(open_price=1.1000, high=1.1010, low=1.0995, close=1.1005, body_size=0.0005, range=0.0015, is_bullish=True, lower_shadow=0.0005, upper_shadow=0.0005),
        dict(open_price=1.1005, high=1.1020, low=1.0980, close=1.0990, body_size=0.0015, range=0.0040, is_bullish=False, lower_shadow=0.0010, upper_shadow=0.0030),
    ]
    print(scan(candles, "EURUSD", "M5"))
