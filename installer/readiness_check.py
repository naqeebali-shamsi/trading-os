#!/usr/bin/env python3
"""Post-install readiness checks for Trading OS."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "installer"))

from installer.secrets_store import load_secret  # noqa: E402


def check_readiness(install_root: Path, *, wizard: bool = False) -> dict:
    install_root = install_root.resolve()
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    marker = install_root / ".install-complete"
    add("install_marker", marker.exists(), str(marker))

    venv_py = install_root / "venv" / "Scripts" / "python.exe"
    bundled_py = install_root / "runtime" / "python" / "python.exe"
    add("venv_python", venv_py.exists(), str(venv_py))
    add("bundled_python", bundled_py.exists(), str(bundled_py))

    cfg_candidates = [
        Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "TradingOS" / "config.env",
        install_root / "config.env",
    ]
    cfg_ok = any(p.exists() for p in cfg_candidates)
    add("config_env", cfg_ok, ", ".join(str(p) for p in cfg_candidates if p.exists()))

    key_dpapi = load_secret("openrouter_api_key") is not None
    secrets_yaml = install_root / "config" / "secrets.yaml"
    yaml_key = False
    if secrets_yaml.exists():
        text = secrets_yaml.read_text(encoding="utf-8")
        yaml_key = 'openrouter: ""' not in text and "openrouter:" in text
    observe_only = False
    for p in cfg_candidates:
        if p.exists() and "TRADING_OS_LLM_DISABLED=1" in p.read_text(encoding="utf-8"):
            observe_only = True
            break
    add("llm_key_or_observe_only", observe_only or key_dpapi or yaml_key, "observe_only" if observe_only else "key_present" if (key_dpapi or yaml_key) else "missing")

    junction = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "trading-os"
    ipc = install_root / "ipc"
    junction_ok = False
    junction_detail = str(junction)
    if junction.exists():
        try:
            import subprocess

            proc = subprocess.run(
                ["fsutil", "reparsepoint", "query", str(junction)],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and "Mount Point" in (proc.stdout or ""):
                target = install_root / "ipc"
                target_norm = os.path.normcase(str(target.resolve()))
                output_norm = os.path.normcase(proc.stdout or "")
                junction_ok = target_norm in output_norm
                if not junction_ok:
                    junction_detail = f"{junction} (junction target mismatch)"
            else:
                junction_detail = f"{junction} (regular folder, not IPC junction)"
        except Exception as exc:
            junction_detail = f"{junction} (junction check failed: {exc})"
    add("mt5_junction", junction_ok, junction_detail)

    heartbeat = False
    if ipc.exists():
        heartbeat = any(ipc.rglob("heartbeat.txt"))
    add("mt5_heartbeat", heartbeat, "attach EA in MT5 if false")

    ex5 = install_root / "bridge" / "FileBridgeEA_Windows.ex5"
    add("bridge_ex5", ex5.exists(), str(ex5))

    required = {"install_marker", "venv_python", "config_env", "llm_key_or_observe_only"}
    if not wizard:
        required.add("bridge_ex5")
    ok = all(c["ok"] for c in checks if c["name"] in required)
    return {"ok": ok, "install_root": str(install_root), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trading OS readiness check")
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--wizard", action="store_true")
    args = parser.parse_args(argv)
    result = check_readiness(Path(args.install_root), wizard=args.wizard)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
