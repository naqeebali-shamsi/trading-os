"""Load Dream Lab configuration."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "dream_lab.yaml"


def load_config(path: Path = DEFAULT_CONFIG) -> Dict[str, Any]:
    if not path.exists():
        return {
            "human_approval_required": True,
            "schedule": {
                "hourly_sec": 3600,
                "six_hour_sec": 21600,
                "daily_utc_hour": 3,
            },
            "auditor": {"daily_cap": 10},
            "trainer": {"min_rows": 50},
        }
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
