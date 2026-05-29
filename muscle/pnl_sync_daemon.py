#!/usr/bin/env python3
"""Periodic MT5 position reconciliation for brain and immune context."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
sys.path.insert(0, str(ROOT / "muscle"))

from muscle import pnl_sync  # noqa: E402

INTERVAL_SEC = float(os.getenv("TRADING_OS_PNL_SYNC_INTERVAL_SEC", "30"))


def run():
    while True:
        try:
            pnl_sync.check_once(use_command=True, publish_events=True)
        except Exception as exc:
            print(f"[pnl_sync_daemon] error: {exc}", flush=True)
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    run()
