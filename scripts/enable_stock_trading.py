#!/usr/bin/env python3
"""Enable pattern-direct trading for enabled stock CFD symbols.

Updates config/runtime_controls.json (hot-reloaded by signal_generator_v2).
Does not modify instruments.yaml — stock strategies come from asset_classes.stock_cfd.default_strategies.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex.instrument_registry import InstrumentRegistry  # noqa: E402
from ops.chart_bootstrap import generate_manifest  # noqa: E402
from runtime_controls import apply_preset, load_controls, write_controls  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Enable stock CFD direct order intents")
    parser.add_argument(
        "--preset",
        choices=("production", "demo_stocks", "demo_cautious", "demo_aggressive"),
        default="production",
        help="Runtime preset (production enables FX+stock direct intents; demo_stocks uses M15/H1)",
    )
    parser.add_argument("--merge", action="store_true", help="Merge flags into current controls instead of replacing preset")
    args = parser.parse_args()

    generate_manifest()

    registry = InstrumentRegistry()
    stocks = registry.enabled_symbols(asset_class="stock_cfd")
    if args.merge:
        controls = write_controls({"stock_direct_intents": True, "signal_direct_intents": True})
    else:
        controls = apply_preset(args.preset)

    print(json.dumps({
        "ok": True,
        "preset": controls.get("preset"),
        "signal_direct_intents": controls.get("signal_direct_intents"),
        "stock_direct_intents": controls.get("stock_direct_intents"),
        "signal_timeframes": controls.get("signal_timeframes"),
        "enabled_stock_symbols": stocks,
        "strategies_example": (registry.get(stocks[0]) or {}).get("strategies") if stocks else [],
    }, indent=2))
    print("\nRestart not required — signal_generator_v2 reloads controls every few seconds.")
    print("Ensure supervisor runs: sensory (multisymbol), signal_generator_v2, muscle, immune.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
