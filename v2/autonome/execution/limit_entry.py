"""
autonome/execution/limit_entry.py  v2.0
Limit order entry with timeout-based market fallback.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from autonome.broker.alpaca_client import AlpacaClient, OrderResult

log = logging.getLogger("execution.limit_entry")


def submit_limit_with_fallback(
    client: AlpacaClient,
    symbol: str,
    side: str,
    qty: float,
    limit_price: float,
    bracket_payload: dict,
    timeout_sec: float = 30.0,
    poll_interval: float = 1.0,
) -> tuple[Optional[OrderResult], str]:
    """
    Submit a LIMIT order. If not filled within timeout_sec, cancel and submit MARKET.
    Returns (OrderResult, order_type_used) where order_type_used is 'limit' or 'market'.
    """
    # Submit limit order
    limit_result = client.submit_order(
        symbol=symbol,
        side=side,
        qty=qty,
        order_type="limit",
        time_in_force="day",
        limit_price=limit_price,
        extra=bracket_payload,
    )

    if limit_result.status == "rejected":
        log.warning("LIMIT rejected for %s: %s — falling back to market", symbol, limit_result.error)
        return _submit_market(client, symbol, side, qty, bracket_payload)

    entry_id = limit_result.id
    log.info("LIMIT submitted %s %s @ %.2f id=%s", symbol, side, limit_price, entry_id)

    # Poll until filled or timeout
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        time.sleep(poll_interval)
        check = client.get_order(entry_id)
        if check is None:
            continue
        if check.status == "filled":
            log.info("LIMIT filled %s @ %.2f", symbol, check.filled_avg_price)
            return check, "limit"
        if check.status in ("canceled", "expired"):
            log.info("LIMIT %s for %s — using market fallback", check.status, symbol)
            return _submit_market(client, symbol, side, qty, bracket_payload)

    # Timeout — cancel limit, submit market
    log.info("LIMIT timeout for %s — canceling and using market", symbol)
    try:
        client.cancel_order(entry_id)
    except Exception as e:
        log.warning("Failed to cancel limit order %s: %s", entry_id, e)
    return _submit_market(client, symbol, side, qty, bracket_payload)


def _submit_market(
    client: AlpacaClient,
    symbol: str,
    side: str,
    qty: float,
    bracket_payload: dict,
) -> tuple[Optional[OrderResult], str]:
    result = client.submit_order(
        symbol=symbol,
        side=side,
        qty=qty,
        order_type="market",
        time_in_force="day",
        extra=bracket_payload,
    )
    if result.status == "rejected":
        log.error("MARKET fallback rejected for %s: %s", symbol, result.error)
    else:
        log.info("MARKET submitted %s %s id=%s", symbol, side, result.id)
    return result, "market"
