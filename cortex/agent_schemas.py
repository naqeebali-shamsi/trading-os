#!/usr/bin/env python3
"""Typed schemas for the future Trading OS brain graph.

Dependency-free dataclass schemas today, with explicit validation and JSON-schema-
like metadata. These form the contract for future LangGraph/PydanticAI nodes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Dict, List, Optional


class SchemaValidationError(ValueError):
    pass


def _ensure_choice(name: str, value: str, allowed: set[str]):
    if value not in allowed:
        raise SchemaValidationError(f"{name}_invalid:{value}")


def _ensure_confidence(value: float):
    if not isinstance(value, (int, float)) or value < 0 or value > 1:
        raise SchemaValidationError("confidence_out_of_range")


def _ensure_symbol(symbol: Optional[str], *, required: bool = False):
    if symbol is None:
        if required:
            raise SchemaValidationError("symbol_required")
        return
    if not isinstance(symbol, str) or not symbol.strip():
        raise SchemaValidationError("symbol_invalid")


@dataclass
class BaseSchema:
    schema_version: ClassVar[int] = 1

    def validate(self):
        return self

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = self.schema_version
        return data


@dataclass
class MacroAssessment(BaseSchema):
    risk_regime: str
    affected_symbols: List[str] = field(default_factory=list)
    blackout_recommended: bool = False
    confidence: float = 0.0
    reason: str = ""
    source: str = "macro_agent"

    def validate(self):
        _ensure_choice("risk_regime", self.risk_regime, {"risk_on", "risk_off", "neutral"})
        _ensure_confidence(self.confidence)
        for symbol in self.affected_symbols:
            _ensure_symbol(symbol)
        return self


@dataclass
class MarketStructureAssessment(BaseSchema):
    symbol: str
    bias: str
    setup_quality: float
    invalidations: List[str] = field(default_factory=list)
    reason: str = ""
    timeframes: Dict[str, Any] = field(default_factory=dict)

    def validate(self):
        _ensure_symbol(self.symbol, required=True)
        _ensure_choice("bias", self.bias, {"bullish", "bearish", "neutral"})
        if not isinstance(self.setup_quality, (int, float)) or self.setup_quality < 0 or self.setup_quality > 1:
            raise SchemaValidationError("setup_quality_out_of_range")
        return self


@dataclass
class RiskAssessment(BaseSchema):
    allow_new_risk: bool
    max_risk_pct: float
    reasons: List[str] = field(default_factory=list)
    symbol_limits: Dict[str, float] = field(default_factory=dict)
    severity: str = "medium"

    def validate(self):
        if not isinstance(self.allow_new_risk, bool):
            raise SchemaValidationError("allow_new_risk_not_bool")
        if not isinstance(self.max_risk_pct, (int, float)) or self.max_risk_pct < 0:
            raise SchemaValidationError("max_risk_pct_invalid")
        _ensure_choice("severity", self.severity, {"low", "medium", "high", "critical"})
        return self


@dataclass
class TradeProposal(BaseSchema):
    action: str
    symbol: Optional[str] = None
    side: Optional[str] = None
    qty: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    confidence: float = 0.0
    strategy_id: Optional[str] = None
    reasoning: str = ""
    urgency: str = "watch"

    def validate(self):
        _ensure_choice("action", self.action, {"HOLD", "PROPOSE_ORDER", "CLOSE", "REDUCE_RISK"})
        _ensure_confidence(self.confidence)
        _ensure_choice("urgency", self.urgency, {"immediate", "watch", "defer"})
        if self.action == "PROPOSE_ORDER":
            _ensure_symbol(self.symbol, required=True)
            _ensure_choice("side", self.side or "", {"BUY", "SELL"})
            if self.qty is None or self.qty <= 0:
                raise SchemaValidationError("qty_invalid")
            if self.sl is None or self.sl <= 0:
                raise SchemaValidationError("sl_required")
        return self

    def to_guard_decision(self) -> Dict[str, Any]:
        action = "NEW_ORDER" if self.action == "PROPOSE_ORDER" else "HOLD"
        return {
            "action": action,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "sl": self.sl,
            "tp": self.tp,
            "confidence": self.confidence,
            "strategy_id": self.strategy_id,
            "reasoning": self.reasoning,
        }


@dataclass
class BrainDecision(BaseSchema):
    macro: MacroAssessment
    market: List[MarketStructureAssessment]
    risk: RiskAssessment
    proposal: TradeProposal
    warnings: List[str] = field(default_factory=list)
    market_outlook: str = ""

    def validate(self):
        self.macro.validate()
        for item in self.market:
            item.validate()
        self.risk.validate()
        self.proposal.validate()
        if not self.risk.allow_new_risk and self.proposal.action == "PROPOSE_ORDER":
            raise SchemaValidationError("proposal_conflicts_with_risk_block")
        if self.macro.blackout_recommended and self.proposal.action == "PROPOSE_ORDER":
            raise SchemaValidationError("proposal_conflicts_with_macro_blackout")
        return self


@dataclass
class PostTradeReview(BaseSchema):
    trade_quality: str
    mistakes: List[str] = field(default_factory=list)
    strategy_update_recommended: bool = False
    reason: str = ""

    def validate(self):
        _ensure_choice("trade_quality", self.trade_quality, {"good", "bad", "unclear"})
        return self


def schema_catalog() -> Dict[str, Dict[str, Any]]:
    return {
        "MacroAssessment": {"risk_regime": ["risk_on", "risk_off", "neutral"], "confidence": "0..1"},
        "MarketStructureAssessment": {"bias": ["bullish", "bearish", "neutral"], "setup_quality": "0..1"},
        "RiskAssessment": {"severity": ["low", "medium", "high", "critical"]},
        "TradeProposal": {"action": ["HOLD", "PROPOSE_ORDER", "CLOSE", "REDUCE_RISK"]},
        "BrainDecision": {"description": "Composition of macro, market, risk, and proposal outputs"},
        "PostTradeReview": {"trade_quality": ["good", "bad", "unclear"]},
    }


def export_json_schemas() -> Dict[str, Any]:
    """Export JSON Schema-like contracts for ADK/A2A agent cards."""
    catalog = schema_catalog()
    return {
        "schema_version": 1,
        "agent_card": {
            "name": "trading-os-brain",
            "description": "Trading OS AgentBrain decision contract",
            "input_schema_ref": "BrainDecisionContext",
            "output_schema_ref": "BrainDecision",
        },
        "schemas": {
            "MacroAssessment": {
                "type": "object",
                "required": ["risk_regime", "confidence"],
                "properties": {
                    "risk_regime": {"type": "string", "enum": catalog["MacroAssessment"]["risk_regime"]},
                    "affected_symbols": {"type": "array", "items": {"type": "string"}},
                    "blackout_recommended": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                    "source": {"type": "string"},
                },
            },
            "MarketStructureAssessment": {
                "type": "object",
                "required": ["symbol", "bias", "setup_quality"],
                "properties": {
                    "symbol": {"type": "string"},
                    "bias": {"type": "string", "enum": catalog["MarketStructureAssessment"]["bias"]},
                    "setup_quality": {"type": "number", "minimum": 0, "maximum": 1},
                    "invalidations": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "timeframes": {"type": "object"},
                },
            },
            "RiskAssessment": {
                "type": "object",
                "required": ["allow_new_risk", "max_risk_pct", "severity"],
                "properties": {
                    "allow_new_risk": {"type": "boolean"},
                    "max_risk_pct": {"type": "number", "minimum": 0},
                    "reasons": {"type": "array", "items": {"type": "string"}},
                    "symbol_limits": {"type": "object"},
                    "severity": {"type": "string", "enum": catalog["RiskAssessment"]["severity"]},
                },
            },
            "TradeProposal": {
                "type": "object",
                "required": ["action", "confidence", "urgency"],
                "properties": {
                    "action": {"type": "string", "enum": catalog["TradeProposal"]["action"]},
                    "symbol": {"type": "string"},
                    "side": {"type": "string", "enum": ["BUY", "SELL"]},
                    "qty": {"type": "number", "exclusiveMinimum": 0},
                    "sl": {"type": "number"},
                    "tp": {"type": "number"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "strategy_id": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "urgency": {"type": "string", "enum": ["immediate", "watch", "defer"]},
                },
            },
            "BrainDecision": {
                "type": "object",
                "required": ["macro", "market", "risk", "proposal"],
                "properties": {
                    "macro": {"$ref": "#/schemas/MacroAssessment"},
                    "market": {"type": "array", "items": {"$ref": "#/schemas/MarketStructureAssessment"}},
                    "risk": {"$ref": "#/schemas/RiskAssessment"},
                    "proposal": {"$ref": "#/schemas/TradeProposal"},
                    "warnings": {"type": "array", "items": {"type": "string"}},
                    "market_outlook": {"type": "string"},
                },
            },
            "PostTradeReview": {
                "type": "object",
                "required": ["trade_quality"],
                "properties": {
                    "trade_quality": {"type": "string", "enum": catalog["PostTradeReview"]["trade_quality"]},
                    "mistakes": {"type": "array", "items": {"type": "string"}},
                    "strategy_update_recommended": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
            },
        },
    }
