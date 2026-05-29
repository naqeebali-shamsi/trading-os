#!/usr/bin/env python3
"""Provider-agnostic Trading OS agent brain orchestrator.

Phase 2 wires the previously isolated primitives together:
- hook gateway for pre/post policy checks
- LLM adapter for remote or local JSON inference
- typed brain schemas for deterministic validation
- decision guard for final action safety

This module intentionally does not execute trades. It returns an auditable
BrainRunResult and, at most, a guarded proposal that downstream execution layers
must explicitly consume.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from cortex.agent_schemas import (
    BrainDecision,
    MacroAssessment,
    MarketStructureAssessment,
    RiskAssessment,
    SchemaValidationError,
    TradeProposal,
    schema_catalog,
)
from cortex.decision_guard import GuardResult, guard_decision
from cortex.llm_client import LLMClient, LLMResult, get_client
from cortex.llm_status import build_llm_status_payload, merge_llm_into_health, publish_llm_status
from kernel.hooks import HookManager, HookResult, get_hook_manager

ROOT = Path(__file__).resolve().parent.parent
AUDIT_LOG = ROOT / "logs" / "agent_brain.jsonl"

SYSTEM_PROMPT = """You are the Trading OS advisory brain.
Return one JSON object only. Never request tool execution. Never claim certainty.
You may only propose orders when risk allows it and a stop loss is present.
Use the signals block to understand pattern-engine activity (emitted, blocked, near-miss).
Use market_structure for multi-timeframe trend/pattern context across symbols.
Do not blindly repeat a signal-engine proposal; add independent conviction or explain HOLD.
Schema:
{
  "macro": {"risk_regime": "risk_on|risk_off|neutral", "affected_symbols": [], "blackout_recommended": false, "confidence": 0.0, "reason": ""},
  "market": [{"symbol": "EURUSD", "bias": "bullish|bearish|neutral", "setup_quality": 0.0, "invalidations": [], "reason": "", "timeframes": {}}],
  "risk": {"allow_new_risk": false, "max_risk_pct": 0.0, "reasons": [], "symbol_limits": {}, "severity": "low|medium|high|critical"},
  "proposal": {"action": "HOLD|PROPOSE_ORDER|CLOSE|REDUCE_RISK", "symbol": null, "side": null, "qty": null, "sl": null, "tp": null, "confidence": 0.0, "strategy_id": null, "reasoning": "", "urgency": "immediate|watch|defer"},
  "warnings": [],
  "market_outlook": ""
}
"""


@dataclass
class BrainRunResult:
    ok: bool
    decision: Optional[BrainDecision] = None
    guard: Optional[GuardResult] = None
    llm: Optional[LLMResult] = None
    blocked_by_hook: Optional[str] = None
    error: Optional[str] = None
    correlation_id: str = ""
    elapsed_ms: int = 0
    audit_path: str = str(AUDIT_LOG)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "decision": self.decision.as_dict() if self.decision else None,
            "guard": self.guard.as_dict() if self.guard else None,
            "llm": self.llm.as_dict() if self.llm else None,
            "blocked_by_hook": self.blocked_by_hook,
            "error": self.error,
            "correlation_id": self.correlation_id,
            "elapsed_ms": self.elapsed_ms,
            "audit_path": self.audit_path,
            "events": self.events,
        }


def _safe_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def build_context(
    market_snapshot: Optional[Dict[str, Any]] = None,
    *,
    news: Optional[List[Dict[str, Any]]] = None,
    positions: Optional[List[Dict[str, Any]]] = None,
    forecasts: Optional[List[Dict[str, Any]]] = None,
    macro_events: Optional[List[Dict[str, Any]]] = None,
    signals: Optional[Dict[str, Any]] = None,
    market_structure: Optional[Dict[str, Any]] = None,
    constraints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build minimal, serializable context for the LLM.

    Do not include secrets, filesystem paths, shell affordances, or bridge command
    files. The context is pure data and safe to log.
    """
    return {
        "market_snapshot": market_snapshot or {},
        "news": news or [],
        "positions": positions or [],
        "forecasts": forecasts or [],
        "macro_events": macro_events or [],
        "signals": signals or {},
        "market_structure": market_structure or {},
        "constraints": constraints or {"default_action": "HOLD", "requires_stop_loss": True},
        "schema_catalog": schema_catalog(),
    }


def _parse_decision(parsed: Dict[str, Any]) -> BrainDecision:
    macro_raw = parsed.get("macro") or {}
    market_raw = parsed.get("market") or []
    risk_raw = parsed.get("risk") or {}
    proposal_raw = parsed.get("proposal") or {}

    if not isinstance(market_raw, list):
        raise SchemaValidationError("market_not_list")

    decision = BrainDecision(
        macro=MacroAssessment(**macro_raw),
        market=[MarketStructureAssessment(**item) for item in market_raw],
        risk=RiskAssessment(**risk_raw),
        proposal=TradeProposal(**proposal_raw),
        warnings=list(parsed.get("warnings") or []),
        market_outlook=str(parsed.get("market_outlook") or ""),
    )
    return decision.validate()


def fallback_hold(reason: str) -> BrainDecision:
    return BrainDecision(
        macro=MacroAssessment("neutral", confidence=0.0, reason=f"fallback:{reason}"),
        market=[],
        risk=RiskAssessment(False, 0.0, reasons=[reason], severity="high"),
        proposal=TradeProposal("HOLD", confidence=0.0, reasoning=reason, urgency="defer"),
        warnings=[reason],
        market_outlook="fallback_hold",
    ).validate()


class AgentBrain:
    def __init__(self, *, llm_client: Optional[LLMClient] = None, hook_manager: Optional[HookManager] = None):
        self.llm_client = llm_client or get_client()
        self.hooks = hook_manager or get_hook_manager()

    def _audit(self, result: BrainRunResult):
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = result.as_dict()
        record["ts"] = time.time()
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    @staticmethod
    def _blocked(hook: str, result: HookResult, *, correlation_id: str, started: float, llm: Optional[LLMResult] = None) -> BrainRunResult:
        decision = fallback_hold(f"hook_block:{hook}:{result.reason}")
        guard = guard_decision(decision.proposal.to_guard_decision(), mode="ADVISORY")
        return BrainRunResult(False, decision=decision, guard=guard, llm=llm, blocked_by_hook=hook, error=result.reason, correlation_id=correlation_id, elapsed_ms=int((time.time() - started) * 1000))

    def _emit_llm_status(
        self,
        llm: LLMResult,
        *,
        correlation_id: str,
        trigger: Optional[str] = None,
    ) -> Dict[str, Any]:
        raw_meta = llm.raw_meta or {}
        payload = build_llm_status_payload(
            ok=llm.ok,
            provider=llm.provider,
            model=llm.model,
            error=llm.error,
            http_code=raw_meta.get("http_code"),
            error_code=raw_meta.get("error_code") or llm.as_dict().get("error_code"),
            latency_ms=llm.latency_ms,
            correlation_id=correlation_id,
            trigger=trigger,
            http_body=raw_meta.get("http_body"),
        )
        publish_llm_status(payload)
        merge_llm_into_health(
            ok=llm.ok,
            provider=llm.provider,
            model=llm.model,
            error=llm.error,
            error_code=payload.get("error_code"),
            latency_ms=llm.latency_ms,
            correlation_id=correlation_id,
            trigger=trigger,
        )
        return payload

    def run(
        self,
        *,
        market_snapshot: Optional[Dict[str, Any]] = None,
        news: Optional[List[Dict[str, Any]]] = None,
        positions: Optional[List[Dict[str, Any]]] = None,
        forecasts: Optional[List[Dict[str, Any]]] = None,
        macro_events: Optional[List[Dict[str, Any]]] = None,
        signals: Optional[Dict[str, Any]] = None,
        market_structure: Optional[Dict[str, Any]] = None,
        constraints: Optional[Dict[str, Any]] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        decision_mode: str = "ADVISORY",
        human_approved: bool = False,
        correlation_id: Optional[str] = None,
        trigger: Optional[str] = None,
    ) -> BrainRunResult:
        started = time.time()
        corr = correlation_id or f"brain-{int(started * 1000)}"
        events: List[Dict[str, Any]] = []

        pre_context = self.hooks.run("pre_context_build", {"market_snapshot": market_snapshot or {}}, actor="agent_brain", risk_level="low", correlation_id=corr)
        events.append({"hook": "pre_context_build", "result": pre_context.as_dict()})
        if not pre_context.allow:
            result = self._blocked("pre_context_build", pre_context, correlation_id=corr, started=started)
            result.events = events
            self._audit(result)
            return result

        context = build_context(
            market_snapshot,
            news=news,
            positions=positions,
            forecasts=forecasts,
            macro_events=macro_events,
            signals=signals,
            market_structure=market_structure,
            constraints=constraints,
        )
        post_context = self.hooks.run("post_context_build", {"context_keys": sorted(context.keys())}, actor="agent_brain", risk_level="low", correlation_id=corr)
        events.append({"hook": "post_context_build", "result": post_context.as_dict()})
        if not post_context.allow:
            result = self._blocked("post_context_build", post_context, correlation_id=corr, started=started)
            result.events = events
            self._audit(result)
            return result

        pre_llm = self.hooks.run("pre_llm_call", {"provider": provider, "model": model, "human_approved": human_approved}, actor="agent_brain", risk_level="high", correlation_id=corr)
        events.append({"hook": "pre_llm_call", "result": pre_llm.as_dict()})
        if not pre_llm.allow:
            result = self._blocked("pre_llm_call", pre_llm, correlation_id=corr, started=started)
            result.events = events
            self._audit(result)
            return result

        prompt = "Analyze this context and return the required JSON only:\n" + _safe_json(context)
        llm = self.llm_client.complete_json(prompt, system=SYSTEM_PROMPT, provider=provider, model=model, temperature=0.1, max_tokens=1600)
        self._emit_llm_status(llm, correlation_id=corr, trigger=trigger)
        if not llm.ok:
            decision = fallback_hold(f"llm_error:{llm.error}")
            guard = guard_decision(decision.proposal.to_guard_decision(), mode=decision_mode, market_snapshot=market_snapshot)
            result = BrainRunResult(False, decision=decision, guard=guard, llm=llm, error=llm.error, correlation_id=corr, elapsed_ms=int((time.time() - started) * 1000), events=events)
            self._audit(result)
            return result

        post_llm = self.hooks.run("post_llm_call", {"ok": llm.ok, "parsed": llm.parsed, "error": llm.error}, actor="agent_brain", risk_level="high", correlation_id=corr)
        events.append({"hook": "post_llm_call", "result": post_llm.as_dict()})
        if not post_llm.allow:
            result = self._blocked("post_llm_call", post_llm, correlation_id=corr, started=started, llm=llm)
            result.events = events
            self._audit(result)
            return result

        pre_decision = self.hooks.run("pre_decision", {"parsed": llm.parsed}, actor="agent_brain", risk_level="high", correlation_id=corr)
        events.append({"hook": "pre_decision", "result": pre_decision.as_dict()})
        if not pre_decision.allow:
            result = self._blocked("pre_decision", pre_decision, correlation_id=corr, started=started, llm=llm)
            result.events = events
            self._audit(result)
            return result

        try:
            decision = _parse_decision(llm.parsed or {})
        except Exception as exc:
            decision = fallback_hold(f"schema_error:{exc}")
            guard = guard_decision(decision.proposal.to_guard_decision(), mode=decision_mode, market_snapshot=market_snapshot)
            result = BrainRunResult(False, decision=decision, guard=guard, llm=llm, error=f"schema_error:{exc}", correlation_id=corr, elapsed_ms=int((time.time() - started) * 1000), events=events)
            self._audit(result)
            return result

        guard = guard_decision(decision.proposal.to_guard_decision(), mode=decision_mode, market_snapshot=market_snapshot)
        if decision.proposal.action == "PROPOSE_ORDER":
            # Instrument validation already happens inside the decision guard.
            inst_ok = guard.ok and guard.reason == "ok"
            order_hook = self.hooks.run("pre_order_intent", {"guard": guard.as_dict(), "instrument": {"ok": inst_ok}, "human_approved": human_approved}, actor="agent_brain", risk_level="critical", correlation_id=corr)
            events.append({"hook": "pre_order_intent", "result": order_hook.as_dict()})
            if not order_hook.allow:
                result = BrainRunResult(False, decision=decision, guard=guard, llm=llm, blocked_by_hook="pre_order_intent", error=order_hook.reason, correlation_id=corr, elapsed_ms=int((time.time() - started) * 1000), events=events)
                self._audit(result)
                return result

        post_decision = self.hooks.run("post_decision", {"decision": decision.as_dict(), "guard": guard.as_dict()}, actor="agent_brain", risk_level="medium", correlation_id=corr)
        events.append({"hook": "post_decision", "result": post_decision.as_dict()})
        ok = guard.ok and decision.proposal.action != "PROPOSE_ORDER" or (decision.proposal.action == "PROPOSE_ORDER" and guard.ok)
        result = BrainRunResult(ok=bool(ok), decision=decision, guard=guard, llm=llm, correlation_id=corr, elapsed_ms=int((time.time() - started) * 1000), events=events)
        self._audit(result)
        return result


def run_brain(**kwargs) -> BrainRunResult:
    return AgentBrain().run(**kwargs)


if __name__ == "__main__":
    print(json.dumps(run_brain().as_dict(), indent=2, default=str))
