#!/usr/bin/env python3
"""Shared IPC path resolver — WSL/Windows cross-compatible."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from paths import ipc_dir  # noqa: E402


def get_ipc_dir():
    """Return the shared IPC directory. Override with ``TRADING_OS_IPC``."""
    return ipc_dir()
