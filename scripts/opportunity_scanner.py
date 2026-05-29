#!/usr/bin/env python3
"""Read-only opportunity scanner for live/demo trading.

The scanner is deliberately non-executing. It watches the same gates that protect
live trading, calls the real advisory brain only when market/instrument/risk
conditions permit it, and prints an auditable proposal for human confirmation.
It never writes MT5 command files and never places orders.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from bus import subscribe  # noqa: E402
from bridge.mt5_ipc_protocol import make_paths, get_symbol_info  # noqa: E402
from cortex.agent import AgentBrain, BrainRunResult  # noqa: E402
from cortex.instrument_registry import load_registry  # noqa: E402
from immune import main as immune_main  # noqa: E402
from muscle import pnl_sync  # noqa: E402
from scripts import readiness_gate, real_mode_audit  # noqa: E402
from research.snapshot import load_snapshot, pick_research_candidates, research_by_symbol  # noqa: E402

IPC = readiness_gate.IPC
BrainRunner = Callable[..., BrainRunResult]


def broker_tick(symbol: str, broker_symbol: str, timeout_sec: float = 5.0) -> Optional[Dict[str, Any]]:
    """Fetch a live bid/ask from the root EA's read-only symbol metadata command.

    This lets one attached root bridge hydrate ticks for symbols that are selected
    in the broker but do not have dedicated chart_* IPC folders. It never places
    orders and only uses the no-trade GET_SYMBOL_INFO command.
    """
    try:
        info = get_symbol_info(make_paths(IPC), broker_symbol, timeout_sec=timeout_sec)
    except Exception:
        return None
    if not info.get("ok") or not info.get("has_tick"):
        return None
    try:
        bid = float(info.get("bid") or 0)
        ask = float(info.get("ask") or 0)
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask < bid:
        return None
    tick_time = info.get("tick_time") or info.get("ts") or int(time.time())
    return {
        "symbol": symbol,
        "broker_symbol": broker_symbol,
        "bid": bid,
        "ask": ask,
        "raw": f"{broker_symbol},{bid},{ask},{tick_time}",
        "source": "broker_symbol_info",
        "broker_info": info,
    }


def latest_payload(topic: str) -> Optional[Dict[str, Any]]:
    events = subscribe(topic, limit=1)
    if not events:
        return None
    payload = events[-1].get("payload")
    return payload if isinstance(payload, dict) else None


def root_tick() -> Optional[Dict[str, Any]]:
    return readiness_gate.read_tick(IPC / "tick.txt")


def bridge_gate(max_heartbeat_age: float) -> Dict[str, Any]:
    charts = readiness_gate.chart_dirs()
    mode = readiness_gate.detect_ipc_mode(charts, max_heartbeat_age=max_heartbeat_age)
    return {
        "ok": mode.get("mode") in {"root", "chart", "mixed"},
        "mode": mode,
        "charts": [p.name for p in charts],
        "reason": "ok" if mode.get("mode") in {"root", "chart", "mixed"} else mode.get("detail", "bridge_offline"),
    }


def risk_gate(symbol: Optional[str] = None) -> Dict[str, Any]:
    stop = ROOT / "STOP_TRADING"
    limits = immune_main.load_limits()
    journal = immune_main.load_journal()
    intent = {"symbol": symbol} if symbol else {}
    cooldown = immune_main.loss_streak_block_reason(intent, limits, journal)
    positions = pnl_sync.load_state().get("positions", {})
    open_count = len(positions) if isinstance(positions, dict) else 0
    reasons = []
    if stop.exists():
        reasons.append("STOP_TRADING_present")
    if cooldown:
        reasons.append(cooldown)
    max_positions = int(limits.get("max_positions", 0) or 0)
    if max_positions and open_count >= max_positions:
        reasons.append(f"max_positions_reached:{open_count}/{max_positions}")
    return {
        "ok": not reasons,
        "mode": limits.get("mode"),
        "open_positions": open_count,
        "cooldown": cooldown,
        "stop_trading": stop.exists(),
        "reasons": reasons,
    }


def instrument_gate(max_heartbeat_age: float, *, include_watchlist: bool = True, hydrate_broker_ticks: bool = True) -> Dict[str, Any]:
    charts = readiness_gate.chart_dirs()
    chart_names = [p.name for p in charts]
    tick_map: Dict[str, Dict[str, Any]] = {}

    # Root bridge is the active path in the current MT5 setup. Prefer its tick so
    # stale chart_* folders do not create false opportunities.
    tick = root_tick()
    if tick:
        tick_map[str(tick["symbol"]).upper()] = tick

    # If chart bridges are fresh, also include them as candidates.
    for chart in charts:
        age, _ = readiness_gate.heartbeat_age(chart / "heartbeat.txt")
        fresh = age is not None and age <= max_heartbeat_age
        if fresh:
            chart_tick = readiness_gate.read_tick(chart / "tick.txt")
            if chart_tick:
                tick_map[str(chart_tick["symbol"]).upper()] = chart_tick

    registry = load_registry(force=True)
    if hydrate_broker_ticks:
        for sym in registry.all_symbols():
            cfg = registry.get(sym) or {}
            if not cfg.get("enabled") or sym in tick_map:
                continue
            live_tick = broker_tick(sym, cfg.get("broker_symbol") or sym)
            if live_tick:
                tick_map[sym] = live_tick
    snapshot = registry.readiness_snapshot(chart_names, tick_map)
    ready = {sym: row for sym, row in snapshot.items() if row.get("enabled") and row.get("ready")}
    blocked = {sym: row for sym, row in snapshot.items() if row.get("enabled") and not row.get("ready")}
    watchlist = {}
    if include_watchlist:
        for sym, row in snapshot.items():
            if row.get("enabled"):
                continue
            watchlist[sym] = {
                **row,
                "watch_only": True,
                "not_executable_reason": disabled_instrument_reason(row),
            }
    asset_classes: Dict[str, List[str]] = {}
    for sym, row in snapshot.items():
        asset_classes.setdefault(str(row.get("asset_class") or "unknown"), []).append(sym)
    return {"ok": bool(ready), "ready": ready, "blocked": blocked, "watchlist": watchlist, "asset_classes": asset_classes, "all": snapshot, "tick_map": tick_map}


def disabled_instrument_reason(row: Dict[str, Any]) -> str:
    reasons = ["symbol_disabled"]
    if not row.get("chart_present"):
        reasons.append("chart_missing")
    if not row.get("session_ok"):
        reasons.append(str(row.get("session_reason") or "session_not_ready"))
    if not row.get("spread_ok"):
        reasons.append(str(row.get("spread_reason") or "spread_not_ready"))
    return ",".join(dict.fromkeys(reasons))


def context_gate(symbol: str) -> Dict[str, Any]:
    radar = latest_payload("macro.event_radar")
    forecast = latest_payload(f"market.forecast.{symbol}") or latest_payload("market.forecast")
    reasons = []
    if radar and radar.get("severity") in {"high", "critical"} and radar.get("action_hint", "").startswith("observe"):
        reasons.append(f"macro_observe_only:{radar.get('category')}")
    if forecast and forecast.get("ok") is False:
        reasons.append(f"forecast_unavailable:{forecast.get('model')}")
    # These are not hard blockers by themselves. They are context for the brain.
    return {"ok": True, "radar": radar, "forecast": forecast, "warnings": reasons}


def run_brain_for_candidate(symbol: str, tick: Dict[str, Any], context: Dict[str, Any], *, brain_runner: Optional[BrainRunner] = None) -> Dict[str, Any]:
    runner = brain_runner or AgentBrain().run
    registry = load_registry(force=True)
    instrument_cfg = registry.get(symbol) or {}
    news = []
    macro_events = []
    forecasts = []
    if context.get("radar"):
        macro_events.append(context["radar"])
        news.append(context["radar"])
    if context.get("forecast"):
        forecasts.append(context["forecast"])
    result = runner(
        market_snapshot={"symbol": symbol, "bid": tick.get("bid"), "ask": tick.get("ask"), "raw": tick.get("raw")},
        news=news,
        forecasts=forecasts,
        macro_events=macro_events,
        positions=list((pnl_sync.load_state().get("positions", {}) or {}).values()),
        constraints={
            "default_action": "HOLD",
            "requires_stop_loss": True,
            "dry_run": True,
            "human_confirmation_required": True,
            "scanner": "opportunity_scanner",
            "symbol": symbol,
            "asset_class": instrument_cfg.get("asset_class"),
            "min_qty": instrument_cfg.get("min_lot"),
            "max_qty": instrument_cfg.get("max_lot"),
            "qty_step": instrument_cfg.get("lot_step"),
            "sizing_instruction": f"If proposing {symbol}, qty must be between {instrument_cfg.get('min_lot')} and {instrument_cfg.get('max_lot')} in steps of {instrument_cfg.get('lot_step')}.",
        },
        decision_mode="ADVISORY",
        correlation_id=f"opportunity-scan-{symbol}-{int(time.time())}",
    )
    return result.as_dict()


def scan_once(*, max_heartbeat_age: float = 30.0, brain_runner: Optional[BrainRunner] = None, include_watchlist: bool = True, candidate_symbols: Optional[List[str]] = None, asset_class: Optional[str] = None, region: Optional[str] = None, research_prefer: bool = False, research_min_tier: Optional[str] = None, research_min_confidence: float = 0.0, research_limit: Optional[int] = None) -> Dict[str, Any]:
    audit = real_mode_audit.audit()
    bridge = bridge_gate(max_heartbeat_age)
    instruments = instrument_gate(max_heartbeat_age, include_watchlist=include_watchlist)
    risk = risk_gate()
    hard_reasons: List[str] = []
    if not audit.get("ok"):
        hard_reasons.append("real_mode_audit_failed")
    if not bridge.get("ok"):
        hard_reasons.append(f"bridge:{bridge.get('reason')}")
    if not risk.get("ok"):
        hard_reasons.extend(risk.get("reasons", []))
    if not instruments.get("ok"):
        hard_reasons.append("no_enabled_instrument_ready")

    decisions = []
    research_snapshot = load_snapshot()
    research_map = research_by_symbol(research_snapshot)
    if not hard_reasons:
        registry = load_registry(force=True)
        allowed_candidates = {s.upper() for s in candidate_symbols} if candidate_symbols else None
        if asset_class or region:
            filtered = registry.symbols_matching(enabled_only=True, asset_class=asset_class, region=region)
            filter_set = {s.upper() for s in filtered}
            allowed_candidates = filter_set if allowed_candidates is None else (allowed_candidates & filter_set)

        ready_symbols = list(instruments["ready"].keys())
        if research_prefer or research_min_tier or research_min_confidence > 0:
            ready_symbols = pick_research_candidates(
                ready_symbols,
                min_tier=research_min_tier,
                min_confidence=research_min_confidence,
                limit=research_limit,
                snapshot=research_snapshot,
            )
        elif research_limit:
            ready_symbols = ready_symbols[:research_limit]

        for symbol in ready_symbols:
            row = instruments["ready"].get(symbol)
            if not row:
                continue
            if allowed_candidates and symbol.upper() not in allowed_candidates:
                continue
            tick = instruments["tick_map"].get(symbol)
            if not tick:
                continue
            context = context_gate(symbol)
            brain = run_brain_for_candidate(symbol, tick, context, brain_runner=brain_runner)
            proposal = ((brain.get("decision") or {}).get("proposal") or {})
            guard = brain.get("guard") or {}
            executable = proposal.get("action") == "PROPOSE_ORDER" and guard.get("ok") is True
            research_row = research_map.get(symbol.upper()) or {}
            decisions.append({
                "symbol": symbol,
                "tick": tick,
                "context_warnings": context.get("warnings", []),
                "brain": brain,
                "proposal": proposal,
                "requires_confirmation": executable,
                "scanner_recommendation": "SURFACE_FOR_CONFIRMATION" if executable else "HOLD_OR_OBSERVE",
                "research": {
                    "tier": research_row.get("tier"),
                    "confidence": research_row.get("confidence"),
                    "composite_score": research_row.get("composite_score"),
                    "thesis": research_row.get("thesis"),
                } if research_row else None,
            })

    return {
        "ts": time.time(),
        "ok": not hard_reasons,
        "hard_reasons": hard_reasons,
        "audit": audit,
        "bridge": bridge,
        "risk": risk,
        "instruments": {
            "ready": instruments.get("ready", {}),
            "blocked": instruments.get("blocked", {}),
            "watchlist": instruments.get("watchlist", {}),
            "asset_classes": instruments.get("asset_classes", {}),
        },
        "decisions": decisions,
        "research": {
            "available": research_snapshot.get("available"),
            "prefer": research_prefer,
            "min_tier": research_min_tier,
            "min_confidence": research_min_confidence,
            "limit": research_limit,
            "top_picks": [
                {"symbol": r.get("symbol"), "tier": r.get("tier"), "confidence": r.get("confidence")}
                for r in (research_snapshot.get("top_picks") or [])[:8]
            ] if research_snapshot.get("available") else [],
        },
        "summary": summarize(hard_reasons, decisions),
    }


def summarize(hard_reasons: List[str], decisions: List[Dict[str, Any]]) -> str:
    if hard_reasons:
        return "NO_TRADE: " + ", ".join(hard_reasons)
    actionable = [d for d in decisions if d.get("requires_confirmation")]
    if actionable:
        symbols = ", ".join(d["symbol"] for d in actionable)
        return f"OPPORTUNITY_REQUIRES_CONFIRMATION: {symbols}"
    if decisions:
        return "NO_TRADE: brain returned HOLD/observe"
    return "NO_TRADE: no candidates evaluated"


def print_text(report: Dict[str, Any], *, verbose_watchlist: bool = False, watchlist_preview: int = 12) -> None:
    print("=" * 72)
    print("  Trading OS Opportunity Scanner, read-only")
    print("=" * 72)
    print(report["summary"])
    print(f"Bridge: {report['bridge']['mode'].get('mode')} {report['bridge']['mode'].get('detail')}")
    print(f"Risk: ok={report['risk']['ok']} positions={report['risk']['open_positions']} stop={report['risk']['stop_trading']} cooldown={report['risk']['cooldown']}")
    if report["hard_reasons"]:
        print("Hard blockers:")
        for reason in report["hard_reasons"]:
            print(f"  - {reason}")
    if report["instruments"].get("blocked"):
        print("Blocked enabled instruments:")
        for sym, row in report["instruments"]["blocked"].items():
            reason = None
            for ok_key, reason_key in (("session_ok", "session_reason"), ("spread_ok", "spread_reason"), ("broker_trade_ok", "broker_trade_reason")):
                if row.get(ok_key) is False:
                    reason = row.get(reason_key)
                    break
            print(f"  - {sym}: {row.get('result')} ({reason or 'not_ready'})")
    if report["instruments"].get("watchlist"):
        total_watch = len(report["instruments"]["watchlist"])
        print(f"Watchlist/non-executable portfolio instruments: {total_watch}")
        by_asset: Dict[str, List[str]] = {}
        for sym, row in report["instruments"]["watchlist"].items():
            by_asset.setdefault(str(row.get("asset_class") or "unknown"), []).append(sym)
        for asset_class in sorted(by_asset):
            symbols = by_asset[asset_class]
            visible = symbols if verbose_watchlist else symbols[:watchlist_preview]
            suffix = "" if len(visible) == len(symbols) else f" ... +{len(symbols) - len(visible)} more"
            print(f"  {asset_class} ({len(symbols)}): {', '.join(visible)}{suffix}")
            if verbose_watchlist:
                for sym in symbols:
                    row = report["instruments"]["watchlist"][sym]
                    print(f"    - {sym}: {row.get('not_executable_reason')}")
        if not verbose_watchlist:
            print("  Use --verbose-watchlist for per-symbol not-executable reasons.")
    for decision in report.get("decisions", []):
        proposal = decision.get("proposal") or {}
        print(f"Decision {decision['symbol']}: {proposal.get('action')} confidence={proposal.get('confidence')} reason={proposal.get('reasoning')}")
        if decision.get("requires_confirmation"):
            print("  CONFIRMATION REQUIRED before any execution")
            print(json.dumps(proposal, indent=2, sort_keys=True))
    sys.stdout.flush()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only opportunity scanner. Never places trades.")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    parser.add_argument("--loop", action="store_true", help="Loop until interrupted")
    parser.add_argument("--watch", action="store_true", help="Alias for --loop")
    parser.add_argument("--interval", type=float, default=60.0, help="Loop interval seconds")
    parser.add_argument("--max-heartbeat-age", type=float, default=30.0)
    parser.add_argument("--symbols", default="", help="Comma-separated symbols to evaluate")
    parser.add_argument("--asset-class", default="", help="Limit scan to asset_class (e.g. stock_cfd)")
    parser.add_argument("--region", default="", help="Limit scan to region tag (e.g. IN)")
    parser.add_argument("--no-watchlist", action="store_true", help="Hide disabled/non-executable portfolio instruments")
    parser.add_argument("--verbose-watchlist", action="store_true", help="Show every watchlist symbol with not-executable reason")
    parser.add_argument("--research-prefer", action="store_true", help="Sort/limit scan to research-ranked stock candidates")
    parser.add_argument("--research-min-tier", default="", help="Minimum research tier (multibagger_candidate|high_conviction|accumulate|watch)")
    parser.add_argument("--research-min-confidence", type=float, default=0.0)
    parser.add_argument("--research-limit", type=int, default=0, help="Max research-ranked candidates to evaluate (0=all)")
    args = parser.parse_args(argv)
    candidate_symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    asset_class = args.asset_class.strip() or None
    region = args.region.strip() or None
    scan_kwargs = dict(
        max_heartbeat_age=args.max_heartbeat_age,
        include_watchlist=not args.no_watchlist,
        candidate_symbols=candidate_symbols,
        asset_class=asset_class,
        region=region,
        research_prefer=args.research_prefer,
        research_min_tier=args.research_min_tier.strip() or None,
        research_min_confidence=args.research_min_confidence,
        research_limit=args.research_limit or None,
    )

    if args.loop or args.watch:
        while True:
            report = scan_once(**scan_kwargs)
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True, default=str), flush=True)
            else:
                print_text(report, verbose_watchlist=args.verbose_watchlist)
            time.sleep(args.interval)
    report = scan_once(**scan_kwargs)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str), flush=True)
    else:
        print_text(report, verbose_watchlist=args.verbose_watchlist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
