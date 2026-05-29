#!/usr/bin/env python3
"""QA for cortex runtime integration with the guarded AgentBrain."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cortex.main as main  # noqa: E402
from cortex.agent import BrainRunResult, fallback_hold  # noqa: E402
from cortex.decision_guard import guard_decision  # noqa: E402


PUBLISHED = []


def fake_publish(topic, payload, meta=None):
    PUBLISHED.append({"topic": topic, "payload": payload, "meta": meta or {}})
    return len(PUBLISHED)


def reset_publish(monkey=True):
    PUBLISHED.clear()
    if monkey:
        main.publish = fake_publish


def test_build_brain_context_sanitizes_runtime_state():
    recent = [
        {"topic": "market.tick", "payload": {"symbol": "EURUSD", "bid": 1.08, "ask": 1.081}, "seq": 1},
        {"topic": "news.decision", "payload": {"assessment": "neutral"}, "seq": 2},
        {"topic": "macro.news.oil", "payload": {"route": "oil", "advisory_only": True}, "seq": 3},
        {"topic": "market.forecast", "payload": {"symbol": "EURUSD", "advisory_only": True}, "seq": 3},
        {"topic": "debug.secret", "payload": {"api_key": "NOPE"}, "seq": 3},
    ]
    ctx = main.build_brain_context({}, {"ok": True}, {"S1": {}}, recent, [], "qa_trigger")
    assert ctx["market_snapshot"]["symbol"] == "EURUSD"
    assert ctx["constraints"]["requires_stop_loss"] is True
    assert ctx["constraints"]["trigger"] == "qa_trigger"
    assert len(ctx["news"]) == 2
    assert any(item.get("route") == "oil" for item in ctx["news"] if isinstance(item, dict))
    assert ctx["forecasts"] == [{"symbol": "EURUSD", "advisory_only": True}]
    assert "signals" in ctx
    assert "market_structure" in ctx
    assert "evaluation_summary" in ctx["signals"]
    assert "api_key" not in str(ctx)
    print("[test] PASS: cortex brain context sanitized")


def test_publish_blocked_result_holds_and_does_not_emit_order():
    reset_publish()
    decision = fallback_hold("qa_block")
    guard = guard_decision(decision.proposal.to_guard_decision(), mode="ADVISORY")
    result = BrainRunResult(False, decision=decision, guard=guard, error="qa_block", correlation_id="qa")
    intent = main.publish_brain_result(result, trigger="qa")
    assert intent is None
    topics = [e["topic"] for e in PUBLISHED]
    assert "cortex.brain.result" in topics
    assert "cortex.decision_guard" in topics
    assert "cortex.decision" in topics
    assert "muscle.order.intent" not in topics
    assert PUBLISHED[-1]["payload"]["action"] == "HOLD"
    print("[test] PASS: blocked brain result publishes HOLD only")


def test_publish_guarded_order_emits_intent_once():
    reset_publish()
    decision = fallback_hold("seed")
    decision.proposal.action = "PROPOSE_ORDER"
    decision.proposal.symbol = "EURUSD"
    decision.proposal.side = "BUY"
    decision.proposal.qty = 0.01
    decision.proposal.sl = 1.08
    decision.proposal.tp = 1.09
    decision.proposal.confidence = 0.95
    decision.proposal.strategy_id = "MA_CROSS_SMA9_21"
    guard = guard_decision(decision.proposal.to_guard_decision(), mode="PAPER")
    assert guard.ok, guard.as_dict()
    result = BrainRunResult(True, decision=decision, guard=guard, correlation_id="qa")
    intent = main.publish_brain_result(result, trigger="qa_order")
    assert intent is not None
    intents = [e for e in PUBLISHED if e["topic"] == "muscle.order.intent"]
    assert len(intents) == 1
    assert intents[0]["payload"]["source"] == "guarded_agent_brain"
    assert intents[0]["payload"]["sl"] == 1.08
    print("[test] PASS: guarded brain order publishes one intent")


def test_agent_brain_decide_uses_injected_brain():
    reset_publish()

    class FakeBrain:
        def run(self, **kwargs):
            assert kwargs["constraints"]["trigger"] == "qa_injected"
            assert kwargs["forecasts"] == [{"symbol": "EURUSD", "advisory_only": True}]
            decision = fallback_hold("fake")
            guard = guard_decision(decision.proposal.to_guard_decision(), mode="ADVISORY")
            return BrainRunResult(True, decision=decision, guard=guard, correlation_id="fake")

    recent = [{"topic": "market.forecast", "payload": {"symbol": "EURUSD", "advisory_only": True}}]
    result = main.agent_brain_decide({}, {"ok": True}, {}, recent, [], "qa_injected", brain=FakeBrain())
    assert result.ok
    assert any(e["topic"] == "cortex.brain.result" for e in PUBLISHED)
    print("[test] PASS: cortex agent_brain_decide injected brain seam")


def test_all():
    print("=" * 60)
    print("  CORTEX BRAIN INTEGRATION TESTS")
    print("=" * 60)
    test_build_brain_context_sanitizes_runtime_state()
    test_publish_blocked_result_holds_and_does_not_emit_order()
    test_publish_guarded_order_emits_intent_once()
    test_agent_brain_decide_uses_injected_brain()
    print("=" * 60)
    print("  ALL CORTEX BRAIN INTEGRATION TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
