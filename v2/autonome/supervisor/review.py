"""
autonome/supervisor/review.py  v2.0
LLM cockpit dashboard -- run this to get a summary for decision-making.
"""
import os, sys, sqlite3, json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from autonome.journal.trade_journal import TradeJournal

DB = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/data/journal.sqlite"


def _db():
    return sqlite3.connect(DB)


def recent_signals(hours: int = 24) -> list:
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with _db() as db:
        rows = db.execute(
            "SELECT t, symbol, direction, entry_price, stop_loss, take_profit, confidence, meta FROM signals WHERE t > ? ORDER BY t DESC",
            (cutoff,)
        ).fetchall()
    return rows


def recent_trades(hours: int = 24) -> list:
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with _db() as db:
        rows = db.execute(
            "SELECT t, symbol, side, qty, entry_price, status, error FROM orders WHERE t > ? ORDER BY t DESC",
            (cutoff,)
        ).fetchall()
    return rows


def recent_pnl(hours: int = 24) -> list:
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with _db() as db:
        rows = db.execute(
            "SELECT t, symbol, side, qty, exit_price, pnl, pnl_pct, reason FROM pnl WHERE t > ? ORDER BY t DESC",
            (cutoff,)
        ).fetchall()
    return rows


def equity_curve(points: int = 50) -> list:
    with _db() as db:
        rows = db.execute(
            "SELECT t, equity, drawdown, positions FROM equity ORDER BY t DESC LIMIT ?",
            (points,)
        ).fetchall()
    return list(reversed(rows))


def print_report():
    print("=" * 60)
    print("AUTONOME v2.0 -- LLM COCKPIT DASHBOARD")
    print(f"Now (UTC): {datetime.utcnow().isoformat()}")
    print("=" * 60)

    # Equity
    eq = equity_curve(1)
    if eq:
        print(f"\nCurrent Equity: ${eq[-1][1]:,.2f}")
        print(f"Drawdown: {eq[-1][2]*100:.2f}%")
        print(f"Open Positions: {eq[-1][3]}")
    else:
        print("\nNo equity data yet")

    # PnL
    pnl = recent_pnl(24)
    if pnl:
        total = sum(r[5] for r in pnl)
        wins = sum(1 for r in pnl if r[5] > 0)
        print(f"\nLast 24h PnL: ${total:,.2f}  Wins: {wins}/{len(pnl)}")
        for r in pnl[:5]:
            print(f"  {r[0]} | {r[1]} {r[2]} | PnL=${r[5]:,.2f} ({r[6]*100:+.2f}%) | {r[7]}")
    else:
        print("\nNo closed trades in last 24h")

    # Signals
    sigs = recent_signals(24)
    if sigs:
        print(f"\nSignals (last 24h): {len(sigs)}")
        for s in sigs[:5]:
            print(f"  {s[0]} | {s[1]} {s[2]} @ {s[3]:.2f} | conf={s[6]:.2f} | {s[7]}")
    else:
        print("\nNo signals in last 24h")

    # Trades
    tr = recent_trades(24)
    if tr:
        print(f"\nOrders (last 24h): {len(tr)}")
        for t in tr[:5]:
            print(f"  {t[0]} | {t[1]} {t[2]} qty={t[3]} @ {t[4]} | status={t[5]}")
    else:
        print("\nNo orders in last 24h")

    print("\n" + "=" * 60)
    print("ACTION: Review playbook.md and update thesis if needed.")
    print("ACTION: If PnL negative > 2 days, consider halting or reducing size.")
    print("=" * 60)


if __name__ == "__main__":
    print_report()
