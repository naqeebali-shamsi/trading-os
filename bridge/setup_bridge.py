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


def _junction_target(junction: str) -> str | None:
    """Return normalized junction target path, or None if not a mount point."""
    proc = subprocess.run(
        ["fsutil", "reparsepoint", "query", junction],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or "Mount Point" not in (proc.stdout or ""):
        return None
    for line in (proc.stdout or "").splitlines():
        if line.strip().startswith("Print Name:"):
            return os.path.normcase(os.path.normpath(line.split(":", 1)[1].strip()))
    return None


def _junction_points_to(junction: str, target: str) -> bool:
    current = _junction_target(junction)
    if current is None:
        return False
    return current == os.path.normcase(os.path.normpath(target))


def _clear_bridge_link(path: str) -> None:
    """Remove an existing junction or replace a stale real directory."""
    if not os.path.lexists(path):
        return
    try:
        os.rmdir(path)
        return
    except OSError:
        shutil.rmtree(path, ignore_errors=True)


def _create_junction(junction: str, target: str) -> None:
    """Create a directory junction; argv form avoids cmd quoting bugs with spaces."""
    ipc_target = os.path.normpath(target)
    if not os.path.isdir(ipc_target):
        raise FileNotFoundError(f"IPC target directory missing: {ipc_target}")

    if _junction_points_to(junction, ipc_target):
        return

    _clear_bridge_link(junction)

    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", junction, ipc_target],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PermissionError(
            f"IPC junction failed ({junction} -> {ipc_target}). "
            f"Run as Administrator. {detail}"
        )


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
    _create_junction(junction, str(ipc_win))

    heartbeat_visible = os.path.exists(os.path.join(junction, "heartbeat.txt"))
    return {
        "ok": True,
        "terminal": active,
        "experts_dir": experts,
        "junction": junction,
        "ipc_dir": os.path.normpath(str(ipc_win)),
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
