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


def test_safe_panel_timeout_serves_fallback():
    import time as _time

    tp._PANEL_CACHE.clear()

    def slow_builder():
        _time.sleep(0.5)
        return {"available": True, "value": 1}

    result = tp._safe_panel(
        "slow_no_cache",
        slow_builder,
        {"available": False, "message": "Slow panel"},
        timeout=0.05,
    )
    assert result["available"] is False
    assert result["stale"] is True
    assert "timed out" in result["message"]


def test_safe_panel_timeout_serves_cached_value():
    import time as _time

    tp._PANEL_CACHE.clear()

    fast_result = tp._safe_panel(
        "cached_panel",
        lambda: {"available": True, "value": 42, "message": "fresh"},
        {"available": False},
        timeout=1.0,
        cache=True,
    )
    assert fast_result["value"] == 42

    def slow_builder():
        _time.sleep(0.5)
        return {"available": True, "value": 99}

    stale = tp._safe_panel(
        "cached_panel",
        slow_builder,
        {"available": False},
        timeout=0.05,
        cache=True,
    )
    assert stale["value"] == 42
    assert stale["stale"] is True
    assert "cached" in stale["message"].lower()


def test_forecast_thesis_labels_staleness_by_timeframe(monkeypatch):
    import research.snapshot as snap

    monkeypatch.setattr(snap, "load_snapshot", lambda: {"available": False})
    base = 1_000.0
    events = [
        {"topic": "market.forecast", "ts": base, "payload": {"symbol": "EURUSD", "timeframe": "M5", "direction": "up"}},
    ]
    fresh = tp.forecast_thesis_panel(events, now=base + 100)["rows"][0]
    assert fresh["staleness"] == "fresh"
    assert fresh["age_sec"] == 100.0

    aging = tp.forecast_thesis_panel(events, now=base + 600)["rows"][0]
    assert aging["staleness"] == "aging"

    stale = tp.forecast_thesis_panel(events, now=base + 5_000)["rows"][0]
    assert stale["staleness"] == "stale"


def test_forecast_thesis_flags_macro_conflict(monkeypatch):
    import research.snapshot as snap

    monkeypatch.setattr(snap, "load_snapshot", lambda: {"available": False})
    events = [
        {
            "topic": "market.forecast.NVDA",
            "ts": 10.0,
            "payload": {"symbol": "NVDA", "timeframe": "M15", "direction": "up", "advisory_only": True},
        },
        {
            "topic": "cortex.decision",
            "ts": 11.0,
            "payload": {
                "recommendation": "halt_symbols",
                "assessment": "risk_off",
                "affected_symbols": {"NVDA": 0.9},
                "halt_symbols": ["NVDA"],
            },
        },
    ]
    row = tp.forecast_thesis_panel(events, now=20.0)["rows"][0]
    assert row["macro_conflict"]["conflict"] is True
    assert row["macro_conflict"]["severity"] == "high"
    assert tp.forecast_thesis_panel(events, now=20.0)["conflict_count"] == 1


def test_forecast_thesis_bullish_vs_caution_conflict(monkeypatch):
    import research.snapshot as snap

    monkeypatch.setattr(snap, "load_snapshot", lambda: {"available": False})
    events = [
        {
            "topic": "market.forecast.XAUUSD",
            "ts": 10.0,
            "payload": {"symbol": "XAUUSD", "timeframe": "M5", "direction": "up", "advisory_only": True},
        },
        {
            "topic": "cortex.decision",
            "ts": 11.0,
            "payload": {"recommendation": "reduce_size", "assessment": "caution", "affected_symbols": {"XAUUSD": 0.8}},
        },
    ]
    row = tp.forecast_thesis_panel(events, now=20.0)["rows"][0]
    assert row["macro_conflict"]["conflict"] is True
    assert row["macro_conflict"]["severity"] == "medium"


def test_forecast_thesis_includes_recent_history(monkeypatch):
    import research.snapshot as snap

    monkeypatch.setattr(snap, "load_snapshot", lambda: {"available": False})
    events = [
        {"topic": "market.forecast", "ts": 100.0, "payload": {"symbol": "EURUSD", "timeframe": "M5", "direction": "up", "confidence": 0.7}},
        {"topic": "market.forecast", "ts": 99.0, "payload": {"symbol": "EURUSD", "timeframe": "M5", "direction": "down", "confidence": 0.5}},
        {"topic": "market.forecast", "ts": 98.0, "payload": {"symbol": "EURUSD", "timeframe": "M5", "direction": "up", "confidence": 0.6}},
    ]
    panel = tp.forecast_thesis_panel(events, now=120.0)
    row = panel["rows"][0]
    assert len(row["history"]) == 3
    assert [h["direction"] for h in row["history"]] == ["up", "down", "up"]
    assert row["direction"] == "up"
    assert row["confidence"] == 0.7


def test_forecast_thesis_skips_malformed_events(monkeypatch):
    import research.snapshot as snap

    monkeypatch.setattr(snap, "load_snapshot", lambda: {"available": False})
    events = [
        {"topic": "market.forecast", "ts": 1.0, "payload": {"timeframe": "M5"}},
        {"topic": "market.tick", "ts": 2.0, "payload": {"symbol": "EURUSD"}},
        "not-a-dict",
        {"topic": "market.forecast", "ts": 3.0, "payload": {"symbol": "EURUSD", "timeframe": "M5", "direction": "up"}},
    ]
    panel = tp.forecast_thesis_panel(events, now=10.0)
    assert panel["count"] == 1
    assert panel["rows"][0]["symbol"] == "EURUSD"


def test_forecast_thesis_reads_nested_timesfm_payload(monkeypatch):
    import research.snapshot as snap

    monkeypatch.setattr(snap, "load_snapshot", lambda: {"available": False})
    events = [
        {
            "topic": "market.forecast.EURUSD",
            "ts": 100.0,
            "payload": {
                "symbol": "EURUSD",
                "timeframe": "M5",
                "last_close": 1.1,
                "forecast": {"direction": "up", "confidence": 0.55, "predicted_close": [1.11, 1.12]},
            },
        },
    ]
    row = tp.forecast_thesis_panel(events, now=120.0)["rows"][0]
    assert row["direction"] == "up"
    assert row["confidence"] == 0.55
    assert row["predicted_close"] == 1.12


def test_edge_validation_panel_reads_gate_report(tmp_path):
    import json

    report_path = tmp_path / "edge_gate_report.json"
    report_path.write_text(
        json.dumps(
            {
                "candidate_count": 10,
                "label_count": 25,
                "group_count": 2,
                "promotable_count": 1,
                "groups": [
                    {
                        "symbol": "EURUSD",
                        "timeframe": "M5",
                        "samples": 25,
                        "win_rate": 0.6,
                        "edge": 0.001,
                        "profit_factor": 1.5,
                        "promotable": True,
                        "reasons": [],
                    },
                    {
                        "symbol": "NVDA",
                        "timeframe": "M15",
                        "samples": 5,
                        "win_rate": 0.4,
                        "edge": -0.01,
                        "profit_factor": 0.8,
                        "promotable": False,
                        "reasons": ["samples<20"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    panel = tp.edge_validation_panel(path=report_path)
    assert panel["available"] is True
    assert panel["promotable_count"] == 1
    assert len(panel["groups"]) == 2


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
