"""Fail-closed runtime safety gates for trading execution paths."""
from __future__ import annotations

import os
from pathlib import Path

VALID_TRADING_MODES = frozenset({"SIMULATION", "LIVE"})
DEFAULT_TRADING_MODE = "SIMULATION"
STOP_TRADING_FILE = "STOP_TRADING"


class RuntimeSafetyError(RuntimeError):
    """Raised when runtime safety validation fails."""


def normalize_trading_mode(value: str | None = None) -> str:
    """Return a validated trading runtime mode.

    Missing mode defaults to SIMULATION. Any present but unknown value is an
    unsafe configuration and must fail closed instead of falling through to LIVE.
    """
    raw = DEFAULT_TRADING_MODE if value is None else value.strip().upper()
    if raw not in VALID_TRADING_MODES:
        allowed = ",".join(sorted(VALID_TRADING_MODES))
        raise RuntimeSafetyError(f"runtime_mode_invalid:{raw or '<empty>'}:allowed={allowed}")
    return raw


def current_trading_mode() -> str:
    return normalize_trading_mode(os.getenv("TRADING_OS_MODE"))


def stop_trading_path(root: Path) -> Path:
    return Path(root) / STOP_TRADING_FILE


def stop_trading_active(root: Path) -> bool:
    return stop_trading_path(root).exists()


def runtime_block_reasons(root: Path, *, validate_mode: bool = True) -> list[str]:
    """Return fail-closed block reasons for the current runtime environment."""
    reasons: list[str] = []
    if stop_trading_active(root):
        reasons.append("stop_trading_active")
    if validate_mode:
        try:
            current_trading_mode()
        except RuntimeSafetyError as exc:
            reasons.append(str(exc))
    return reasons


def assert_runtime_safe(root: Path, *, validate_mode: bool = True) -> None:
    reasons = runtime_block_reasons(root, validate_mode=validate_mode)
    if reasons:
        raise RuntimeSafetyError(";".join(reasons))


def append_runtime_reasons(reasons: list[str], root: Path, *, validate_mode: bool = True) -> list[str]:
    """Append runtime block reasons in-place and return the same list."""
    reasons.extend(runtime_block_reasons(root, validate_mode=validate_mode))
    return reasons
