#!/usr/bin/env python3
"""DEPRECATED — DO NOT RUN IN PRODUCTION.

Superseded by muscle/muscle_main.py (canonical execution + response polling).
This module double-publishes cmd_out as muscle.order.filled, lacks UTF-16 IPC
handling, and races with the supervised muscle process.

Read commands sent to MT5 IPC and dispatch responses back to bus.
"""
import json, time, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/"nervous"))
from bus import publish, subscribe
from ipc_path import get_ipc_dir

IPC = get_ipc_dir()
CMD_OUT = IPC / "cmd_out.txt"


def relay_response():
    if not CMD_OUT.exists():
        return
    try:
        resp = json.loads(CMD_OUT.read_text().strip())
        publish("muscle.response", resp)
        # muscle/main may also consume; don't delete here
        publish("muscle.order.filled", resp)
    except (json.JSONDecodeError, IOError):
        pass


def run():
    while True:
        relay_response()
        time.sleep(3)


if __name__ == "__main__":
    run()
