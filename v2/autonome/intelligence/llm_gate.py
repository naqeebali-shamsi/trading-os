"""
autonome/intelligence/llm_gate.py  v2.1
LLM Gate: qualitative signal review before execution.
Every signal passes through an LLM "cockpit" review with rich context.
The LLM can APPROVE, REJECT, or MODIFY the signal with reasoning.
All decisions are logged for audit and future training.
"""
from __future__ import annotations

import os
import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path

import requests
import yaml

log = logging.getLogger("llm_gate")


@dataclass(frozen=True)
class GateDecision:
    decision: str       # "APPROVE" | "REJECT" | "MODIFY"
    confidence: float   # 0.0-1.0
    reasoning: str
    modified_entry: Optional[float] = None
    modified_stop: Optional[float] = None
    modified_target: Optional[float] = None
    modified_qty_pct: Optional[float] = None   # e.g. 0.5 = halve size
    meta: Optional[Dict[str, Any]] = None


@dataclass
class SignalContext:
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float
    strategy: str
    regime: str
    vix_estimate: Optional[float] = None
    sector: Optional[str] = None
    catalyst: Optional[str] = None


class LLMGate:
    """
    Qualitative trading signal reviewer.
    Uses OpenRouter (configurable) for LLM inference.
    Structured JSON output via prompt engineering.
    """

    def __init__(self):
        cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml"
        sec_path = Path(__file__).resolve().parent.parent.parent / "config" / "secrets.yaml"

        with open(cfg_path) as f:
            self.cfg = yaml.safe_load(f)
        with open(sec_path) as f:
            secrets = yaml.safe_load(f)

        intel = self.cfg.get("intelligence") or {}
        self.enabled = intel.get("llm_gate_enabled", True)
        self.model = intel.get("llm_gate_model", "openai/gpt-4o-mini")
        self.max_context_bars = intel.get("llm_gate_context_bars", 50)
        self.auto_approve_below_confidence = intel.get("auto_approve_below_confidence", 0.0)

        # OpenRouter setup
        self.api_key = (secrets.get("openrouter") or {}).get("api_key", "")
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        self.db_path = Path(__file__).resolve().parent.parent.parent / "data" / "journal.sqlite"
        self.gate_log_path = Path(__file__).resolve().parent.parent.parent / "data" / "gate_decisions.jsonl"

    # ── context builders ─────────────────────────────────────────────────────

    def _recent_pnl(self, symbol: str, hours: int = 72) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            conn = sqlite3.connect(str(self.db_path))
            rows = conn.execute(
                "SELECT t, pnl, pnl_pct, reason FROM pnl WHERE symbol=? AND t > ? ORDER BY t DESC LIMIT 5",
                (symbol, cutoff)
            ).fetchall()
            conn.close()
            return [{"t": r[0], "pnl": r[1], "pnl_pct": r[2], "reason": r[3]} for r in rows]
        except Exception:
            return []

    def _recent_signals(self, symbol: str, hours: int = 72) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            conn = sqlite3.connect(str(self.db_path))
            rows = conn.execute(
                "SELECT t, direction, confidence, meta FROM signals WHERE symbol=? AND t > ? ORDER BY t DESC LIMIT 5",
                (symbol, cutoff)
            ).fetchall()
            conn.close()
            return [{"t": r[0], "direction": r[1], "confidence": r[2], "meta": r[3]} for r in rows]
        except Exception:
            return []

    def _load_playbook(self) -> Dict[str, Any]:
        pb_path = Path(__file__).resolve().parent.parent.parent / "config" / "playbook.md"
        if not pb_path.exists():
            return {"raw": "No playbook found"}
        return {"raw": pb_path.read_text(encoding="utf-8")}

    def _market_regime(self) -> str:
        # Simple regime from last equity snapshots
        try:
            conn = sqlite3.connect(str(self.db_path))
            rows = conn.execute(
                "SELECT equity FROM equity ORDER BY t DESC LIMIT 20"
            ).fetchall()
            conn.close()
            if len(rows) >= 10:
                eqs = [r[0] for r in rows]
                trend = (eqs[0] - eqs[-1]) / eqs[-1] if eqs[-1] > 0 else 0
                if trend > 0.02:
                    return "strong_uptrend"
                elif trend > 0.005:
                    return "uptrend"
                elif trend < -0.02:
                    return "strong_downtrend"
                elif trend < -0.005:
                    return "downtrend"
                return "ranging"
        except Exception:
            pass
        return "unknown"

    # ── prompt engineering ───────────────────────────────────────────────────

    def _build_prompt(self, ctx: SignalContext) -> str:
        playbook = self._load_playbook()
        recent_pnl = self._recent_pnl(ctx.symbol)
        recent_sigs = self._recent_signals(ctx.symbol)
        regime = self._market_regime()

        pnl_summary = ""
        if recent_pnl:
            total = sum(p["pnl"] or 0 for p in recent_pnl)
            wins = sum(1 for p in recent_pnl if (p["pnl"] or 0) > 0)
            pnl_summary = f"Recent PnL (last 72h): ${total:.2f} ({wins}/{len(recent_pnl)} wins)\n"
            for p in recent_pnl[:3]:
                pnl_summary += f"  - {p['t']}: ${p['pnl']:.2f} ({p['pnl_pct']*100:+.1f}%) reason={p['reason']}\n"
        else:
            pnl_summary = "No recent trade history for this symbol.\n"

        sig_summary = ""
        if recent_sigs:
            sig_summary = f"Recent signals (last 72h): {len(recent_sigs)}\n"
            for s in recent_sigs[:3]:
                sig_summary += f"  - {s['t']}: {s['direction']} conf={s['confidence']:.2f}\n"
        else:
            sig_summary = "No recent signals for this symbol.\n"

        prompt = f"""You are an elite quantitative trader with deep macro and geopolitical awareness. Review the following trade signal and make a decision.

## Current Market Context
- Regime: {regime}
- Strategy: {ctx.strategy}
- VIX (estimated): {ctx.vix_estimate or 'unknown'}

## Playbook Context
{playbook['raw'][:2000]}

## Signal Under Review
- Symbol: {ctx.symbol}
- Direction: {ctx.direction}
- Entry: ${ctx.entry_price:.2f}
- Stop: ${ctx.stop_loss:.2f}
- Target: ${ctx.take_profit:.2f}
- Strategy Confidence: {ctx.confidence:.2f}
- Sector: {ctx.sector or 'unknown'}
- Catalyst: {ctx.catalyst or 'none'}

## Recent History
{pnl_summary}
{sig_summary}

## Your Task
Evaluate this signal considering:
1. Does it align with current market regime and playbook thesis?
2. Is risk/reward attractive given macro conditions?
3. Any red flags (overbought/oversold, earnings risk, sector rotation, geopolitical)?
4. Would you take this trade with real money?

Respond ONLY with valid JSON in this exact format:
{{
  "decision": "APPROVE" | "REJECT" | "MODIFY",
  "confidence": 0.0-1.0,
  "reasoning": "concise expert reasoning",
  "modified_entry": null or float,
  "modified_stop": null or float,
  "modified_target": null or float,
  "modified_qty_pct": null or float (1.0=full, 0.5=half size, 0.0=pass)
}}

Be decisive. Most signals should be APPROVED or REJECTED. Only MODIFY if you see a clear adjustment.
"""
        return prompt

    # ── LLM call ─────────────────────────────────────────────────────────────

    def _call_llm(self, prompt: str) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            log.warning("No OpenRouter API key configured; auto-approving")
            return {"decision": "APPROVE", "confidence": 0.5, "reasoning": "no_llm_key_auto_approve"}

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 800,
        }

        try:
            r = requests.post(self.api_url, headers=self.headers, json=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]

            # Extract JSON from markdown code blocks if present
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
                if content.endswith("```"):
                    content = content[:-3].strip()
            elif content.startswith("```"):
                content = content[3:]
                if content.endswith("```"):
                    content = content[:-3].strip()

            parsed = json.loads(content)
            return parsed
        except requests.exceptions.RequestException as e:
            log.error("LLM API error: %s", e)
            return {"decision": "APPROVE", "confidence": 0.5, "reasoning": f"llm_api_error:{e}"}
        except json.JSONDecodeError as e:
            log.error("LLM returned invalid JSON: %s | raw: %s", e, content[:500])
            return {"decision": "REJECT", "confidence": 0.5, "reasoning": "llm_invalid_json"}
        except Exception as e:
            log.error("LLM unexpected error: %s", e)
            return {"decision": "APPROVE", "confidence": 0.5, "reasoning": f"llm_error:{e}"}

    # ── public API ───────────────────────────────────────────────────────────

    def review(self, ctx: SignalContext) -> GateDecision:
        if not self.enabled:
            return GateDecision("APPROVE", 1.0, "llm_gate_disabled")

        # Auto-approve very low confidence signals (likely noise, don't waste tokens)
        if ctx.confidence < self.auto_approve_below_confidence:
            return GateDecision("APPROVE", ctx.confidence, "below_auto_approve_threshold")

        prompt = self._build_prompt(ctx)
        raw = self._call_llm(prompt)

        if raw is None:
            # Fail-safe: if LLM completely fails, reject the signal
            return GateDecision("REJECT", 0.0, "llm_call_failed")

        decision = GateDecision(
            decision=str(raw.get("decision", "REJECT")).upper(),
            confidence=float(raw.get("confidence", 0.0)),
            reasoning=str(raw.get("reasoning", "no_reasoning")),
            modified_entry=raw.get("modified_entry"),
            modified_stop=raw.get("modified_stop"),
            modified_target=raw.get("modified_target"),
            modified_qty_pct=raw.get("modified_qty_pct"),
            meta={"model": self.model, "prompt_tokens": len(prompt) // 4},
        )

        self._log_decision(ctx, decision, prompt)
        return decision

    def _log_decision(self, ctx: SignalContext, decision: GateDecision, prompt: str) -> None:
        record = {
            "t": datetime.now(timezone.utc).isoformat(),
            "symbol": ctx.symbol,
            "direction": ctx.direction,
            "signal": asdict(ctx) if hasattr(ctx, "__dataclass_fields__") else repr(ctx),
            "decision": asdict(decision),
            "cost_estimate_usd": round(len(prompt) / 4000 * 0.0015, 6),  # rough cost
        }
        self.gate_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.gate_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    # ── helpers ──────────────────────────────────────────────────────────────

    def apply_modifications(self, sig, decision: GateDecision):
        """Return a new signal with modifications applied. Signal is frozen."""
        if decision.decision != "MODIFY":
            return sig
        from autonome.strategy.momentum_breakout import Signal
        return Signal(
            symbol=sig.symbol,
            direction=sig.direction,
            entry_price=decision.modified_entry if decision.modified_entry is not None else sig.entry_price,
            stop_loss=decision.modified_stop if decision.modified_stop is not None else sig.stop_loss,
            take_profit=decision.modified_target if decision.modified_target is not None else sig.take_profit,
            confidence=sig.confidence,
            meta=json.dumps({"llm_modified": True, "original": {"entry": sig.entry_price, "stop": sig.stop_loss, "target": sig.take_profit}}),
        )
