#!/usr/bin/env python3
"""Unit tests for point-in-time quarterly fundamentals (no network)."""
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.pit_fundamentals import (  # noqa: E402
    QuarterlySeries,
    fundamentals_from_quarterly_series,
    price_on_date,
)


def _series():
    return QuarterlySeries(
        periods=[
            date(2024, 3, 31),
            date(2023, 12, 31),
            date(2023, 9, 30),
            date(2023, 6, 30),
            date(2023, 3, 31),
        ],
        revenue=[120.0, 110.0, 105.0, 100.0, 90.0],
        net_income=[24.0, 20.0, 18.0, 16.0, 12.0],
        gross_profit=[72.0, 66.0, 63.0, 60.0, 54.0],
        equity=[200.0, 190.0, 185.0, 180.0, 170.0],
        debt=[50.0, 52.0, 55.0, 58.0, 60.0],
    )


def test_pit_uses_reporting_lag():
    snap = fundamentals_from_quarterly_series(
        _series(),
        date(2024, 6, 1),
        symbol="TEST",
        reporting_lag_days=45,
        momentum_12_1=0.12,
    )
    assert snap["ok"] is True
    assert snap["latest_quarter_end"] == "2024-03-31"
    assert snap["revenue_growth"] is not None
    assert snap["revenue_growth"] > 0
    print("[test] PASS: PIT reporting lag excludes unreleased quarter")


def test_pit_no_lookahead_on_future_quarter():
    snap = fundamentals_from_quarterly_series(
        _series(),
        date(2024, 2, 1),
        symbol="TEST",
        reporting_lag_days=45,
    )
    assert snap["latest_quarter_end"] == "2023-09-30"
    print("[test] PASS: PIT picks older quarter before filing window")


def test_price_on_date():
    closes = {"2024-01-01": 100.0, "2024-02-01": 105.0, "2024-03-01": 110.0}
    px = price_on_date(closes, date(2024, 2, 15))
    assert px == 105.0
    print("[test] PASS: price_on_date uses last close on or before date")


def test_all():
    print("=" * 60)
    print("  PIT FUNDAMENTALS UNIT TESTS")
    print("=" * 60)
    test_pit_uses_reporting_lag()
    test_pit_no_lookahead_on_future_quarter()
    test_price_on_date()
    print("=" * 60)
    print("  ALL PIT FUNDAMENTALS TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
