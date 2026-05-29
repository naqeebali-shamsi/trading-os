#!/usr/bin/env python3
"""Classify market regime: trend/range/breakout from tick buffer."""
import json, time, sys
from pathlib import Path
from collections import deque

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/"nervous"))
from bus import publish, subscribe

WINDOW = 60  # lookback ticks

def classify_trend(prices: list) -> str:
    if len(prices) < 20: 
        return "insufficient_data"
    
    n = len(prices)
    # Linear regression slope and R-squared
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = sum(prices) / n
    
    ss_xy = sum((x[i] - x_mean) * (prices[i] - y_mean) for i in range(n))
    ss_xx = sum((i - x_mean) ** 2 for i in x)
    
    slope = ss_xy / ss_xx if ss_xx != 0 else 0
    
    # R-squared
    ss_tot = sum((p - y_mean) ** 2 for p in prices)
    ss_res = sum((prices[i] - (y_mean + slope * (x[i] - x_mean))) ** 2 for i in range(n))
    r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
    
    # Volatility
    atr = max(prices[-20:]) - min(prices[-20:])
    avg = sum(prices[-20:]) / 20
    vol = atr / avg if avg > 0 else 0
    
    # Strong trend: tight R2 > 0.7 and noticeable slope
    slope_pct = abs(slope) / avg if avg > 0 else 0
    if r_squared > 0.65 and slope_pct > 0.0001:
        return "trending"
    
    # Very flat
    if vol < 0.0003:
        return "flat"
    
    return "ranging"

def run():
    buf = deque(maxlen=WINDOW)
    last_seq = 0
    while True:
        evs = subscribe("market.tick", since_seq=last_seq)
        for ev in evs:
            seq = ev.get("seq",0)
            if seq > last_seq: last_seq = seq
            p = ev.get("payload",{})
            bid = p.get("bid")
            if bid: buf.append(bid)
        if len(buf) >= 20:
            regime = classify_trend(list(buf))
            publish("market.regime", {"regime": regime, "samples": len(buf), "last_price": buf[-1]})
        time.sleep(10)

if __name__ == "__main__":
    run()
