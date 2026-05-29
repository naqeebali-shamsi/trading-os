#!/usr/bin/env python3
"""
scripts/bus_rotate.py — Archive event bus when it grows too large.
Run via cron daily or when bus.jsonl > threshold.
"""
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paths import archive_dir, nervous_dir  # noqa: E402

NERVOUS = nervous_dir()
ARCHIVE_DIR = archive_dir()
BUS_FILE = NERVOUS / "bus.jsonl"
TOPICS_DIR = NERVOUS / "topics"

MAX_BUS_MB = 50
MIN_ARCHIVE_AGE_HOURS = 24

def now():
    return time.time()

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    print(f"[{ts}] {msg}")

def main():
    marker = ARCHIVE_DIR / ".last_bus_archive"
    if marker.exists():
        last = float(marker.read_text().strip())
        if (now() - last) / 3600 < MIN_ARCHIVE_AGE_HOURS:
            log("SKIP: archived within last 24h")
            return

    if not BUS_FILE.exists():
        log("SKIP: no bus.jsonl")
        return

    size_mb = BUS_FILE.stat().st_size / (1024 * 1024)
    if size_mb < MAX_BUS_MB:
        log(f"SKIP: bus.jsonl {size_mb:.1f}MB < {MAX_BUS_MB}MB threshold")
        return

    ts_str = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    dest = ARCHIVE_DIR / f"bus_{ts_str}"
    dest.mkdir(parents=True, exist_ok=True)

    shutil.move(str(BUS_FILE), str(dest / "bus.jsonl"))

    for f in TOPICS_DIR.glob("*.jsonl"):
        shutil.move(str(f), str(dest / f.name))

    BUS_FILE.touch()
    for f in dest.glob("*.jsonl"):
        if f.name != "bus.jsonl":
            (TOPICS_DIR / f.name).touch()

    marker.write_text(str(now()))
    log(f"ARCHIVED bus to {dest} ({size_mb:.1f}MB)")

if __name__ == "__main__":
    main()
