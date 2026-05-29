#!/usr/bin/env python3
"""Gated unattended demo executor.

This is intentionally conservative: it only executes proposals already surfaced by
opportunity_scanner as executable, then rechecks local approval limits, immune
policy, and the muscle execution path. It is for demo/sandbox runs only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
sys.path.insert(0, str(ROOT / "muscle"))

from bus import publish  # noqa: E402
from immune import main as immune_main  # noqa: E402
from immune.provenance import attach_proof  # noqa: E402
from muscle import muscle_main  # noqa: E402
from muscle import pnl_sync  # noqa: E402
from scripts import opportunity_scanner, real_mode_audit  # noqa: E402


DEFAULT_ALLOWED = ["EURUSD", "XAUUSD", "NVDA", "MSFT", "AAPL", "TSLA", "AMZN", "GOOGL", "META"]
DEFAULT_STOCK_SYMBOLS = ["NVDA", "MSFT", "AAPL", "TSLA", "AMZN", "GOOGL", "META"]


def rotating_candidates(allowed: List[str], iteration: int, symbols_per_cycle: int = 1) -> List[str]:
    """Return a bounded rotating subset of approved symbols for this cadence tick."""
    if not allowed:
        return []
    size = max(1, min(int(symbols_per_cycle or 1), len(allowed)))
    start = max(0, iteration - 1) % len(allowed)
    return [allowed[(start + offset) % len(allowed)] for offset in range(size)]


def max_lot_for_symbol(symbol: str, *, default_max_lot: float, stock_symbols: List[str], stock_max_qty: float) -> float:
    return float(stock_max_qty if str(symbol).upper() in {s.upper() for s in stock_symbols} else default_max_lot)


def open_positions() -> Dict[str, Any]:
    state = pnl_sync.load_state()
    positions = state.get("positions", {})
    return positions if isinstance(positions, dict) else {}


def floating_pnl() -> float:
    try:
        return float(pnl_sync.load_state().get("floating_pnl", 0) or 0)
    except Exception:
        return 0.0


def recent_consecutive_losses() -> int:
    limits = immune_main.load_limits()
    journal = immune_main.load_journal()
    streak, _ = immune_main.recent_loss_streak(journal)
    return int(streak or 0)


def approval_gate(proposal: Dict[str, Any], *, allowed: List[str], max_lot: float, max_loss: float, max_trades: int, trades_sent: int, max_consecutive_losses: int, stock_symbols: List[str] | None = None, stock_max_qty: float | None = None) -> List[str]:
    reasons: List[str] = []
    symbol = str(proposal.get("symbol") or "").upper()
    side = str(proposal.get("side") or "").upper()
    try:
        qty = float(proposal.get("qty") or 0)
    except Exception:
        qty = 0.0
    if trades_sent >= max_trades:
        reasons.append(f"max_trades_reached:{trades_sent}/{max_trades}")
    if symbol not in allowed:
        reasons.append(f"symbol_not_in_unattended_approval:{symbol}")
    if side not in {"BUY", "SELL"}:
        reasons.append(f"invalid_side:{side}")
    symbol_max_lot = max_lot_for_symbol(symbol, default_max_lot=max_lot, stock_symbols=stock_symbols or [], stock_max_qty=stock_max_qty or max_lot)
    if qty <= 0 or qty > symbol_max_lot:
        reasons.append(f"qty_exceeds_approval:{qty}>{symbol_max_lot}")
    if not proposal.get("sl") or float(proposal.get("sl") or 0) <= 0:
        reasons.append("missing_stop_loss")
    if not proposal.get("tp") or float(proposal.get("tp") or 0) <= 0:
        reasons.append("missing_take_profit")
    if floating_pnl() <= -abs(max_loss):
        reasons.append(f"max_total_loss_reached:{floating_pnl():.2f}")
    if recent_consecutive_losses() >= max_consecutive_losses:
        reasons.append(f"consecutive_loss_stop:{recent_consecutive_losses()}")
    positions = open_positions()
    for pos in positions.values():
        if str(pos.get("symbol") or "").upper() == symbol and str(pos.get("side") or pos.get("type") or "").upper() == side:
            reasons.append(f"no_averaging_down_existing_same_direction:{symbol}:{side}")
    return reasons


def execute_proposal(proposal: Dict[str, Any], *, run_id: str) -> Dict[str, Any]:
    symbol = str(proposal.get("symbol") or "").upper()
    order_id = f"overnight-{run_id}-{symbol}-{uuid.uuid4().hex[:8]}"
    intent = {
        "order_id": order_id,
        "symbol": symbol,
        "side": str(proposal.get("side") or "").upper(),
        "qty": float(proposal.get("qty") or 0),
        "type": "MARKET",
        "price": proposal.get("price"),
        "sl": float(proposal.get("sl") or 0),
        "tp": float(proposal.get("tp") or 0) if proposal.get("tp") else 0,
        "source": "unattended_demo_executor",
        "strategy_id": proposal.get("strategy_id") or "brain_scanner",
    }

    limits = immune_main.load_limits()
    journal = immune_main.load_journal()
    ok, reasons, intent = immune_main.check_order(intent, limits, journal)
    if not ok:
        publish("unattended_demo.block", {"order_id": order_id, "intent": intent, "reasons": reasons})
        return {"status": "blocked_by_immune", "order_id": order_id, "reasons": reasons, "intent": intent}

    source_event = {"topic": "unattended_demo.executor", "seq": None}
    approved = attach_proof(intent, source_event)
    publish("immune.pass", {"type": "order_pass", "intent": approved, "provenance": approved["immune_proof"]})
    muscle_main.process_order_intent(approved)
    deadline = time.time() + 75
    while time.time() < deadline:
        muscle_main.check_responses()
        muscle_main.check_timeouts_and_queue()
        state = muscle_main.ORDER_STATE.get(order_id, {})
        if state.get("status") in {"filled", "rejected", "timeout", "error"}:
            return {"status": state.get("status"), "order_id": order_id, "state": state}
        time.sleep(2)
    return {"status": "pending_after_wait", "order_id": order_id, "state": muscle_main.ORDER_STATE.get(order_id, {})}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gated unattended demo executor")
    parser.add_argument("--duration-hours", type=float, default=6.0)
    parser.add_argument("--cadence-minutes", type=float, default=5.0)
    parser.add_argument("--max-trades", type=int, default=5)
    parser.add_argument("--max-lot", type=float, default=0.01)
    parser.add_argument("--stock-symbols", default=",".join(DEFAULT_STOCK_SYMBOLS), help="Approved stock/ETF symbols allowed to use --stock-max-qty instead of --max-lot.")
    parser.add_argument("--stock-max-qty", type=float, default=1.0)
    parser.add_argument("--max-loss", type=float, default=100.0)
    parser.add_argument("--allowed-symbols", default=",".join(DEFAULT_ALLOWED))
    parser.add_argument("--max-consecutive-losses", type=int, default=2)
    parser.add_argument("--out-dir", default=str(ROOT / "reports" / "overnight_execution"))
    parser.add_argument("--symbols-per-cycle", type=int, default=1, help="Approved symbols to evaluate per cadence tick. Keeps unattended loops bounded when live LLMs are slow.")
    args = parser.parse_args(argv)

    allowed = [s.strip().upper() for s in args.allowed_symbols.split(",") if s.strip()]
    stock_symbols = [s.strip().upper() for s in args.stock_symbols.split(",") if s.strip()]
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = Path(args.out_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "executor.jsonl"
    deadline = time.time() + args.duration_hours * 3600
    trades_sent = 0
    iteration = 0

    print(f"Trading OS unattended demo executor started: {run_id}", flush=True)
    print(f"READ THIS: demo execution enabled, max_trades={args.max_trades}, max_lot={args.max_lot}, stock_max_qty={args.stock_max_qty}, allowed={allowed}, stock_symbols={stock_symbols}", flush=True)

    while time.time() < deadline:
        iteration += 1
        progress = {"current": iteration, "total": max(1, int(args.duration_hours * 60 / args.cadence_minutes)), "unit": "checks", "message": f"demo executor check {iteration}"}
        print("JCODE_PROGRESS " + json.dumps(progress), flush=True)
        record: Dict[str, Any] = {"ts": time.time(), "iteration": iteration, "trades_sent": trades_sent}
        try:
            audit = real_mode_audit.audit()
            if not audit.get("ok"):
                record.update({"action": "skip", "reason": "real_mode_audit_failed", "audit": audit})
            elif trades_sent >= args.max_trades:
                record.update({"action": "stop", "reason": "max_trades_reached"})
                with open(log_path, "a") as f: f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
                break
            elif floating_pnl() <= -abs(args.max_loss):
                record.update({"action": "stop", "reason": "max_loss_reached", "floating_pnl": floating_pnl()})
                with open(log_path, "a") as f: f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
                break
            elif recent_consecutive_losses() >= args.max_consecutive_losses:
                record.update({"action": "stop", "reason": "consecutive_loss_limit", "losses": recent_consecutive_losses()})
                with open(log_path, "a") as f: f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
                break
            else:
                candidates = rotating_candidates(allowed, iteration, args.symbols_per_cycle)
                record["candidate_symbols"] = candidates
                scan = opportunity_scanner.scan_once(include_watchlist=False, candidate_symbols=candidates)
                record["scanner_summary"] = scan.get("summary")
                executable = [d for d in scan.get("decisions", []) if d.get("requires_confirmation")]
                if not executable:
                    record.update({"action": "hold", "reason": scan.get("summary"), "hard_reasons": scan.get("hard_reasons")})
                else:
                    for decision in executable:
                        proposal = dict(decision.get("proposal") or {})
                        reasons = approval_gate(proposal, allowed=allowed, max_lot=args.max_lot, max_loss=args.max_loss, max_trades=args.max_trades, trades_sent=trades_sent, max_consecutive_losses=args.max_consecutive_losses, stock_symbols=stock_symbols, stock_max_qty=args.stock_max_qty)
                        if reasons:
                            record.update({"action": "blocked", "proposal": proposal, "reasons": reasons})
                            continue
                        result = execute_proposal(proposal, run_id=run_id)
                        trades_sent += 1 if result.get("status") in {"filled", "pending_after_wait", "sent"} else 0
                        record.update({"action": "executed", "proposal": proposal, "result": result, "trades_sent": trades_sent})
                        break
        except Exception as exc:
            record.update({"action": "error", "error": f"{type(exc).__name__}:{exc}"})
        with open(log_path, "a") as f:
            f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        print(json.dumps(record, sort_keys=True, default=str), flush=True)
        time.sleep(args.cadence_minutes * 60)

    print("JCODE_CHECKPOINT " + json.dumps({"message": "unattended demo executor completed", "trades_sent": trades_sent}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
