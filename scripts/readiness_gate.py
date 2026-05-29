#!/usr/bin/env python3
"""Live/demo readiness gate with no trade placement."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from ipc_path import get_ipc_dir  # noqa: E402
from ops.bridge_status import (  # noqa: E402
    chart_dirs as _chart_dirs,
    detect_ipc_mode as _detect_ipc_mode,
    heartbeat_age,
    read_tick,
    tick_ok,
)
from ops import readiness_eval as _readiness_eval  # noqa: E402
from ops.readiness_eval import ReadinessOptions, evaluate_readiness  # noqa: E402

IPC = get_ipc_dir()


def chart_dirs():
    return _chart_dirs(IPC)


def detect_ipc_mode(charts=None, *, max_heartbeat_age=15.0):
    return _detect_ipc_mode(IPC, charts, max_heartbeat_age=max_heartbeat_age)


def roundtrip_targets(mode: str, charts, instrument_status):
    return _readiness_eval.roundtrip_targets(mode, IPC, charts, instrument_status)


def run_ping_roundtrip(target, timeout_sec: float):
    return _readiness_eval.run_ping_roundtrip(target, timeout_sec)


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    return bool(ok)


def main():
    parser = argparse.ArgumentParser(description="Trading OS readiness gate")
    parser.add_argument("--live", action="store_true", help="Require fresh real MT5 chart heartbeats")
    parser.add_argument("--strict-instruments", action="store_true", help="Legacy gate: all enabled instruments must be ready (overrides per_asset_class policy)")
    parser.add_argument(
        "--instrument-gate",
        choices=("off", "all_enabled", "per_asset_class"),
        default="",
        help="Override defaults.readiness.instrument_gate from instruments.yaml",
    )
    parser.add_argument(
        "--chart-gate",
        choices=("off", "all_present", "enabled_symbols"),
        default="",
        help="Override defaults.readiness.chart_gate from instruments.yaml",
    )
    parser.add_argument("--max-heartbeat-age", type=float, default=15.0)
    parser.add_argument("--roundtrip", action="store_true", help="Disabled-by-default no-trade PING,cid IPC roundtrip")
    parser.add_argument("--roundtrip-timeout", type=float, default=5.0, help="Seconds to wait for --roundtrip PING response")
    parser.add_argument(
        "--all-symbols",
        action="store_true",
        help="Print readiness for every symbol in instruments.yaml (default: enabled only)",
    )
    parser.add_argument(
        "--roundtrip-target",
        choices=("root", "enabled-charts", "present-charts"),
        default="root",
        help="IPC directory set to PING when --roundtrip is explicitly enabled",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Trading OS Readiness Gate")
    print(f"  Mode: {'LIVE/DEMO MT5' if args.live else 'OFFLINE/SIM'}")
    print(f"  IPC:  {IPC}")
    print("=" * 60)

    opts = ReadinessOptions(
        live=args.live,
        strict_instruments=args.strict_instruments,
        instrument_gate=args.instrument_gate.strip() or None,
        chart_gate=args.chart_gate.strip() or None,
        max_heartbeat_age=args.max_heartbeat_age,
        roundtrip=args.roundtrip,
        roundtrip_timeout=args.roundtrip_timeout,
        roundtrip_target=args.roundtrip_target,
        all_symbols=args.all_symbols,
    )
    result = evaluate_readiness(ROOT, opts, ipc_dir=IPC, on_check=check)

    print("\n  Instrument readiness:")
    print("  SYMBOL   ENABLED CHART SPREAD SESSION QUOTE  RESULT")
    for symbol, status in result.instruments.items():
        if not args.all_symbols and not status.get("enabled"):
            continue
        quote_age = status.get("quote_age_sec")
        if quote_age is None:
            quote_col = "n/a   "
        elif status.get("quote_skipped"):
            quote_col = f"{int(quote_age):4d}s~"
        elif status.get("quote_ok"):
            quote_col = f"{int(quote_age):4d}s "
        else:
            quote_col = f"!{int(quote_age):3d}s"
        print(
            f"  {symbol:8s} "
            f"{'yes' if status['enabled'] else 'no ':7s} "
            f"{'yes' if status['chart_present'] else 'no ':5s} "
            f"{'ok' if status['spread_ok'] else 'no':6s} "
            f"{'ok' if status['session_ok'] else 'no':7s} "
            f"{quote_col} "
            f"{status['result']}"
        )

    if result.enabled_stocks:
        print("\n  Enabled stock CFDs:", ", ".join(result.enabled_stocks))
        print("  (~) quote age not enforced while session is closed; re-run during NY session.")

    print("=" * 60)
    if result.ok:
        print("  READINESS GATE PASSED")
    else:
        print("  READINESS GATE FAILED")
        missing_chart = any(
            status.get("enabled") and not status.get("chart_present")
            for status in result.instruments.values()
        )
        if missing_chart or args.live:
            from ops.chart_bootstrap import evaluate_bootstrap_gaps  # noqa: WPS433

            gaps = evaluate_bootstrap_gaps(max_heartbeat_age=args.max_heartbeat_age)
            summary = gaps.get("summary") or {}
            if summary.get("missing") or summary.get("stale"):
                print("\n  Chart bootstrap gaps:")
                print(
                    f"    ready={summary.get('ready')} missing={summary.get('missing')} stale={summary.get('stale')}"
                )
                actions = gaps.get("actions") or {}
                for label, action in actions.items():
                    print(f"    {label}: {action}")
    import json

    print(json.dumps(result.as_dict(), indent=2))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
