#!/usr/bin/env python3
"""Generate MT5 chart manifest and report bootstrap gaps for enabled instruments."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from ops.chart_bootstrap import evaluate_bootstrap_gaps, generate_manifest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="MT5 chart bootstrap manifest + gap report")
    parser.add_argument("--write", action="store_true", help="Write ipc/chart_manifest.csv and config/chart_manifest.json")
    parser.add_argument("--json", action="store_true", help="Print JSON gap report")
    parser.add_argument("--max-heartbeat-age", type=float, default=120.0)
    args = parser.parse_args()

    if args.write:
        manifest = generate_manifest()
        print(json.dumps({"ok": True, "written": manifest.get("charts", [])}, indent=2))

    report = evaluate_bootstrap_gaps(max_heartbeat_age=args.max_heartbeat_age)
    if args.json or not args.write:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = report.get("summary") or {}
        ready = report.get("ready_symbols") or []
        missing = report.get("missing_symbols") or []
        stale = report.get("stale_symbols") or []
        print(
            f"Bootstrap: ready={summary.get('ready')} missing={summary.get('missing')} stale={summary.get('stale')}",
            file=sys.stderr,
        )
        if ready:
            print(f"  ready: {', '.join(ready)}", file=sys.stderr)
        if missing:
            print(f"  missing: {', '.join(missing)}", file=sys.stderr)
        if stale:
            print(f"  stale: {', '.join(stale)}", file=sys.stderr)
    missing = (report.get("summary") or {}).get("missing", 0)
    stale = (report.get("summary") or {}).get("stale", 0)
    return 0 if missing == 0 and stale == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
