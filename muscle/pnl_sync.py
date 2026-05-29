#!/usr/bin/env python3
"""
muscle/pnl_sync.py — MT5 Position/PnL Reconciler
-------------------------------------------------
Consumes MT5 position snapshots from EA responses or data_out.txt and publishes
position lifecycle/PnL events. This module is intentionally testable offline so
readiness checks do not require a live broker connection.
"""
import json
import os
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish  # noqa: E402
from ipc_path import get_ipc_dir  # noqa: E402
from bridge.mt5_ipc_protocol import CommandSlotBusy, IPCPaths, ResponseTimeout, get_positions  # noqa: E402

IPC_DIR = get_ipc_dir()
CMD_FILE = IPC_DIR / "cmd_in.txt"
RESP_FILE = IPC_DIR / "cmd_out.txt"
DATA_FILE = IPC_DIR / "data_out.txt"
STATE_FILE = ROOT / "muscle" / ".positions_state.json"

TRACKED_POSITIONS = {}  # ticket -> normalized position dict


def _read_file_auto(path: Path):
    if not path.exists():
        return None
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace").lstrip("\ufeff").strip()
    return raw.decode("utf-8", errors="replace").strip()


def _write_cmd(text: str):
    IPC_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CMD_FILE.with_suffix(f".tmp.{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(b"\xff\xfe")
        f.write(text.encode("utf-16-le"))
        f.write(b"\n")
    tmp.replace(CMD_FILE)


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_position(raw):
    ticket = str(raw.get("ticket") or raw.get("id") or "")
    side = str(raw.get("side") or raw.get("type") or "").upper()
    if side in ("0", "BUY"):
        side = "BUY"
    elif side in ("1", "SELL"):
        side = "SELL"
    return {
        "ticket": ticket,
        "order_id": str(raw.get("order_id") or raw.get("comment") or ""),
        "symbol": str(raw.get("symbol") or ""),
        "side": side,
        "qty": _to_float(raw.get("qty", raw.get("volume"))),
        "open_price": _to_float(raw.get("open_price", raw.get("price_open"))),
        "current_price": _to_float(raw.get("current_price", raw.get("price_current"))),
        "sl": _to_float(raw.get("sl")),
        "tp": _to_float(raw.get("tp")),
        "profit": _to_float(raw.get("profit")),
        "swap": _to_float(raw.get("swap")),
        "commission": _to_float(raw.get("commission")),
        "magic": int(_to_float(raw.get("magic"), 0)),
        "ts": _to_float(raw.get("ts"), time.time()),
    }


def parse_positions_response(text):
    """Parse EA position snapshots.

    Supported formats:
    - JSON: {"type":"positions","positions":[...]}
    - Pipe response: CID|OK|positions=<json-array>
    - Legacy data_out lines: POSITION|ticket|symbol|volume|side|open|current|sl|tp|profit|time
    """
    if not text:
        return []
    text = text.strip()
    if not text:
        return []

    if text.startswith("{"):
        payload = json.loads(text)
        if payload.get("type") not in (None, "positions", "position_snapshot"):
            return []
        return [_normalize_position(p) for p in payload.get("positions", [])]

    if "|positions=" in text:
        positions_json = text.split("|positions=", 1)[1]
        return [_normalize_position(p) for p in json.loads(positions_json)]

    positions = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if not parts or parts[0] != "POSITION" or len(parts) < 10:
            continue
        positions.append(_normalize_position({
            "ticket": parts[1],
            "symbol": parts[2],
            "volume": parts[3],
            "side": parts[4],
            "open_price": parts[5],
            "current_price": parts[6],
            "sl": parts[7],
            "tp": parts[8],
            "profit": parts[9],
            "ts": time.time(),
        }))
    return positions


def parse_account_snapshot(text: str | None = None) -> dict | None:
    """Parse ACCOUNT line from EA data_out snapshot.

    Format: ACCOUNT|balance|equity|margin|margin_free|margin_level|login|server
    """
    if text is None:
        text = _read_file_auto(DATA_FILE)
    if not text:
        return None
    for line in text.splitlines():
        parts = line.strip().split("|")
        if not parts or parts[0] != "ACCOUNT" or len(parts) < 7:
            continue
        margin_level = _to_float(parts[5], default=-1.0)
        return {
            "balance": round(_to_float(parts[1]), 2),
            "equity": round(_to_float(parts[2]), 2),
            "margin_used": round(_to_float(parts[3]), 2),
            "margin_free": round(_to_float(parts[4]), 2),
            "margin_level_pct": round(margin_level, 2) if margin_level >= 0 else None,
            "login": parts[6] if len(parts) > 6 else None,
            "server": parts[7] if len(parts) > 7 else None,
        }
    return None


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(positions):
    STATE_FILE.write_text(json.dumps({"positions": positions, "ts": time.time()}, indent=2, sort_keys=True))


def reconcile_positions(snapshot, previous=None, publish_events=True):
    """Compare MT5 snapshot against previous known positions."""
    previous = dict(TRACKED_POSITIONS if previous is None else previous)
    current = {str(p["ticket"]): p for p in snapshot if p.get("ticket")}
    opened = [p for ticket, p in current.items() if ticket not in previous]
    closed = [p for ticket, p in previous.items() if ticket not in current]
    updated = [p for ticket, p in current.items() if ticket in previous]
    floating_pnl = round(sum(p.get("profit", 0.0) + p.get("swap", 0.0) + p.get("commission", 0.0) for p in current.values()), 2)

    report = {
        "ts": time.time(),
        "open_count": len(current),
        "opened_count": len(opened),
        "closed_count": len(closed),
        "floating_pnl": floating_pnl,
        "positions": list(current.values()),
        "opened": opened,
        "closed": closed,
        "updated_count": len(updated),
    }

    TRACKED_POSITIONS.clear()
    TRACKED_POSITIONS.update(current)
    save_state(current)

    if publish_events:
        publish("position.reconcile", {k: v for k, v in report.items() if k != "positions"})
        for pos in opened:
            publish("position.opened", pos)
        for pos in closed:
            publish("position.closed", pos)
        account = parse_account_snapshot()
        pnl_payload = {"floating_pnl": floating_pnl, "open_count": len(current)}
        if account:
            pnl_payload["equity"] = account.get("equity")
            pnl_payload["balance"] = account.get("balance")
            publish(
                "portfolio.equity",
                {
                    "ts": report["ts"],
                    "equity": account.get("equity"),
                    "balance": account.get("balance"),
                    "floating_pnl": floating_pnl,
                    "open_count": len(current),
                },
            )
        publish("position.pnl", pnl_payload)
    return report


def query_positions(timeout_sec=5):
    """Request a fresh EA position snapshot and return parsed positions if available."""
    paths = IPCPaths.from_root(IPC_DIR)
    try:
        return [_normalize_position(p) for p in get_positions(paths, timeout_sec=timeout_sec)]
    except CommandSlotBusy as exc:
        publish("position.reconcile.deferred", {"reason": "command_slot_busy", "error": str(exc)})
        return None
    except ResponseTimeout as exc:
        publish("position.reconcile.deferred", {"reason": "response_timeout", "error": str(exc)})
        return None
    except Exception as exc:
        publish("position.reconcile.error", {"error": f"query_positions_failed: {exc}"})
        return None


def snapshot_from_data_file():
    text = _read_file_auto(DATA_FILE)
    return parse_positions_response(text) if text else []


def check_once(use_command=True, publish_events=True):
    positions = query_positions() if use_command else None
    source = "GET_POSITIONS"
    if positions is None:
        positions = snapshot_from_data_file()
        source = "data_out"
    report = reconcile_positions(positions, publish_events=publish_events)
    report["source"] = source
    return report


def run():
    state = load_state().get("positions", {})
    TRACKED_POSITIONS.update(state)
    while True:
        report = check_once(use_command=True, publish_events=True)
        print(f"[pnl_sync] source={report['source']} open={report['open_count']} floating_pnl={report['floating_pnl']}", flush=True)
        time.sleep(30)


if __name__ == "__main__":
    run()
