#!/usr/bin/env python3
"""
scripts/diagnose_mt5_link.py
One-shot diagnostic to check MT5 bridge health and EA version.
"""
import json, time, sys, os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT / "nervous"))
from ipc_path import get_ipc_dir

IPC = get_ipc_dir()


def read_file_auto(path: Path):
    if not path.exists():
        return None
    raw = path.read_bytes()
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        return raw.decode('utf-16').strip()
    return raw.decode('utf-8').strip()


def diagnose():
    results = {}

    # 1. Heartbeat
    hb_text = read_file_auto(IPC / "heartbeat.txt")
    if hb_text:
        if "|alive" in hb_text:
            results["hb_format"] = "v5 (pipe-delimited)"
        elif ",alive" in hb_text:
            results["hb_format"] = "v4 (comma-delimited) — STALE EA"
        else:
            results["hb_format"] = "unknown"
        parts = hb_text.replace("\x00", "").split("|") if "|" in hb_text else hb_text.replace("\x00", "").split(",")
        try:
            hb_ts = float(parts[0])
            results["hb_age_sec"] = round(time.time() - hb_ts, 1)
        except Exception:
            results["hb_age_sec"] = "unparseable"
    else:
        results["hb_format"] = "missing"
        results["hb_age_sec"] = "n/a"

    # 2. Tick
    tick_text = read_file_auto(IPC / "tick.txt")
    if tick_text:
        parts = tick_text.split(",")
        results["tick_symbol"] = parts[0] if len(parts) > 0 else "unknown"
        results["tick_bid"] = parts[1] if len(parts) > 1 else "unknown"
    else:
        results["tick_symbol"] = "missing"

    # 3. cmd_in.txt presence
    cmd_in = IPC / "cmd_in.txt"
    results["cmd_in_exists"] = cmd_in.exists()
    if cmd_in.exists():
        results["cmd_in_content"] = read_file_auto(cmd_in)[:80]

    # 4. Test write
    test_payload = "ORDER,XAUUSD,BUY,0.01,4700.0,4730.0,TEST_DIAG"
    cmd_in.write_text(test_payload, encoding="utf-8")
    time.sleep(4)  # Wait for EA timer (3s default)
    if cmd_in.exists():
        remaining = read_file_auto(cmd_in)
        if remaining == test_payload:
            results["cmd_test"] = "NOT_CONSUMED — EA did not read cmd_in.txt (wrong path / not running / FILE_COMMON issue)"
        else:
            results["cmd_test"] = f"MODIFIED_NOT_DELETED — content changed: {remaining[:80]}"
    else:
        results["cmd_test"] = "CONSUMED — EA read and deleted cmd_in.txt"

    # 5. cmd_out.txt presence
    cmd_out = IPC / "cmd_out.txt"
    results["cmd_out_exists"] = cmd_out.exists()
    if cmd_out.exists():
        resp = read_file_auto(cmd_out)
        results["cmd_out_preview"] = resp[:200] if resp else "empty"
        # Leave it for muscle to process
    else:
        results["cmd_out_preview"] = "none"

    # 6. Clock skew
    if isinstance(results.get("hb_age_sec"), (int, float)):
        age = results["hb_age_sec"]
        if age < -300:
            results["clock_status"] = f"WSL clock LAGS MT5 by {abs(age):.0f}s — sync needed"
        elif age > 300:
            results["clock_status"] = f"WSL clock AHEAD of MT5 by {age:.0f}s"
        else:
            results["clock_status"] = f"OK (skew {age:.0f}s)"
    else:
        results["clock_status"] = "unknown"

    return results


if __name__ == "__main__":
    res = diagnose()
    print(json.dumps(res, indent=2))
