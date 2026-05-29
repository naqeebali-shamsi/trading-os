#!/usr/bin/env python3
"""Single writer/reader for cortex/strategy_live_metrics.json runtime strategy telemetry."""
import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_METRICS_FILE = ROOT / "cortex" / "strategy_live_metrics.json"
_STATE_FILE = ROOT / "introspect" / ".state.json"

# Runtime fields overlaid onto declarative strategies.json for reads
OVERLAY_FIELDS = ("wins", "losses", "sharpe", "weight", "active")


def load_live_metrics() -> dict:
    if not LIVE_METRICS_FILE.exists():
        return {}
    try:
        return json.loads(LIVE_METRICS_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _load_checksum_state() -> dict:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_checksum_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state))


def merge_strategy_metrics(updates: dict[str, dict], *, source: str) -> bool:
    """Merge per-strategy patches into strategy_live_metrics.json.

    Returns True when the file was updated (checksum changed).
    """
    if not updates:
        return False

    live = load_live_metrics()
    now = time.time()
    changed_any = False
    for sid, patch in updates.items():
        entry = dict(live.get(sid, {}))
        for key, value in patch.items():
            if entry.get(key) != value:
                changed_any = True
                break
        if sid not in live:
            changed_any = True
        if not changed_any:
            continue
        entry.update(patch)
        entry["updated_ts"] = now
        entry["source"] = source
        live[sid] = entry

    if not changed_any:
        return False

    new_text = json.dumps(live, indent=2, sort_keys=True) + "\n"
    new_checksum = hashlib.sha256(new_text.encode()).hexdigest()[:16]
    state = _load_checksum_state()
    if new_checksum == state.get("last_live_metrics_checksum", ""):
        return False

    LIVE_METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIVE_METRICS_FILE.write_text(new_text)
    state["last_live_metrics_checksum"] = new_checksum
    _save_checksum_state(state)
    return True


def overlay_declarative_strategies(strats: dict) -> dict:
    """Merge live runtime fields onto declarative strategies.json config."""
    live = load_live_metrics()
    for sid, s in strats.items():
        overlay = live.get(sid, {})
        for field in OVERLAY_FIELDS:
            if field in overlay:
                s[field] = overlay[field]
    return strats
