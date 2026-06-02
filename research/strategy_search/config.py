"""Load strategy search configuration."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG = ROOT / "config" / "strategy_search.yaml"


def load_config(path: Path | None = None) -> Dict[str, Any]:
    cfg_path = path or DEFAULT_CONFIG
    if not cfg_path.exists():
        return {}
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
