#!/usr/bin/env python3
"""Alert router. Reads alert events and routes to console/file/email."""
import json, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"nervous"))
from bus import subscribe, publish

ALERT_LOG = Path(__file__).resolve().parent / "alerts.jsonl"
last_seq = 0
ALERT_TOPICS = {"immune.block", "immune.anomaly", "kernel.alert.fatal", "cortex.decision"}

def run():
    global last_seq
    while True:
        for topic in ALERT_TOPICS:
            evs = subscribe(topic, since_seq=last_seq)
            for ev in evs:
                seq=ev.get("seq",0)
                if seq>last_seq: last_seq=seq
                with open(ALERT_LOG,"a") as f:
                    f.write(json.dumps(ev)+"\n")
                publish("alert.routed", {"topic":topic, "seq":seq})
        time.sleep(2)
if __name__ == "__main__":
    run()
