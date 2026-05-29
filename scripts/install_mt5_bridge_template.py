#!/usr/bin/env python3
"""Locate or install MT5 bridge templates for ChartBootstrapService."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ops.mt5_template import discover_templates, load_template_config, template_filename  # noqa: E402


def newest_terminal() -> Path | None:
    from ops.mt5_template import terminal_roots

    roots = terminal_roots()
    if not roots:
        return None
    return max(roots, key=lambda p: p.stat().st_mtime)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate/install MT5 bridge template(s) from config")
    parser.add_argument(
        "--install",
        action="store_true",
        help="Copy the first discovered candidate to the preferred template name",
    )
    args = parser.parse_args(argv)

    status = discover_templates()
    cfg = load_template_config()
    preferred = cfg["template_preferred"]
    ext = cfg["template_extension"]

    print("Configured candidates:")
    for stem in cfg["template_candidates"]:
        print(f"  - {template_filename(stem, ext)}")

    if not status["any_present"]:
        print(status.get("hint") or "No bridge template found.")
        return 1

    print(f"Resolved: {status['resolved_template']} -> {status['resolved_path']}")
    if status.get("hint"):
        print(f"Note: {status['hint']}")

    if args.install:
        terminal = newest_terminal()
        source = status.get("resolved_path")
        if terminal is None or not source:
            print("Cannot install: terminal or source template missing.")
            return 1
        dest_dir = terminal / "MQL5" / "Profiles" / "Templates"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / template_filename(preferred, ext)
        shutil.copy2(source, dest)
        print(f"Installed preferred {dest.name} -> {dest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
