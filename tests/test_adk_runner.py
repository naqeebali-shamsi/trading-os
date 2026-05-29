#!/usr/bin/env python3
"""Tests for ADK/A2A brain runner wrapper."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_adk_runner_schemas_and_card():
    from cortex.adk_runner import ADKBrainRunner, agent_card

    runner = ADKBrainRunner()
    export = runner.schemas()
    card = agent_card()

    assert export["schema_version"] == 1
    assert "BrainDecision" in export["schemas"]
    assert card["name"] == "trading-os-brain"
    assert card["output_schema_ref"] == "BrainDecision"


def test_adk_runner_delegates_to_brain(monkeypatch):
    from cortex import adk_runner

    calls = []

    class StubBrain:
        def run(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "kwargs": kwargs}

    runner = adk_runner.ADKBrainRunner(brain=StubBrain())
    result = runner.decide(market_snapshot={"EURUSD": 1.08}, decision_mode="ADVISORY")
    assert result["ok"] is True
    assert calls[0]["market_snapshot"]["EURUSD"] == 1.08
