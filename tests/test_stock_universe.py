#!/usr/bin/env python3
"""Tests for India overlay, stock universe helpers, and registry filters."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex.instrument_registry import InstrumentRegistry  # noqa: E402
from cortex.stock_universe import deprioritize_crowded, india_watchlist_symbols  # noqa: E402


def test_india_overlay_loads_session_and_watchlist():
    registry = InstrumentRegistry(ROOT / "config" / "instruments.yaml")
    assert "india_nse" in registry.sessions
    india = india_watchlist_symbols(registry)
    assert "RELIANCE" in india
    assert "TCS" in india
    assert all(not registry.get(sym).get("enabled") for sym in india)


def test_enabled_symbols_asset_class_filter():
    registry = InstrumentRegistry(ROOT / "config" / "instruments.yaml")
    stocks = registry.enabled_symbols(asset_class="stock_cfd")
    assert "NVDA" in stocks
    assert "EURUSD" not in stocks


def test_deprioritize_crowded_moves_hot_names_down():
    ranked = deprioritize_crowded(
        ["NVDA", "RELIANCE", "TCS"],
        {"NVDA": 0.95, "RELIANCE": 0.2, "TCS": 0.1},
    )
    assert ranked[0] in {"RELIANCE", "TCS"}
    assert ranked[-1] == "NVDA"
