"""
autonome/execution/engine.py  v2.0
Bracket order execution: market entry + OCO stop/target.
"""
from __future__ import annotations

import os, logging, time
from dataclasses import dataclass
from typing import Optional

import yaml

from autonome.broker.alpaca_client import AlpacaClient, OrderResult
from autonome.risk.risk_manager import RiskDecision
from autonome.strategy.momentum_breakout import Signal

log = logging.getLogger("execution")


@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    side: str
    qty: float
    entry_order_id: str
    entry_price: Optional[float]
    stop_order_id: Optional[str]
    target_order_id: Optional[str]
    status: str
    error: Optional[str] = None


class ExecutionEngine:
    def __init__(self, client: AlpacaClient):
        self.client = client
        cfg_path = os.path.join(os.path.dirname(__file__), "../../config/settings.yaml")
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        ec = cfg["execution"]
        self.order_type = ec.get("order_type", "market")
        self.tif = ec.get("time_in_force", "day")
        self.max_retry = ec.get("retry_attempts", 3)
        self.retry_delay = ec.get("retry_delay_sec", 2)

    def enter_position(self, sig: Signal, rd: RiskDecision) -> TradeRecord:
        side = "buy" if sig.direction == "LONG" else "sell"
        qty = rd.qty

        # market entry
        entry: Optional[OrderResult] = None
        for attempt in range(1, self.max_retry + 1):
            entry = self.client.submit_order(
                symbol=sig.symbol, side=side, qty=qty,
                order_type=self.order_type, time_in_force=self.tif
            )
            if entry.status != "rejected":
                break
            log.warning("Entry rejected on attempt %d: %s", attempt, entry.error)
            time.sleep(self.retry_delay)

        if entry is None or entry.status == "rejected":
            return TradeRecord(sig.symbol, side, qty, "", None, None, None,
                               "REJECTED", entry.error if entry else "no_result")

        # wait briefly for fill so we have avg price for bracket legs
        filled_price = entry.filled_avg_price
        if not filled_price:
            for _ in range(10):
                time.sleep(0.5)
                check = self.client.get_order(entry.id)
                if check and check.filled_avg_price:
                    filled_price = check.filled_avg_price
                    break

        if not filled_price:
            # can't build bracket without fill price -- cancel and abort
            try:
                self.client.cancel_all_orders()
            except Exception:
                pass
            return TradeRecord(sig.symbol, side, qty, entry.id, None, None, None,
                               "NO_FILL", "entry_not_filled")

        # bracket: OCO stop + target
        # Alpaca bracket requires stop_loss and take_profit on the original order
        # We already submitted market.  Submit separate stop + limit now.
        reverse_side = "sell" if side == "buy" else "buy"
        stop = self.client.submit_order(
            symbol=sig.symbol, side=reverse_side, qty=qty,
            order_type="stop", time_in_force="gtc",
            stop_price=sig.stop_loss
        )
        target = self.client.submit_order(
            symbol=sig.symbol, side=reverse_side, qty=qty,
            order_type="limit", time_in_force="gtc",
            limit_price=sig.take_profit
        )

        # NOTE: these are independent orders -- they don't OCO.  When one fills,
        # the other remains.  We rely on the supervisor exposure loop to cancel
        # dangling orders when position drops to zero.

        return TradeRecord(
            symbol=sig.symbol,
            side=side,
            qty=qty,
            entry_order_id=entry.id,
            entry_price=filled_price,
            stop_order_id=stop.id if stop.status != "rejected" else None,
            target_order_id=target.id if target.status != "rejected" else None,
            status="OPEN"
        )

    def flatten_symbol(self, symbol: str):
        """Market-order out entire position for symbol."""
        pos = self.client.get_position(symbol)
        if not pos or pos.qty == 0:
            return
        side = "sell" if pos.qty > 0 else "buy"
        qty = abs(pos.qty)
        self.client.submit_order(symbol, side, qty, "market", "day")
        log.warning("Flattened %s qty=%.2f", symbol, qty)
