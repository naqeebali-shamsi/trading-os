"""
autonome/execution/reconcile.py  v2.0
Broker reconciliation: compare Alpaca state to local journal.
Flags mismatches, orphans, and drift.
"""
from __future__ import annotations

import logging
from typing import List, Dict, Optional

from autonome.broker.alpaca_client import AlpacaClient
from autonome.journal.trade_journal import TradeJournal

log = logging.getLogger("execution.reconcile")


class Reconciler:
    def reconcile_positions(self, client: AlpacaClient, journal: TradeJournal) -> List[dict]:
        """
        Compare broker positions to journal OPEN orders.
        Returns list of discrepancies.
        """
        discrepancies = []
        try:
            broker_positions = {p.symbol: p for p in client.list_positions()}
        except Exception as e:
            log.error("Failed to fetch broker positions: %s", e)
            return [{"type": "fetch_error", "detail": str(e)}]

        # Get journal orders with OPEN status
        try:
            with sqlite3.connect(journal.db_path) as db:
                rows = db.execute(
                    "SELECT symbol, side, qty, entry_order_id, status FROM orders WHERE status = 'OPEN'"
                ).fetchall()
        except Exception as e:
            log.error("Failed to query journal: %s", e)
            return [{"type": "journal_error", "detail": str(e)}]

        journal_symbols = {r[0] for r in rows}
        broker_symbols = set(broker_positions.keys())

        # Case 1: Journal thinks OPEN but broker has no position
        for symbol in journal_symbols - broker_symbols:
            discrepancies.append({
                "type": "ghost_position",
                "symbol": symbol,
                "detail": "Journal OPEN but broker shows no position",
            })

        # Case 2: Broker has position but journal doesn't track it
        for symbol in broker_symbols - journal_symbols:
            pos = broker_positions[symbol]
            discrepancies.append({
                "type": "untracked_position",
                "symbol": symbol,
                "detail": f"Broker has {pos.qty} shares but journal has no OPEN order",
            })

        # Case 3: Both have it — verify qty matches
        for symbol in journal_symbols & broker_symbols:
            pos = broker_positions[symbol]
            journal_rows = [r for r in rows if r[0] == symbol]
            total_journal_qty = sum(r[2] for r in journal_rows)
            if abs(total_journal_qty - abs(pos.qty)) > 0.01:
                discrepancies.append({
                    "type": "qty_mismatch",
                    "symbol": symbol,
                    "detail": f"Journal={total_journal_qty:.2f} Broker={abs(pos.qty):.2f}",
                })

        for d in discrepancies:
            log.warning("RECONCILE: %s — %s", d["type"], d["detail"])
        return discrepancies

    def reconcile_orders(self, client: AlpacaClient) -> List[dict]:
        """Check for orphaned stop/target orders not linked to tracked entries."""
        discrepancies = []
        try:
            open_orders = client.list_orders(status="open", limit=500)
        except Exception as e:
            log.error("Failed to fetch open orders: %s", e)
            return [{"type": "fetch_error", "detail": str(e)}]

        # Orphaned = no parent order_id in the order's client_order_id field
        for o in open_orders:
            client_id = o.get("client_order_id", "")
            order_type = o.get("type", "")
            # Bracket child orders typically have client_order_id like "entry_123_stop"
            if order_type in ("stop", "limit") and not client_id:
                discrepancies.append({
                    "type": "orphan_order",
                    "symbol": o.get("symbol"),
                    "order_id": o.get("id"),
                    "detail": f"Orphaned {order_type} order",
                })

        for d in discrepancies:
            log.warning("RECONCILE ORDER: %s — %s", d["type"], d["detail"])
        return discrepancies
