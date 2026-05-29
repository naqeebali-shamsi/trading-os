#!/usr/bin/env python3
"""CLI wrapper for walk-forward stock research validation."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.validate_walk_forward import main

if __name__ == "__main__":
    raise SystemExit(main())
