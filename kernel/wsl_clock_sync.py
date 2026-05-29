#!/usr/bin/env python3
"""
kernel/wsl_clock_sync.py
Sync WSL clock to MT5 heartbeat timestamp using wsl.exe interop.
"""
import os
import sys
import subprocess
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from paths import ipc_dir  # noqa: E402

DRIFT_THRESHOLD_SEC = 60.0


def get_hb_epoch(ipc_dir):
    hb_file = os.path.join(ipc_dir, "heartbeat.txt")
    if not os.path.exists(hb_file):
        return None
    try:
        with open(hb_file, "rb") as f:
            raw = f.read()
        if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
            text = raw.decode('utf-16').strip()
        else:
            text = raw.decode('utf-8').strip()
        # v5 pipe or v4 comma
        delim = "|" if "|" in text else ","
        parts = text.split(delim)
        if len(parts) >= 2:
            return float(parts[0])
    except Exception:
        pass
    return None


def run_as_root(cmd_list):
    """Run command inside this WSL distro as root via wsl.exe interop."""
    # wsl.exe -u root -e COMMAND
    wsl = "/mnt/c/WINDOWS/system32/wsl.exe"
    args = [wsl, "-u", "root", "-e"] + cmd_list
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    return result.returncode == 0, result.stdout, result.stderr


def sync_clock(target_epoch):
    dt_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(target_epoch))
    ok, out, err = run_as_root(["date", "-u", "-s", dt_str])
    if ok:
        return True, f"Synced WSL clock to {dt_str} UTC"
    return False, f"Failed: {err or out}"


def main():
    hb_ts = get_hb_epoch(str(ipc_dir()))
    if hb_ts is None:
        print("No heartbeat available, skipping.")
        sys.exit(0)

    now = time.time()
    drift = hb_ts - now

    if abs(drift) > DRIFT_THRESHOLD_SEC:
        print(f"Drift detected: {drift:.1f}s. Syncing...")
        ok, msg = sync_clock(hb_ts)
        print(msg)
        sys.exit(0 if ok else 1)
    else:
        print(f"Clock OK: drift={drift:.1f}s")
        sys.exit(0)


if __name__ == "__main__":
    main()
