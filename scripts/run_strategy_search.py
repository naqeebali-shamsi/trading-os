#!/usr/bin/env python3
"""CLI for guarded automated strategy search."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.strategy_search.engine import run_strategy_search  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run guarded strategy search (train/val/test, anti-overfit)")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--report", default=None, help="Override report output path")
    args = parser.parse_args(argv)

    report_path = Path(args.report) if args.report else None
    report = run_strategy_search(
        symbol=args.symbol,
        timeframe=args.timeframe,
        report_path=report_path,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
