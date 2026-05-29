"""Tests for release installer / launcher helpers."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "installer"))

from bridge.setup_bridge import setup_bridge  # noqa: E402


def test_load_config_env_from_example(tmp_path):
    cfg = tmp_path / "config.env"
    cfg.write_text("TRADING_OS_ROOT=C:\\Test\nTRADING_OS_MODE=LIVE\n", encoding="utf-8")
    # Non-canonical keys (e.g. mode) are read from config.env beside the install root.
    import installer.launcher as launcher_mod

    values = launcher_mod.load_config_env(tmp_path)
    assert values["TRADING_OS_MODE"] == "LIVE"
    # TRADING_OS_ROOT is deliberately forced to the real install directory,
    # overriding any (possibly stale) value baked into config.env.
    assert values["TRADING_OS_ROOT"] == str(tmp_path)


def test_setup_bridge_module_importable():
    assert callable(setup_bridge)


def test_install_config_dry_run(tmp_path, monkeypatch):
    progdata = tmp_path / "ProgramData"
    monkeypatch.setenv("TRADING_OS_PROGRAMDATA", str(progdata))
    src = ROOT / "installer" / "install_config.py"
    env = os.environ.copy()
    env["TRADING_OS_ROOT"] = str(tmp_path)
    env["TRADING_OS_PROGRAMDATA"] = str(progdata)
    proc = subprocess.run(
        [sys.executable, str(src), "--install-root", str(tmp_path), "--mode", "SIMULATION", "--observe-only", "--json"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["install_ok"] is True
    assert payload["observe_only"] is True
    assert (tmp_path / "venv" / "Scripts" / "python.exe").exists()
    assert (tmp_path / ".install-complete").exists()
    cfg = (tmp_path / "config.env").read_text(encoding="utf-8")
    assert "TRADING_OS_LLM_DISABLED=1" in cfg


def test_install_config_requires_key_without_observe_only(tmp_path):
    src = ROOT / "installer" / "install_config.py"
    env = os.environ.copy()
    env["TRADING_OS_ROOT"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(src), "--install-root", str(tmp_path), "--mode", "SIMULATION", "--json"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["install_ok"] is False
