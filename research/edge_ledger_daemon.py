#!/usr/bin/env python3
"""Continuous edge candidate ledger.

Runs the ingest -> label -> gate-report cycle on an interval so candidates and
their forward outcomes accumulate automatically toward the promotion gates. This
is a passive measurement layer: it never publishes execution topics and never
promotes a source on its own. The dashboard edge_validation panel renders the
gate report this daemon keeps fresh.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "nervous") not in sys.path:
    sys.path.insert(0, str(ROOT / "nervous"))

from research import edge_ledger_ops as ops  # noqa: E402

INTERVAL_SEC = float(os.getenv("TRADING_OS_EDGE_LEDGER_INTERVAL_SEC", "300"))
TAIL_LIMIT = int(os.getenv("TRADING_OS_EDGE_LEDGER_TAIL", "2000"))
COST_PER_TRADE = float(os.getenv("TRADING_OS_EDGE_LEDGER_COST", "0"))


def run_once() -> dict:
    summary = ops.run_once(tail_limit=TAIL_LIMIT, cost_per_trade=COST_PER_TRADE)
    print(
        f"[edge_ledger_daemon] events={summary['events']} appended={summary['appended']} "
        f"labeled={summary['labeled']} candidates={summary['candidates']} "
        f"labels={summary['labels']} groups={summary['groups']} "
        f"promotable={summary['promotable']}",
        flush=True,
    )
    return summary


def run(interval: float = INTERVAL_SEC) -> None:
    print(f"[edge_ledger_daemon] started interval={interval}s tail={TAIL_LIMIT}", flush=True)
    while True:
        try:
            run_once()
        except Exception as exc:  # pragma: no cover - defensive daemon guard
            print(f"[edge_ledger_daemon] error: {exc}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    else:
        run()
