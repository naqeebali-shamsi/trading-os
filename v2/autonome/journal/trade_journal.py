"""
autonome/journal/trade_journal.py  v2.0
SQLite append-only journal: signals, orders, PnL, equity curve.
"""
from __future__ import annotations

import os, sqlite3, logging, json
from dataclasses import asdict
from datetime import datetime
from typing import Optional

import yaml

log = logging.getLogger("journal")


def _db_path() -> str:
    p = os.path.join(os.path.dirname(__file__), "../../config/settings.yaml")
    with open(p) as f:
        cfg = yaml.safe_load(f)
    return cfg.get("journal", {}).get("db_path", "data/journal.sqlite")


class TradeJournal:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _db_path()
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    t TEXT,
                    symbol TEXT,
                    direction TEXT,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    confidence REAL,
                    meta TEXT
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    t TEXT,
                    symbol TEXT,
                    side TEXT,
                    qty REAL,
                    entry_order_id TEXT,
                    entry_price REAL,
                    stop_order_id TEXT,
                    target_order_id TEXT,
                    status TEXT,
                    error TEXT
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS pnl (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    t TEXT,
                    symbol TEXT,
                    side TEXT,
                    qty REAL,
                    exit_price REAL,
                    pnl REAL,
                    pnl_pct REAL,
                    reason TEXT
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS equity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    t TEXT,
                    equity REAL,
                    buying_power REAL,
                    cash REAL,
                    drawdown REAL,
                    positions INTEGER
                )
            """)

    def log_signal(self, signal, t: Optional[datetime] = None, meta: Optional[dict] = None):
        t = t or datetime.utcnow()
        meta_json = json.dumps(meta) if meta else (signal.meta if hasattr(signal, 'meta') and signal.meta else "")
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO signals (t,symbol,direction,entry_price,stop_loss,take_profit,confidence,meta) VALUES (?,?,?,?,?,?,?,?)",
                (t.isoformat(), signal.symbol, signal.direction, signal.entry_price,
                 signal.stop_loss, signal.take_profit, signal.confidence, meta_json)
            )

    def log_order(self, trade_record, t: Optional[datetime] = None):
        t = t or datetime.utcnow()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO orders (t,symbol,side,qty,entry_order_id,entry_price,stop_order_id,target_order_id,status,error) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (t.isoformat(), trade_record.symbol, trade_record.side, trade_record.qty,
                 trade_record.entry_order_id, trade_record.entry_price,
                 trade_record.stop_order_id, trade_record.target_order_id,
                 trade_record.status, trade_record.error)
            )

    def log_pnl(self, symbol: str, side: str, qty: float, exit_price: float,
                pnl: float, pnl_pct: float, reason: str, t: Optional[datetime] = None):
        t = t or datetime.utcnow()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO pnl (t,symbol,side,qty,exit_price,pnl,pnl_pct,reason) VALUES (?,?,?,?,?,?,?,?)",
                (t.isoformat(), symbol, side, qty, exit_price, pnl, pnl_pct, reason)
            )

    def log_equity(self, equity: float, buying_power: float, cash: float,
                   drawdown: float, positions: int, t: Optional[datetime] = None):
        t = t or datetime.utcnow()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO equity (t,equity,buying_power,cash,drawdown,positions) VALUES (?,?,?,?,?,?)",
                (t.isoformat(), equity, buying_power, cash, drawdown, positions)
            )

    # ── queries for supervisor ───────────────────────────────────────────
    def today_pnl(self) -> float:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT SUM(pnl) FROM pnl WHERE t LIKE ?", (today + "%",)
            ).fetchone()
        return row[0] or 0.0

    def today_signals_count(self) -> int:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT COUNT(*) FROM signals WHERE t LIKE ?", (today + "%",)
            ).fetchone()
        return row[0] or 0

    # ── rotation ─────────────────────────────────────────────────────────
    def db_size_mb(self) -> float:
        try:
            return os.path.getsize(self.db_path) / (1024 * 1024)
        except OSError:
            return 0.0

    def rotate(self, keep_months: int = 3):
        """Archive records older than keep_months to a separate DB, then VACUUM."""
        from datetime import datetime, timedelta
        cutoff = (datetime.utcnow() - timedelta(days=keep_months * 30)).strftime("%Y-%m-%d")
        archive_path = self.db_path.replace(".sqlite", f"_archive_{datetime.utcnow():%Y%m}.sqlite")

        with sqlite3.connect(self.db_path) as src:
            # Copy old records to archive
            with sqlite3.connect(archive_path) as dst:
                for table in ("signals", "orders", "pnl", "equity"):
                    dst.execute(f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM {table} WHERE 0")
                    rows = src.execute(f"SELECT * FROM {table} WHERE t < ?", (cutoff,)).fetchall()
                    if rows:
                        cols = [d[0] for d in src.execute(f"PRAGMA table_info({table})").fetchall()]
                        placeholders = ",".join("?" * len(cols))
                        dst.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})", rows)
                        log.info("Archived %d rows from %s", len(rows), table)
                # Delete from source
                for table in ("signals", "orders", "pnl", "equity"):
                    src.execute(f"DELETE FROM {table} WHERE t < ?", (cutoff,))
                src.execute("VACUUM")
        log.info("Journal rotated. Archive: %s", archive_path)
