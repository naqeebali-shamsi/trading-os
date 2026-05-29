#!/usr/bin/env python3
"""
scripts/eod_review.py — End-of-Day Obsidian Report

Reads the day's bus events, trade journal, and equity curve to generate
a structured EOD review note in Obsidian vault.

Run: python3 scripts/eod_review.py
Schedule: daily via cron at 21:00 UTC
"""
import sys, json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from obsidian_bridge import write_daily_review, FOLDERS
from bus import subscribe


def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_journal():
    jfile = ROOT / "memory" / "journal.jsonl"
    if not jfile.exists():
        return []
    today = today_str()
    entries = []
    with open(jfile) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if e.get("date") == today:
                    entries.append(e)
            except json.JSONDecodeError:
                continue
    return entries


def load_equity():
    efile = ROOT / "memory" / "equity.jsonl"
    if not efile.exists():
        return []
    with open(efile) as f:
        return [json.loads(line) for line in f if line.strip()]


def gather_decisions():
    """Pull cortex decisions from today's bus events."""
    evs = subscribe("cortex.decision", limit=50)
    today = today_str()
    lines = []
    for ev in evs:
        pl = ev.get("payload", {})
        ts = ev.get("ts", 0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")
        lines.append(f"- **{dt}** — {pl.get('action','?')} {pl.get('symbol','')} @ {pl.get('price','')}")
    return "\n".join(lines) if lines else "*No decisions logged.*"


def gather_anomalies():
    evs = subscribe("immune.anomaly", limit=20)
    if not evs:
        return "*No anomalies.*"
    lines = []
    for ev in evs:
        pl = ev.get("payload", {})
        lines.append(f"- `{pl.get('type','anomaly')}`: {pl.get('score','?')}")
    return "\n".join(lines)


def calc_stats(journal, equity):
    today = today_str()
    trades = [e for e in journal if e.get("type") == "trade_closed"]
    trade_count = len(trades)
    pnl = sum(t.get("pnl", 0) for t in trades)
    wins = [t for t in trades if t.get("pnl", 0) > 0]
    win_rate = f"{len(wins)/trade_count*100:.1f}%" if trade_count else "N/A"

    # Rough Sharpe from equity curve
    sharpe = "N/A"
    if len(equity) >= 2:
        rets = [equity[i]["balance"] / equity[i-1]["balance"] - 1
                for i in range(1, len(equity)) if equity[i-1]["balance"] > 0]
        if rets:
            avg = sum(rets) / len(rets)
            var = sum((r - avg) ** 2 for r in rets) / len(rets)
            std = var ** 0.5
            sharpe = f"{avg / std:.2f}" if std > 0 else "inf"

    return {
        "pnl": f"{pnl:+.2f}",
        "win_rate": win_rate,
        "sharpe": sharpe,
        "trade_count": trade_count,
        "decisions": gather_decisions(),
        "anomalies": gather_anomalies(),
        "next": "Review [[Strategy Registry]] for parameter tuning.",
    }


def main():
    print(f"[EOD] Generating review for {today_str()}...")
    journal = load_journal()
    equity = load_equity()
    stats = calc_stats(journal, equity)
    path = write_daily_review(stats)
    print(f"[EOD] Written: {path}")


if __name__ == "__main__":
    main()
