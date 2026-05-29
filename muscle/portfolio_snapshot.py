#!/usr/bin/env python3
"""Portfolio snapshot for trader dashboard — account, PnL, and exposure."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
JOURNAL_FILE = ROOT / "memory" / "journal.jsonl"
EQUITY_FILE = ROOT / "memory" / "equity.jsonl"


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _leg_pnl(position: dict) -> float:
    return round(
        _to_float(position.get("profit"))
        + _to_float(position.get("swap"))
        + _to_float(position.get("commission")),
        2,
    )


def _notional(position: dict) -> float:
    qty = abs(_to_float(position.get("qty")))
    price = _to_float(position.get("open_price") or position.get("current_price"))
    return round(qty * price, 2)


def _read_journal_closes() -> List[dict]:
    if not JOURNAL_FILE.exists():
        return []
    rows = []
    for line in JOURNAL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") == "trade_closed":
            rows.append(row)
    return rows


def _realized_pnl(closes: List[dict], *, today_only: bool = False) -> float:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0.0
    for row in closes:
        if today_only:
            row_day = str(row.get("date") or "")
            if not row_day:
                row_day = datetime.fromtimestamp(float(row.get("ts") or 0), timezone.utc).strftime("%Y-%m-%d")
            if row_day != today:
                continue
        total += _to_float(row.get("pnl"))
    return round(total, 2)


def _equity_points(limit: int = 60) -> List[dict]:
    if EQUITY_FILE.exists():
        points = []
        for line in EQUITY_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = row.get("equity")
            if value is None:
                value = row.get("balance")
            if value is None:
                continue
            points.append({"ts": row.get("ts"), "equity": round(_to_float(value), 2)})
        return points[-limit:]

    closes = _read_journal_closes()
    if not closes:
        return []
    balance = 10000.0
    points = [{"ts": closes[0].get("ts"), "equity": balance}]
    for row in closes:
        balance += _to_float(row.get("pnl"))
        points.append({"ts": row.get("ts"), "equity": round(balance, 2)})
    return points[-limit:]


def _append_live_equity_point(points: List[dict], equity: float, *, ts: Optional[float] = None) -> List[dict]:
    """Ensure the curve ends with the current live equity for sparkline accuracy."""
    if equity <= 0:
        return points
    ts = ts if ts is not None else time.time()
    live = {"ts": ts, "equity": round(equity, 2), "live": True}
    if not points:
        return [live]
    last = points[-1]
    last_equity = round(_to_float(last.get("equity")), 2)
    last_ts = _to_float(last.get("ts"))
    if abs(last_equity - live["equity"]) < 0.01 and abs(last_ts - ts) < 30:
        points[-1] = {**last, **live}
        return points
    return points + [live]


def build_portfolio_snapshot(*, refresh_positions: bool = False) -> Dict[str, Any]:
    """Aggregate broker account, open exposure, and realized PnL for the trader desk."""
    try:
        from muscle import pnl_sync
    except ImportError:
        return {"available": False, "message": "Portfolio module unavailable"}

    source = "state_file"
    account = pnl_sync.parse_account_snapshot()
    if account:
        source = "ipc_account"

    positions: List[dict] = []
    floating_pnl = 0.0
    try:
        if refresh_positions:
            report = pnl_sync.check_once(use_command=False, publish_events=False)
            positions = list(report.get("positions") or [])
            floating_pnl = float(report.get("floating_pnl") or 0.0)
            source = str(report.get("source") or source)
        else:
            snapshot = pnl_sync.snapshot_from_data_file()
            if snapshot:
                positions = snapshot
                source = "data_out"
            else:
                state = pnl_sync.load_state()
                positions = list((state.get("positions") or {}).values())
            floating_pnl = round(sum(_leg_pnl(p) for p in positions), 2)
    except Exception:
        state = pnl_sync.load_state()
        positions = list((state.get("positions") or {}).values())
        floating_pnl = round(sum(_leg_pnl(p) for p in positions), 2)

    closes = _read_journal_closes()
    realized_total = _realized_pnl(closes, today_only=False)
    realized_today = _realized_pnl(closes, today_only=True)
    invested_notional = round(sum(_notional(p) for p in positions), 2)

    balance = _to_float((account or {}).get("balance"))
    equity = _to_float((account or {}).get("equity"))
    if equity <= 0 and balance > 0:
        equity = round(balance + floating_pnl, 2)
    if balance <= 0 and equity > 0:
        balance = round(equity - floating_pnl, 2)

    total_pnl = round(floating_pnl + realized_total, 2)
    session_pnl = round(floating_pnl + realized_today, 2)
    return_pct = None
    if balance > 0:
        return_pct = round((equity - balance) / balance * 100, 2)

    by_symbol: Dict[str, dict] = {}
    for pos in positions:
        symbol = str(pos.get("symbol") or "UNKNOWN")
        entry = by_symbol.setdefault(
            symbol,
            {"symbol": symbol, "open_count": 0, "notional": 0.0, "floating_pnl": 0.0},
        )
        entry["open_count"] += 1
        entry["notional"] = round(entry["notional"] + _notional(pos), 2)
        entry["floating_pnl"] = round(entry["floating_pnl"] + _leg_pnl(pos), 2)

    equity_points = _equity_points()
    if equity > 0:
        equity_points = _append_live_equity_point(equity_points, equity, ts=time.time())
    available = bool(account) or bool(positions) or bool(closes) or balance > 0

    return {
        "available": available,
        "as_of_ts": time.time(),
        "source": source,
        "account": {
            "balance": balance if balance else None,
            "equity": equity if equity else None,
            "margin_used": (account or {}).get("margin_used"),
            "margin_free": (account or {}).get("margin_free"),
            "margin_level_pct": (account or {}).get("margin_level_pct"),
            "login": (account or {}).get("login"),
            "server": (account or {}).get("server"),
        },
        "pnl": {
            "floating_pnl": floating_pnl,
            "realized_today": realized_today,
            "realized_total": realized_total,
            "total_pnl": total_pnl,
            "session_pnl": session_pnl,
            "return_pct": return_pct,
        },
        "exposure": {
            "open_count": len(positions),
            "invested_notional": invested_notional,
            "by_symbol": sorted(by_symbol.values(), key=lambda row: abs(row.get("floating_pnl") or 0), reverse=True),
        },
        "equity_curve": {
            "available": bool(equity_points),
            "points": equity_points,
        },
        "message": _portfolio_message(balance, equity, floating_pnl, len(positions)),
    }


def _portfolio_message(balance: float, equity: float, floating_pnl: float, open_count: int) -> str:
    if open_count == 0 and not balance:
        return "Connect the broker bridge to see live portfolio metrics."
    if open_count == 0:
        return "Flat book. Account metrics reflect realized balance only."
    direction = "up" if floating_pnl >= 0 else "down"
    return f"{open_count} open leg{'s' if open_count != 1 else ''}; floating PnL {direction} ${abs(floating_pnl):,.2f}"
