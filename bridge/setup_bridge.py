#!/usr/bin/env python3
"""
Setup the MT5 bridge: detects active terminal, copies .ex5,
creates IPC junction at GLOBAL Common\\Files (where MQL5 FILE_COMMON resolves).

Run as administrator on Windows. Uses TRADING_OS_ROOT / paths.repo_root().
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paths import ipc_dir, repo_root  # noqa: E402

TERMINAL_ROOT = os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal")
GLOBAL_COMMON = os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\Common\Files")


def _latest_log_mtime(term_dir: str) -> float:
    logs = glob.glob(os.path.join(term_dir, "logs", "*.log"))
    if not logs:
        return 0.0
    return max(os.path.getmtime(f) for f in logs)


def setup_bridge(*, root: Path | None = None, require_ex5: bool = True) -> dict:
    """Configure MT5 bridge junction and copy EA. Returns status dict."""
    root = Path(root or repo_root()).resolve()
    ipc_win = ipc_dir(create=True)
    src_ex5 = root / "bridge" / "FileBridgeEA_Windows.ex5"
    src_mq5 = root / "bridge" / "FileBridgeEA_Windows.mq5"

    if require_ex5 and not src_ex5.exists():
        if src_mq5.exists():
            raise FileNotFoundError(
                f"Compiled EA missing: {src_ex5}\n"
                "Compile bridge/FileBridgeEA_Windows.mq5 in MetaEditor first, "
                "or run bridge/compile_on_windows.bat as Administrator."
            )
        raise FileNotFoundError(f"EA not found: {src_ex5}")

    terminals = [
        d
        for d in glob.glob(os.path.join(TERMINAL_ROOT, "*"))
        if os.path.isdir(d) and os.path.basename(d) != "Common"
    ]
    if not terminals:
        raise RuntimeError("No MetaTrader 5 terminal directories found.")

    active = max(terminals, key=_latest_log_mtime)
    experts = os.path.join(active, "MQL5", "Experts")
    junction = os.path.join(GLOBAL_COMMON, "trading-os")

    copied_ex5 = False
    if src_ex5.exists():
        os.makedirs(experts, exist_ok=True)
        shutil.copy2(src_ex5, os.path.join(experts, "FileBridgeEA_Windows.ex5"))
        copied_ex5 = True

    os.makedirs(GLOBAL_COMMON, exist_ok=True)
    if os.path.exists(junction):
        # Junctions report as directories; rmdir removes junction only.
        os.rmdir(junction)

    result = subprocess.run(
        ["cmd", "/c", f'mklink /J "{junction}" "{ipc_win}"'],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PermissionError(
            f"IPC junction failed ({junction} -> {ipc_win}). "
            f"Run as Administrator. {detail}"
        )

    heartbeat_visible = os.path.exists(os.path.join(junction, "heartbeat.txt"))
    return {
        "ok": True,
        "terminal": active,
        "experts_dir": experts,
        "junction": junction,
        "ipc_dir": str(ipc_win),
        "copied_ex5": copied_ex5,
        "heartbeat_visible": heartbeat_visible,
        "terminal_last_log": datetime.fromtimestamp(_latest_log_mtime(active)).isoformat(),
    }


def main() -> int:
    print(f"[ROOT] {repo_root()}")
    print(f"[IPC ] {ipc_dir()}")
    try:
        status = setup_bridge()
    except Exception as exc:
        print(f"[!] Bridge setup failed: {exc}")
        return 1

    print(f"[ACTIVE] {status['terminal']}")
    print(f"  last log: {status['terminal_last_log']}")
    if status["copied_ex5"]:
        print(f"[COPY] FileBridgeEA_Windows.ex5 -> {status['experts_dir']}")
    print(f"[JUNCTION] {status['junction']} -> {status['ipc_dir']}")
    if status["heartbeat_visible"]:
        print("[TEST] heartbeat.txt visible through junction")
    print("\n" + "=" * 48)
    print("DONE — restart MT5, attach EA, enable algo trading")
    print("=" * 48)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
