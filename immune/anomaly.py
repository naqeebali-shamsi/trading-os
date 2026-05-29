#!/usr/bin/env python3
"""Z-score anomaly detection on returns and tick gaps."""
import json, time, sys, statistics
from pathlib import Path
from collections import deque

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/"nervous"))
from bus import publish, subscribe

WINDOW = 50
returns = deque(maxlen=WINDOW)
last_seq = 0
last_tick = None

def z_score(vals, new):
    if len(vals) < 10: return 0
    m = sum(vals)/len(vals)
    s = statistics.stdev(vals)
    return 0 if s == 0 else (new - m) / s

def run():
    global last_seq, last_tick
    while True:
        evs = subscribe("market.tick", since_seq=last_seq)
        for ev in evs:
            seq=ev.get("seq",0)
            if seq>last_seq: last_seq=seq
            bid=ev.get("payload",{}).get("bid")
            if bid is None: continue
            if last_tick is not None:
                ret = (bid - last_tick) / last_tick
                z = z_score(list(returns), ret)
                returns.append(ret)
                if abs(z) > 2.5:
                    publish("immune.anomaly", {"type":"volatility_spike", "z": round(z,4), "price":bid})
            last_tick = bid
        time.sleep(5)
if __name__ == "__main__":
    run()
