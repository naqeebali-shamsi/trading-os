#!/usr/bin/env python3
"""Dry-run the guarded AgentBrain from ops/CI.

This command never writes MT5 bridge command files. It evaluates the brain,
publishes normal audit/bus telemetry through the AgentBrain path, and prints the
result JSON for inspection.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex.agent import AgentBrain  # noqa: E402


def parse_json_arg(value: str, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dry-run the guarded provider-agnostic agent brain")
    parser.add_argument("--provider", default=None, help="LLM provider from config/llm.yaml, default uses config/env")
    parser.add_argument("--model", default=None, help="Optional model override")
    parser.add_argument("--mode", default="ADVISORY", choices=["ADVISORY", "PAPER", "LIVE"], help="Decision guard mode")
    parser.add_argument("--symbol", default="EURUSD", help="Symbol for the synthetic market snapshot")
    parser.add_argument("--bid", type=float, default=1.0850)
    parser.add_argument("--ask", type=float, default=1.0852)
    parser.add_argument("--news-json", default="", help="JSON array of news items")
    parser.add_argument("--positions-json", default="", help="JSON array of open positions")
    args = parser.parse_args(argv)

    news = parse_json_arg(args.news_json, [])
    positions = parse_json_arg(args.positions_json, [])
    if not isinstance(news, list) or not isinstance(positions, list):
        parser.error("--news-json and --positions-json must be JSON arrays")

    result = AgentBrain().run(
        market_snapshot={"symbol": args.symbol.upper(), "bid": args.bid, "ask": args.ask},
        news=news,
        positions=positions,
        constraints={"default_action": "HOLD", "requires_stop_loss": True, "dry_run": True},
        provider=args.provider,
        model=args.model,
        decision_mode=args.mode,
        correlation_id="brain-smoke",
    )
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True, default=str))
    return 0 if result.decision and result.decision.proposal.action == "HOLD" or result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
