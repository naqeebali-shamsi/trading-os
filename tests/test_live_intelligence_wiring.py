#!/usr/bin/env python3
"""No-trade live intelligence wiring tests."""
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
sys.path.insert(0, str(ROOT / "muscle"))


def test_macro_news_reaches_event_radar_and_brain_context():
    from cortex import event_radar
    from cortex.main import build_brain_context

    payload = {
        "route": "oil",
        "advisory_only": True,
        "headlines": [{"title": "OPEC crude oil supply cut near Strait of Hormuz", "source": "qa"}],
    }
    events = [{"topic": "macro.news.oil", "seq": 10, "payload": payload}]
    radar = event_radar.classify_event(event_radar._texts_from_events(events))
    assert radar["category"] == "oil_shock"
    assert radar["advisory_only"] is True

    ctx = build_brain_context({}, {"ok": True}, {}, events + [{"topic": "macro.event_radar", "payload": radar}], [], "qa_macro")
    assert ctx["news"] == [payload]
    assert ctx["macro_events"] == [radar]
    assert "signals" in ctx
    assert "muscle.order.intent" not in str(ctx)


def test_root_bridge_auto_mode_ignores_stale_chart_dirs(tmp_path, monkeypatch):
    import muscle_main

    ipc = tmp_path / "ipc"
    ipc.mkdir()
    (ipc / "heartbeat.txt").write_text(f"{time.time()}|alive\n")
    (ipc / "tick.txt").write_text("EURUSD,1.10000,1.10002,0\n")
    chart = ipc / "chart_EURUSD"
    chart.mkdir()
    (chart / "heartbeat.txt").write_text(f"{time.time() - 999}|alive\n")
    (chart / "tick.txt").write_text("EURUSD,1.00000,1.00020,0\n")

    monkeypatch.setattr(muscle_main, "IPC_DIR", ipc)
    assert muscle_main.root_bridge_active(max_age_sec=15) is True
    assert muscle_main.is_multisymbol() is False

    (ipc / "heartbeat.txt").write_text(f"{time.time() - 999}|alive\n")
    assert muscle_main.root_bridge_active(max_age_sec=15) is False
    assert muscle_main.is_multisymbol() is True


def test_signal_generator_history_and_cooldown_gates(monkeypatch):
    from cortex import signal_generator_v2 as gen

    def candle_payload(i):
        close = 1.1000 + i * 0.0001
        open_price = close - 0.00005
        return {
            "symbol": "EURUSD",
            "timeframe": "M5",
            "ts_close": 1000 + i * 300,
            "open_price": open_price,
            "high": close + 0.0001,
            "low": open_price - 0.0001,
            "close": close,
            "range": 0.00025,
        }

    def seed_history(count):
        gen.CANDLE_HISTORY.clear()
        for i in range(count):
            gen.remember_candle(candle_payload(i))

    published = []
    now = [1000.0]
    event_seq = [0]
    controls = {
        "signal_timeframes": ["M5"],
        "signal_min_candles": 10,
        "signal_min_confidence": 0.5,
        "signal_macro_gate": False,
        "signal_direct_intents": False,
    }

    def mock_subscribe(topic, since_seq=0, limit=100):
        if topic != "candle.close":
            return []
        if since_seq == 0 and limit >= 500:
            return []
        event_seq[0] += 1
        return [{"seq": event_seq[0], "topic": "candle.close", "payload": candle_payload(999)}]

    gen.CANDLE_HISTORY.clear()
    gen.LAST_SIGNAL_TIME.clear()
    monkeypatch.setattr(gen, "publish", lambda topic, payload: published.append((topic, payload)) or len(published))
    monkeypatch.setattr(gen.time, "time", lambda: now[0])
    monkeypatch.setattr(gen.time, "sleep", lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(gen, "current_seq", lambda: 0)
    monkeypatch.setattr(gen, "subscribe", mock_subscribe)
    monkeypatch.setattr(gen, "bootstrap_candle_history", lambda limit=500: 0)
    monkeypatch.setattr(gen, "current_controls", lambda: controls)
    monkeypatch.setattr(
        gen,
        "pattern_scan",
        lambda hist, symbol, tf: [{"pattern": "bullish_engulfing", "direction": "bullish", "strength": "strong"}],
    )
    monkeypatch.setattr(gen, "latest_tick", lambda symbol: {"bid": 1.1013, "ask": 1.10135, "symbol": symbol})
    monkeypatch.setattr(gen, "latest_market_regime", lambda: "trending")
    monkeypatch.setattr(gen, "macro_gate", lambda symbol, controls=None: (True, "ok", {}))

    seed_history(8)
    try:
        gen.run()
    except KeyboardInterrupt:
        pass
    assert not any(topic == "market.signal" for topic, _ in published)

    def signal_count():
        return sum(1 for topic, _ in published if topic == "market.signal")

    seed_history(14)
    event_seq[0] = 0
    try:
        gen.run()
    except KeyboardInterrupt:
        pass
    assert signal_count() >= 1
    first_count = signal_count()

    try:
        gen.run()
    except KeyboardInterrupt:
        pass
    assert signal_count() == first_count, "cooldown should block immediate duplicate signal"

    now[0] += gen.SIGNAL_COOLDOWN_SEC + 1
    try:
        gen.run()
    except KeyboardInterrupt:
        pass
    assert signal_count() > first_count, "signal should resume after cooldown expires"


def test_all():
    print("=" * 60)
    print("  LIVE INTELLIGENCE WIRING TESTS")
    print("=" * 60)
    test_macro_news_reaches_event_radar_and_brain_context()
    print("[test] PASS: macro news reaches Event Radar and brain context")
    print("=" * 60)
    print("  ALL LIVE INTELLIGENCE WIRING TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
