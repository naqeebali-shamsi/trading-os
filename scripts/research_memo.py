#!/usr/bin/env python3
"""CLI: build research packets from the latest stock research and write a memo.

Loads the latest ranked stock research snapshot (or a JSON file passed via
--input), enriches each row into a research packet via ``derive_packet``, and
writes a dated JSON + Markdown memo via ``write_memo``. Prints the output paths.

  python scripts/research_memo.py
  python scripts/research_memo.py --input intel/stock_research_latest.json
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.news_context import build_news_context  # noqa: E402
from research.research_packet import derive_packet, write_memo  # noqa: E402

DEFAULT_SNAPSHOT = ROOT / "intel" / "stock_research_latest.json"


def _load_ranked(input_path: Path) -> List[Dict[str, Any]]:
    """Pull ranked rows from a stock research snapshot JSON."""
    if not input_path.exists():
        return []
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    for key in ("ranked", "top_picks", "multibagger_candidates"):
        rows = payload.get(key)
        if rows:
            return [r for r in rows if isinstance(r, dict)]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a dated research memo from ranked stock research")
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help="Ranked stock research JSON (default: intel/stock_research_latest.json)",
    )
    args = parser.parse_args(argv)

    ranked = _load_ranked(args.input)
    if not ranked:
        print(f"[research_memo] no ranked rows found in {args.input}", flush=True)
        return 1

    symbols = [str(r.get("symbol") or "").upper() for r in ranked if r.get("symbol")]
    try:
        news_by_symbol = build_news_context(symbols)
    except Exception as exc:  # pragma: no cover - news is best-effort
        news_by_symbol = {}
        print(f"[research_memo] news context skipped: {exc}", flush=True)

    packets = [
        derive_packet(row, news=news_by_symbol.get(str(row.get("symbol") or "").upper()))
        for row in ranked
    ]
    paths = write_memo(packets)
    print(json.dumps(paths, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
