#!/usr/bin/env python3
"""
mt5_ipc_engine.py — File-based IPC bridge between Linux Python and MT5 MQL5 EA.

Watches cmd_out.txt from MT5, writes cmd_in.txt for MT5.
Converts human-friendly commands to pipe-delimited MQL5 format.
Runs as a persistent daemon — MT5 must be running with FileBridgeEA attached.

Architecture:
    Linux Python (this process)  <--->  MQL5/Files/trading-os/*.txt  <--->  FileBridgeEA.mq5 on MT5

Usage:
    python mt5_ipc_engine.py [--workspace <trading-os-root>]

Environment:
    MT5_FILE_DIR — override the MQL5/Files path (auto-detected from Wine prefix)
"""

import os
import sys
import time
import argparse
import logging
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

# ── Configuration ─────────────────────────────────────────────────────────────────────────────────────

DEFAULT_WINE_PREFIX = Path.home() / ".mt5"
DEFAULT_MT5_INSTALL_DIR = DEFAULT_WINE_PREFIX / "drive_c" / "Program Files" / "MetaTrader 5"
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = Path(os.environ.get("TRADING_OS_ROOT", str(_DEFAULT_ROOT))).resolve()
POLL_INTERVAL = 0.2          # seconds between cmd_out.txt reads
COMMAND_TIMEOUT = 30.0       # seconds before a command is considered failed
HEARTBEAT_TIMEOUT = 30.0     # seconds before MT5 considered dead

# ── Logging ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mt5_bridge")


# ── Command Tracker ──────────────────────────────────────────────────

@dataclass
class PendingCmd:
    cid: str
    cmd: str
    params: list
    issued_at: float


class MT5IPCBridge:
    """
    Manages bidirectional file-based IPC with MT5's FileBridgeEA.
    """

    def __init__(self, workspace_dir: Path, wine_prefix: Optional[Path] = None):
        self.workspace = Path(workspace_dir)
        self.wine_prefix = Path(wine_prefix or DEFAULT_WINE_PREFIX)
        self.ipc_dir = self._resolve_ipc_dir()

        self.cmd_in = self.ipc_dir / "cmd_in.txt"
        self.cmd_out = self.ipc_dir / "cmd_out.txt"
        self.data_out = self.ipc_dir / "data_out.txt"
        self.heartbeat = self.ipc_dir / "heartbeat.txt"
        self.ea_log = self.ipc_dir / "ea.log"

        self.pending: Optional[PendingCmd] = None
        self.cmd_counter = 0
        self.last_heartbeat: Optional[datetime] = None
        self.connected = False

        self._ensure_ipc_dir()

    # ── internal helpers ────────────────────────────────────────────

    def _resolve_ipc_dir(self) -> Path:
        """Find where MT5's MQL5/Files/ lives in the Wine prefix."""
        # Portable mode: MQL5/Files is always under the Terminal install dir
        ipc = (
            self.wine_prefix / "drive_c" / "Program Files" / "MetaTrader 5"
            / "MQL5" / "Files" / "trading-os"
        )
        ipc.mkdir(parents=True, exist_ok=True)
        return ipc

    def _ensure_ipc_dir(self):
        self.ipc_dir.mkdir(parents=True, exist_ok=True)
        logger.info("IPC dir: %s", self.ipc_dir)

    def _atomic_write(self, path: Path, text: str):
        """Write to tmp then rename for atomicity."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.rename(path)

    def _read_last_line(self, path: Path) -> str:
        """Read last non-empty line from a text file."""
        if not path.exists():
            return ""
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
            lines = [l.strip() for l in data.strip().splitlines() if l.strip()]
            return lines[-1] if lines else ""
        except Exception:
            return ""

    # ── public interface ────────────────────────────────────────────

    def send_command(self, cmd: str, params: list) -> Tuple[bool, str]:
        """
        Fire a command to MT5 and block until response or timeout.
        Thread-safe single-file model: only one pending cmd at a time.
        """
        if self.pending is not None:
            # Drain old command first
            self.poll_response(blocking=True, timeout=3.0)
            if self.pending is not None:
                old = self.pending
                self.pending = None
                return False, f"stale_cmd:{old.cid}"

        self.cmd_counter += 1
        cid = f"{int(time.time())}.{self.cmd_counter}"
        payload = "|".join([cmd, cid] + [str(p) for p in params]) + "\n"

        self._atomic_write(self.cmd_in, payload)
        self.pending = PendingCmd(cid=cid, cmd=cmd, params=params, issued_at=time.time())
        logger.debug("SENT cmd=%s cid=%s params=%s", cmd, cid, params)

        ok, response = self.poll_response(blocking=True, timeout=COMMAND_TIMEOUT)
        return ok, response

    def send_async(self, cmd: str, params: list) -> str:
        """Fire-and-forget command. Returns cid."""
        self.cmd_counter += 1
        cid = f"{int(time.time())}.{self.cmd_counter}"
        payload = "|".join([cmd, cid] + [str(p) for p in params]) + "\n"
        self._atomic_write(self.cmd_in, payload)
        return cid

    def poll_response(self, blocking: bool = False, timeout: float = 5.0) -> Tuple[bool, str]:
        """Check cmd_out.txt for a response matching pending command."""
        if self.pending is None:
            return True, "no_pending"

        deadline = time.time() + timeout
        while True:
            line = self._read_last_line(self.cmd_out)
            if line and "|" in line:
                parts = line.split("|", 2)
                if len(parts) >= 2 and parts[1] == self.pending.cid:
                    self.pending = None
                    return True, line

            if not blocking:
                break
            if time.time() >= deadline:
                break
            time.sleep(0.1)

        # Check for timeout
        if self.pending and (time.time() - self.pending.issued_at) > COMMAND_TIMEOUT:
            old = self.pending
            self.pending = None
            return False, f"timeout:{old.cid}"

        return False, "no_response"

    def health(self) -> Dict[str, Any]:
        """Check bridge and MT5 health."""
        hb_line = self._read_last_line(self.heartbeat)
        connected = False
        hb_time = None
        if hb_line and "|" in hb_line:
            ts_str = hb_line.split("|", 1)[0]
            try:
                hb_time = datetime.strptime(ts_str, "%Y.%m.%d %H:%M:%S")
                lag = (datetime.now() - hb_time).total_seconds()
                connected = lag < HEARTBEAT_TIMEOUT
                self.last_heartbeat = hb_time
                self.connected = connected
            except ValueError:
                pass

        return {
            "connected": connected,
            "heartbeat_age_sec": (datetime.now() - hb_time).total_seconds() if hb_time else None,
            "pending_cmd": self.pending.cid if self.pending else None,
            "cmd_counter": self.cmd_counter,
            "ipc_dir": str(self.ipc_dir),
        }

    # ── convenience wrappers ────────────────────────────────────────

    def ping(self) -> str:
        ok, resp = self.send_command("PING", [])
        return resp if ok else f"ERROR|{resp}"

    def get_balance(self) -> Dict[str, Any]:
        ok, resp = self.send_command("BALANCE", [])
        if not ok:
            return {"status": "error", "message": resp}
        # Parse: cid|OK|balance=X,equity=Y,...
        parts = resp.split("|", 3)
        if len(parts) < 3:
            return {"status": "error", "message": "bad_format"}
        kv = {}
        for pair in parts[2].split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                kv[k.strip()] = v.strip()
        return {"status": "ok", **kv}

    def get_positions(self) -> Dict[str, Any]:
        """Read positions from data_out.txt (MT5 writes them automatically)."""
        if not self.data_out.exists():
            return {"status": "error", "positions": []}
        try:
            raw = self.data_out.read_text(encoding="utf-8", errors="replace")
            positions = []
            for line in raw.strip().splitlines():
                if line.startswith("POSITION|"):
                    parts = line.split("|")
                    if len(parts) >= 11:
                        positions.append({
                            "ticket": parts[1],
                            "symbol": parts[2],
                            "volume": parts[3],
                            "type": parts[4],
                            "price_open": parts[5],
                            "price_current": parts[6],
                            "sl": parts[7],
                            "tp": parts[8],
                            "profit": parts[9],
                            "time": parts[10],
                        })
            return {"status": "ok", "positions": positions}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_symbols(self) -> Dict[str, Any]:
        ok, resp = self.send_command("SYMBOLS", [])
        if not ok:
            return {"status": "error", "message": resp}
        parts = resp.split("|", 3)
        syms = parts[2].replace("symbols=", "").split(",") if len(parts) >= 3 else []
        return {"status": "ok", "symbols": [s.strip() for s in syms if s.strip()]}

    def get_rates(self, symbol: str, timeframe: int = 1, count: int = 10) -> Dict[str, Any]:
        ok, resp = self.send_command("RATES", [symbol, timeframe, count])
        if not ok:
            return {"status": "error", "message": resp}
        parts = resp.split("|", 4)
        if len(parts) < 3:
            return {"status": "error", "message": "bad_format"}
        return {"status": "ok", "raw": resp}

    def place_order(self, symbol: str, volume: float, side: str,
                    sl: float = 0.0, tp: float = 0.0, comment: str = "os_order") -> Dict[str, Any]:
        ok, resp = self.send_command("ORDER", [symbol, volume, side, sl or "", tp or "", comment])
        if not ok:
            return {"status": "error", "message": resp}
        # Parse: cid|OK|ticket=X|price=Y|volume=Z
        result = {"status": "ok", "raw": resp}
        for part in resp.split("|")[2:]:
            if "=" in part:
                k, v = part.split("=", 1)
                result[k] = v
        return result

    def close_position(self, ticket: int) -> Dict[str, Any]:
        ok, resp = self.send_command("CLOSE", [ticket])
        if not ok:
            return {"status": "error", "message": resp}
        result = {"status": "ok", "raw": resp}
        for part in resp.split("|")[2:]:
            if "=" in part:
                k, v = part.split("=", 1)
                result[k] = v
        return result

    def close_all(self) -> Dict[str, Any]:
        ok, resp = self.send_command("CLOSE_ALL", [])
        if not ok:
            return {"status": "error", "message": resp}
        result = {"status": "ok", "raw": resp}
        for part in resp.split("|")[2:]:
            if "=" in part:
                k, v = part.split("=", 1)
                result[k] = v
        return result

    def modify(self, ticket: int, sl: float = 0.0, tp: float = 0.0) -> Dict[str, Any]:
        ok, resp = self.send_command("MODIFY", [ticket, sl or "", tp or ""])
        if not ok:
            return {"status": "error", "message": resp}
        return {"status": "ok", "raw": resp}


# ── Standalone test ──────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MT5 File IPC Bridge")
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE), help="Workspace dir")
    parser.add_argument("--wine-prefix", default=str(DEFAULT_WINE_PREFIX), help="Wine prefix")
    parser.add_argument("--cmd", help="One-shot command (ping/balance/positions/symbols)")
    args = parser.parse_args()

    bridge = MT5IPCBridge(Path(args.workspace), Path(args.wine_prefix))
    print(json.dumps(bridge.health(), indent=2))

    if args.cmd:
        if args.cmd == "ping":
            print(bridge.ping())
        elif args.cmd == "balance":
            print(json.dumps(bridge.get_balance(), indent=2))
        elif args.cmd == "positions":
            print(json.dumps(bridge.get_positions(), indent=2))
        elif args.cmd == "symbols":
            print(json.dumps(bridge.get_symbols(), indent=2))
        else:
            print(f"Unknown cmd: {args.cmd}")
