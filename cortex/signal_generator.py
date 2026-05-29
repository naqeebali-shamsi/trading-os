#!/usr/bin/env python3
"""
DEPRECATED: Not supervised. Use signal_generator_v2.py. Do not run in production.

cortex/signal_generator.py -- Strategy Signals (v1)
---------------------------------------------------
Reads market ticks, computes technical signals, emits
market.signal events with full order intent (SL/TP/qty).

Strategies:
  - SMA9/21 cross (trend-following)
  - RSI mean-reversion (oversold/overbought)
  - Regime-adaptive switcher

SIGNAL format:
{
  "strategy_id": "SMA_CROSS",
  "symbol": "XAUUSD",
  "side": "BUY",
  "qty": 0.01,
  "sl": 4705.50,
  "tp": 4730.00,
  "type": "MARKET",
  "confidence": 0.72,
  "reason": "SMA9 crossed above SMA21, regime=trending"
}
"""
import json, time, sys
from pathlib import Path
from collections import deque

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, subscribe
from cortex.instrument_registry import InstrumentRegistry
from cortex.strategy_registry import normalize_strategy_id

TICK_BUFS = {}  # symbol -> deque of (ts, bid, ask)
BUF_LEN = 200

# ---- Strategy state ----
STRAT_STATE = {
    "MA_CROSS_SMA9_21": {"last_signal_side": None, "sma9_prev": None, "sma21_prev": None},
    "RSI_MEAN_REVERSION": {"last_signal_side": None},
}

REGISTRY = InstrumentRegistry()


def validate_generated_intent(intent):
    """Canonicalize and validate a generated intent before it is published."""
    if not intent:
        return None
    intent = dict(intent)
    intent["strategy_id"] = normalize_strategy_id(intent.get("strategy_id"))
    result = REGISTRY.validate_order(intent)
    if not result.ok:
        print(f"[signal_generator] blocked invalid signal: {result.as_dict()}")
        return None
    intent["symbol"] = result.symbol or intent.get("symbol")
    intent["strategy_id"] = result.details.get("strategy_id", intent.get("strategy_id"))
    if "rounded_qty" in result.details:
        intent["qty"] = result.details["rounded_qty"]
    return intent

def get_buf(symbol):
    if symbol not in TICK_BUFS:
        TICK_BUFS[symbol] = deque(maxlen=BUF_LEN)
    return TICK_BUFS[symbol]

def sma(arr, period):
    if len(arr) < period:
        return None
    return sum(arr[-period:]) / period

def rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        ch = prices[-i] - prices[-i - 1]
        if ch > 0:
            gains.append(ch)
        else:
            losses.append(abs(ch))
    avg_gain = sum(gains) / len(gains) if gains else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    rs = avg_gain / max(avg_loss, 1e-10)
    return 100 - (100 / (1 + rs))

def ema(arr, period):
    """Simple EMA for regime detection."""
    if len(arr) < period:
        return None
    k = 2.0 / (period + 1)
    ema_val = arr[0]
    for price in arr[1:]:
        ema_val = price * k + ema_val * (1 - k)
    return ema_val

def atr(prices, period=14):
    if len(prices) < period + 1:
        return None
    trs = []
    for i in range(1, period + 1):
        trs.append(abs(prices[-i] - prices[-i - 1]))
    return sum(trs) / len(trs)

def calc_qty(balance=10000, risk_pct=1.5, sl_dist=50, symbol="XAUUSD"):
    """Position sizing: risk_pct of balance / sl_dist in points.
    Returns lot size rounded to 0.01.
    """
    if sl_dist <= 0:
        return 0.01
    risk_amt = balance * (risk_pct / 100)
    # For forex: 1 lot = 100k units, 1 pip ~ $10. For XAUUSD, 1 lot ~ $1 per 0.01.
    # Simplified: lots = risk_amt / (sl_dist * point_value)
    point_value = 1.0 if "USD" in symbol else 0.1  # rough
    lots = risk_amt / (sl_dist * point_value)
    return max(0.01, round(min(lots, 0.5), 2))

def get_pip_mult(symbol):
    """Return pip multiplier for SL/TP distance calculation."""
    if symbol == "XAUUSD":
        return 0.01  # 1 pip = $0.01 for gold
    if symbol in ("EURUSD", "GBPUSD"):
        return 0.0001  # 4-decimal
    return 0.01

def sma_cross_strategy(buf, state, regime):
    prices = [b[1] for b in buf]  # bid prices
    if len(prices) < 30:
        return None
    sma9 = sma(prices, 9)
    sma21 = sma(prices, 21)
    if sma9 is None or sma21 is None:
        return None
    prev9 = state["sma9_prev"]
    prev21 = state["sma21_prev"]
    state["sma9_prev"] = sma9
    state["sma21_prev"] = sma21
    if prev9 is None or prev21 is None:
        return None
    # Cross detection
    cross_up = prev9 <= prev21 and sma9 > sma21
    cross_down = prev9 >= prev21 and sma9 < sma21
    if not cross_up and not cross_down:
        return None
    # Only trade trending regime
    if regime not in ("trending",):
        return None
    # Avoid duplicate same-direction signals
    side = "BUY" if cross_up else "SELL"
    if state["last_signal_side"] == side:
        return None
    state["last_signal_side"] = side
    # SL/TP using ATR
    latest = buf[-1]
    bid, ask = latest[1], latest[2]
    symbol = latest[3]
    price = ask if side == "BUY" else bid
    pip = get_pip_mult(symbol)
    atr_val = atr(prices, 14) or (20 * pip)
    sl_dist = max(atr_val * 1.5, 5 * pip)
    tp_dist = max(atr_val * 2.5, 10 * pip)
    if side == "BUY":
        sl = round(price - sl_dist, 2)
        tp = round(price + tp_dist, 2)
    else:
        sl = round(price + sl_dist, 2)
        tp = round(price - tp_dist, 2)
    qty = calc_qty(balance=10000, risk_pct=1.5, sl_dist=sl_dist / pip, symbol=symbol)
    return {
        "strategy_id": "MA_CROSS_SMA9_21",
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "sl": sl,
        "tp": tp,
        "type": "MARKET",
        "confidence": 0.7,
        "reason": f"SMA9 crossed {'above' if cross_up else 'below'} SMA21, regime={regime}, sma9={sma9:.4f}",
    }

def rsi_mean_reversion_strategy(buf, state, regime):
    prices = [b[1] for b in buf]
    if len(prices) < 20:
        return None
    val = rsi(prices, 14)
    if val is None:
        return None
    # Only trade range-bound / flat regimes
    if regime not in ("ranging", "flat"):
        return None
    latest = buf[-1]
    bid, ask = latest[1], latest[2]
    symbol = latest[3]
    pip = get_pip_mult(symbol)
    side = None
    if val < 28:
        side = "BUY"
    elif val > 72:
        side = "SELL"
    if side is None:
        return None
    if state["last_signal_side"] == side:
        return None
    state["last_signal_side"] = side
    price = ask if side == "BUY" else bid
    sl_dist = 15 * pip
    tp_dist = 8 * pip
    if side == "BUY":
        sl = round(price - sl_dist, 2)
        tp = round(price + tp_dist, 2)
    else:
        sl = round(price + sl_dist, 2)
        tp = round(price - tp_dist, 2)
    qty = calc_qty(balance=10000, risk_pct=1.0, sl_dist=sl_dist / pip, symbol=symbol)
    return {
        "strategy_id": "RSI_MEAN_REVERSION",
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "sl": sl,
        "tp": tp,
        "type": "MARKET",
        "confidence": min(abs(val - 50) / 50, 0.85),
        "reason": f"RSI={val:.1f} {'oversold' if side=='BUY' else 'overbought'} in {regime} regime",
    }

def run():
    last_seq = 0
    last_regime = "flat"
    while True:
        evs = subscribe("market.tick", since_seq=last_seq)
        for ev in evs:
            seq = ev.get("seq", 0)
            if seq > last_seq:
                last_seq = seq
            p = ev.get("payload", {})
            sym = p.get("symbol", "UNKNOWN")
            buf = get_buf(sym)
            buf.append((ev["ts"], p.get("bid", 0), p.get("ask", 0), sym))
        # Update regime from bus
        reg_evs = subscribe("market.regime", limit=1)
        if reg_evs:
            last_regime = reg_evs[-1]["payload"].get("regime", "flat")
        # Generate signals
        for sym, buf in TICK_BUFS.items():
            if len(buf) < 30:
                continue
            sig = validate_generated_intent(sma_cross_strategy(buf, STRAT_STATE["MA_CROSS_SMA9_21"], last_regime))
            if sig:
                publish("market.signal", sig)
                # Direct intent for immediate execution pipeline
                intent = {**sig, "order_id": f"sma_{int(time.time())}", "mode_check": False}
                publish("muscle.order.intent", intent)
                continue
            sig = validate_generated_intent(rsi_mean_reversion_strategy(buf, STRAT_STATE["RSI_MEAN_REVERSION"], last_regime))
            if sig:
                publish("market.signal", sig)
                intent = {**sig, "order_id": f"rsi_{int(time.time())}", "mode_check": False}
                publish("muscle.order.intent", intent)
        time.sleep(15)


if __name__ == "__main__":
    run()
