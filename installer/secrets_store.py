"""Windows DPAPI-backed secret storage for Trading OS installer."""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

CRYPTPROTECT_LOCAL_MACHINE = 0x4


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _program_data_dir() -> Path:
    base = Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "TradingOS"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _to_blob(data: bytes) -> DATA_BLOB:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = DATA_BLOB()
    blob.cbData = len(data)
    blob.pbData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    return blob


def _from_blob(blob: DATA_BLOB) -> bytes:
    if not blob.pbData or blob.cbData == 0:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


def dpapi_available() -> bool:
    return sys.platform == "win32"


def is_admin() -> bool:
    """True when the current process has administrator privileges (Windows only)."""
    if not dpapi_available():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _decrypt_secret_blob(path: Path, *, local_machine: bool) -> str | None:
    crypt32 = ctypes.windll.crypt32
    in_blob = _to_blob(path.read_bytes())
    out_blob = DATA_BLOB()
    flags = CRYPTPROTECT_LOCAL_MACHINE if local_machine else 0
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, flags, ctypes.byref(out_blob)):
        return None
    try:
        return _from_blob(out_blob).decode("utf-8")
    except UnicodeDecodeError:
        return None


def store_secret(name: str, value: str, *, local_machine: bool | None = None) -> Path:
    if not dpapi_available():
        raise RuntimeError("DPAPI storage is Windows-only")
    if not value.strip():
        raise ValueError("empty secret")

    use_local_machine = is_admin() if local_machine is None else local_machine

    crypt32 = ctypes.windll.crypt32
    in_blob = _to_blob(value.encode("utf-8"))
    out_blob = DATA_BLOB()
    flags = CRYPTPROTECT_LOCAL_MACHINE if use_local_machine else 0
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, flags, ctypes.byref(out_blob)):
        raise OSError("CryptProtectData failed")

    path = _program_data_dir() / f"{name}.dpapi"
    path.write_bytes(_from_blob(out_blob))
    return path


def load_secret(name: str, *, local_machine: bool | None = None) -> str | None:
    if not dpapi_available():
        return None
    path = _program_data_dir() / f"{name}.dpapi"
    if not path.exists():
        return None

    if local_machine is None:
        return _decrypt_secret_blob(path, local_machine=False) or _decrypt_secret_blob(path, local_machine=True)
    return _decrypt_secret_blob(path, local_machine=local_machine)


def delete_secret(name: str) -> None:
    path = _program_data_dir() / f"{name}.dpapi"
    path.unlink(missing_ok=True)
