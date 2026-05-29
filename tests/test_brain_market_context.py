#!/usr/bin/env python3
"""Tests for multi-TF market structure in brain context."""
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from cortex.brain_market_context import (  # noqa: E402
    build_symbol_structure,
    resolve_context_symbols,
)
from cortex.brain_signal_context import detect_signal_brain_trigger  # noqa: E402
import cortex.main as main  # noqa: E402


def test_resolve_context_symbols_prefers_ready_stocks():
    health = {
        "enabled_stocks": ["NVDA:READY", "MSFT:BLOCKED_SESSION_CLOSED"],
        "ipc_mode": {"fresh_charts": ["chart_EURUSD", "chart_NVDA"]},
    }
    symbols = resolve_context_symbols(health=health)
    assert "NVDA" in symbols
    assert "EURUSD" in symbols


def test_build_symbol_structure_includes_patterns():
    history = {
        ("EURUSD", "M5"): deque(
            [
                {
                    "symbol": "EURUSD",
                    "timeframe": "M5",
                    "ts_close": 1000 + i * 300,
                    "open_price": 1.10 + i * 0.0001,
                    "high": 1.101 + i * 0.0001,
                    "low": 1.099 + i * 0.0001,
                    "close": 1.1005 + i * 0.0001,
                }
                for i in range(12)
            ],
            maxlen=50,
        )
    }
    rows = build_symbol_structure("EURUSD", history, timeframes=["M5"])
    assert "M5" in rows
    assert rows["M5"]["trend"] in {"uptrend", "downtrend", "range/consolidation", "indeterminate"}
    assert "patterns" in rows["M5"]


def test_detect_signal_brain_trigger_near_miss():
    now = time.time()
    events = [
        {
            "topic": "market.signal.candidate",
            "ts": now,
            "payload": {"symbol": "EURUSD", "blocked_reason": "below_min_confidence", "confidence": 0.73},
        }
    ]
    needed, trigger = detect_signal_brain_trigger(events, now=now)
    assert needed is True
    assert trigger == "signal_near_miss"


def test_detect_decision_needed_prioritizes_signal_near_miss():
    now = time.time()
    recent = [
        {"topic": "market.tick", "payload": {"bid": 1.0}, "ts": now},
        {
            "topic": "market.signal.candidate",
            "ts": now,
            "payload": {"symbol": "NVDA", "blocked_reason": "below_min_confidence", "confidence": 0.74},
        },
    ]
    wm = {"last_llm_call": 0}
    needed, trigger = main.detect_decision_needed(wm, recent, {})
    assert needed is True
    assert trigger == "signal_near_miss"


def test_build_brain_context_includes_market_structure(monkeypatch):
    monkeypatch.setattr(
        main,
        "build_market_structure_context",
        lambda **kwargs: {"symbols": {"EURUSD": {"M5": {"trend": "uptrend"}}}, "timeframes": ["M5"]},
    )
    ctx = main.build_brain_context({}, {"ok": True}, {}, [], [], "signal_near_miss")
    assert ctx["market_structure"]["symbols"]["EURUSD"]["M5"]["trend"] == "uptrend"
