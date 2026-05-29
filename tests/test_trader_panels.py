#!/usr/bin/env python3
"""Unit tests for trader-facing dashboard panels."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from consciousness import trader_panels as tp  # noqa: E402


def test_signal_drilldown_humanizes_blocked_reason():
    events = [
        {
            "topic": "market.signal.evaluation",
            "ts": 100.0,
            "payload": {
                "symbol": "NVDA",
                "timeframe": "M15",
                "status": "blocked",
                "stage": "confidence",
                "reason": "below_min_confidence",
                "confidence": 0.68,
                "min_confidence": 0.70,
                "patterns": [{"pattern": "bullish_engulfing"}],
            },
        }
    ]
    panel = tp.signal_evaluation_drilldown(events)
    assert panel["blocked_count"] == 1
    assert panel["recent"][0]["reason_label"] == "Setup confidence too low"
    assert panel["recent"][0]["stage_label"] == "Confidence threshold"
    print("[test] PASS: signal drilldown humanizes labels")


def test_macro_news_summarizes_decision():
    events = [
        {
            "topic": "cortex.decision",
            "ts": 50.0,
            "payload": {
                "recommendation": "reduce_size",
                "assessment": "risk_off",
                "impact_score": 0.62,
                "affected_symbols": {"XAUUSD": 0.9, "EURUSD": 0.4},
                "halt_symbols": ["XAUUSD"],
                "top_keywords": ["oil", "fed"],
            },
        }
    ]
    panel = tp.macro_news_impact(events)
    assert panel["risk_level"] == "caution"
    assert panel["recommendation_label"] == "Reduce size"
    assert panel["halt_symbols"] == ["XAUUSD"]
    print("[test] PASS: macro news panel summarizes decision")


def test_readiness_table_from_preflight():
    preflight = {
        "instruments": {
            "EURUSD": {
                "enabled": True,
                "asset_class": "fx",
                "ready": True,
                "result": "READY",
                "session_ok": True,
                "spread_ok": True,
                "quote_skipped": False,
                "quote_age_sec": 2,
                "chart_present": True,
            },
            "NVDA": {
                "enabled": True,
                "asset_class": "stock_cfd",
                "ready": False,
                "result": "BLOCKED_SESSION_CLOSED",
                "session_ok": False,
                "spread_ok": True,
                "quote_skipped": True,
                "quote_age_sec": 3600,
                "chart_present": True,
            },
        }
    }
    panel = tp.readiness_table(preflight)
    assert panel["ready_count"] == 1
    assert panel["rows"][0]["status_label"] == "Ready"
    assert panel["rows"][1]["session"] == "Closed"
    print("[test] PASS: readiness table maps instrument status")


def test_research_watchlist_handles_missing_snapshot(monkeypatch):
    import research.snapshot as snap

    monkeypatch.setattr(snap, "load_snapshot", lambda: {"available": False})
    panel = tp.research_watchlist()
    assert panel["available"] is False
    assert "pending" in panel["message"].lower() or "research" in panel["message"].lower()
    print("[test] PASS: research watchlist handles missing snapshot")


def test_pending_promotions_panel_empty(tmp_path, monkeypatch):
    queue_file = tmp_path / "promotion_queue.jsonl"
    import rd.promotions as prom

    monkeypatch.setattr(prom, "QUEUE_FILE", queue_file)
    panel = tp.pending_promotions_panel()
    assert panel["available"] is True
    assert panel["count"] == 0


def test_dream_lab_summary_from_events():
    events = [
        {"topic": "rd.dream.cycle.complete", "ts": 100.0, "payload": {"cycle": "hourly", "agents": ["historian"]}},
        {"topic": "rd.promotion.proposed", "ts": 99.0, "payload": {"id": "promo_x", "type": "strategy_weight"}},
    ]
    panel = tp.dream_lab_summary(events)
    assert panel["available"] is True
    assert panel["last_cycle"]["payload"]["cycle"] == "hourly"


def test_portfolio_summary_shape(monkeypatch):
    import muscle.portfolio_snapshot as ps

    monkeypatch.setattr(
        ps,
        "build_portfolio_snapshot",
        lambda refresh_positions=False: {
            "available": True,
            "account": {"equity": 10125.5, "balance": 10000.0},
            "pnl": {"floating_pnl": 125.5, "realized_today": 0.0, "total_pnl": 125.5},
            "exposure": {"open_count": 1, "invested_notional": 1100.0, "by_symbol": []},
            "equity_curve": {
                "available": True,
                "points": [{"ts": 1, "equity": 10000.0}, {"ts": 2, "equity": 10125.5}],
            },
            "message": "test",
        },
    )
    panel = tp.portfolio_summary(refresh=False)
    assert panel["available"] is True
    assert panel["account"]["equity"] == 10125.5


def test_all():
    print("=" * 60)
    print("  TRADER PANELS UNIT TESTS")
    print("=" * 60)
    test_signal_drilldown_humanizes_blocked_reason()
    test_macro_news_summarizes_decision()
    test_readiness_table_from_preflight()
    test_dream_lab_summary_from_events()
    print("=" * 60)
    print("  ALL TRADER PANELS TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
