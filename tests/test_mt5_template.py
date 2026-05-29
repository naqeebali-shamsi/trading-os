#!/usr/bin/env python3
"""Tests for config-driven MT5 template discovery."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex.instrument_registry import InstrumentRegistry  # noqa: E402
from ops.mt5_template import discover_templates, load_template_config, template_filename  # noqa: E402


def test_template_config_from_instruments_yaml():
    registry = InstrumentRegistry(ROOT / "config" / "instruments.yaml")
    cfg = load_template_config(registry)
    assert cfg["template_preferred"] == "trading_os_bridge"
    assert "trading_os" in cfg["template_candidates"]
    assert template_filename("trading_os") == "trading_os.tpl"


def test_discover_templates_windows_or_wsl():
    status = discover_templates()
    # On dev machine templates exist under Windows APPDATA (direct or /mnt/c).
    assert status["scan_root"] is not None
    assert status["any_present"] is True
    assert status["resolved_template"] in {
        "trading_os_bridge.tpl",
        "trading_os.tpl",
    }
