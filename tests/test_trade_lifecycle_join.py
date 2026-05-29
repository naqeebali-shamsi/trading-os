#!/usr/bin/env python3
"""Tests for the source-side trade-lifecycle join fixes (WS4).

These guard against the spurious dashboard defects caused by position events
that could not be correlated on order_id, and by closes that never reached
memory because nothing bridged position.closed -> memory.trade_outcome.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

import muscle.position_tracker as pt  # noqa: E402
import memory.main as mm  # noqa: E402


def test_position_opened_carries_order_id():
    fill = {"order_id": "OID-1", "fill_price": 1.2345, "symbol": "EURUSD", "side": "BUY", "qty": 1.0}
    pos = pt.position_from_fill(fill)
    assert pos["order_id"] == "OID-1"
    assert pos["open"] is True
    assert pos["symbol"] == "EURUSD"


def test_close_event_from_position_maps_pnl_and_exit():
    payload = {
        "order_id": "OID-1",
        "ticket": "555",
        "symbol": "EURUSD",
        "side": "BUY",
        "volume": 1.0,
        "profit": 12.5,
        "swap": -0.5,
        "commission": -1.0,
        "current_price": 1.2400,
    }
    close_event = mm.close_event_from_position(payload)
    assert close_event["order_id"] == "OID-1"
    assert close_event["symbol"] == "EURUSD"
    assert close_event["qty"] == 1.0
    assert abs(close_event["pnl"] - 11.0) < 1e-9  # 12.5 - 0.5 - 1.0
    assert close_event["exit_price"] == 1.2400


def test_close_event_falls_back_to_comment_for_order_id():
    payload = {"comment": "OID-9", "symbol": "XAUUSD", "profit": 3.0, "current_price": 1900.0}
    close_event = mm.close_event_from_position(payload)
    assert close_event["order_id"] == "OID-9"
    assert abs(close_event["pnl"] - 3.0) < 1e-9


def test_close_event_prefers_explicit_pnl():
    payload = {"order_id": "OID-2", "pnl": 7.0, "profit": 99.0, "current_price": 100.0}
    close_event = mm.close_event_from_position(payload)
    assert close_event["pnl"] == 7.0


if __name__ == "__main__":
    test_position_opened_carries_order_id()
    test_close_event_from_position_maps_pnl_and_exit()
    print("trade lifecycle join smoke OK")
