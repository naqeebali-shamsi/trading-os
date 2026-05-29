#!/usr/bin/env python3
"""Execute audited MCP position commands after immune.position.pass."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
sys.path.insert(0, str(ROOT / "muscle"))

from bus import current_seq, publish, subscribe  # noqa: E402
from ipc_path import get_ipc_dir  # noqa: E402
from runtime_safety import current_trading_mode, runtime_block_reasons  # noqa: E402


def _position_by_ticket(ticket: int | str) -> dict | None:
    from muscle import pnl_sync

    ticket_s = str(ticket)
    for pos in (pnl_sync.load_state().get("positions") or {}).values():
        if str(pos.get("ticket")) == ticket_s:
            return pos
    return None


def _write_chart_cmd(chart: str, line: str) -> None:
    from muscle.multisymbol_router import _chart_lock, _write_file

    cmd_file = Path(get_ipc_dir()) / chart / "cmd_in.txt"
    lock = _chart_lock(chart)
    with lock:
        if cmd_file.exists():
            raise RuntimeError(f"cmd_busy:{chart}")
        _write_file(cmd_file, line)


def execute_position_command(command: dict) -> dict:
    if not command.get("immune_approved"):
        publish(
            "muscle.position.rejected",
            {"command_id": command.get("command_id"), "reason": "not_immune_approved", "command": command},
        )
        return {"ok": False, "reason": "not_immune_approved"}

    runtime_reasons = runtime_block_reasons(ROOT)
    if runtime_reasons:
        publish(
            "muscle.position.rejected",
            {
                "command_id": command.get("command_id"),
                "reason": "runtime_block",
                "reasons": runtime_reasons,
            },
        )
        return {"ok": False, "reason": "runtime_block", "reasons": runtime_reasons}

    command_id = command.get("command_id", "unknown")
    action = command.get("action")
    mode = current_trading_mode()

    if action == "close_all":
        from muscle.multisymbol_router import discover_charts

        charts = discover_charts()
        if not charts:
            publish(
                "muscle.position.rejected",
                {"command_id": command_id, "reason": "no_charts", "action": action},
            )
            return {"ok": False, "reason": "no_charts"}

        if mode == "SIMULATION":
            publish(
                "muscle.position.ack",
                {"command_id": command_id, "action": action, "charts": charts, "simulated": True},
            )
            return {"ok": True, "simulated": True, "charts": charts}

        sent = []
        for chart in charts:
            try:
                _write_chart_cmd(chart, "CLOSE_ALL\n")
                sent.append(chart)
            except Exception as exc:
                publish(
                    "muscle.position.error",
                    {"command_id": command_id, "chart": chart, "error": str(exc), "action": action},
                )
        publish("muscle.position.sent", {"command_id": command_id, "action": action, "charts": sent})
        publish("muscle.position.ack", {"command_id": command_id, "action": action, "charts": sent})
        return {"ok": bool(sent), "charts": sent}

    if action in {"close_position", "modify"}:
        ticket = command.get("ticket")
        pos = _position_by_ticket(ticket)
        if not pos:
            publish(
                "muscle.position.rejected",
                {"command_id": command_id, "reason": "ticket_not_found", "ticket": ticket},
            )
            return {"ok": False, "reason": "ticket_not_found"}

        from muscle.multisymbol_router import chart_dir_for_symbol

        chart = chart_dir_for_symbol(pos.get("symbol") or "")
        if not chart:
            publish(
                "muscle.position.rejected",
                {"command_id": command_id, "reason": "chart_not_found", "symbol": pos.get("symbol")},
            )
            return {"ok": False, "reason": "chart_not_found"}

        if action == "modify":
            sl = float(command.get("sl") or 0.0)
            tp = float(command.get("tp") or 0.0)
            line = f"MODIFY,{ticket},{sl},{tp},{command_id}\n"
        else:
            line = f"CLOSE,{ticket},{command_id}\n"

        if mode == "SIMULATION":
            publish(
                "muscle.position.ack",
                {
                    "command_id": command_id,
                    "action": action,
                    "chart": chart,
                    "symbol": pos.get("symbol"),
                    "simulated": True,
                    "cmd": line.strip(),
                },
            )
            return {"ok": True, "simulated": True, "chart": chart}

        try:
            _write_chart_cmd(chart, line)
        except Exception as exc:
            publish(
                "muscle.position.error",
                {"command_id": command_id, "chart": chart, "error": str(exc), "action": action},
            )
            return {"ok": False, "reason": str(exc)}

        publish(
            "muscle.position.sent",
            {"command_id": command_id, "action": action, "chart": chart, "cmd": line.strip()},
        )
        publish(
            "muscle.position.ack",
            {"command_id": command_id, "action": action, "chart": chart, "cmd": line.strip()},
        )
        return {"ok": True, "chart": chart}

    publish(
        "muscle.position.rejected",
        {"command_id": command_id, "reason": "unknown_action", "action": action},
    )
    return {"ok": False, "reason": "unknown_action"}


def run() -> None:
    last_seq = current_seq()
    while True:
        events = subscribe("immune.position.pass", since_seq=last_seq)
        for event in events:
            seq = event.get("seq", 0)
            if seq > last_seq:
                last_seq = seq
            payload = event.get("payload") or {}
            command = payload.get("command") or {}
            execute_position_command(command)
        time.sleep(1)


if __name__ == "__main__":
    run()
