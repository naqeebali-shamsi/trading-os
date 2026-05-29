#!/usr/bin/env python3
"""
consciousness/traces.py — Structured Event Tracing
---------------------------------------------------
Every layer emits trace spans. This collector writes them to
consciousness/traces/<component>/<date>.jsonl for post-hoc analysis.
Also builds a real-time summary for the dashboard.
"""
import json, os, time, sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/"nervous"))
from bus import subscribe, publish

TRACES_DIR = ROOT / "consciousness" / "traces"
SUMMARY_FILE = ROOT / "consciousness" / "trace_summary.json"

def trace_path(component):
    d = TRACES_DIR / component / datetime.utcnow().strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    return d / "spans.jsonl"

last_seq_by_topic = {}

def run():
    while True:
        # Subscribe to everything — get last 50 events across all topics
        from bus import tail  # dynamic import to avoid circular
        events = tail(50)
        new_count = 0
        for ev in events:
            topic = ev.get("topic", "unknown")
            seq = ev.get("seq", 0)
            if seq <= last_seq_by_topic.get(topic, 0):
                continue
            last_seq_by_topic[topic] = seq
            new_count += 1
            
            # Extract component from topic (first.segment)
            component = topic.split(".")[0]
            path = trace_path(component)
            with open(path, "a") as f:
                f.write(json.dumps(ev) + "\n")
        
        # Update summary
        summary = {t: last_seq_by_topic[t] for t in last_seq_by_topic}
        summary["_last_update"] = time.time()
        summary["_topics_tracked"] = len(last_seq_by_topic)
        SUMMARY_FILE.write_text(json.dumps(summary, indent=2))
        
        time.sleep(3)

if __name__ == "__main__":
    run()
