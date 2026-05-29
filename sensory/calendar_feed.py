#!/usr/bin/env python3
"""Stub economic calendar feeder. In production, fetch from RSS/API."""
import time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"nervous"))
from bus import publish

def run():
    while True:
        publish("calendar.status", {"next_event": "None", "impact": "low"})
        time.sleep(3600)
if __name__ == "__main__":
    run()
