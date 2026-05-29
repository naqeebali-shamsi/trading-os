#!/usr/bin/env python3
"""Tests for signal generator v2 local candle history."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex import signal_generator_v2 as sg  # noqa: E402


def candle(i, symbol="EURUSD", tf="M5", close=None, unit=0.0001):
    close = close if close is not None else 1.1000 + i * 0.0001
    open_price = close - unit * 0.5
    return {
        "symbol": symbol,
        "timeframe": tf,
        "ts_close": 1000 + i * 300,
        "open_price": open_price,
        "high": close + unit,
        "low": open_price - unit,
        "close": close,
        "range": unit * 2.5,
        "body_size": abs(close - open_price),
        "upper_shadow": unit,
        "lower_shadow": unit,
        "is_bullish": close >= open_price,
    }


def test_remember_candle_keeps_local_history_and_dedupes():
    sg.CANDLE_HISTORY.clear()
    rows = []
    for i in range(12):
        rows = sg.remember_candle(candle(i))
    assert len(rows) == 12
    rows2 = sg.remember_candle(candle(11))
    assert len(rows2) == 12
    assert sg.CANDLE_HISTORY[("EURUSD", "M5")][-1]["ts_close"] == 1000 + 11 * 300
    print("[test] PASS: signal generator local candle history")


def test_build_intent_uses_regime_in_confidence():
    patterns = [{"pattern": "bullish_engulfing", "direction": "bullish", "strength": "strong"}]
    candles = [candle(i) for i in range(14)]
    intent = sg.build_intent("EURUSD", "BUY", patterns, candles, "MA_CROSS_SMA9_21", regime="trending", tick={"bid": 1.1013, "ask": 1.10135})
    assert intent["confidence"] >= 0.75
    assert intent["regime"] == "trending"
    assert intent["sl"] and intent["tp"]
    print("[test] PASS: signal intent confidence includes regime")


def test_build_intent_is_instrument_aware_for_usdjpy():
    patterns = [{"pattern": "bullish_engulfing", "direction": "bullish", "strength": "strong"}]
    candles = [candle(i, symbol="USDJPY", close=155.00 + i * 0.01, unit=0.01) for i in range(14)]
    intent = sg.build_intent("USDJPY", "BUY", patterns, candles, "MA_CROSS_SMA9_21", regime="trending", tick={"bid": 155.13, "ask": 155.135})
    assert intent["symbol"] == "USDJPY"
    assert intent["sizing"]["unit"] == 0.01
    assert intent["sizing"]["unit_label"] == "pips"
    assert round(intent["price"] - intent["sl"], 3) >= 0.05
    assert intent["qty"] == 0.01
    print("[test] PASS: USDJPY intent uses JPY pip sizing")


def test_build_intent_is_instrument_aware_for_xauusd():
    patterns = [{"pattern": "bearish_engulfing", "direction": "bearish", "strength": "strong"}]
    candles = [candle(i, symbol="XAUUSD", close=3200.0 + i * 0.5, unit=0.01) for i in range(14)]
    intent = sg.build_intent("XAUUSD", "SELL", patterns, candles, "MA_CROSS_SMA9_21", regime="trending", tick={"bid": 3206.4, "ask": 3206.7})
    assert intent["symbol"] == "XAUUSD"
    assert intent["sizing"]["unit"] == 0.01
    assert intent["sizing"]["unit_label"] == "points"
    assert intent["sl"] > intent["price"] > intent["tp"]
    assert intent["qty"] == 0.01
    print("[test] PASS: XAUUSD intent uses metal point sizing")


def test_macro_gate_blocks_high_confidence_risk_off_for_symbol():
    old_subscribe = sg.subscribe
    try:
        sg.subscribe = lambda topic, limit=1, since_seq=0: [{
            "ts": sg.time.time(),
            "payload": {"candidate_symbols": ["USDJPY"], "severity": "high", "bias": "risk_off", "confidence": 0.95},
        }] if topic == "macro.event_radar" else []
        ok, reason, ctx = sg.macro_gate("USDJPY", controls={"signal_macro_gate": True, "signal_macro_gate_max_age_sec": 900})
        assert not ok
        assert reason == "macro_event_radar_halt"
        assert ctx["confidence"] == 0.95
    finally:
        sg.subscribe = old_subscribe
    print("[test] PASS: macro gate blocks high-confidence risk-off")


def test_macro_gate_scopes_news_halt_to_symbol():
    old_subscribe = sg.subscribe
    now = sg.time.time()
    news = {
        "source": "news_orchestrator",
        "recommendation": "halt_symbols",
        "halt_symbols": ["EURUSD", "XAUUSD"],
        "ts": now,
        "expires_ts": now + 900,
        "ttl_sec": 900,
    }
    try:
        def fake_subscribe(topic, limit=1, since_seq=0, max_age_sec=60):
            if topic == "cortex.decision":
                return [{"ts": now, "payload": news}]
            return []

        sg.subscribe = fake_subscribe
        ok, reason, _ = sg.macro_gate("GOOGL", controls={"signal_macro_gate": True, "signal_macro_gate_max_age_sec": 900})
        assert ok, reason
        ok, reason, _ = sg.macro_gate("EURUSD", controls={"signal_macro_gate": True, "signal_macro_gate_max_age_sec": 900})
        assert not ok
        assert reason == "cortex.decision_news_halt_symbol"
    finally:
        sg.subscribe = old_subscribe
    print("[test] PASS: macro gate scopes news halt to symbol")


def test_runtime_signal_controls_accept_m1_and_lower_warmup():
    controls = {"signal_timeframes": ["M1", "M5"], "signal_min_candles": 5}
    assert sg.signal_timeframes(controls) == {"M1", "M5"}
    assert sg.signal_min_candles(controls) == 5
    print("[test] PASS: runtime signal controls configure timeframes and warmup")


def test_select_strategy_respects_instrument_allowed_strategies():
    patterns = [{"pattern": "bullish_engulfing", "direction": "bullish", "strength": "strong"}]
    selected = sg.select_strategy_for_symbol("USDJPY", "trending", patterns)
    assert selected is not None
    assert selected["id"] == "MA_CROSS_SMA9_21"
    selected_xau = sg.select_strategy_for_symbol("XAUUSD", "trending", patterns)
    assert selected_xau is not None
    assert selected_xau["id"] == "MA_CROSS_SMA9_21"
    print("[test] PASS: signal strategy selection respects instrument allowlists")


def test_bootstrap_candle_history_loads_from_bus():
    sg.CANDLE_HISTORY.clear()
    old_subscribe = sg.subscribe
    try:
        sg.subscribe = lambda topic, limit=500, since_seq=0: [
            {"seq": i + 1, "payload": candle(i, tf="M5")} for i in range(12)
        ]
        loaded = sg.bootstrap_candle_history()
        assert loaded == 12
        assert len(sg.CANDLE_HISTORY[("EURUSD", "M5")]) == 12
    finally:
        sg.subscribe = old_subscribe
    print("[test] PASS: signal generator bootstraps candle history")


def test_publish_signal_evaluation_has_training_fields():
    old_publish = sg.publish
    events = []
    try:
        sg.publish = lambda topic, payload: events.append((topic, payload))
        sg.publish_signal_evaluation(candle(1), status="skipped", reason="no_patterns", stage="pattern_scan", candles=12)
    finally:
        sg.publish = old_publish
    assert events[0][0] == "market.signal.evaluation"
    payload = events[0][1]
    assert payload["symbol"] == "EURUSD"
    assert payload["timeframe"] == "M5"
    assert payload["reason"] == "no_patterns"
    assert payload["candles"] == 12
    print("[test] PASS: signal evaluation rows include training fields")


def test_all():
    print("=" * 60)
    print("  SIGNAL GENERATOR V2 TESTS")
    print("=" * 60)
    test_remember_candle_keeps_local_history_and_dedupes()
    test_build_intent_uses_regime_in_confidence()
    test_build_intent_is_instrument_aware_for_usdjpy()
    test_build_intent_is_instrument_aware_for_xauusd()
    test_macro_gate_blocks_high_confidence_risk_off_for_symbol()
    test_macro_gate_scopes_news_halt_to_symbol()
    test_runtime_signal_controls_accept_m1_and_lower_warmup()
    test_select_strategy_respects_instrument_allowed_strategies()
    test_bootstrap_candle_history_loads_from_bus()
    test_publish_signal_evaluation_has_training_fields()
    print("=" * 60)
    print("  ALL SIGNAL GENERATOR V2 TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
