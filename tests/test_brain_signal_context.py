#!/usr/bin/env python3
"""Tests for signal-engine context fed into AgentBrain."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from cortex.brain_signal_context import build_signal_context, compact_intent  # noqa: E402
from cortex.agent import build_context  # noqa: E402
import cortex.main as main  # noqa: E402


def _eval(symbol, reason, status="skipped", confidence=None, patterns=None):
    return {
        "topic": "market.signal.evaluation",
        "ts": time.time(),
        "payload": {
            "symbol": symbol,
            "timeframe": "M5",
            "status": status,
            "reason": reason,
            "stage": "confidence" if reason == "below_min_confidence" else "pattern_scan",
            "confidence": confidence,
            "patterns": patterns or [],
        },
    }


def test_build_signal_context_summarizes_evaluations():
    now = time.time()
    events = [
        _eval("EURUSD", "timeframe_disabled"),
        _eval("EURUSD", "below_min_confidence", status="blocked", confidence=0.73, patterns=[{"pattern": "bearish_pinbar"}]),
        _eval("GBPUSD", "no_patterns"),
        {
            "topic": "market.signal.candidate",
            "ts": now,
            "payload": {
                "symbol": "EURUSD",
                "side": "SELL",
                "confidence": 0.73,
                "strategy_id": "MA_CROSS_SMA9_21",
                "blocked_reason": "below_min_confidence",
                "patterns": [{"pattern": "bearish_pinbar", "direction": "bearish"}],
            },
        },
        {
            "topic": "market.signal",
            "ts": now,
            "payload": {
                "symbol": "USDJPY",
                "side": "BUY",
                "confidence": 0.76,
                "strategy_id": "MA_CROSS_SMA9_21",
                "sl": 158.8,
                "tp": 159.0,
                "qty": 0.01,
                "patterns": [{"pattern": "bullish_engulfing"}],
            },
        },
    ]
    ctx = build_signal_context(events, now=now, window_sec=3600)
    assert ctx["emitted"][0]["symbol"] == "USDJPY"
    assert ctx["candidates"][0]["reason"] == "below_min_confidence"
    assert ctx["evaluation_summary"]["by_reason"]["below_min_confidence"] == 1
    assert any(row["symbol"] == "EURUSD" for row in ctx["evaluation_summary"]["latest_per_symbol"])
    assert "gates" in ctx


def test_build_brain_context_includes_signals():
    events = [
        {"topic": "market.tick", "payload": {"symbol": "EURUSD", "bid": 1.08, "ask": 1.081}, "ts": time.time()},
        _eval("EURUSD", "below_min_confidence", status="blocked", confidence=0.71),
    ]
    ctx = main.build_brain_context({}, {"ok": True}, {}, events, [], "volatility_spike")
    assert ctx["signals"]["evaluation_summary"]["total"] >= 1
    assert ctx["market_snapshot"]["symbol"] == "EURUSD"


def test_build_context_exposes_signals_to_llm_payload():
    signal_ctx = {
        "window_sec": 3600,
        "emitted": [{"symbol": "EURUSD", "side": "BUY", "confidence": 0.8}],
        "evaluation_summary": {"total": 1, "by_reason": {"no_patterns": 1}},
    }
    ctx = build_context({"symbol": "EURUSD"}, signals=signal_ctx, constraints={"default_action": "HOLD"})
    assert ctx["signals"]["emitted"][0]["side"] == "BUY"
    assert "signals" in ctx


def test_compact_intent_strips_heavy_fields():
    row = compact_intent(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "confidence": 0.8,
            "strategy_id": "MA_CROSS_SMA9_21",
            "sizing": {"atr": 0.001, "unit_label": "pips"},
            "patterns": [{"pattern": "doji", "direction": "neutral"}],
        }
    )
    assert row["patterns"] == ["doji"]
    assert "sizing" not in row
