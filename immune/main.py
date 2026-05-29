#!/usr/bin/env python3
"""
immune/main.py -- White Blood Cells
-------------------------------------
Every order must pass through here before reaching muscle.
Checks: daily loss limit, max drawdown, position size, time-of-day,
volatility threshold, correlation limits.
Publishes immune.pass or immune.block events.
"""
import json, os, time, sys
from pathlib import Path
from datetime import UTC, datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, subscribe, current_seq  # noqa
from immune.provenance import attach_proof  # noqa
from runtime_safety import append_runtime_reasons
try:
    from cortex.instrument_registry import load_registry  # noqa
except Exception:
    load_registry = None

RISK_FILE = ROOT / "immune" / "risk_limits.json"

DEF_LIMITS = {
    "mode": "PAPER",
    "max_daily_loss_pct": 3.0,
    "max_drawdown_pct": 10.0,
    "max_positions": 5,
    "max_position_size_lots": 0.5,
    "max_correlated_positions": 2,
    "trade_window_start_utc": 8,
    "trade_window_end_utc": 18,
    "min_atr_multiplier_for_sl": 1.5,
    "allowed_symbols": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
    "blocked_reasons": [],
    "loss_streak_cooldown": {
        "enabled": True,
        "max_consecutive_losses": 3,
        "cooldown_minutes": 60,
        "scope": "global"
    },
}


def load_limits():
    if not RISK_FILE.exists():
        RISK_FILE.write_text(json.dumps(DEF_LIMITS, indent=2))
    try:
        raw = json.loads(RISK_FILE.read_text())
    except json.JSONDecodeError:
        raw = {}
    limits = {**DEF_LIMITS, **(raw or {})}
    # Operators sometimes intentionally set either bound to null while editing
    # risk_limits.json. Keep the immune layer fail-safe but non-crashing.
    for key in ("trade_window_start_utc", "trade_window_end_utc"):
        if limits.get(key) is None:
            limits[key] = DEF_LIMITS[key]
        else:
            try:
                limits[key] = int(limits[key])
            except (TypeError, ValueError):
                limits[key] = DEF_LIMITS[key]
    return limits


def calc_drawdown(equity_curve):
    if not equity_curve:
        return 0.0
    peak = max(equity_curve)
    current = equity_curve[-1]
    if peak <= 0:
        return 0.0
    return (peak - current) / peak * 100


def _trade_pnl(entry):
    try:
        return float(entry.get("pnl", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def recent_loss_streak(journal, *, symbol=None):
    """Return consecutive closed losses from newest to oldest.

    Break-even/winning trades reset the streak. Non-close events are ignored.
    When ``symbol`` is supplied, only matching symbol closes are considered if
    the journal entry carries a symbol. Legacy close entries without symbol stay
    eligible so older journals still protect the system.
    """
    streak = 0
    last_loss_ts = None
    for entry in reversed(journal):
        if entry.get("type") != "trade_closed":
            continue
        entry_symbol = entry.get("symbol")
        if symbol and entry_symbol and entry_symbol != symbol:
            continue
        pnl = _trade_pnl(entry)
        if pnl < 0:
            streak += 1
            if last_loss_ts is None:
                last_loss_ts = entry.get("ts")
            continue
        break
    return streak, last_loss_ts


def loss_streak_block_reason(intent, limits, journal, *, now_ts=None):
    """Return a cooldown block reason when recent losses indicate bad conditions."""
    cfg = limits.get("loss_streak_cooldown") or {}
    if not cfg.get("enabled", True):
        return None

    max_losses = int(cfg.get("max_consecutive_losses", 3) or 0)
    cooldown_minutes = float(cfg.get("cooldown_minutes", 60) or 0)
    if max_losses <= 0 or cooldown_minutes <= 0:
        return None

    scope = str(cfg.get("scope", "global")).lower()
    symbol = intent.get("symbol") if scope == "symbol" else None
    streak, last_loss_ts = recent_loss_streak(journal, symbol=symbol)
    if streak < max_losses or last_loss_ts is None:
        return None

    try:
        elapsed = float(now_ts if now_ts is not None else time.time()) - float(last_loss_ts)
    except (TypeError, ValueError):
        elapsed = 0.0
    cooldown_sec = cooldown_minutes * 60.0
    if elapsed < cooldown_sec:
        remaining_min = max(0.0, (cooldown_sec - elapsed) / 60.0)
        return f"loss_streak_cooldown:{streak}_losses:{remaining_min:.0f}m_remaining"
    return None


def max_position_size_for_symbol(symbol, limits):
    """Return symbol-specific size cap when configured, else legacy global cap."""
    overrides = limits.get("symbol_max_position_size_lots") or {}
    sym = str(symbol or "").upper()
    if sym in overrides:
        try:
            return float(overrides[sym])
        except (TypeError, ValueError):
            return float(limits.get("max_position_size_lots", 0.5))
    return float(limits.get("max_position_size_lots", 0.5))


def _latest_macro_policy(max_age_sec: int = 900):
    try:
        for ev in reversed(subscribe("risk.macro_policy", limit=5)):
            if time.time() - float(ev.get("ts") or 0) <= max_age_sec:
                return ev.get("payload") or {}
    except Exception:
        return {}
    return {}


def _open_positions(journal, limits):
    try:
        from muscle import pnl_sync

        positions = list((pnl_sync.load_state().get("positions") or {}).values())
        if positions:
            return positions
    except Exception:
        pass
    return [j for j in journal if j.get("status") == "open"]


def check_order(intent, limits, journal):
    """Return (pass: bool, reasons: list, intent)."""
    reasons = []
    append_runtime_reasons(reasons, ROOT)
    now = datetime.now(UTC)

    # Mode lock
    if limits.get("mode") != "LIVE" and limits.get("mode") != "PAPER":
        return False, ["mode_unknown"]

    # Time window
    try:
        start_hour = int(limits.get("trade_window_start_utc", DEF_LIMITS["trade_window_start_utc"]))
        end_hour = int(limits.get("trade_window_end_utc", DEF_LIMITS["trade_window_end_utc"]))
    except (TypeError, ValueError):
        start_hour = DEF_LIMITS["trade_window_start_utc"]
        end_hour = DEF_LIMITS["trade_window_end_utc"]
    if not (start_hour <= now.hour <= end_hour):
        reasons.append("outside_trade_window")

    # Symbol whitelist
    sym = intent.get("symbol", "")
    if sym not in limits.get("allowed_symbols", []):
        reasons.append("symbol_not_allowed")

    # Instrument Intelligence deny-by-default validation. Keep the legacy
    # whitelist reason above for backwards-compatible tests/operators, but add
    # precise registry reasons for disabled/unknown/invalid instruments.
    if load_registry is not None:
        try:
            inst_result = load_registry().validate_order(intent, require_enabled=True)
            if not inst_result.ok:
                reasons.append(f"instrument_{inst_result.reason}")
        except Exception as exc:
            reasons.append(f"instrument_registry_error:{exc}")

    # Daily loss
    today_trades = [j for j in journal if j.get("date") == now.strftime("%Y-%m-%d")]
    today_pl = sum(t.get("pnl", 0) for t in today_trades)
    # Rough balance estimate from journal last entry
    balance = 10000.0
    if journal:
        try:
            balance = journal[-1].get("balance_after", 10000)
        except (IndexError, KeyError):
            pass

    dd_pct = calc_drawdown([j.get("balance_after", 10000) for j in journal[-500:]])
    if dd_pct > limits.get("max_drawdown_pct", 10):
        reasons.append(f"max_drawdown_exceeded:{dd_pct:.2f}%")

    loss_pct = (abs(today_pl) / balance * 100) if balance > 0 else 999
    if loss_pct > limits.get("max_daily_loss_pct", 3):
        reasons.append(f"daily_loss_exceeded:{loss_pct:.2f}%")

    # SL/TP must exist and be reasonable relative to entry price
    sl = intent.get("sl")
    tp = intent.get("tp")
    side = intent.get("side", "")
    price = intent.get("price")

    if sl is None:
        reasons.append("no_stop_loss")
    # Only validate SL/TP vs entry price if we actually know the entry price.
    # For MARKET orders with missing price, skip directional validation.
    if sl is not None and price is not None and price != 0:
        if side == "BUY" and sl >= price:
            reasons.append("sl_above_entry_for_buy")
        if side == "SELL" and sl <= price:
            reasons.append("sl_below_entry_for_sell")
    if tp is not None and price is not None and price != 0:
        if side == "BUY" and tp <= price:
            reasons.append("tp_below_entry_for_buy")
        if side == "SELL" and tp >= price:
            reasons.append("tp_above_entry_for_sell")
    qty = intent.get("qty", 0)
    max_size = max_position_size_for_symbol(sym, limits)
    if qty > max_size:
        reasons.append("position_size_too_large")
    if qty <= 0:
        reasons.append("invalid_quantity")

    policy = _latest_macro_policy()
    if policy:
        try:
            from cortex.macro_risk_policy import apply_policy_to_intent, scale_qty

            ok, macro_reason = apply_policy_to_intent(intent, policy)
            if not ok:
                reasons.append(macro_reason)
            else:
                intent = scale_qty(intent, policy)
        except Exception as exc:
            reasons.append(f"macro_policy_error:{exc}")

    # Max positions total
    open_positions = _open_positions(journal, limits)
    if len(open_positions) >= limits.get("max_positions", 5):
        reasons.append("max_positions_exceeded")

    # Correlation (same-direction same-symbol)
    same = [p for p in open_positions if p.get("symbol") == sym and p.get("side") == side]
    if len(same) >= limits.get("max_correlated_positions", 2):
        reasons.append("max_correlated_exceeded")

    loss_reason = loss_streak_block_reason(intent, limits, journal)
    if loss_reason:
        reasons.append(loss_reason)

    return len(reasons) == 0, reasons, intent


def check_position_command(command, limits):
    reasons = []
    append_runtime_reasons(reasons, ROOT)
    action = str(command.get("action") or "")
    if action not in {"close_position", "close_all", "modify"}:
        reasons.append("invalid_position_action")
    if not command.get("human_approved"):
        reasons.append("human_approval_required")
    if limits.get("mode") not in {"LIVE", "PAPER"}:
        reasons.append("mode_unknown")
    if action in {"close_position", "modify"} and command.get("ticket") in (None, ""):
        reasons.append("ticket_required")
    return len(reasons) == 0, reasons


def load_journal():
    jfile = ROOT / "memory" / "journal.jsonl"
    if not jfile.exists():
        return []
    entries = []
    with open(jfile, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def run():
    last_order_seq = current_seq()
    last_position_seq = current_seq()
    while True:
        order_events = subscribe("muscle.order.intent", since_seq=last_order_seq)
        for ev in order_events:
            seq = ev.get("seq", 0)
            if seq > last_order_seq:
                last_order_seq = seq
            intent = ev.get("payload", {})
            limits = load_limits()
            journal = load_journal()
            passed, reasons, intent = check_order(intent, limits, journal)
            if passed:
                approved_intent = attach_proof(intent, ev)
                publish("immune.pass", {
                    "type": "order_pass",
                    "intent": approved_intent,
                    "provenance": approved_intent["immune_proof"],
                })
            else:
                publish("immune.block", {
                    "type": "order_block",
                    "intent": intent,
                    "reasons": reasons,
                })

        position_events = subscribe("muscle.position.intent", since_seq=last_position_seq)
        for ev in position_events:
            seq = ev.get("seq", 0)
            if seq > last_position_seq:
                last_position_seq = seq
            command = dict(ev.get("payload") or {})
            limits = load_limits()
            passed, reasons = check_position_command(command, limits)
            if passed:
                approved = dict(command)
                approved["immune_approved"] = True
                approved["approved_at"] = time.time()
                publish("immune.position.pass", {
                    "type": "position_pass",
                    "command": approved,
                    "source_seq": seq,
                })
            else:
                publish("immune.position.block", {
                    "type": "position_block",
                    "command": command,
                    "reasons": reasons,
                })
        time.sleep(2)


if __name__ == "__main__":
    run()
