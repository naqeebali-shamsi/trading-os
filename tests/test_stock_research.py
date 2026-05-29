#!/usr/bin/env python3
"""Tests for stock research factor engine (no network)."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.stock_factors import (  # noqa: E402
    build_research_row,
    classify_tier,
    composite_score,
    research_confidence,
    score_multibagger_potential,
    score_quality,
)
from cortex.stock_universe import rank_long_term_candidates  # noqa: E402
import research.stock_researcher as sr  # noqa: E402


_RUN_CFG = {
    "weights": {"quality": 0.25, "growth": 0.30, "value": 0.15, "momentum": 0.15, "multibagger": 0.15},
    "thresholds": {"min_confidence": 0.65, "multibagger_tier": 0.75, "strong_buy_composite": 0.72, "publish_min_confidence": 0.0},
}


def _patch_universe(monkeypatch):
    monkeypatch.setattr(sr, "InstrumentRegistry", lambda *a, **k: object())
    monkeypatch.setattr(sr, "resolve_universe", lambda registry, cfg: ["NVDA", "TRAP"])
    monkeypatch.setattr(sr, "symbol_meta", lambda registry, symbols: {})
    monkeypatch.setattr(
        sr,
        "fetch_universe",
        lambda symbols, meta_by_symbol=None: {"NVDA": _growth_compounder(), "TRAP": _value_trap()},
    )
    monkeypatch.setattr(sr, "yfinance_available", lambda: True)


def _growth_compounder():
    return {
        "ok": True,
        "data_completeness": 0.9,
        "market_cap": 8_000_000_000,
        "revenue_growth": 0.28,
        "earnings_growth": 0.35,
        "roe": 0.22,
        "gross_margin": 0.55,
        "profit_margin": 0.18,
        "debt_to_equity": 45.0,
        "peg": 1.2,
        "pe": 28.0,
        "fcf_yield": 0.04,
        "momentum_12_1": 0.35,
        "payout_ratio": 0.10,
    }


def _value_trap():
    return {
        "ok": True,
        "data_completeness": 0.7,
        "market_cap": 2_000_000_000,
        "revenue_growth": -0.05,
        "earnings_growth": -0.20,
        "roe": 0.04,
        "gross_margin": 0.20,
        "profit_margin": 0.02,
        "debt_to_equity": 180.0,
        "peg": 4.5,
        "pe": 55.0,
        "momentum_12_1": -0.25,
    }


def test_quality_scores_higher_for_compounder():
    good = score_quality(_growth_compounder())
    bad = score_quality(_value_trap())
    assert good is not None and bad is not None
    assert good > bad
    print("[test] PASS: quality factor discriminates compounder vs trap")


def test_multibagger_tier_for_high_growth_midcap():
    cfg = {
        "weights": {"quality": 0.25, "growth": 0.30, "value": 0.15, "momentum": 0.15, "multibagger": 0.15},
        "thresholds": {"min_confidence": 0.65, "multibagger_tier": 0.75, "strong_buy_composite": 0.72},
    }
    row = build_research_row("DEMO", _growth_compounder(), cfg=cfg)
    assert row["composite_score"] is not None
    assert row["confidence"] >= 0.65
    assert row["tier"] in {"multibagger_candidate", "high_conviction", "accumulate"}
    assert "superior_growth" in row["thesis_tags"]
    print(f"[test] PASS: multibagger tier={row['tier']} conf={row['confidence']}")


def test_value_trap_stays_watch_or_low_tier():
    cfg = {
        "weights": {"quality": 0.25, "growth": 0.30, "value": 0.15, "momentum": 0.15, "multibagger": 0.15},
        "thresholds": {"min_confidence": 0.65, "multibagger_tier": 0.75, "strong_buy_composite": 0.72},
    }
    row = build_research_row("TRAP", _value_trap(), cfg=cfg)
    assert row["tier"] in {"watch", "accumulate"}
    assert row["confidence"] < 0.75
    print("[test] PASS: value trap not promoted to multibagger")


def test_stock_universe_uses_research_rows():
    research_rows = [
        build_research_row(
            "NVDA",
            _growth_compounder(),
            cfg={"weights": {"quality": 0.25, "growth": 0.30, "value": 0.15, "momentum": 0.15, "multibagger": 0.15}, "thresholds": {}},
        ),
        build_research_row(
            "TRAP",
            _value_trap(),
            cfg={"weights": {"quality": 0.25, "growth": 0.30, "value": 0.15, "momentum": 0.15, "multibagger": 0.15}, "thresholds": {}},
        ),
    ]
    ranked = rank_long_term_candidates(["TRAP", "NVDA"], research_rows=research_rows, popularity={"NVDA": 0.95})
    # Crowding penalizes NVDA but research score should still surface ordering metadata
    assert ranked[0]["symbol"] in {"NVDA", "TRAP"}
    assert ranked[0].get("tier") is not None
    print("[test] PASS: stock_universe integrates research rows")


def test_run_once_enriches_rows_with_packet_fields(monkeypatch):
    _patch_universe(monkeypatch)
    payload = sr.run_once(cfg=_RUN_CFG, persist=False)

    assert payload["ranked"], "expected ranked rows"
    row = payload["ranked"][0]
    # Packet fields are additive on top of the existing factor-screen row.
    for key in ("symbol", "tier", "confidence", "composite_score", "thesis"):
        assert key in row
    for key in ("final_score", "thesis_headline", "rating", "conviction"):
        assert key in row, f"missing packet field {key}"
    assert "accumulate" in payload  # new watchlist bucket


def test_run_once_writes_dated_memo(monkeypatch):
    _patch_universe(monkeypatch)
    # Stub the persistence side effects so the test stays offline and on-disk free.
    monkeypatch.setattr(sr, "append_ledger", lambda *a, **k: None)
    monkeypatch.setattr(sr, "write_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(sr, "write_research_overlay", lambda *a, **k: None)
    monkeypatch.setattr(sr, "_propose_symbol_enables", lambda *a, **k: None)
    monkeypatch.setattr(sr, "publish", lambda *a, **k: None)

    captured = {}

    def fake_write_memo(packets, **kwargs):
        captured["packets"] = list(packets)
        return {"json": "memo.json", "md": "memo.md"}

    monkeypatch.setattr(sr, "write_memo", fake_write_memo)

    payload = sr.run_once(cfg=_RUN_CFG, persist=True)

    assert payload.get("research_memo") == {"json": "memo.json", "md": "memo.md"}
    # Memo is ordered best-first by final_score.
    scores = [float(p.get("final_score") or 0) for p in captured["packets"]]
    assert scores == sorted(scores, reverse=True)


def test_all():
    print("=" * 60)
    print("  STOCK RESEARCH TESTS")
    print("=" * 60)
    test_quality_scores_higher_for_compounder()
    test_multibagger_tier_for_high_growth_midcap()
    test_value_trap_stays_watch_or_low_tier()
    test_stock_universe_uses_research_rows()
    print("=" * 60)
    print("  ALL STOCK RESEARCH TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
