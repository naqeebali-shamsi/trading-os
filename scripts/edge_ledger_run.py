#!/usr/bin/env python3
"""Thin CLI wrapper around research.edge_ledger_ops.run_once().

One-shot ingest -> label -> gate-report for the edge candidate ledger. The
heavy lifting lives in research/edge_ledger_ops.py so this CLI and the
background daemon (research/edge_ledger_daemon.py) share one implementation.
"""
import argparse
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "nervous") not in sys.path:
    sys.path.insert(0, str(ROOT / "nervous"))

from research import edge_ledger_ops as ops  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the edge candidate ledger once.")
    parser.add_argument("--tail", type=int, default=2000, help="Bus events to tail for ingest.")
    parser.add_argument("--cost", type=float, default=0.0, help="Cost per trade for the gate report.")
    parser.add_argument("--no-label", action="store_true", help="Ingest + report only; skip forward labeling.")
    args = parser.parse_args(argv)

    if args.no_label:
        # Ingest + report only: pass an empty event window after ingest by reusing ops
        # with a price lookup that returns nothing so no horizons close.
        result = ops.run_once(
            tail_limit=args.tail,
            cost_per_trade=args.cost,
            price_lookup=lambda symbol, ts: None,
        )
    else:
        result = ops.run_once(tail_limit=args.tail, cost_per_trade=args.cost)

    report = result["report"]
    print(f"[edge-ledger] ingested {result['appended']} new candidate(s) "
          f"from {result['events']} bus event(s)")
    if not args.no_label:
        print(f"[edge-ledger] appended {result['labeled']} new forward label(s)")
    print(f"[edge-ledger] {result['candidates']} candidate(s), {result['labels']} label(s), "
          f"{result['groups']} group(s), {result['promotable']} promotable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
