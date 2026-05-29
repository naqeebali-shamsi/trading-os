"""Shared MT5 IPC text file read/write — UTF-16 BOM detect, UTF-8 fallback."""
import os
from pathlib import Path
from typing import Optional

_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def read_ipc_text(path: Path) -> Optional[str]:
    """Read IPC text; UTF-16 if BOM present, else UTF-8. None if missing/unreadable."""
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        if raw.startswith(_UTF16_BOMS[0]) or raw.startswith(_UTF16_BOMS[1]):
            text = raw.decode("utf-16", errors="replace").lstrip("\ufeff")
        else:
            text = raw.decode("utf-8", errors="replace")
            if text.startswith("\ufeff"):
                text = text.lstrip("\ufeff")
        return text.strip()
    except Exception:
        return None


def write_ipc_utf16(path: Path, text: str) -> None:
    """Atomic tmp+replace write. UTF-16LE with CRLF (matches muscle/main EA format)."""
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write((text + "\r\n").encode("utf-16"))
    os.replace(str(tmp), str(path))
