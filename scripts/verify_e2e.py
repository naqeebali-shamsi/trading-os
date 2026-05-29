#!/usr/bin/env python3
"""
scripts/verify_e2e.py -- End-to-End Bridge Verification (V2)
------------------------------------------------------------
Tests the WSL->junction->Windows->MT5 bridge path.

Modes:
  --sim    (default) Simulate MT5 by writing files directly
  --live   Require a real MT5 EA heartbeat (fresh within last 10s)

Exit code 0 = all passed, 1 = any failed.
"""
import argparse, json, os, sys, time
from pathlib import Path

# ── bootstrap repo root into sys.path ──────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sensory.main import read_heartbeat, read_tick  # noqa
from muscle.muscle_main import process_order_intent, check_responses, ORDER_STATE  # noqa
from ipc_path import get_ipc_dir  # noqa

IPC = get_ipc_dir()


def check(name, cond, detail=""):
    marker = "[PASS]" if cond else "[FAIL]"
    print(f"  {marker} {name} {detail}")
    return cond


def test_ipc_dir_exists():
    return check("IPC directory exists", IPC.exists(), str(IPC))


def test_heartbeat_simulated():
    """Simulate MT5 heartbeat by writing directly to IPC dir."""
    hb = IPC / "heartbeat.txt"
    now = int(time.time())
    hb.write_text(f"{now}|alive")
    time.sleep(0.1)

    result = read_heartbeat()
    if result is None:
        return check("sensory reads heartbeat", False, "read_heartbeat returned None")
    age = time.time() - result["ts"]
    return check("sensory reads heartbeat", age < 5, f"age={age:.1f}s")


def test_tick_simulated():
    tick = IPC / "tick.txt"
    tick.write_text("EURUSD,1.08500,1.08520,1778197537\n")
    time.sleep(0.1)

    result = read_tick()
    ok = result is not None and result.get("symbol") == "EURUSD"
    return check("sensory reads tick", ok, str(result))


def test_muscle_csv():
    intent = {
        "order_id": "e2e_002",
        "symbol": "EURUSD",
        "side": "BUY",
        "qty": 0.1,
        "sl": 1.08,
        "tp": 1.09,
        "mode_check": True,
    }
    process_order_intent(intent)

    cmd = IPC / "cmd_in.txt"
    ok = cmd.exists()
    text = cmd.read_text().strip() if ok else ""
    return check("muscle writes CSV", ok and text.startswith("ORDER,EURUSD,BUY"), text)


def test_muscle_json_response():
    resp = IPC / "cmd_out.txt"
    resp.write_text(json.dumps({
        "type": "fill",
        "order_id": "e2e_002",
        "retcode": 10009,
        "fill_price": 1.08501,
        "symbol": "EURUSD",
        "side": "BUY",
        "qty": 0.1,
    }))
    time.sleep(0.5)

    check_responses()

    st = ORDER_STATE.get("e2e_002", {})
    return check("muscle reads JSON response", st.get("status") == "filled", str(st))


def test_obsidian_write():
    obs = ROOT / "vault" / "06-System"
    obs.mkdir(parents=True, exist_ok=True)
    note = obs / f"{time.strftime('%Y-%m-%d')}.md"
    note.write_text("# E2E Test\nAutomated checkpoint.\n", encoding="utf-8")
    return check("Obsidian write", note.exists(), str(note))


# ────────────────────────────────────────────────────────
# LIVE MODE: Real MT5 EA required
# ────────────────────────────────────────────────────────

def test_live_heartbeat():
    hb = IPC / "heartbeat.txt"
    if not hb.exists():
        return check("live heartbeat exists", False, "No heartbeat.txt -- EA not attached?")

    result = read_heartbeat()
    if result is None:
        return check("live heartbeat parseable", False, "Could not parse heartbeat content")

    age = time.time() - result["ts"]
    return check("live heartbeat fresh", age < 10,
                 f"age={age:.1f}s (ts={result['ts']})")


def test_live_tick():
    result = read_tick()
    ok = result is not None and "bid" in result and "ask" in result
    return check("live tick readable", ok, str(result))


def test_live_ea_roundtrip():
    """Send a test command and wait for response."""
    cmd = IPC / "cmd_in.txt"
    resp = IPC / "cmd_out.txt"
    # Clean slate
    if cmd.exists():
        cmd.unlink()
    if resp.exists():
        resp.unlink()

    intent = {
        "order_id": "e2e_roundtrip",
        "symbol": "XAUUSD",
        "side": "BUY",
        "qty": 0.01,
        "sl": 0,
        "tp": 0,
        "mode_check": True,
    }
    process_order_intent(intent)

    # Wait up to 15s for EA response
    for i in range(15):
        check_responses()
        st = ORDER_STATE.get("e2e_roundtrip", {})
        if st.get("status") in ("filled", "rejected", "timeout"):
            break
        time.sleep(1)

    st = ORDER_STATE.get("e2e_roundtrip", {})
    detail = f"status={st.get('status')} retcode={st.get('retcode')} error={st.get('error_type')}"
    return check("EA roundtrip", st.get("status") in ("filled", "rejected"), detail)


def main():
    parser = argparse.ArgumentParser(description="Trading OS E2E Verification")
    parser.add_argument("--live", action="store_true", help="Require real MT5 EA (not simulation)")
    parser.add_argument("--sim", action="store_true", help="Force simulation mode (default)")
    args = parser.parse_args()

    print("=" * 50)
    print("  Trading OS E2E Verification")
    print(f"  Mode: {'LIVE (real MT5)' if args.live else 'SIMULATION'}")
    print(f"  IPC:  {IPC}")
    print("=" * 50)

    all_ok = True
    all_ok &= test_ipc_dir_exists()

    if args.live:
        all_ok &= test_live_heartbeat()
        all_ok &= test_live_tick()
        all_ok &= test_live_ea_roundtrip()
    else:
        all_ok &= test_heartbeat_simulated()
        all_ok &= test_tick_simulated()
        all_ok &= test_muscle_csv()
        all_ok &= test_muscle_json_response()
        all_ok &= test_obsidian_write()

    print("=" * 50)
    if all_ok:
        print("  ALL CHECKS PASSED")
        if not args.live:
            print("  Run with --live to verify real MT5 EA bridge.")
    else:
        print("  SOME CHECKS FAILED")
        print("  Inspect logs/ and MT5 Experts tab.")
    print("=" * 50)

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
