#!/usr/bin/env python3
"""Grounded post-trade learning helpers.

This module turns journaled closed trades into structured outcome records and
small daily summaries. It is advisory/reporting only and never changes strategy
or risk controls.
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from bus import publish, subscribe  # noqa: E402
from introspect.io import read_jsonl  # noqa: E402
from memory import main as memory_main  # noqa: E402

OUTCOME_FILE = ROOT / "memory" / "trade_outcomes.jsonl"
SUMMARY_FILE = ROOT / "memory" / "daily_summaries.jsonl"
ENTRY_CONTEXT_FILE = ROOT / "memory" / "trade_entry_context.jsonl"


def _day(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d")


def latest_payload(topic: str):
    events = subscribe(topic, limit=1)
    return events[-1].get("payload", {}) if events else {}


def capture_entry_context(fill_event, *, extra_context=None):
    """Capture the advisory context visible when a trade opens.

    This is read-only attribution data. It intentionally snapshots advisory
    signals without giving them execution authority.
    """
    symbol = fill_event.get("symbol")
    context = {
        "ts": time.time(),
        "type": "trade_entry_context",
        "order_id": fill_event.get("order_id") or fill_event.get("ticket") or fill_event.get("comment"),
        "symbol": symbol,
        "side": fill_event.get("side"),
        "qty": fill_event.get("qty"),
        "fill_price": fill_event.get("fill_price") or fill_event.get("price"),
        "strategy_id": fill_event.get("strategy_id"),
        "decision": latest_payload("cortex.decision"),
        "brain_result": latest_payload("cortex.brain.result"),
        "event_radar": latest_payload("macro.event_radar"),
        "forecast": latest_payload(f"market.forecast.{symbol}") if symbol else latest_payload("market.forecast"),
        "regime": latest_payload("market.regime"),
        "tick": latest_payload(f"market.tick.{symbol}") if symbol else latest_payload("market.tick"),
    }
    if extra_context:
        context.update(extra_context)
    memory_main.append_event(ENTRY_CONTEXT_FILE, context)
    publish("memory.trade_entry_context", context)
    return context


def load_entry_contexts(path=ENTRY_CONTEXT_FILE):
    by_order = {}
    for row in read_jsonl(path):
        oid = row.get("order_id")
        if oid:
            by_order[oid] = row
    return by_order


def _forecast_direction(context):
    forecast = context.get("forecast") or {}
    nested = forecast.get("forecast") if isinstance(forecast, dict) else {}
    return (nested or forecast or {}).get("direction")


def _event_category(context):
    event = context.get("event_radar") or {}
    return event.get("category"), event.get("severity"), event.get("bias")


def attribute_outcome(outcome, entry_context=None):
    """Return likely explanatory tags for a trade outcome.

    The output is intentionally probabilistic language: these are review hints,
    not causal proof and not automatic strategy changes.
    """
    entry_context = entry_context or {}
    pnl = float(outcome.get("pnl", 0) or 0)
    tags = []
    confidence = 0.2

    if pnl >= 0:
        tags.append("worked_as_planned_or_market_helped")
        confidence = 0.3
    else:
        category, severity, bias = _event_category(entry_context)
        forecast_dir = _forecast_direction(entry_context)
        side = str(outcome.get("side") or entry_context.get("side") or "").upper()
        decision = entry_context.get("decision") or {}
        brain = entry_context.get("brain_result") or {}
        regime = entry_context.get("regime") or {}
        tick = entry_context.get("tick") or {}

        if category and category != "none" and severity in {"medium", "high"}:
            tags.append(f"event_regime_loss:{category}")
            confidence += 0.2
        if bias in {"risk_off", "equity_volatility", "rates_volatility", "inflation_risk"}:
            tags.append(f"macro_bias_present:{bias}")
            confidence += 0.1
        if forecast_dir in {"up", "down"} and side in {"BUY", "SELL"}:
            if (forecast_dir == "up" and side == "SELL") or (forecast_dir == "down" and side == "BUY"):
                tags.append("forecast_direction_conflict")
                confidence += 0.2
        if decision.get("action") == "HOLD" or (brain.get("decision") or {}).get("proposal", {}).get("action") == "HOLD":
            tags.append("brain_or_guard_preferred_hold")
            confidence += 0.15
        spread = tick.get("spread") or tick.get("spread_points") or tick.get("spread_pips")
        try:
            if spread is not None and float(spread) > 0:
                tags.append("execution_spread_context_present")
        except (TypeError, ValueError):
            pass
        if isinstance(regime, dict) and regime.get("regime") in {"choppy", "volatile", "range"}:
            tags.append(f"hostile_market_regime:{regime.get('regime')}")
            confidence += 0.15

        if not tags:
            tags.append("unattributed_loss_needs_review")

    return {
        "tags": tags,
        "primary": tags[0] if tags else "unknown",
        "confidence": round(min(0.95, confidence), 2),
        "review_required": pnl < 0 and (not entry_context or "unattributed_loss_needs_review" in tags),
    }


def build_outcome(close_event, opened_by_order=None, context=None):
    order_id = close_event.get("order_id") or close_event.get("ticket") or close_event.get("comment")
    opened = (opened_by_order or {}).get(order_id, {})
    pnl = float(close_event.get("pnl", close_event.get("profit", 0)) or 0)
    ts = float(close_event.get("ts") or time.time())
    outcome = {
        "ts": ts,
        "date": close_event.get("date") or _day(ts),
        "type": "trade_outcome",
        "order_id": order_id,
        "symbol": close_event.get("symbol") or opened.get("symbol"),
        "side": close_event.get("side") or opened.get("side"),
        "qty": close_event.get("qty") or opened.get("qty"),
        "entry_price": opened.get("fill_price") or opened.get("entry_price"),
        "exit_price": close_event.get("exit_price") or close_event.get("current_price"),
        "pnl": pnl,
        "result": "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
        "entry_context": opened.get("context", {}),
        "exit_context": context or close_event.get("context", {}),
    }
    entry_context = opened.get("context") or opened
    outcome["attribution"] = attribute_outcome(outcome, entry_context=entry_context)
    return outcome


def record_outcome(close_event, *, opened_by_order=None, context=None, publish_event=True):
    if opened_by_order is None:
        opened_by_order = load_entry_contexts()
    outcome = build_outcome(close_event, opened_by_order=opened_by_order, context=context)
    memory_main.append_event(OUTCOME_FILE, outcome)
    if publish_event:
        publish("memory.trade_outcome", outcome)
    return outcome


def outcomes_for_date(date=None, path=OUTCOME_FILE):
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return [row for row in read_jsonl(path) if row.get("date") == date and row.get("type") == "trade_outcome"]


def summarize_outcomes(outcomes, *, date=None):
    total = len(outcomes)
    pnl = round(sum(float(o.get("pnl", 0) or 0) for o in outcomes), 2)
    wins = sum(1 for o in outcomes if o.get("result") == "win")
    losses = sum(1 for o in outcomes if o.get("result") == "loss")
    by_symbol = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0, "losses": 0})
    reasons = Counter()
    attribution_tags = Counter()
    for outcome in outcomes:
        sym = outcome.get("symbol") or "UNKNOWN"
        row = by_symbol[sym]
        row["count"] += 1
        row["pnl"] += float(outcome.get("pnl", 0) or 0)
        row["wins"] += 1 if outcome.get("result") == "win" else 0
        row["losses"] += 1 if outcome.get("result") == "loss" else 0
        for key in ("regime", "decision", "reason"):
            value = (outcome.get("entry_context") or {}).get(key)
            if value:
                reasons[str(value)] += 1
        for tag in (outcome.get("attribution") or {}).get("tags", []):
            attribution_tags[str(tag)] += 1
    return {
        "ts": time.time(),
        "date": date or (outcomes[0].get("date") if outcomes else datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        "type": "daily_trade_summary",
        "trade_count": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total, 3) if total else 0.0,
        "net_pnl": pnl,
        "by_symbol": {sym: {**row, "pnl": round(row["pnl"], 2)} for sym, row in sorted(by_symbol.items())},
        "top_context_tags": reasons.most_common(5),
        "top_attribution_tags": attribution_tags.most_common(8),
    }


def write_daily_summary(date=None, publish_event=True):
    outcomes = outcomes_for_date(date)
    summary = summarize_outcomes(outcomes, date=date)
    memory_main.append_event(SUMMARY_FILE, summary)
    if publish_event:
        publish("memory.daily_summary", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Post-trade learning summaries")
    parser.add_argument("--date", default=None, help="UTC date YYYY-MM-DD")
    parser.add_argument("--write", action="store_true", help="Persist and publish the summary")
    args = parser.parse_args(argv)
    summary = write_daily_summary(args.date) if args.write else summarize_outcomes(outcomes_for_date(args.date), date=args.date)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
