#!/usr/bin/env python3
"""Unit tests for walk-forward validation and research snapshot (no network)."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.snapshot import pick_research_candidates, sort_symbols_by_research, tier_rank  # noqa: E402
from research.validate_walk_forward import (  # noqa: E402
    composite_proxy,
    momentum_12_1,
    quintile_bucket,
    rank_ic,
    run_walk_forward,
)


def _synthetic_prices(trend: float, months: int = 30, start: float = 100.0):
    dates = [f"2022-{m:02d}-01" if m <= 12 else f"2023-{m-12:02d}-01" for m in range(1, months + 1)]
    closes = {}
    price = start
    for i, d in enumerate(dates):
        price *= 1 + trend + (0.01 if i % 5 == 0 else 0)
        closes[d] = price
    return closes


def test_quintile_spread_on_synthetic_momentum():
    winners = {f"WIN{i}": _synthetic_prices(0.03, months=30, start=100 + i) for i in range(5)}
    losers = {f"LOSS{i}": _synthetic_prices(-0.01, months=30, start=100 + i) for i in range(5)}
    data = {**winners, **losers}
    wf = run_walk_forward(data, forward_months=3, min_history=14)
    assert (wf.get("momentum_quintile_spread_6m_mean") or 0) > 0
    assert (wf.get("top5_forward_6m_mean") or 0) > (wf.get("bottom5_forward_6m_mean") or 0)
    print("[test] PASS: synthetic momentum winners beat losers")


def test_rank_ic_positive_for_monotonic_scores():
    scores = {"A": 0.9, "B": 0.7, "C": 0.5, "D": 0.3, "E": 0.1}
    forwards = {"A": 0.2, "B": 0.15, "C": 0.05, "D": -0.05, "E": -0.1}
    ic = rank_ic(scores, forwards)
    assert ic is not None and ic > 0.5
    print(f"[test] PASS: rank IC={ic}")


def test_research_snapshot_sort_and_filter():
    snapshot = {
        "available": True,
        "top_picks": [
            {"symbol": "NVDA", "tier": "high_conviction", "confidence": 0.8, "composite_score": 0.75},
            {"symbol": "META", "tier": "multibagger_candidate", "confidence": 0.88, "composite_score": 0.85},
        ],
    }
    ordered = pick_research_candidates(
        ["NVDA", "META", "AAPL"],
        min_tier="high_conviction",
        min_confidence=0.7,
        snapshot=snapshot,
    )
    assert ordered[0] == "META"
    assert "AAPL" not in ordered
    assert tier_rank("multibagger_candidate") < tier_rank("watch")
    print("[test] PASS: research snapshot filter/sort")


def test_all():
    print("=" * 60)
    print("  RESEARCH VALIDATION UNIT TESTS")
    print("=" * 60)
    test_quintile_spread_on_synthetic_momentum()
    test_rank_ic_positive_for_monotonic_scores()
    test_research_snapshot_sort_and_filter()
    print("=" * 60)
    print("  ALL RESEARCH VALIDATION UNIT TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
