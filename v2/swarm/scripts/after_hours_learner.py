"""
swarm/scripts/after_hours_learner.py  v1.0
Deterministic post-market diagnostics.

Reads journal.sqlite, compares today's live trades against a naive
backtest rerun on the same 15m bars, and writes improvement notes
to swarm/intel/whats_broken.md.
"""
from __future__ import annotations

import os
import sys
import sqlite3
import logging
import statistics
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from autonome.data.bars import Bar
from autonome.data.yahoo_feed import fetch_history

log = logging.getLogger("swarm.learner")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WORKSPACE = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DB_PATH = os.path.join(WORKSPACE, "data", "journal.sqlite")
OUTPUT_DIR = os.path.join(WORKSPACE, "swarm", "intel")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "whats_broken.md")

# Thresholds
SLIPPAGE_FLAG_PCT = 20.0          # live underperforms backtest by >20%
MAX_DRAWDOWN_PCT = 10.0


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def fetch_today_pnl(db_path: str) -> List[Dict]:
    """Return all pnl rows from today."""
    today = _today_iso()
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "SELECT t, symbol, side, qty, exit_price, pnl, pnl_pct, reason "
            "FROM pnl WHERE t LIKE ? ORDER BY t",
            (today + "%",),
        ).fetchall()
    cols = ["t", "symbol", "side", "qty", "exit_price", "pnl", "pnl_pct", "reason"]
    return [dict(zip(cols, r)) for r in rows]


def fetch_today_signals(db_path: str) -> List[Dict]:
    """Return all signal rows from today."""
    today = _today_iso()
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "SELECT t, symbol, direction, entry_price, stop_loss, take_profit, confidence, meta "
            "FROM signals WHERE t LIKE ? ORDER BY t",
            (today + "%",),
        ).fetchall()
    cols = ["t", "symbol", "direction", "entry_price", "stop_loss", "take_profit", "confidence", "meta"]
    return [dict(zip(cols, r)) for r in rows]


def fetch_today_equity(db_path: str) -> List[Dict]:
    """Return equity snapshots from today."""
    today = _today_iso()
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "SELECT t, equity, buying_power, cash, drawdown, positions "
            "FROM equity WHERE t LIKE ? ORDER BY t",
            (today + "%",),
        ).fetchall()
    cols = ["t", "equity", "buying_power", "cash", "drawdown", "positions"]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Naive backtest engine (deterministic, same-day only)
# ---------------------------------------------------------------------------
def backtest_signals(signals: List[Dict], bars: Dict[str, List[Bar]]) -> Dict[str, float]:
    """
    Simulate each signal with a fixed 1:2 R/R on the same-day 15m bars.
    Uses the first bar after signal time as entry, then checks for
    stop or target hit on subsequent bars.

    Returns:
        {
            "total_pnl": sum of PnLs,
            "win_rate": wins / total trades,
            "avg_return_pct": mean return per trade,
            "max_drawdown_pct": worst peak-to-trough,
            "trades_taken": int,
        }
    """
    pnls: List[float] = []
    returns: List[float] = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0

    for sig in signals:
        sym = sig["symbol"]
        if sym not in bars or not bars[sym]:
            continue

        sig_time = datetime.fromisoformat(sig["t"].replace("Z", "+00:00"))
        sym_bars = bars[sym]

        # Find entry bar (first bar at or after signal)
        entry_bar = None
        for b in sym_bars:
            if b.t >= sig_time:
                entry_bar = b
                break
        if entry_bar is None:
            continue

        entry = entry_bar.open
        stop = sig.get("stop_loss") or entry * 0.985
        target = sig.get("take_profit") or entry * 1.03
        direction = sig.get("direction", "LONG")

        # Walk forward
        pnl = 0.0
        for b in sym_bars[sym_bars.index(entry_bar) + 1 :]:
            if direction == "LONG":
                if b.low <= stop:
                    pnl = stop - entry
                    returns.append((stop - entry) / entry * 100)
                    break
                if b.high >= target:
                    pnl = target - entry
                    returns.append((target - entry) / entry * 100)
                    break
            else:  # SHORT
                if b.high >= stop:
                    pnl = entry - stop
                    returns.append((entry - stop) / entry * 100)
                    break
                if b.low <= target:
                    pnl = entry - target
                    returns.append((entry - target) / entry * 100)
                    break
        else:
            # No exit hit — mark-to-market at last close
            last_close = sym_bars[-1].close
            pnl = last_close - entry if direction == "LONG" else entry - last_close
            returns.append((pnl / entry) * 100)

        pnls.append(pnl)
        equity += pnl / entry * 0.01  # micro-sized for metric only
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        max_dd = max(max_dd, dd)

    if not pnls:
        return {
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "avg_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "trades_taken": 0,
        }

    wins = sum(1 for p in pnls if p > 0)
    return {
        "total_pnl": round(sum(pnls), 4),
        "win_rate": round(wins / len(pnls), 4),
        "avg_return_pct": round(statistics.mean(returns), 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "trades_taken": len(pnls),
    }


# ---------------------------------------------------------------------------
# Data fetcher (15m bars for every symbol that had a signal today)
# ---------------------------------------------------------------------------
def fetch_15m_bars(symbols: List[str]) -> Dict[str, List[Bar]]:
    """Pull today's 15m bars for all given symbols."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    out: Dict[str, List[Bar]] = {}
    for sym in symbols:
        try:
            bars = fetch_history(sym, start, now, timeframe="15m")
            out[sym] = bars
        except Exception:
            log.warning("Failed to fetch 15m bars for %s", sym)
            out[sym] = []
    return out


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
def compute_live_metrics(pnl_rows: List[Dict]) -> Dict[str, float]:
    if not pnl_rows:
        return {
            "live_total_pnl": 0.0,
            "live_win_rate": 0.0,
            "live_avg_return_pct": 0.0,
            "live_trades": 0,
        }
    pnls = [r["pnl"] or 0.0 for r in pnl_rows]
    pcts = [r["pnl_pct"] or 0.0 for r in pnl_rows]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "live_total_pnl": round(sum(pnls), 4),
        "live_win_rate": round(wins / len(pnls), 4),
        "live_avg_return_pct": round(statistics.mean(pcts), 4),
        "live_trades": len(pnls),
    }


def diagnose(
    live: Dict[str, float], backtest: Dict[str, float]
) -> Tuple[bool, List[str]]:
    """
    Returns (has_issue: bool, [issue_strings]).
    """
    issues: List[str] = []

    if backtest["trades_taken"] == 0:
        issues.append("ZERO_BACKTEST_TRADES — no signals hit exits today; check stop/target widths")
        return len(issues) > 0, issues

    # Slippage / execution gap
    if live["live_total_pnl"] < backtest["total_pnl"]:
        gap = backtest["total_pnl"] - live["live_total_pnl"]
        if backtest["total_pnl"] != 0:
            gap_pct = abs(gap) / abs(backtest["total_pnl"]) * 100
        else:
            gap_pct = 0.0

        if gap_pct > SLIPPAGE_FLAG_PCT:
            issues.append(
                f"SLIPPAGE_OR_EXECUTION_ISSUE | live PnL {live['live_total_pnl']:.4f} vs "
                f"backtest {backtest['total_pnl']:.4f} (gap {gap_pct:.1f}%). "
                "Possible causes: wide spreads at entry, partial fills, market orders on illiquid names."
            )
        else:
            issues.append(
                f"PERFORMANCE_GAP | live underperformed backtest by {gap:.4f} ({gap_pct:.1f}%) — "
                "within tolerance but worth monitoring."
            )

    # Win rate collapse
    if live["live_win_rate"] < backtest["win_rate"] * 0.5 and backtest["win_rate"] > 0:
        issues.append(
            "WIN_RATE_COLLAPSE | live win rate far below backtest — "
            "review early-exit logic or adverse selection on entry timing."
        )

    # High drawdown
    if backtest["max_drawdown_pct"] > MAX_DRAWDOWN_PCT:
        issues.append(
            f"HIGH_DRAWDOWN | backtest drawdown {backtest['max_drawdown_pct']:.2f}% > {MAX_DRAWDOWN_PCT}% — "
            "consider tightening stops or reducing position size."
        )

    # Zero live trades but signals fired
    if live["live_trades"] == 0 and backtest["trades_taken"] > 0:
        issues.append(
            "ZERO_LIVE_EXECUTION | signals fired but no fills — "
            "check broker connectivity, buying power, or HTB rejections."
        )

    return len(issues) > 0, issues


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------
def write_report(
    live_metrics: Dict[str, float],
    backtest_metrics: Dict[str, float],
    issues: List[str],
    equity_snapshots: List[Dict],
) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build equity summary
    if equity_snapshots:
        first_eq = equity_snapshots[0]["equity"]
        last_eq = equity_snapshots[-1]["equity"]
        eq_change = last_eq - first_eq
        max_dd = max(r.get("drawdown") or 0.0 for r in equity_snapshots)
    else:
        first_eq = last_eq = eq_change = max_dd = 0.0

    lines = [
        "# After-Hours Diagnostic Report",
        "",
        f"**Generated:** {now}",
        f"**Date:** {_today_iso()}",
        "",
        "## 1. Live Results",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total PnL | {live_metrics['live_total_pnl']:.4f} |",
        f"| Win Rate | {live_metrics['live_win_rate']*100:.1f}% |",
        f"| Avg Return / Trade | {live_metrics['live_avg_return_pct']:.4f}% |",
        f"| Trades Taken | {live_metrics['live_trades']} |",
        f"| Equity Change | {eq_change:.2f} |",
        f"| Max Drawdown | {max_dd*100:.2f}% |",
        "",
        "## 2. Backtest (Same-Day 15m Replay)",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total PnL | {backtest_metrics['total_pnl']:.4f} |",
        f"| Win Rate | {backtest_metrics['win_rate']*100:.1f}% |",
        f"| Avg Return / Trade | {backtest_metrics['avg_return_pct']:.4f}% |",
        f"| Max Drawdown | {backtest_metrics['max_drawdown_pct']:.2f}% |",
        f"| Trades Simulated | {backtest_metrics['trades_taken']} |",
        "",
        "## 3. Issues Flagged",
        "",
    ]

    if issues:
        for i, issue in enumerate(issues, 1):
            lines.append(f"{i}. **{issue}**")
    else:
        lines.append("_No issues flagged — live and backtest aligned within tolerance._")

    lines.extend([
        "",
        "## 4. Recommended Actions",
        "",
    ])

    # Deterministic recommendations based on issue keywords
    recs: List[str] = []
    for issue in issues:
        if "SLIPPAGE_OR_EXECUTION" in issue:
            recs.append("- Switch entry order type from `market` to `limit_with_fallback` to reduce spread cost.")
            recs.append("- Audit order fill prices vs signal entry prices in journal.")
        if "WIN_RATE_COLLAPSE" in issue:
            recs.append("- Review signal timestamps vs bar open — late-entry signals often hit stops first.")
            recs.append("- Consider widening SL on volatile names or adding a cooldown after loss.")
        if "HIGH_DRAWDOWN" in issue:
            recs.append("- Reduce `account_risk_per_trade_pct` by 0.25× until drawdown recovers.")
            recs.append("- Enable tighter ATR multiplier for stop loss.")
        if "ZERO_LIVE_EXECUTION" in issue:
            recs.append("- Verify Alpaca API keys and buying power.")
            recs.append("- Check `reject_htb` and `reject_non_shortable` flags if shorting.")
        if "PERFORMANCE_GAP" in issue:
            recs.append("- Monitor gap for 3 consecutive days; if persistent, re-run parameter optimisation.")

    if not recs:
        recs.append("- Continue current parameters; review again tomorrow.")

    lines.extend(recs)
    lines.append("")

    with open(OUTPUT_FILE, "w") as f:
        f.write("\n".join(lines))

    log.info("Report written to %s", OUTPUT_FILE)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-16s | %(levelname)-7s | %(message)s",
    )

    if not os.path.exists(DB_PATH):
        log.error("Journal DB not found at %s — aborting", DB_PATH)
        sys.exit(1)

    log.info("Reading journal: %s", DB_PATH)

    pnl_rows = fetch_today_pnl(DB_PATH)
    signals = fetch_today_signals(DB_PATH)
    equity_rows = fetch_today_equity(DB_PATH)

    log.info(
        "Today: %d PnL rows, %d signals, %d equity snapshots",
        len(pnl_rows), len(signals), len(equity_rows),
    )

    live_metrics = compute_live_metrics(pnl_rows)

    # Backtest needs 15m bars for every symbol that saw a signal
    symbols = list({s["symbol"] for s in signals})
    bars_15m = fetch_15m_bars(symbols)
    backtest_metrics = backtest_signals(signals, bars_15m)

    has_issue, issues = diagnose(live_metrics, backtest_metrics)

    write_report(live_metrics, backtest_metrics, issues, equity_rows)

    if has_issue:
        log.warning("Issues detected — see %s", OUTPUT_FILE)
    else:
        log.info("All clear — no issues flagged")


if __name__ == "__main__":
    main()
