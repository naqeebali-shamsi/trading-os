#!/usr/bin/env python3
"""Circuit-breaker halt — portable entry point for Unix and Windows.

Sets ``STOP_TRADING``, stops the supervisor when found, and publishes an audit
event on the nervous bus.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from paths import repo_root, stop_trading_path  # noqa: E402
from ops.process_utils import find_supervisor_pids, stop_supervisor_processes  # noqa: E402


def trigger_emergency_stop(reason: str = "manual_emergency", *, stop_processes: bool = True) -> dict:
    root = repo_root()
    stop_file = stop_trading_path(root)
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    stop_file.write_text(f"{reason} {stamp}\n", encoding="utf-8")

    stopped: list[int] = []
    running = find_supervisor_pids()
    if stop_processes and running:
        stopped = stop_supervisor_processes()

    from bus import publish  # noqa: WPS433 — runtime import after sys.path setup

    publish(
        "alert.routed",
        {
            "severity": "critical",
            "source": "emergency_stop",
            "message": f"Emergency stop triggered: {reason}",
            "ts": time.time(),
            "supervisor_pids": running,
            "stopped_pids": stopped,
        },
    )

    return {
        "ok": True,
        "reason": reason,
        "stop_file": str(stop_file),
        "supervisor_pids": running,
        "stopped_pids": stopped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Halt Trading OS and set STOP_TRADING.")
    parser.add_argument("reason", nargs="?", default="manual_emergency")
    parser.add_argument(
        "--no-kill-supervisor",
        action="store_true",
        help="Only set STOP_TRADING; do not terminate supervisor processes.",
    )
    args = parser.parse_args(argv)

    print(f"[!] EMERGENCY STOP: {args.reason}")
    result = trigger_emergency_stop(args.reason, stop_processes=not args.no_kill_supervisor)

    if result["supervisor_pids"]:
        print(f"[*] Supervisor PIDs: {result['supervisor_pids']}")
    if result["stopped_pids"]:
        print(f"[*] Stopped PIDs: {result['stopped_pids']}")

    print("[ok] STOP_TRADING flag set. System halted.")
    print(f"     To resume: remove {result['stop_file']} and restart supervisor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
