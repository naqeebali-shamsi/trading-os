#!/usr/bin/env python3
"""Read-only broker/instrument verification report.

This script does not enable instruments and does not place trades. It classifies
configured symbols by how much evidence we currently have:
- verified: broker responded with symbol metadata and a live tick
- metadata_only: broker selected symbol but no tick was available
- pending: no live broker query was attempted or the bridge does not support it
- not_found: broker rejected the configured broker symbol
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from bridge.mt5_ipc_protocol import CommandSlotBusy, IPCPaths, ResponseTimeout, get_symbol_info, make_paths, search_symbols  # noqa: E402
from cortex.instrument_registry import load_registry  # noqa: E402
from ops.bridge_status import chart_dirs as bridge_chart_dirs, detect_ipc_mode as bridge_detect_ipc_mode  # noqa: E402
from scripts import readiness_gate  # noqa: E402

SymbolInfoFetcher = Callable[[IPCPaths, str, float], Dict[str, Any]]


def broker_candidates(symbol: str, cfg: Dict[str, Any]) -> List[str]:
    values = [cfg.get("broker_symbol") or symbol, symbol]
    values.extend(cfg.get("aliases") or [])
    out = []
    for value in values:
        value = str(value).strip()
        if value and value not in out:
            out.append(value)
    return out


def current_bridge(max_heartbeat_age: float = 30.0) -> Dict[str, Any]:
    mode = readiness_gate.detect_ipc_mode(readiness_gate.chart_dirs(), max_heartbeat_age=max_heartbeat_age)
    tick = readiness_gate.read_tick(readiness_gate.IPC / "tick.txt")
    return {"ok": mode.get("mode") in {"root", "chart", "mixed"}, "mode": mode, "root_tick": tick}


def resolve_no_trade_paths(*, max_heartbeat_age: float = 30.0) -> tuple[IPCPaths, str]:
    """Use root IPC when fresh; otherwise route read-only queries through a live chart bridge."""
    ipc = readiness_gate.IPC
    charts = bridge_chart_dirs(ipc)
    mode = bridge_detect_ipc_mode(ipc, charts, max_heartbeat_age=max_heartbeat_age)
    if mode.get("root_fresh"):
        return make_paths(ipc), "root"
    fresh = list(mode.get("fresh_charts") or [])
    if fresh:
        chart_name = fresh[0]
        return IPCPaths.from_root(ipc / chart_name), chart_name
    return make_paths(ipc), "offline"


def local_evidence(symbol: str, cfg: Dict[str, Any], bridge: Dict[str, Any]) -> Dict[str, Any]:
    root_tick = bridge.get("root_tick") or {}
    broker_symbol = str(cfg.get("broker_symbol") or symbol).upper()
    root_symbol = str(root_tick.get("symbol") or "").upper()
    chart = readiness_gate.IPC / f"chart_{cfg.get('broker_symbol', symbol)}"
    chart_tick = readiness_gate.read_tick(chart / "tick.txt") if chart.exists() else None
    return {
        "root_tick_match": bool(root_symbol and root_symbol == broker_symbol),
        "root_tick": root_tick if root_symbol == broker_symbol else None,
        "chart_present": chart.exists(),
        "chart_tick": chart_tick,
    }


def classify_from_info(info: Dict[str, Any]) -> str:
    if not info:
        return "pending"
    if info.get("ok") and info.get("has_tick"):
        return "verified"
    if info.get("ok"):
        return "metadata_only"
    if info.get("selected") is False or info.get("ok") is False:
        return "not_found"
    return "pending"


def verify_symbol(paths: IPCPaths, symbol: str, cfg: Dict[str, Any], *, timeout_sec: float, fetcher: SymbolInfoFetcher, live_query: bool, bridge: Dict[str, Any], max_candidates: Optional[int] = 2) -> Dict[str, Any]:
    evidence = local_evidence(symbol, cfg, bridge)
    broker_symbol = str(cfg.get("broker_symbol") or symbol).upper()
    if evidence["root_tick_match"]:
        return {
            "symbol": symbol,
            "broker_symbol": cfg.get("broker_symbol") or symbol,
            "asset_class": cfg.get("asset_class"),
            "enabled": bool(cfg.get("enabled")),
            "status": "verified",
            "source": "root_tick",
            "broker_info": None,
            "evidence": evidence,
            "reasons": [],
        }
    chart_tick = evidence.get("chart_tick") or {}
    if chart_tick and str(chart_tick.get("symbol") or "").upper() == broker_symbol:
        return {
            "symbol": symbol,
            "broker_symbol": cfg.get("broker_symbol") or symbol,
            "asset_class": cfg.get("asset_class"),
            "enabled": bool(cfg.get("enabled")),
            "status": "verified",
            "source": "chart_tick",
            "broker_info": None,
            "evidence": evidence,
            "reasons": [],
        }
    if not live_query:
        reasons = []
        if not bridge.get("ok"):
            reasons.append("bridge_not_fresh")
        reasons.append("live_query_disabled")
        return {
            "symbol": symbol,
            "broker_symbol": cfg.get("broker_symbol") or symbol,
            "asset_class": cfg.get("asset_class"),
            "enabled": bool(cfg.get("enabled")),
            "status": "pending",
            "source": "local_config_only",
            "broker_info": None,
            "evidence": evidence,
            "reasons": reasons,
        }

    errors = []
    candidates = broker_candidates(symbol, cfg)
    if max_candidates is not None and max_candidates > 0:
        candidates = candidates[:max_candidates]
    for candidate in candidates:
        try:
            info = fetcher(paths, candidate, timeout_sec)
        except CommandSlotBusy as exc:
            return {"symbol": symbol, "broker_symbol": cfg.get("broker_symbol") or symbol, "asset_class": cfg.get("asset_class"), "enabled": bool(cfg.get("enabled")), "status": "pending", "source": "broker_query", "broker_info": None, "evidence": evidence, "reasons": [f"cmd_slot_busy:{exc}"]}
        except ResponseTimeout as exc:
            return {"symbol": symbol, "broker_symbol": cfg.get("broker_symbol") or symbol, "asset_class": cfg.get("asset_class"), "enabled": bool(cfg.get("enabled")), "status": "pending", "source": "broker_query", "broker_info": None, "evidence": evidence, "reasons": [f"response_timeout:{exc}"]}
        except Exception as exc:
            errors.append(f"{candidate}:{type(exc).__name__}:{exc}")
            continue
        status = classify_from_info(info)
        if status in {"verified", "metadata_only"}:
            return {"symbol": symbol, "broker_symbol": candidate, "asset_class": cfg.get("asset_class"), "enabled": bool(cfg.get("enabled")), "status": status, "source": "broker_query", "broker_info": info, "evidence": evidence, "reasons": [] if status == "verified" else ["no_live_tick"]}
        errors.append(f"{candidate}:{info.get('error') or 'not_found'}")

    if max_candidates is not None and max_candidates > 0 and len(broker_candidates(symbol, cfg)) > len(candidates):
        errors.append(f"aliases_skipped:{len(broker_candidates(symbol, cfg)) - len(candidates)}")
    return {"symbol": symbol, "broker_symbol": cfg.get("broker_symbol") or symbol, "asset_class": cfg.get("asset_class"), "enabled": bool(cfg.get("enabled")), "status": "not_found" if live_query else "pending", "source": "broker_query" if live_query else "local_config_only", "broker_info": None, "evidence": evidence, "reasons": errors or ["not_verified"]}


def preflight_symbol_info(paths: IPCPaths, names: List[str], registry: Any, bridge: Dict[str, Any], *, timeout_sec: float, fetcher: SymbolInfoFetcher, attempts: int = 5) -> Dict[str, Any]:
    """Check whether the running EA supports GET_SYMBOL_INFO before a large scan.

    This avoids waiting one timeout per symbol when MT5 is still running an older
    EA build. A not_found response still proves support because it means the EA
    understood the read-only command and answered with symbol metadata semantics.
    """
    failures = []
    checked = 0
    for symbol in names:
        cfg = registry.get(symbol) or {}
        if local_evidence(symbol, cfg, bridge).get("root_tick_match"):
            continue
        checked += 1
        candidate = broker_candidates(symbol, cfg)[0]
        try:
            info = fetcher(paths, candidate, timeout_sec)
        except (CommandSlotBusy, ResponseTimeout) as exc:
            failures.append({"symbol": symbol, "candidate": candidate, "reason": f"{type(exc).__name__}:{exc}"})
            if checked >= attempts:
                break
            continue
        except Exception as exc:
            failures.append({"symbol": symbol, "candidate": candidate, "reason": f"{type(exc).__name__}:{exc}"})
            if checked >= attempts:
                break
            continue
        if (info or {}).get("type") == "symbol_info":
            return {"supported": True, "symbol": symbol, "candidate": candidate, "sample": info, "failures_before_success": failures}
        failures.append({"symbol": symbol, "candidate": candidate, "reason": "unrecognized_response", "sample": info})
        if checked >= attempts:
            break
    if failures:
        return {"supported": False, "reason": "symbol_info_preflight_failed", "failures": failures}
    return {"supported": True, "reason": "all_requested_symbols_already_verified_by_local_tick"}


def resolve_symbol_subset(
    registry,
    *,
    symbols: Optional[Iterable[str]] = None,
    enabled_only: bool = False,
    asset_class: Optional[str] = None,
    region: Optional[str] = None,
) -> List[str]:
    if symbols:
        return [str(s).strip().upper() for s in symbols if str(s).strip()]
    if enabled_only or asset_class or region:
        return registry.symbols_matching(
            enabled_only=enabled_only,
            asset_class=asset_class,
            region=region,
        )
    return registry.all_symbols()


def verify_universe(*, symbols: Optional[Iterable[str]] = None, live_query: bool = False, timeout_sec: float = 2.0, max_symbols: Optional[int] = None, max_candidates: Optional[int] = 2, fetcher: SymbolInfoFetcher = get_symbol_info, enabled_only: bool = False, asset_class: Optional[str] = None, region: Optional[str] = None) -> Dict[str, Any]:
    registry = load_registry(force=True)
    names = resolve_symbol_subset(
        registry,
        symbols=symbols,
        enabled_only=enabled_only,
        asset_class=asset_class,
        region=region,
    )
    if max_symbols is not None:
        names = names[:max_symbols]
    paths, query_route = resolve_no_trade_paths(max_heartbeat_age=30.0)
    bridge = current_bridge()
    bridge["query_route"] = query_route
    bridge["query_paths"] = str(paths.root)
    preflight = None
    effective_live_query = bool(live_query and bridge.get("ok"))
    if effective_live_query:
        preflight = preflight_symbol_info(paths, names, registry, bridge, timeout_sec=timeout_sec, fetcher=fetcher)
        bridge["symbol_info_preflight"] = preflight
        effective_live_query = bool(preflight.get("supported"))
    rows = [verify_symbol(paths, symbol, registry.get(symbol) or {}, timeout_sec=timeout_sec, fetcher=fetcher, live_query=effective_live_query, bridge=bridge, max_candidates=max_candidates) for symbol in names]
    if live_query and not effective_live_query:
        for row in rows:
            if row["status"] == "pending" and "live_query_disabled" in row.get("reasons", []):
                row["reasons"] = ["symbol_info_command_unavailable", str((preflight or {}).get("reason") or "bridge_not_fresh")]
    counts = Counter(row["status"] for row in rows)
    by_asset: Dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        by_asset[str(row.get("asset_class") or "unknown")][row["status"]] += 1
    return {
        "ts": time.time(),
        "live_query": live_query,
        "effective_live_query": effective_live_query,
        "query_route": query_route,
        "query_paths": str(paths.root),
        "timeout_sec": timeout_sec,
        "max_candidates": max_candidates,
        "bridge": bridge,
        "total": len(rows),
        "counts": dict(counts),
        "by_asset": {asset: dict(counter) for asset, counter in sorted(by_asset.items())},
        "results": rows,
    }


def print_text(report: Dict[str, Any], *, limit: int = 40) -> None:
    print("=" * 72)
    print("  Instrument Verification Report, read-only")
    print("=" * 72)
    print(f"Bridge: {report['bridge']['mode'].get('mode')} {report['bridge']['mode'].get('detail')}")
    if report.get("query_route"):
        print(f"Query route: {report['query_route']} ({report.get('query_paths')})")
    print(f"Live broker query: {report['live_query']} effective={report.get('effective_live_query')}")
    print(f"Total: {report['total']}  Counts: {report['counts']}")
    print("By asset class:")
    for asset, counts in report["by_asset"].items():
        print(f"  {asset}: {counts}")
    print("Sample rows:")
    for row in report["results"][:limit]:
        print(f"  {row['symbol']:10s} {row.get('asset_class'):12s} {row['status']:13s} broker={row['broker_symbol']} reasons={','.join(row.get('reasons') or [])}")
    if report["total"] > limit:
        print(f"  ... +{report['total'] - limit} more. Use --json for full details.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only broker/instrument verification")
    parser.add_argument("--live-query", action="store_true", help="Use no-trade GET_SYMBOL_INFO IPC command if the EA supports it")
    parser.add_argument("--symbols", default="", help="Comma-separated subset to verify")
    parser.add_argument("--max-symbols", type=int, default=None, help="Limit number of symbols checked")
    parser.add_argument("--timeout", type=float, default=2.0, help="Per candidate timeout in seconds")
    parser.add_argument("--max-candidates", type=int, default=2, help="Max broker aliases tried per symbol; use 0 for all aliases")
    parser.add_argument("--enabled-only", action="store_true", help="Verify only enabled symbols")
    parser.add_argument("--asset-class", default="", help="Filter by asset_class (e.g. stock_cfd)")
    parser.add_argument("--region", default="", help="Filter by region tag (e.g. IN)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    subset = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    max_candidates = None if args.max_candidates == 0 else args.max_candidates
    report = verify_universe(
        symbols=subset,
        live_query=args.live_query,
        timeout_sec=args.timeout,
        max_symbols=args.max_symbols,
        max_candidates=max_candidates,
        enabled_only=args.enabled_only,
        asset_class=args.asset_class.strip() or None,
        region=args.region.strip() or None,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str), flush=True)
    else:
        print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
