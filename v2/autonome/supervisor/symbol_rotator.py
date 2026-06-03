"""
autonome/supervisor/symbol_rotator.py  v1.0
Reads the daily watchlist and rotates trading symbols for the supervisor.
Called at market open to pick the top N dark horses for the day.
"""
from __future__ import annotations

import json, logging, os
from typing import List
from datetime import datetime, timezone, timedelta

log = logging.getLogger("supervisor.rotator")

WATCHLIST_PATH = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/intel/daily_watchlist.json"
MAX_SYMBOLS = 3  # Maximum dark horses to trade simultaneously
DEFAULT_SYMBOLS = ["TQQQ", "SPY"]


def load_watchlist(path: str = WATCHLIST_PATH) -> List[str]:
    """
    Load today's dark horse picks from the watchlist file.
    Returns sorted list of symbols to trade.
    """
    if not os.path.exists(path):
        log.info("No watchlist found at %s — using defaults", path)
        return DEFAULT_SYMBOLS

    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        log.error("Failed to read watchlist: %s — using defaults", e)
        return DEFAULT_SYMBOLS

    generated = data.get("generated_at", "")
    if generated:
        try:
            gen_time = datetime.fromisoformat(generated.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - gen_time
            if age > timedelta(hours=25):
                log.warning("Watchlist is %s old — using defaults", age)
                return DEFAULT_SYMBOLS
        except ValueError:
            pass

    picks = data.get("picks", [])
    if not picks:
        log.info("Watchlist empty — using defaults")
        return DEFAULT_SYMBOLS

    # Sort by score descending, take top N
    picks.sort(key=lambda x: x.get("score", 0), reverse=True)
    symbols = [p["symbol"] for p in picks[:MAX_SYMBOLS]]

    log.info("Symbol rotator loaded: %s", symbols)
    return symbols


def should_update_symbols(current: List[str], proposed: List[str]) -> bool:
    """Check if proposed symbols are sufficiently different from current."""
    current_set = set(current)
    proposed_set = set(proposed)
    # Update if more than half the symbols changed
    changed = len(current_set.symmetric_difference(proposed_set))
    return changed >= max(1, len(current_set) // 2)
