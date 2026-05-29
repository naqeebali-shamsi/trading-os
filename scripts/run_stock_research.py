#!/usr/bin/env python3
"""CLI wrapper for the stock researcher arm."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.stock_researcher import main

if __name__ == "__main__":
    raise SystemExit(main())
