#!/usr/bin/env python3
"""Safe no-trade helpers for the canonical MT5 file IPC protocol.

The helpers in this module only emit read-only/no-trade commands and never delete
or truncate response files. They are intended for readiness checks and offline
validation before any trading command path is canonicalized.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # Keep this module importable in isolated unit tests.
    from nervous.ipc_path import get_ipc_dir
except Exception:  # pragma: no cover
    get_ipc_dir = None  # type: ignore

NO_TRADE_COMMANDS = frozenset({"PING", "GET_STATUS", "STATUS", "GET_POSITIONS", "GET_SYMBOL_INFO", "SEARCH_SYMBOLS"})
DEFAULT_TIMEOUT_SEC = 5.0
POLL_INTERVAL_SEC = 0.05
SLOT_CLEAR_TIMEOUT_SEC = 1.0


class MT5IPCProtocolError(RuntimeError):
    """Base error for canonical no-trade IPC helpers."""


class CommandSlotBusy(MT5IPCProtocolError):
    """Raised when cmd_in.txt already contains an unconsumed command."""


class ResponseTimeout(MT5IPCProtocolError):
    """Raised when no matching response arrives before timeout."""


@dataclass(frozen=True)
class IPCPaths:
    """Canonical IPC file layout shared by Python and MT5."""

    root: Path
    cmd_in: Path
    cmd_out: Path
    data_out: Path
    heartbeat: Path
    tick: Path

    @classmethod
    def from_root(cls, root: Path) -> "IPCPaths":
        root = Path(root)
        return cls(
            root=root,
            cmd_in=root / "cmd_in.txt",
            cmd_out=root / "cmd_out.txt",
            data_out=root / "data_out.txt",
            heartbeat=root / "heartbeat.txt",
            tick=root / "tick.txt",
        )


def canonical_ipc_root(explicit: Optional[Path] = None, *, create: bool = True) -> Path:
    """Resolve the canonical shared IPC root.

    Precedence:
    1. explicit path argument
    2. TRADING_OS_IPC environment variable via nervous.ipc_path.get_ipc_dir()
    3. repository default from nervous.ipc_path
    """
    if explicit is not None:
        root = Path(explicit)
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root
    if get_ipc_dir is None:
        env = os.getenv("TRADING_OS_IPC")
        if not env:
            raise MT5IPCProtocolError("TRADING_OS_IPC is not set and nervous.ipc_path is unavailable")
        root = Path(env)
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root
    return Path(get_ipc_dir())


def new_cid(prefix: str = "py") -> str:
    """Create an opaque correlation id safe for CSV/pipe file protocols."""
    return f"{prefix}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:10]}"


def encode_command(action: str, cid: Optional[str] = None, *fields: str) -> str:
    """Encode a canonical no-trade command as CSV: ACTION,CID."""
    action = action.upper().strip()
    if action not in NO_TRADE_COMMANDS:
        raise ValueError(f"refusing non-readiness command: {action}")
    cid = cid or new_cid()
    if any(ch in cid for ch in ",|\r\n"):
        raise ValueError("cid must not contain comma, pipe, or newline")
    clean_fields = []
    for field in fields:
        value = str(field).strip()
        if any(ch in value for ch in ",|\r\n"):
            raise ValueError("command fields must not contain comma, pipe, or newline")
        clean_fields.append(value)
    suffix = ("," + ",".join(clean_fields)) if clean_fields else ""
    return f"{action},{cid}{suffix}\r\n"


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace").lstrip("\ufeff")
    return raw.decode("utf-8", errors="replace")


def _slot_has_command(path: Path) -> bool:
    return bool(_read_text(path).strip())


def write_command_no_overwrite(paths: IPCPaths, action: str, cid: Optional[str] = None, *fields: str) -> str:
    """Atomically write a no-trade command only when cmd_in.txt is empty/missing."""
    paths.root.mkdir(parents=True, exist_ok=True)
    if _slot_has_command(paths.cmd_in):
        wait_for_command_slot_clear(paths)
    if _slot_has_command(paths.cmd_in):
        raise CommandSlotBusy(f"command slot occupied: {paths.cmd_in}")
    cid = cid or new_cid()
    payload = encode_command(action, cid, *fields)
    tmp = paths.cmd_in.with_name(f".{paths.cmd_in.name}.{os.getpid()}.{cid}.tmp")
    tmp.write_bytes(payload.encode("utf-16"))
    # replace is safe here because we already verified missing/empty and the EA
    # consumes by deleting the file. We never overwrite a non-empty command.
    if _slot_has_command(paths.cmd_in):
        tmp.unlink(missing_ok=True)
        raise CommandSlotBusy(f"command slot occupied: {paths.cmd_in}")
    tmp.replace(paths.cmd_in)
    return cid


def response_matches_cid(text: str, cid: str) -> bool:
    """Return True when a response text explicitly carries cid."""
    text = text.strip()
    if not text:
        return False
    for line in reversed([line.strip() for line in text.splitlines() if line.strip()]):
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(payload.get("cid") or payload.get("correlation_id") or "") == cid:
                return True
        parts = line.split("|")
        if len(parts) >= 2 and parts[0] == cid:
            return True
        csv_parts = line.split(",")
        if len(csv_parts) >= 2 and csv_parts[1] == cid:
            return True
    return False


def wait_for_response(paths: IPCPaths, cid: str, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> str:
    """Wait for a matching cid in cmd_out.txt without deleting/truncating it."""
    deadline = time.time() + timeout_sec
    while time.time() <= deadline:
        text = _read_text(paths.cmd_out)
        if response_matches_cid(text, cid):
            return text.strip()
        time.sleep(POLL_INTERVAL_SEC)
    raise ResponseTimeout(f"no response for cid={cid} within {timeout_sec:.1f}s")


def wait_for_command_slot_clear(paths: IPCPaths, timeout_sec: float = SLOT_CLEAR_TIMEOUT_SEC) -> None:
    """Give the EA a short grace period to delete cmd_in after responding."""
    deadline = time.time() + timeout_sec
    while time.time() <= deadline:
        if not _slot_has_command(paths.cmd_in):
            return
        time.sleep(POLL_INTERVAL_SEC)


def roundtrip(paths: IPCPaths, action: str, timeout_sec: float = DEFAULT_TIMEOUT_SEC, cid: Optional[str] = None, *fields: str) -> str:
    """Send one no-trade command and return the matching raw response."""
    cid = write_command_no_overwrite(paths, action, cid, *fields)
    response = wait_for_response(paths, cid, timeout_sec=timeout_sec)
    wait_for_command_slot_clear(paths)
    return response


def ping(paths: IPCPaths, timeout_sec: float = DEFAULT_TIMEOUT_SEC, cid: Optional[str] = None) -> str:
    return roundtrip(paths, "PING", timeout_sec=timeout_sec, cid=cid)


def status(paths: IPCPaths, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> str:
    return roundtrip(paths, "GET_STATUS", timeout_sec=timeout_sec)


def get_positions(paths: IPCPaths, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> List[Dict[str, Any]]:
    """Roundtrip GET_POSITIONS and parse a JSON positions payload when present."""
    raw = roundtrip(paths, "GET_POSITIONS", timeout_sec=timeout_sec)
    for line in reversed([line.strip() for line in raw.splitlines() if line.strip()]):
        if line.startswith("{"):
            payload = json.loads(line)
            if payload.get("type") in ("positions", "position_snapshot"):
                return list(payload.get("positions", []))
            continue
        if "|positions=" in line:
            return list(json.loads(line.split("|positions=", 1)[1]))
    return []


def get_symbol_info(paths: IPCPaths, symbol: str, timeout_sec: float = DEFAULT_TIMEOUT_SEC, cid: Optional[str] = None) -> Dict[str, Any]:
    """Roundtrip GET_SYMBOL_INFO for one broker symbol and parse JSON."""
    raw = roundtrip(paths, "GET_SYMBOL_INFO", timeout_sec, cid, symbol)
    for line in reversed([line.strip() for line in raw.splitlines() if line.strip()]):
        if line.startswith("{"):
            payload = json.loads(line)
            if payload.get("type") == "symbol_info":
                return payload
    return {"type": "symbol_info", "ok": False, "symbol": symbol, "error": "unparseable_response", "raw": raw[:300]}


def search_symbols(paths: IPCPaths, query: str, limit: int = 50, timeout_sec: float = DEFAULT_TIMEOUT_SEC, cid: Optional[str] = None) -> Dict[str, Any]:
    """Roundtrip SEARCH_SYMBOLS and parse broker symbol-search metadata."""
    raw = roundtrip(paths, "SEARCH_SYMBOLS", timeout_sec, cid, query, str(limit))
    for line in reversed([line.strip() for line in raw.splitlines() if line.strip()]):
        if line.startswith("{"):
            payload = json.loads(line)
            if payload.get("type") == "symbol_search":
                return payload
    return {"type": "symbol_search", "ok": False, "query": query, "symbols": [], "error": "unparseable_response", "raw": raw[:300]}


def make_paths(root: Optional[Path] = None) -> IPCPaths:
    return IPCPaths.from_root(canonical_ipc_root(root))
