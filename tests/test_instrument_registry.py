#!/usr/bin/env python3
"""Tests for the Instrument Intelligence Registry."""
from datetime import datetime
from pathlib import Path
from collections import Counter
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex.instrument_registry import InstrumentRegistry, ValidationResult  # noqa: E402


def registry():
    return InstrumentRegistry(ROOT / "config" / "instruments.yaml")


def test_symbol_resolution_and_aliases():
    r = registry()
    assert r.resolve_symbol("EURUSD") == "EURUSD"
    assert r.resolve_symbol("EURUSDm") == "EURUSD"
    assert r.resolve_symbol("EURUSD.micro") == "EURUSD"
    assert r.resolve_symbol("GOLD") == "XAUUSD"
    assert r.resolve_broker_symbol("GOLD") == "XAUUSD"
    assert r.resolve_symbol("NOPE") is None
    print("[test] PASS: symbol alias resolution")


def test_enabled_disabled_unknown():
    r = registry()
    assert r.validate_symbol("EURUSD").ok
    disabled = r.validate_symbol("USDZAR")
    assert not disabled.ok and disabled.reason == "symbol_disabled"
    disabled_crypto = r.validate_symbol("BTCUSD")
    assert not disabled_crypto.ok and disabled_crypto.reason == "symbol_disabled"
    unknown = r.validate_symbol("NOPECOIN")
    assert not unknown.ok and unknown.reason == "unknown_symbol"
    print("[test] PASS: enabled/disabled/unknown symbol checks")


def test_expanded_portfolio_universe_breadth():
    r = registry()
    symbols = r.all_symbols()
    counts = Counter((r.get(s) or {}).get("asset_class") for s in symbols)
    assert len(symbols) >= 100
    for asset_class in ["forex", "forex_exotic", "metal", "energy_cfd", "index_cfd", "stock_cfd", "crypto_cfd"]:
        assert counts[asset_class] > 0, counts
    for symbol in ["USOIL", "NAS100", "BTCUSD", "ETHUSD", "DXY", "UKOIL", "US30", "US500", "UK100"]:
        assert r.resolve_symbol(symbol) == symbol
        assert not r.validate_symbol(symbol).ok
    for symbol in ["NVDA", "MSFT", "AAPL", "TSLA", "AMZN", "GOOGL", "META"]:
        assert r.resolve_symbol(symbol) == symbol
        assert r.validate_symbol(symbol).ok
    print("[test] PASS: expanded portfolio universe breadth")


def test_company_name_aliases_resolve_to_us_stocks():
    r = registry()
    cases = {
        "APPLE": "AAPL",
        "MICROSOFT": "MSFT",
        "NVIDIA": "NVDA",
        "AMAZON": "AMZN",
        "TESLA": "TSLA",
        "WALMART": "WMT",
        "COSTCO": "COST",
    }
    for company, ticker in cases.items():
        assert r.resolve_symbol(company) == ticker, (company, ticker)
    # Duplicate ".US" alias artifacts should not multiply the alias map.
    assert r.resolve_symbol("AAPL.US") == "AAPL"
    print("[test] PASS: company-name aliases resolve to US stocks")


def test_lot_validation_and_rounding():
    r = registry()
    assert r.round_lot("EURUSD", 0.037) == 0.03
    ok = r.validate_lot("EURUSD", 0.01)
    assert ok.ok and ok.details["rounded_qty"] == 0.01
    below = r.validate_lot("EURUSD", 0.001)
    assert not below.ok and below.reason == "invalid_lot"
    too_big = r.validate_lot("XAUUSD", 0.5)
    assert not too_big.ok and too_big.reason == "lot_above_max"
    print("[test] PASS: lot validation and rounding")


def test_spread_validation_forex_and_points():
    r = registry()
    assert r.spread_ok("EURUSD", 1.10000, 1.10010).ok
    wide = r.spread_ok("EURUSD", 1.10000, 1.10050)
    assert not wide.ok and wide.reason == "spread_too_wide"
    bad = r.spread_ok("EURUSD", 1.2, 1.1)
    assert not bad.ok and bad.reason == "ask_below_bid"
    assert r.spread_ok("XAUUSD", 2300.00, 2300.40).ok
    wide_xau = r.spread_ok("XAUUSD", 2300.00, 2301.00)
    assert not wide_xau.ok and wide_xau.reason == "spread_too_wide"
    print("[test] PASS: spread validation")


def test_session_validation():
    r = registry()
    monday_london = datetime(2026, 5, 4, 10, 0)  # Monday
    saturday = datetime(2026, 5, 9, 10, 0)
    assert r.session_ok("EURUSD", monday_london).ok
    closed = r.session_ok("EURUSD", saturday)
    assert not closed.ok and closed.reason == "session_closed"
    print("[test] PASS: session validation")


def test_forex_24_5_asian_hours_open():
    r = registry()
    asian_thursday = datetime(2026, 5, 7, 3, 30)  # Thu 03:30 UTC
    assert r.session_ok("EURUSD", now=asian_thursday).ok
    assert r.session_ok("XAUUSD", now=asian_thursday).ok
    print("[test] PASS: forex 24/5 asian hours open")


def test_forex_24_5_weekend_edges():
    r = registry()
    saturday = datetime(2026, 5, 9, 12, 0)
    sunday_before_open = datetime(2026, 5, 10, 21, 30)
    sunday_after_open = datetime(2026, 5, 10, 22, 30)
    friday_after_close = datetime(2026, 5, 8, 22, 15)
    assert not r.session_ok("EURUSD", now=saturday).ok
    assert not r.session_ok("EURUSD", now=sunday_before_open).ok
    assert r.session_ok("EURUSD", now=sunday_after_open).ok
    assert not r.session_ok("EURUSD", now=friday_after_close).ok
    print("[test] PASS: forex 24/5 weekend edges")


def test_forex_24_5_overnight_wrap_window():
    r = registry()
    assert InstrumentRegistry._minutes_in_window(23 * 60 + 30, 22 * 60, 6 * 60)
    assert InstrumentRegistry._minutes_in_window(3 * 60, 22 * 60, 6 * 60)
    assert not InstrumentRegistry._minutes_in_window(12 * 60, 22 * 60, 6 * 60)
    print("[test] PASS: overnight window math")


def test_validate_order_blocks_closed_session():
    r = registry()
    original = InstrumentRegistry.session_ok
    try:
        InstrumentRegistry.session_ok = lambda self, symbol, now=None: ValidationResult(False, "session_closed", symbol)
        blocked = r.validate_order(
            {"symbol": "EURUSD", "side": "BUY", "qty": 0.01, "strategy_id": "MA_CROSS_SMA9_21"},
            {"bid": 1.1, "ask": 1.1002},
        )
        assert not blocked.ok and blocked.reason == "session_closed"
    finally:
        InstrumentRegistry.session_ok = original
    print("[test] PASS: validate_order session gate wiring")


def test_india_nse_session_window():
    r = registry()
    before_open = datetime(2026, 5, 7, 3, 30)  # Thu 03:30 UTC = 09:00 IST
    at_open = datetime(2026, 5, 7, 3, 50)      # Thu 03:50 UTC = 09:20 IST
    after_close = datetime(2026, 5, 7, 10, 30) # Thu 10:30 UTC = 16:00 IST
    assert not r.session_ok("RELIANCE", now=before_open).ok
    assert r.session_ok("RELIANCE", now=at_open).ok
    assert not r.session_ok("RELIANCE", now=after_close).ok
    print("[test] PASS: india_nse session window")


def test_strategy_allowlist_and_order_validation():
    r = registry()
    assert r.strategy_allowed("EURUSD", "MA_CROSS_SMA9_21").ok
    blocked_strategy = r.strategy_allowed("XAUUSD", "RSI_MEAN_REVERSION")
    assert not blocked_strategy.ok and blocked_strategy.reason == "strategy_not_allowed"
    order = {
        "symbol": "EURUSDm",
        "side": "BUY",
        "qty": 0.01,
        "strategy_id": "MA_CROSS_SMA9_21",
    }
    result = r.validate_order(order, {"bid": 1.1000, "ask": 1.1001})
    assert result.ok
    assert result.symbol == "EURUSD"
    assert result.details["rounded_qty"] == 0.01
    disabled = r.validate_order({"symbol": "USDZAR", "side": "BUY", "qty": 0.01})
    assert not disabled.ok and disabled.reason == "symbol_disabled"
    print("[test] PASS: strategy allowlist and order validation")


def test_stock_default_strategies_from_asset_class():
    r = registry()
    cfg = r.get("NVDA") or {}
    assert "MA_CROSS_SMA9_21" in [str(s).upper() for s in (cfg.get("strategies") or [])]
    assert r.strategy_allowed("NVDA", "MA_CROSS_SMA9_21").ok
    print("[test] PASS: stock default strategies from asset class")


def test_tick_quote_freshness_stock_vs_forex():
    r = registry()
    assert r.max_fresh_quote_sec("EURUSD") == 30.0
    assert r.max_fresh_quote_sec("NVDA") == 1200.0
    fresh_stock = r.tick_quote_ok("NVDA", {"quote_age_sec": 890.0})
    assert fresh_stock.ok
    stale_stock = r.tick_quote_ok("NVDA", {"quote_age_sec": 1500.0})
    assert not stale_stock.ok and stale_stock.reason == "quote_stale"
    missing_stock = r.tick_quote_ok("NVDA", {})
    assert not missing_stock.ok and missing_stock.reason == "quote_time_missing"
    missing_forex = r.tick_quote_ok("EURUSD", {"bid": 1.1, "ask": 1.1002})
    assert missing_forex.ok
    blocked = r.validate_order(
        {"symbol": "EURUSD", "side": "BUY", "qty": 0.01, "strategy_id": "MA_CROSS_SMA9_21"},
        {"bid": 1.1, "ask": 1.1002, "quote_age_sec": 200.0},
    )
    assert not blocked.ok and blocked.reason == "quote_stale"
    print("[test] PASS: tick quote freshness stock vs forex")


def test_readiness_snapshot():
    r = registry()
    snap = r.readiness_snapshot(
        charts=["chart_EURUSD", "chart_XAUUSD"],
        ticks={"EURUSD": {"bid": 1.1, "ask": 1.1001}, "XAUUSD": {"bid": 2300.0, "ask": 2300.4}},
        now=datetime(2026, 5, 4, 13, 0),
    )
    assert snap["EURUSD"]["ready"]
    assert snap["XAUUSD"]["ready"]
    assert snap["GBPUSD"]["result"] == "BLOCKED_NO_CHART"
    assert snap["USDZAR"]["result"] == "DISABLED"
    print("[test] PASS: readiness snapshot")


def test_readiness_skips_quote_staleness_when_session_closed():
    r = registry()
    saturday = datetime(2026, 5, 9, 15, 0)
    snap = r.readiness_snapshot(
        charts=["chart_NVDA"],
        ticks={"NVDA": {"bid": 220.0, "ask": 220.2, "quote_age_sec": 4000.0}},
        now=saturday,
    )
    nvda = snap["NVDA"]
    assert nvda["quote_skipped"] is True
    assert nvda["quote_ok"] is True
    assert not nvda["ready"]
    assert nvda["result"] == "BLOCKED_SESSION_CLOSED"
    print("[test] PASS: readiness skips quote staleness when session closed")


def test_broker_hydrated_readiness_enforces_trade_mode_and_lot_floor():
    r = registry()
    now = datetime(2026, 5, 4, 15, 0)
    ticks = {
        "XAUUSD": {
            "source": "broker_symbol_info",
            "bid": 2300.0,
            "ask": 2300.4,
            "broker_info": {"trade_mode": 4, "min_lot": 0.01, "lot_step": 0.01},
        },
        "NVDA": {
            "source": "broker_symbol_info",
            "bid": 229.90,
            "ask": 229.93,
            "quote_age_sec": 600.0,
            "broker_info": {"trade_mode": 4, "min_lot": 1.0, "lot_step": 1.0},
        },
        "US30": {
            "source": "broker_symbol_info",
            "bid": 49896.0,
            "ask": 49898.0,
            "broker_info": {"trade_mode": 0, "min_lot": 0.1, "lot_step": 0.1},
        },
    }
    snap = r.readiness_snapshot(charts=[], ticks=ticks, now=now)
    assert snap["XAUUSD"]["ready"] is True
    assert snap["XAUUSD"]["tick_source"] == "broker_symbol_info"
    assert snap["NVDA"]["ready"] is True
    assert snap["NVDA"]["result"] == "READY"
    assert snap["NVDA"]["broker_trade_details"]["broker_min_lot"] == 1.0
    assert snap["US30"]["ready"] is False
    assert snap["US30"]["result"] == "DISABLED"
    print("[test] PASS: broker-hydrated readiness enforces broker execution metadata")


def test_all():
    print("=" * 60)
    print("  INSTRUMENT REGISTRY TESTS")
    print("=" * 60)
    test_symbol_resolution_and_aliases()
    test_enabled_disabled_unknown()
    test_expanded_portfolio_universe_breadth()
    test_company_name_aliases_resolve_to_us_stocks()
    test_lot_validation_and_rounding()
    test_spread_validation_forex_and_points()
    test_session_validation()
    test_forex_24_5_asian_hours_open()
    test_forex_24_5_weekend_edges()
    test_forex_24_5_overnight_wrap_window()
    test_validate_order_blocks_closed_session()
    test_india_nse_session_window()
    test_strategy_allowlist_and_order_validation()
    test_stock_default_strategies_from_asset_class()
    test_tick_quote_freshness_stock_vs_forex()
    test_readiness_snapshot()
    test_readiness_skips_quote_staleness_when_session_closed()
    test_broker_hydrated_readiness_enforces_trade_mode_and_lot_floor()
    print("=" * 60)
    print("  ALL INSTRUMENT REGISTRY TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
