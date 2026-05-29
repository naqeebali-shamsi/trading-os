#!/usr/bin/env python3
"""Verify India NSE watchlist symbols against MT5 broker (read-only)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from bridge.mt5_ipc_protocol import search_symbols  # noqa: E402
from cortex.instrument_registry import load_registry  # noqa: E402
from cortex.stock_universe import india_watchlist_symbols  # noqa: E402
from scripts.verify_instruments import (  # noqa: E402
    print_text,
    resolve_no_trade_paths,
    verify_universe,
)


def _explain_pending(report: dict) -> list[str]:
    hints: list[str] = []
    if report.get("effective_live_query"):
        return hints
    route = report.get("query_route")
    preflight = (report.get("bridge") or {}).get("symbol_info_preflight") or {}
    hints.append(
        "Live broker query did not run: GET_SYMBOL_INFO is unavailable on the connected EA build."
    )
    if route == "offline":
        hints.append("No fresh root or chart bridge heartbeat — attach a bridge EA with Algo Trading ON.")
    elif route and route != "root":
        hints.append(f"Routing read-only queries via chart bridge {route} (chart-only MT5 setup).")
    hints.append(
        "Recompile tracks/track_b_multisymbol/FileBridgeEA_MultiSymbol.mq5 in MetaEditor, "
        "restart/re-attach on at least one live chart, then re-run with --live-query."
    )
    if preflight.get("failures"):
        sample = preflight["failures"][0]
        hints.append(f"Preflight sample failure: {sample.get('reason')} candidate={sample.get('candidate')}")
    return hints


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify disabled India NSE stock CFD watchlist on broker")
    parser.add_argument("--live-query", action="store_true")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--max-candidates", type=int, default=0, help="Try all broker aliases per symbol")
    parser.add_argument("--search", default="", help="SEARCH_SYMBOLS on broker (e.g. RELIANCE, TCS, .NS)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.search.strip():
        paths, route = resolve_no_trade_paths()
        result = search_symbols(paths, args.search.strip(), limit=50, timeout_sec=args.timeout)
        payload = {"query_route": route, "query_paths": str(paths.root), "search": result}
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True, default=str), flush=True)
        else:
            print(f"Symbol search via {route} ({paths.root}) query={args.search!r}")
            for row in result.get("symbols") or []:
                print(f"  {row.get('symbol'):16s} {row.get('description', '')[:60]}")
        return 0 if result.get("ok") else 1

    registry = load_registry(force=True)
    symbols = india_watchlist_symbols(registry)
    if not symbols:
        print(json.dumps({"ok": False, "reason": "no_india_watchlist_symbols"}))
        return 1

    max_candidates = None if args.max_candidates == 0 else args.max_candidates
    report = verify_universe(
        symbols=symbols,
        live_query=args.live_query,
        timeout_sec=args.timeout,
        max_candidates=max_candidates,
    )
    report["india_watchlist"] = symbols
    report["next_steps"] = [
        "Use --search RELIANCE (or .NS) to discover exact broker symbol names",
        "Set enabled: true in config/instruments.d/india_nse.yaml for verified symbols",
        "Update broker_symbol / aliases to match broker search results",
        "python scripts/bootstrap_mt5_charts.py --write",
        "Attach ChartBootstrapService.ex5 in MT5",
        "python scripts/enable_stock_trading.py --preset production",
    ]
    report["hints"] = _explain_pending(report)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str), flush=True)
    else:
        print_text(report, limit=len(symbols))
        if report.get("hints"):
            print("\nHints:")
            for hint in report["hints"]:
                print(f"  - {hint}")
        if (report.get("counts") or {}).get("pending") == report.get("total") and not report.get("effective_live_query"):
            print("\nInterpretation: pending here means broker lookup was skipped/failed — not proof symbols are absent.")
        print("\nNext steps after broker verification:")
        for step in report["next_steps"]:
            print(f"  - {step}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
