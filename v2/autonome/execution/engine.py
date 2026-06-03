"""
autonome/execution/engine.py  v2.2
Bracket order execution with OCO linkage via Alpaca native API.
Handles partial fills, tracks order lifecycle, cancels orphans.
"""
from __future__ import annotations

import os
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List

import yaml

from autonome.broker.alpaca_client import AlpacaClient, OrderResult
from autonome.risk.risk_manager import RiskDecision
from autonome.strategy.momentum_breakout import Signal
from autonome.execution.rate_limiter import OrderRateLimiter

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
    filled_qty: float = 0.0
    error: Optional[str] = None


class ExecutionEngine:
    def __init__(self, client: AlpacaClient):
        self.client = client
        self.limiter = OrderRateLimiter()
        self.pending_queued: list = []  # queued signals awaiting rate limit slot
        cfg_path = os.path.join(os.path.dirname(__file__), "../../config/settings.yaml")
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        ec = cfg["execution"]
        self.order_type = ec.get("order_type", "market")
        self.tif = ec.get("time_in_force", "day")
        self.max_retry = ec.get("retry_attempts", 3)
        self.retry_delay = ec.get("retry_delay_sec", 2)
        self.reject_htb = ec.get("reject_htb", True)
        self.reject_non_shortable = ec.get("reject_non_shortable", True)

        # Track active orders for lifecycle monitoring
        self.active_orders: Dict[str, dict] = {}
        self._recent_entries: Dict[str, float] = {}  # symbol -> timestamp
        self.duplicate_cooldown_sec = 60.0

    def enter_position(self, sig: Signal, rd: RiskDecision) -> TradeRecord:
        # Duplicate signal protection
        now = time.time()
        last_entry = self._recent_entries.get(sig.symbol, 0)
        if now - last_entry < self.duplicate_cooldown_sec:
            return TradeRecord(
                sig.symbol, "buy" if sig.direction == "LONG" else "sell",
                rd.qty, "", None, None, None,
                "REJECTED", filled_qty=0.0,
                error="duplicate_signal_cooldown")

        # Rate limit check
        if not self.limiter.can_submit(sig.symbol):
            wait = self.limiter.time_to_next(sig.symbol)
            log.warning("RATE LIMIT %s — queued (%.0fs)", sig.symbol, wait)
            self.pending_queued.append((sig, rd))
            return TradeRecord(
                sig.symbol, "buy" if sig.direction == "LONG" else "sell",
                rd.qty, "", None, None, None,
                "QUEUED", filled_qty=0.0,
                error=f"rate_limited_wait_{wait:.0f}s")

        side = "buy" if sig.direction == "LONG" else "sell"
        qty = rd.qty

        # ── SHORT pre-flight guards ────────────────────────────────────────
        if sig.direction == "SHORT":
            asset = self.client.get_asset(sig.symbol)
            if asset is None:
                return TradeRecord(
                    sig.symbol, side, qty, "", None, None, None,
                    "REJECTED", filled_qty=0.0,
                    error=f"asset_fetch_failed_{sig.symbol}")

            if self.reject_non_shortable and not asset.get("shortable", False):
                return TradeRecord(
                    sig.symbol, side, qty, "", None, None, None,
                    "REJECTED", filled_qty=0.0,
                    error="symbol_not_shortable")

            if self.reject_htb and not asset.get("easy_to_borrow", False):
                return TradeRecord(
                    sig.symbol, side, qty, "", None, None, None,
                    "REJECTED", filled_qty=0.0,
                    error="symbol_hard_to_borrow")

            if not self.client.is_margin_enabled():
                return TradeRecord(
                    sig.symbol, side, qty, "", None, None, None,
                    "REJECTED", filled_qty=0.0,
                    error="margin_not_enabled")

            # Short margin requirement: 1.5x notional buying power
            account = self.client.get_account()
            notional = qty * sig.entry_price
            if notional * 1.5 > account.buying_power:
                return TradeRecord(
                    sig.symbol, side, qty, "", None, None, None,
                    "REJECTED", filled_qty=0.0,
                    error="insufficient_buying_power_for_short")

        # Build bracket order payload (Alpaca native OCO)
        payload = {
            "type": self.order_type,
            "time_in_force": self.tif,
            "order_class": "bracket",
            "stop_loss": {"stop_price": str(round(sig.stop_loss, 2))},
            "take_profit": {"limit_price": str(round(sig.take_profit, 2))},
        }

        entry: Optional[OrderResult] = None
        for attempt in range(1, self.max_retry + 1):
            entry = self.client.submit_order(
                symbol=sig.symbol, side=side, qty=qty,
                order_type=self.order_type, time_in_force=self.tif,
                extra=payload  # pass bracket config
            )
            if entry.status != "rejected":
                break
            log.warning("Entry rejected on attempt %d: %s", attempt, entry.error)
            time.sleep(self.retry_delay)

        if entry is None or entry.status == "rejected":
            return TradeRecord(sig.symbol, side, qty, "", None, None, None,
                               "REJECTED", filled_qty=0.0,
                               error=entry.error if entry else "no_result")

        # Wait briefly for fill, but don't block forever
        filled_price = entry.filled_avg_price
        filled_qty = float(entry.filled_qty or 0)
        if not filled_price or filled_qty < qty * 0.99:
            for _ in range(20):
                time.sleep(0.5)
                check = self.client.get_order(entry.id)
                if check and check.filled_avg_price:
                    filled_price = check.filled_avg_price
                    filled_qty = float(check.filled_qty or 0)
                if filled_qty >= qty * 0.99 or check.status in ("filled", "canceled", "expired"):
                    break

        # Track this order for lifecycle monitoring
        self.active_orders[entry.id] = {
            "symbol": sig.symbol,
            "side": side,
            "qty": qty,
            "filled_qty": filled_qty,
            "entry_price": filled_price,
            "created_at": time.time(),
            "sig": sig,
        }

        if not filled_price:
            # Can't confirm fill — cancel and abort
            try:
                self.client.cancel_order(entry.id)
            except Exception:
                pass
            del self.active_orders[entry.id]
            return TradeRecord(sig.symbol, side, qty, entry.id, None, None, None,
                               "NO_FILL", filled_qty=0.0, error="entry_not_filled")

        # Bracket order creates stop + target children automatically
        # Query children and track them
        self._recent_entries[sig.symbol] = time.time()
        stop_id, target_id = self._fetch_bracket_children(entry.id)
        self.active_orders[entry.id]["children"] = [c for c in [stop_id, target_id] if c]
        return TradeRecord(
            symbol=sig.symbol,
            side=side,
            qty=qty,
            entry_order_id=entry.id,
            entry_price=filled_price,
            stop_order_id=stop_id,
            target_order_id=target_id,
            status="OPEN",
            filled_qty=filled_qty,
        )

    def _fetch_bracket_children(self, parent_id: str):
        """Query Alpaca for stop/target child orders of a bracket parent."""
        try:
            orders = self.client._get("/v2/orders?status=open&limit=500")
            stop_id = None
            target_id = None
            for o in orders:
                if o.get("legs"):
                    for leg in o["legs"]:
                        if leg.get("type") == "stop":
                            stop_id = leg.get("id")
                        elif leg.get("type") == "limit":
                            target_id = leg.get("id")
                # Also check by parent order ID in nested structure
                if o.get("id") == parent_id and o.get("legs"):
                    for leg in o.get("legs", []):
                        if leg.get("type") == "stop":
                            stop_id = leg.get("id")
                        elif leg.get("type") == "limit":
                            target_id = leg.get("id")
            return stop_id, target_id
        except Exception as e:
            log.error("Failed to fetch bracket children for %s: %s", parent_id, e)
            return None, None

    def flatten_symbol(self, symbol: str):
        """Market-order out entire position for symbol."""
        pos = self.client.get_position(symbol)
        if not pos or pos.qty == 0:
            return
        side = "sell" if pos.qty > 0 else "buy"
        qty = abs(pos.qty)
        result = self.client.submit_order(symbol, side, qty, "market", "day")
        log.warning("Flattened %s qty=%.2f result=%s", symbol, qty, result.status)

        # Cancel any remaining bracket legs for this symbol
        self._cancel_bracket_legs(symbol)

    def _cancel_bracket_legs(self, symbol: str):
        """Cancel stop/target orders for a symbol when position is flattened."""
        # Find all active orders for this symbol and cancel their children
        cancelled = 0
        for entry_id, info in list(self.active_orders.items()):
            if info.get("symbol") == symbol:
                for child_id in info.get("children", []):
                    try:
                        self.client.cancel_order(child_id)
                        cancelled += 1
                    except Exception as e:
                        log.error("Failed to cancel child %s for %s: %s", child_id, symbol, e)
                # Also cancel the parent entry if still open
                try:
                    self.client.cancel_order(entry_id)
                    cancelled += 1
                except Exception:
                    pass
        log.info("Cancelled %d orders for %s after flatten", cancelled, symbol)

    # ── lifecycle monitoring ─────────────────────────────────────────────────

    def sync_orders(self) -> List[dict]:
        """
        Call periodically to update order statuses, find bracket children,
        and detect orphans. Returns list of status changes.
        """
        changes = []
        try:
            # Get all open orders from broker
            open_orders = self.client._get("/v2/orders?status=open&limit=500")  # raw fetch
        except Exception as e:
            log.error("Order sync failed: %s", e)
            return changes

        # Build map of active order IDs we know about
        tracked_ids = set(self.active_orders.keys())

        # Find bracket children (orders parented by our entries)
        for o in open_orders:
            parent_id = o.get("client_order_id", "")
            if parent_id in tracked_ids or o.get("id") in tracked_ids:
                oid = o["id"]
                status = o.get("status", "unknown")
                if status == "filled":
                    log.info("Order %s filled @ %s", oid, o.get("filled_avg_price"))
                elif status == "canceled":
                    log.info("Order %s canceled", oid)
                changes.append({"id": oid, "status": status, "order": o})

        return changes

    def reconcile_position(self, symbol: str, expected_qty: float) -> bool:
        """
        Check actual position vs expected. Call after bracket fills.
        Returns True if reconciled, False if mismatch.
        """
        try:
            pos = self.client.get_position(symbol)
            actual = abs(pos.qty) if pos else 0.0
            if abs(actual - expected_qty) > 0.01:
                log.warning("Position mismatch for %s: expected=%.2f actual=%.2f",
                            symbol, expected_qty, actual)
                return False
            return True
        except Exception as e:
            log.error("Reconcile failed for %s: %s", symbol, e)
            return False
