"""Shared operations for running the edge candidate ledger.

Both the one-shot CLI (scripts/edge_ledger_run.py) and the background daemon
(research/edge_ledger_daemon.py) call run_once() here so there is a single
implementation of: tail the bus -> append candidates -> label closed horizons
-> write a gate report. Measurement only; nothing here publishes execution
topics or promotes anything.
"""
from __future__ import annotations

import bisect
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from research import edge_ledger as el
from research import edge_validation as ev

try:
    from paths import repo_root

    _ROOT = repo_root()
except Exception:  # pragma: no cover
    _ROOT = Path(__file__).resolve().parent.parent

GATE_REPORT_PATH = _ROOT / "intel" / "edge_gate_report.json"


def tail_events(limit: int) -> List[dict]:
    """Best-effort read of the most recent bus events."""
    try:
        import sys

        nervous = str(_ROOT / "nervous")
        if nervous not in sys.path:
            sys.path.insert(0, nervous)
        from bus import tail  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[edge-ledger] bus unavailable: {exc}", flush=True)
        return []
    try:
        return list(tail(limit))
    except TypeError:
        return list(tail(n=limit))


def build_candle_price_lookup(symbols: Optional[set] = None) -> Callable[[Any, Any], Optional[float]]:
    """Price lookup from the durable candle lake; no-op if the lake is absent."""
    try:
        from data_lake import DATA_ROOT  # type: ignore
    except Exception:
        return lambda symbol, ts: None

    lake = Path(DATA_ROOT) / "lake" / "candles"
    if not lake.exists():
        return lambda symbol, ts: None

    raw: Dict[str, Dict[float, float]] = {}
    for candle_file in lake.glob("symbol=*/timeframe=*/candles.jsonl"):
        symbol_part = candle_file.parent.parent.name.split("=", 1)[-1].upper()
        if symbols and symbol_part not in symbols:
            continue
        for line in candle_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_close = row.get("ts_close")
            close = row.get("close")
            if ts_close is None or close is None:
                continue
            try:
                raw.setdefault(symbol_part, {})[float(ts_close)] = float(close)
            except (TypeError, ValueError):
                continue

    series: Dict[str, Any] = {}
    for symbol_part, points in raw.items():
        ordered = sorted(points.items())
        series[symbol_part] = ([ts for ts, _ in ordered], [close for _, close in ordered])

    def lookup(symbol, ts):
        if ts is None:
            return None
        entry = series.get(str(symbol or "").upper())
        if not entry:
            return None
        timestamps, closes = entry
        idx = bisect.bisect_right(timestamps, float(ts)) - 1
        return closes[idx] if idx >= 0 else None

    return lookup


def run_once(
    *,
    tail_limit: int = 2000,
    cost_per_trade: float = 0.0,
    events: Optional[List[dict]] = None,
    price_lookup: Optional[Callable[[Any, Any], Optional[float]]] = None,
    candidate_path: Path = el.CANDIDATE_PATH,
    label_path: Path = el.LABEL_PATH,
    report_path: Path = GATE_REPORT_PATH,
    now: Optional[float] = None,
    write_report: bool = True,
) -> Dict[str, Any]:
    """Ingest, label closed horizons, and write the gate report. Returns a summary."""
    if events is None:
        events = tail_events(tail_limit)
    appended = el.ingest_events(events, path=candidate_path)

    candidates = el.load_candidates(candidate_path)
    if price_lookup is None:
        symbols = {c.get("symbol") for c in candidates if c.get("symbol")}
        price_lookup = build_candle_price_lookup(symbols)
    labeled = el.label_candidates(
        price_lookup,
        candidate_path=candidate_path,
        label_path=label_path,
        now=now,
    )

    labels = el.load_labels(label_path)
    report = ev.gate_report(candidates, labels, now=now, cost_per_trade=cost_per_trade)
    if write_report:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "events": len(events),
        "appended": appended,
        "labeled": labeled,
        "candidates": len(candidates),
        "labels": len(labels),
        "groups": report["group_count"],
        "promotable": report["promotable_count"],
        "report": report,
    }
