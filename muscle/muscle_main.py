#!/usr/bin/env python3
"""
muscle/muscle_main.py -- Muscle Entry Point (Mode Router)
----------------------------------------------------------
Selects between legacy single-chart muscle/main.py and
multisymbol muscle/multisymbol_router.py based on env + IPC discovery.
"""
import importlib
import os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "muscle"))
sys.path.insert(0, str(ROOT / "nervous"))
from ipc_path import get_ipc_dir
from ipc_text import read_ipc_text
from runtime_safety import assert_runtime_safe

IPC_DIR = get_ipc_dir()
CHART_PREFIX = "chart_"
ROOT_HEARTBEAT_MAX_AGE_SEC = float(os.getenv("TRADING_OS_ROOT_BRIDGE_MAX_AGE_SEC", "15"))


def is_multisymbol():
    """True if chart_* subdirectories exist and root bridge is not active.

    Stale chart folders can remain after switching back to the single root EA.
    In auto mode, prefer the active root bridge so live orders target the IPC path
    the EA is actually consuming.
    """
    if root_bridge_active():
        return False
    if not IPC_DIR.exists():
        return False
    return any(e.is_dir() and e.name.startswith(CHART_PREFIX) for e in IPC_DIR.iterdir())


def _read_auto(path: Path):
    return read_ipc_text(path)


def root_bridge_active(max_age_sec=ROOT_HEARTBEAT_MAX_AGE_SEC):
    hb = _read_auto(IPC_DIR / "heartbeat.txt")
    tick = _read_auto(IPC_DIR / "tick.txt")
    if not hb or not tick:
        return False
    try:
        stamp = float(hb.split("|")[0] if "|" in hb else hb.split(",")[0])
    except Exception:
        return False
    parts = tick.split(",")
    if len(parts) < 3:
        return False
    try:
        bid, ask = float(parts[1]), float(parts[2])
    except Exception:
        return False
    return time.time() - stamp <= max_age_sec and bid > 0 and ask >= bid


def use_multisymbol_mode():
    """Return whether the canonical execution path should use per-chart routing."""
    force = os.getenv("TRADING_OS_MULTISYMBOL", "auto").lower()
    if force == "1" or force == "true":
        return True
    if force == "0" or force == "false":
        return False
    return is_multisymbol()


def execution_module():
    """Return the canonical execution module for the current IPC topology.

    Importing callers, such as unattended demo tools, must not import
    muscle.main directly because that bypasses multisymbol chart routing and the
    durable lifecycle dedupe. This facade keeps scripts and supervisor aligned.
    """
    module_name = "muscle.multisymbol_router" if use_multisymbol_mode() else "muscle.main"
    return importlib.import_module(module_name)


def process_order_intent(intent):
    return execution_module().process_order_intent(intent)


def check_responses():
    return execution_module().check_responses()


def check_timeouts_and_queue():
    return execution_module().check_timeouts_and_queue()


def order_state():
    return execution_module().ORDER_STATE


class _OrderStateProxy:
    def get(self, *args, **kwargs):
        return order_state().get(*args, **kwargs)

    def __getitem__(self, key):
        return order_state()[key]

    def __contains__(self, key):
        return key in order_state()

    def items(self):
        return order_state().items()

    def values(self):
        return order_state().values()

    def keys(self):
        return order_state().keys()


ORDER_STATE = _OrderStateProxy()


if __name__ == "__main__":
    assert_runtime_safe(ROOT)
    if use_multisymbol_mode():
        print("[muscle] Multisymbol mode detected — using multisymbol_router.py")
        execution_module().run()
    else:
        print("[muscle] Legacy single-chart mode — using main.py")
        execution_module().run()
