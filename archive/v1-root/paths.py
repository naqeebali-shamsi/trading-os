#!/usr/bin/env python3
"""Canonical filesystem layout for Trading OS.

Resolve repo-local paths here instead of hardcoding machine-specific mount
points (for example ``/mnt/e/GROWTH/...``).

Environment overrides:
  TRADING_OS_ROOT  — repository root (``trading-os/``)
  TRADING_OS_IPC   — shared WSL/Windows IPC directory
  TRADING_OS_VAULT — Obsidian vault root (default: ``<workspace>/vault``)
"""
from __future__ import annotations

import os
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent


def repo_root() -> Path:
    """Trading-os repository root."""
    return Path(os.environ.get("TRADING_OS_ROOT", str(_PACKAGE_ROOT))).resolve()


def ipc_dir(*, create: bool = True) -> Path:
    """Shared IPC directory (WSL + Windows junction target)."""
    path = Path(os.environ.get("TRADING_OS_IPC", str(repo_root() / "ipc"))).resolve()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def nervous_dir() -> Path:
    return repo_root() / "nervous"


def logs_dir(*, create: bool = True) -> Path:
    path = repo_root() / "logs"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def archive_dir(*, create: bool = True) -> Path:
    path = logs_dir(create=create) / "archive"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def vault_dir(*, create: bool = False) -> Path:
    """Obsidian vault; default is ``vault/`` beside the workspace parent of ``trading-os``."""
    if os.environ.get("TRADING_OS_VAULT"):
        path = Path(os.environ["TRADING_OS_VAULT"])
    else:
        path = repo_root().parent / "vault"
    path = path.resolve()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def stop_trading_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "STOP_TRADING"
