#!/usr/bin/env python3
"""Tests for per-symbol news context used to enrich research packets."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import news_context as nc  # noqa: E402


def _bull_bear_sentiment(title: str) -> float:
    t = title.lower()
    if "soar" in t or "beat" in t or "hike" in t:
        return 0.8
    if "plunge" in t or "miss" in t or "probe" in t:
        return -0.8
    return 0.0


def _impact(title: str) -> float:
    return 0.9 if "earnings" in title.lower() else 0.3


def test_matches_ticker_and_company_alias():
    headlines = [
        {"title": "NVIDIA earnings beat as data-center demand soars"},
        {"title": "Apple faces antitrust probe in Europe"},
        {"title": "Crude oil steadies after OPEC meeting"},
    ]
    ctx = nc.build_news_context(
        ["NVDA", "AAPL", "TSLA"],
        headlines=headlines,
        sentiment_fn=_bull_bear_sentiment,
        impact_fn=_impact,
    )
    # NVDA matched via company name "NVIDIA"; AAPL via "Apple"; TSLA no match.
    assert "NVDA" in ctx and "AAPL" in ctx
    assert "TSLA" not in ctx
    assert ctx["NVDA"]["news_sentiment"] > 0  # bullish earnings beat
    assert ctx["NVDA"]["catalyst_score"] == 0.9  # earnings -> high impact
    assert ctx["AAPL"]["news_sentiment"] < 0  # antitrust probe


def test_no_headlines_returns_empty():
    assert nc.build_news_context(["NVDA"], headlines=[]) == {}


def test_extra_aliases_are_honored():
    headlines = [{"title": "Reliance Industries posts record quarterly profit"}]
    ctx = nc.build_news_context(
        ["RELIANCE"],
        headlines=headlines,
        aliases_by_symbol={"RELIANCE": ["Reliance Industries", "RELIANCE"]},
        sentiment_fn=lambda t: 0.5,
        impact_fn=lambda t: 0.4,
    )
    assert "RELIANCE" in ctx
    assert ctx["RELIANCE"]["headline_matches"] == 1


def test_source_quality_scales_with_matches():
    headlines = [
        {"title": "Tesla deliveries beat estimates"},
        {"title": "Tesla expands gigafactory output"},
        {"title": "Tesla unveils new model"},
    ]
    ctx = nc.build_news_context(
        ["TSLA"],
        headlines=headlines,
        sentiment_fn=lambda t: 0.2,
        impact_fn=lambda t: 0.5,
    )
    # 0.5 baseline + 0.1 per match (3) = 0.8
    assert abs(ctx["TSLA"]["source_quality"] - 0.8) < 1e-9


def test_load_recent_headlines_filters_old_and_empty(tmp_path):
    path = tmp_path / "raw_headlines.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"title": "Fresh NVDA headline", "fetched_ts": 1000.0},
                {"title": "Stale headline", "fetched_ts": 1.0},
                {"title": "", "fetched_ts": 1000.0},
            ]
        ),
        encoding="utf-8",
    )
    rows = nc.load_recent_headlines(path, lookback_sec=100.0, now=1050.0)
    titles = [r["title"] for r in rows]
    assert titles == ["Fresh NVDA headline"]


if __name__ == "__main__":
    test_matches_ticker_and_company_alias()
    print("news context smoke OK")
