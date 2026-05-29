#!/usr/bin/env python3
"""
cortex/signal_generator_v2.py — Multi-Strategy Signal Engine with Patterns
---------------------------------------------------------------------------
Consumes candle.close events from OHLC engine.
Applies pattern recognition + strategy registry + regime detection.
Emits scored market.signal events.
"""
import os, time, sys
from pathlib import Path
from typing import List, Optional, Dict
from collections import deque

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
sys.path.insert(0, str(ROOT / "sensory"))
from bus import publish, subscribe, current_seq

from candle_patterns import scan as pattern_scan
from runtime_controls import load_controls
from trading_profile import env_bool, env_csv
try:
    from cortex import strategy_registry as strat_reg
except ImportError:  # pragma: no cover - direct script execution path
    import strategy_registry as strat_reg
from cortex.instrument_registry import InstrumentRegistry
from research.signal_research import apply_stock_research

# --- Signal scoring ---

MAX_SIGNALS_PER_15MIN = 3
SIGNAL_COOLDOWN_SEC = 300  # 5 min per symbol after a signal
MIN_SIGNAL_CONFIDENCE = float(os.getenv("TRADING_OS_MIN_SIGNAL_CONFIDENCE", "0.75"))
DIRECT_INTENTS_ENABLED = env_bool("TRADING_OS_SIGNAL_DIRECT_INTENTS", production=True, development=False)
STOCK_DIRECT_INTENTS_ENABLED = env_bool("TRADING_OS_STOCK_DIRECT_INTENTS", production=True, development=False)
MACRO_GATE_ENABLED = os.getenv("TRADING_OS_SIGNAL_MACRO_GATE", "true").strip().lower() not in {"0", "false", "no", "off"}
MACRO_GATE_MAX_AGE_SEC = int(os.getenv("TRADING_OS_SIGNAL_MACRO_GATE_MAX_AGE_SEC", "900"))
SIGNAL_MIN_CANDLES = int(os.getenv("TRADING_OS_SIGNAL_MIN_CANDLES", "10"))
SIGNAL_TIMEFRAMES = tuple(
    env_csv("TRADING_OS_SIGNAL_TIMEFRAMES", production=("M5", "M15", "H1"), development=("M5", "M15"))
)
CONTROL_RELOAD_INTERVAL_SEC = 5.0
LAST_CONTROL_LOAD = 0.0
RUNTIME_CONTROLS = load_controls()
LAST_SIGNAL_TIME: Dict[str, float] = {}
REGISTRY = InstrumentRegistry()
CANDLE_HISTORY: Dict[tuple, deque] = {}


def resolve_regime(raw: Optional[str]) -> str:
    """Map sensory `market.regime` payloads to strategy-registry labels."""
    val = str(raw or "").strip().lower()
    if val in {"trending", "ranging"}:
        return val
    # flat / insufficient_data / unknown → ranging-friendly defaults
    return "ranging"


def latest_market_regime() -> str:
    events = subscribe("market.regime", limit=1)
    if not events:
        return "ranging"
    return resolve_regime(events[-1].get("payload", {}).get("regime"))


def publish_signal_evaluation(candle: dict, *, status: str, reason: str, stage: str, **extra):
    """Publish one durable evaluation row per candle decision path.

    This is the training/audit substrate for answering "why no trade?" and for
    later walk-forward mining. Keep fields flat and stable.
    """
    payload = {
        "symbol": str(candle.get("symbol") or "").upper(),
        "timeframe": str(candle.get("timeframe") or "").upper(),
        "ts_close": candle.get("ts_close"),
        "status": status,
        "reason": reason,
        "stage": stage,
        "close": candle.get("close"),
        "open_price": candle.get("open_price"),
        "high": candle.get("high"),
        "low": candle.get("low"),
        "evaluated_ts": time.time(),
    }
    payload.update(extra)
    publish("market.signal.evaluation", payload)

def confluence_score(symbol: str, regime: str, patterns: List[dict]) -> float:
    """
    Score 0.0-1.0 based on:
    - Pattern count (more patterns = higher conviction)
    - Pattern strength (strong vs weak)
    - Regime alignment
    Calibrated so one strong aligned setup reaches the default 0.70 gate.
    """
    base = 0.52
    for p in patterns:
        strength_map = {"strong": 0.18, "moderate": 0.09, "weak": 0.04}
        base += strength_map.get(p.get("strength", "weak"), 0.04)
        if p.get("direction") == "neutral":
            base -= 0.1  # penalize indecision

    # Align with regime
    bullish = any(p.get("direction") == "bullish" for p in patterns)
    bearish = any(p.get("direction") == "bearish" for p in patterns)
    if regime == "trending" and bullish:
        base += 0.1
    elif regime == "ranging" and any(p["pattern"] in ("doji", "inside_bar", "bullish_engulfing", "bearish_engulfing") for p in patterns):
        base += 0.1

    return round(min(1.0, max(0.0, base)), 2)


def remember_candle(candle: dict, maxlen: int = 100) -> List[dict]:
    """Maintain local history from candle.close bus payloads.

    signal_generator_v2 runs in its own process, so importing the OHLC singleton
    does not share combined_feed's in-memory candle history. Without this local
    buffer the strategy engine sees len(hist)=0 forever and emits no signals.
    """
    symbol = str(candle.get("symbol") or "").upper()
    tf = str(candle.get("timeframe") or "").upper()
    if not symbol or not tf:
        return []
    key = (symbol, tf)
    if key not in CANDLE_HISTORY:
        CANDLE_HISTORY[key] = deque(maxlen=maxlen)
    buf = CANDLE_HISTORY[key]
    ts_close = candle.get("ts_close")
    if not buf or buf[-1].get("ts_close") != ts_close:
        buf.append(candle)
    return list(buf)


def latest_topic_payload(topic: str, max_age_sec: int = 60, limit: int = 1) -> Optional[dict]:
    """Return the newest payload for a topic if it is fresh enough."""
    events = subscribe(topic, limit=limit)
    if not events:
        return None
    ev = events[-1]
    if max_age_sec and time.time() - float(ev.get("ts") or 0) > max_age_sec:
        return None
    return ev.get("payload") or {}


def latest_tick(symbol: str, max_age_sec: int = 30) -> Optional[dict]:
    """Fetch latest fresh tick for a symbol, preferring per-symbol topics.

    Freshness uses both bus publish age (IPC still updating) and MT5 quote age
    (``quote_age_sec`` from ``SymbolInfoTick().time``) vs registry limits.
    """
    symbol = str(symbol or "").upper()
    max_quote_age = REGISTRY.max_fresh_quote_sec(symbol)
    for topic in (f"market.tick.{symbol}", "market.tick"):
        for ev in reversed(subscribe(topic, limit=50)):
            payload = ev.get("payload") or {}
            if str(payload.get("symbol") or "").upper() != symbol:
                continue
            if max_age_sec and time.time() - float(ev.get("ts") or 0) > max_age_sec:
                continue
            quote_age = payload.get("quote_age_sec")
            if quote_age is not None and float(quote_age) > max_quote_age:
                continue
            quote_check = REGISTRY.tick_quote_ok(symbol, payload)
            if not quote_check.ok:
                continue
            return payload
    return None


def direct_intents_allowed(symbol: str, controls: Optional[dict] = None) -> tuple[bool, str]:
    """Whether ``muscle.order.intent`` may be published for this symbol."""
    controls = controls or {}
    if not bool(controls.get("signal_direct_intents", DIRECT_INTENTS_ENABLED)):
        return False, "direct_intents_disabled"
    cfg = REGISTRY.get(symbol) or {}
    if str(cfg.get("asset_class") or "") == "stock_cfd":
        if bool(controls.get("stock_direct_intents", STOCK_DIRECT_INTENTS_ENABLED)):
            return True, "stock_direct_enabled"
        return False, "stock_direct_intents_disabled"
    return True, "ok"


def current_controls() -> dict:
    """Hot-reload dashboard/runtime controls without restarting this process."""
    global LAST_CONTROL_LOAD, RUNTIME_CONTROLS
    now = time.time()
    if now - LAST_CONTROL_LOAD >= CONTROL_RELOAD_INTERVAL_SEC:
        previous = RUNTIME_CONTROLS
        RUNTIME_CONTROLS = load_controls()
        LAST_CONTROL_LOAD = now
        if previous != RUNTIME_CONTROLS:
            publish("ops.control.applied", {"component": "cortex.signals", "controls": RUNTIME_CONTROLS})
    return RUNTIME_CONTROLS


def signal_timeframes(controls: Optional[dict] = None) -> set[str]:
    """Return operator-enabled candle timeframes for signal generation."""
    controls = controls or current_controls()
    raw = controls.get("signal_timeframes", SIGNAL_TIMEFRAMES)
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",")]
    allowed = {"M1", "M5", "M15", "M30", "H1"}
    values = {str(x).strip().upper() for x in (raw or []) if str(x).strip().upper() in allowed}
    return values or {"M5", "M15"}


def signal_min_candles(controls: Optional[dict] = None) -> int:
    controls = controls or current_controls()
    try:
        return max(3, int(controls.get("signal_min_candles", SIGNAL_MIN_CANDLES)))
    except (TypeError, ValueError):
        return SIGNAL_MIN_CANDLES


def select_strategy_for_symbol(symbol: str, regime: str, patterns: List[dict]) -> Optional[dict]:
    """Select a strategy that is also allowed by the instrument config.

    The generic registry can prefer RSI for directional patterns, but XAUUSD and
    USDJPY are currently restricted to MA_CROSS_SMA9_21. Filter first so those
    symbols do not generate a valid-looking signal that later fails as
    strategy_not_allowed.
    """
    cfg = REGISTRY.get(symbol) or {}
    allowed = {str(s).upper() for s in (cfg.get("strategies") or [])}
    candidates = strat_reg.REGISTRY.get_active(symbol, regime)
    if allowed:
        candidates = [c for c in candidates if str(c.get("id") or "").upper() in allowed]
    if not candidates:
        return None

    direction = None
    bullish = [p for p in patterns if p.get("direction") == "bullish"]
    bearish = [p for p in patterns if p.get("direction") == "bearish"]
    if len(bullish) > len(bearish):
        direction = "long"
    elif len(bearish) > len(bullish):
        direction = "short"

    def direction_match(strategy: dict) -> bool:
        ptype = strategy.get("position_type")
        if direction is None:
            return True
        if ptype == "single_direction":
            return True
        return (
            direction == "long" and ptype in {"long", "bi_directional", "adaptive"}
        ) or (
            direction == "short" and ptype in {"short", "bi_directional", "adaptive"}
        )

    candidates.sort(key=lambda x: (direction_match(x), x.get("score", 0)), reverse=True)
    return candidates[0]


def macro_gate(symbol: str, controls: Optional[dict] = None) -> tuple[bool, str, dict]:
    """Fail closed on fresh high-severity macro/radar halt signals.

    This does not suppress candidate publication. It only blocks direct order
    intents when direct intents are enabled.
    """
    controls = controls or current_controls()
    macro_enabled = bool(controls.get("signal_macro_gate", MACRO_GATE_ENABLED))
    max_age = int(controls.get("signal_macro_gate_max_age_sec", MACRO_GATE_MAX_AGE_SEC))
    if not macro_enabled:
        return True, "macro_gate_disabled", {}
    symbol = str(symbol or "").upper()
    policy = latest_topic_payload("risk.macro_policy", max_age_sec=max_age)
    if policy:
        from cortex.macro_risk_policy import apply_policy_to_intent

        ok, reason = apply_policy_to_intent({"symbol": symbol}, policy)
        if not ok:
            return False, reason, policy
    radar = latest_topic_payload("macro.event_radar", max_age_sec=max_age)
    if radar:
        symbols = [str(s).upper() for s in radar.get("candidate_symbols") or []]
        severity = str(radar.get("severity") or "").lower()
        action = str(radar.get("action_hint") or "").lower()
        bias = str(radar.get("bias") or "").lower()
        confidence = float(radar.get("confidence") or 0.0)
        if (not symbols or symbol in symbols) and confidence >= 0.8 and (
            severity in {"high", "critical"} or "hold" in action or bias == "risk_off"
        ):
            return False, "macro_event_radar_halt", radar
    from cortex.news_macro_gate import decision_blocks_symbol

    news_decision = latest_topic_payload("cortex.decision", max_age_sec=max_age)
    if news_decision:
        blocked, halt_reason = decision_blocks_symbol(symbol, news_decision, max_age_sec=max_age)
        if blocked:
            return False, f"cortex.decision_{halt_reason}", news_decision

    brain_result = latest_topic_payload("cortex.brain.result", max_age_sec=max_age)
    if brain_result:
        inner = brain_result.get("decision") or {}
        macro = inner.get("macro") or {}
        if macro.get("blackout_recommended"):
            affected = {str(s or "").upper() for s in (macro.get("affected_symbols") or []) if s}
            if not affected or symbol in affected:
                return False, "cortex.brain.macro_blackout", brain_result
    return True, "ok", {}


def _num(value, default=None):
    try:
        if value in (None, "", "auto"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def instrument_unit(symbol: str, cfg: dict) -> tuple[float, int, str]:
    """Return price unit, rounding digits, and unit label for stops/spreads."""
    pip_size = _num(cfg.get("pip_size"))
    point_size = _num(cfg.get("point_size"), _num(cfg.get("tick_size")))
    if pip_size:
        return pip_size, 3 if pip_size >= 0.01 else 5, "pips"
    unit = point_size or 0.01
    return unit, 2 if unit >= 0.01 else 5, "points"


def bootstrap_candle_history(limit: int = 500) -> int:
    """Warm local history from existing candle.close events after restart."""
    loaded = 0
    for ev in subscribe("candle.close", limit=limit):
        payload = ev.get("payload", {})
        before = sum(len(buf) for buf in CANDLE_HISTORY.values())
        remember_candle(payload)
        after = sum(len(buf) for buf in CANDLE_HISTORY.values())
        if after > before:
            loaded += 1
    return loaded


def build_intent(symbol: str, side: str, patterns: List[dict], candles: List[dict], strategy_id: str, regime: str = "trending", tick: Optional[dict] = None) -> Optional[dict]:
    """Build a complete order intent with SL/TP/qty from candle context."""
    if not candles:
        return None
    latest = candles[-1]
    cfg = REGISTRY.get(symbol) or {}
    canonical = cfg.get("symbol", symbol)
    unit, digits, unit_label = instrument_unit(canonical, cfg)
    tick = tick or {}
    bid = _num(tick.get("bid"), _num(latest.get("close")))
    ask = _num(tick.get("ask"))
    if ask is None and bid is not None:
        ask = bid + unit * 0.2  # fallback only for candidate construction
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None

    # ATR-based SL/TP from recent candles
    ranges = [_num(c.get("range")) for c in candles[-14:] if _num(c.get("range"))]
    atr = sum(ranges) / len(ranges) if ranges else unit * 10

    min_stop_units = _num(cfg.get("min_stop_distance_pips"), None)
    if min_stop_units is None:
        min_stop_units = _num(cfg.get("min_stop_distance_points"), None)
    min_stop = (min_stop_units * unit) if min_stop_units else unit * 5
    sl_dist = max(atr * 1.5, min_stop)
    tp_dist = max(atr * 2.5, min_stop * 2)

    if side == "BUY":
        price = ask
        sl = round(price - sl_dist, digits)
        tp = round(price + tp_dist, digits)
    else:
        price = bid
        sl = round(price + sl_dist, digits)
        tp = round(price - tp_dist, digits)

    # Dynamic position sizing
    sl_units = sl_dist / unit
    risk_amt = 10000 * 0.015  # 1.5% of demo 10k
    tick_value = _num(cfg.get("tick_value"))
    tick_size = _num(cfg.get("tick_size"), unit) or unit
    value_per_unit_per_lot = (tick_value / tick_size * unit) if tick_value else 10.0
    lots = risk_amt / max(sl_units * value_per_unit_per_lot, 1)
    min_lot = float(cfg.get("min_lot") or 0.01)
    max_lot = float(cfg.get("max_lot") or 0.5)
    qty = max(min_lot, round(min(lots, max_lot), 2))
    confidence = confluence_score(canonical, regime, patterns)
    try:
        from cortex.live_policy import calibrate_confidence

        confidence = calibrate_confidence(confidence, pattern_count=len(patterns))
    except ImportError:
        pass

    return {
        "strategy_id": strategy_id,
        "symbol": canonical,
        "side": side,
        "qty": qty,
        "price": round(price, digits),
        "sl": sl,
        "tp": tp,
        "type": "MARKET",
        "confidence": confidence,
        "reason": f"Pattern confluence: {', '.join(p['pattern'] for p in patterns[:3])}",
        "patterns": [p["pattern"] for p in patterns],
        "regime": regime,
        "sizing": {"unit": unit, "unit_label": unit_label, "sl_units": round(sl_units, 2), "atr": atr},
    }


def run():
    loaded = bootstrap_candle_history()
    print(f"[signal_generator_v2] bootstrapped {loaded} candle(s) into local history", flush=True)
    last_seq = current_seq()
    while True:
        # Subscribe to candle completion events (published by sensory/ohlc_engine wrapper)
        events = subscribe("candle.close", since_seq=last_seq)
        controls = current_controls()
        for ev in events:
            last_seq = max(last_seq, ev.get("seq", 0))
            payload = ev.get("payload", {})
            symbol = payload.get("symbol", "")
            tf = payload.get("timeframe", "")

            symbol_check = REGISTRY.validate_symbol(symbol)
            if not symbol_check.ok:
                publish_signal_evaluation(payload, status="skipped", reason=symbol_check.reason, stage="symbol_gate", symbol=symbol)
                continue

            enabled_timeframes = signal_timeframes(controls)
            if tf not in enabled_timeframes:
                publish_signal_evaluation(payload, status="skipped", reason="timeframe_disabled", stage="timeframe_filter", enabled_timeframes=sorted(enabled_timeframes))
                continue

            hist = remember_candle(payload)[-50:]
            min_candles = signal_min_candles(controls)
            if len(hist) < min_candles:
                publish("market.signal.candidate", {
                    "symbol": symbol,
                    "timeframe": tf,
                    "blocked_reason": "warming_up",
                    "candles": len(hist),
                    "min_candles": min_candles,
                    "enabled_timeframes": sorted(enabled_timeframes),
                })
                publish_signal_evaluation(payload, status="blocked", reason="warming_up", stage="warmup", candles=len(hist), min_candles=min_candles)
                continue

            # Detect patterns
            patterns = pattern_scan(hist, symbol, tf)
            if not patterns:
                publish_signal_evaluation(payload, status="skipped", reason="no_patterns", stage="pattern_scan", candles=len(hist))
                continue

            # Check cooldown
            now = time.time()
            if symbol in LAST_SIGNAL_TIME and now - LAST_SIGNAL_TIME[symbol] < SIGNAL_COOLDOWN_SEC:
                publish_signal_evaluation(payload, status="blocked", reason="cooldown", stage="cooldown", seconds_since_last=round(now - LAST_SIGNAL_TIME[symbol], 2), cooldown_sec=SIGNAL_COOLDOWN_SEC, patterns=patterns)
                continue

            # Determine direction from strongest patterns
            bullish = [p for p in patterns if p.get("direction") == "bullish"]
            bearish = [p for p in patterns if p.get("direction") == "bearish"]

            if bullish and not bearish:
                side = "BUY"
            elif bearish and not bullish:
                side = "SELL"
            else:
                # Mixed signals or only neutral — skip
                publish_signal_evaluation(payload, status="skipped", reason="mixed_or_neutral_patterns", stage="direction", patterns=patterns)
                continue

            # Registry check — regime from sensory/regime_detector via bus
            regime = latest_market_regime()
            _selected = select_strategy_for_symbol(symbol, regime, patterns)
            if not _selected:
                reason = "no_allowed_strategy_for_symbol"
                print(f"[signal_generator_v2] blocked signal: {reason} {symbol}/{regime}")
                publish("market.signal.blocked", {"reason": reason, "symbol": symbol, "regime": regime, "patterns": patterns})
                publish_signal_evaluation(payload, status="blocked", reason=reason, stage="strategy_selection", regime=regime, patterns=patterns)
                continue
            strat_id = _selected["id"]

            tick = latest_tick(symbol)
            if not tick:
                publish("market.signal.blocked", {"reason": "no_fresh_tick", "symbol": symbol, "patterns": patterns, "timeframe": tf})
                publish_signal_evaluation(payload, status="blocked", reason="no_fresh_tick", stage="market_snapshot", regime=regime, patterns=patterns, strategy_id=strat_id)
                continue
            quote_check = REGISTRY.tick_quote_ok(symbol, tick)
            if not quote_check.ok:
                publish("market.signal.blocked", {"reason": quote_check.reason, "symbol": symbol, "quote": quote_check.as_dict(), "patterns": patterns})
                publish_signal_evaluation(payload, status="blocked", reason=quote_check.reason, stage="quote_freshness", regime=regime, patterns=patterns, strategy_id=strat_id)
                continue
            intent = build_intent(symbol, side, patterns, hist, strat_id, regime=regime, tick=tick)
            if intent:
                cfg = REGISTRY.get(symbol) or {}
                asset_class = str(cfg.get("asset_class") or "")
                intent, research_reason = apply_stock_research(
                    intent,
                    symbol,
                    asset_class=asset_class,
                    controls=controls,
                )
                if intent is None:
                    publish(
                        "market.signal.blocked",
                        {"reason": research_reason, "symbol": symbol, "patterns": patterns, "timeframe": tf},
                    )
                    publish_signal_evaluation(
                        payload,
                        status="blocked",
                        reason=str(research_reason or "research_gate"),
                        stage="research_gate",
                        regime=regime,
                        patterns=patterns,
                        strategy_id=strat_id,
                    )
                    continue
                validation = REGISTRY.validate_order(intent, market_snapshot=tick)
                if not validation.ok:
                    print(f"[signal_generator_v2] blocked invalid signal: {validation.as_dict()}")
                    publish("market.signal.blocked", {"reason": validation.reason, "validation": validation.as_dict(), "intent": intent})
                    publish_signal_evaluation(payload, status="blocked", reason=validation.reason, stage="instrument_validation", regime=regime, patterns=patterns, strategy_id=strat_id, intent=intent, validation=validation.as_dict())
                    continue
                try:
                    from cortex.live_policy import effective_signal_min_confidence

                    min_confidence = effective_signal_min_confidence(
                        float(controls.get("signal_min_confidence", MIN_SIGNAL_CONFIDENCE))
                    )
                except ImportError:
                    min_confidence = float(controls.get("signal_min_confidence", MIN_SIGNAL_CONFIDENCE))
                if float(intent.get("confidence") or 0.0) < min_confidence:
                    publish("market.signal.candidate", {**intent, "blocked_reason": "below_min_confidence", "min_confidence": min_confidence})
                    publish_signal_evaluation(payload, status="blocked", reason="below_min_confidence", stage="confidence", regime=regime, patterns=patterns, strategy_id=strat_id, confidence=intent.get("confidence"), min_confidence=min_confidence, intent=intent)
                    continue
                macro_ok, macro_reason, macro_context = macro_gate(symbol, controls=controls)
                if not macro_ok:
                    publish("market.signal.candidate", {**intent, "blocked_reason": macro_reason, "macro_context": macro_context})
                    publish_signal_evaluation(payload, status="blocked", reason=macro_reason, stage="macro_gate", regime=regime, patterns=patterns, strategy_id=strat_id, confidence=intent.get("confidence"), intent=intent, macro_context=macro_context)
                    continue
                intent["symbol"] = validation.symbol or intent["symbol"]
                intent["strategy_id"] = validation.details.get("strategy_id", strat_id)
                if "rounded_qty" in validation.details:
                    intent["qty"] = validation.details["rounded_qty"]
                intent["strategy_id"] = strat_id
                intent["order_id"] = f"{strat_id}_{symbol}_{int(time.time())}"
                intent["mode_check"] = False
                policy = latest_topic_payload("risk.macro_policy", max_age_sec=MACRO_GATE_MAX_AGE_SEC)
                if policy:
                    from cortex.macro_risk_policy import scale_qty

                    intent = scale_qty(intent, policy)

                LAST_SIGNAL_TIME[symbol] = now
                publish("market.signal", intent)
                direct_enabled, direct_reason = direct_intents_allowed(symbol, controls=controls)
                publish_signal_evaluation(payload, status="passed", reason="signal_emitted", stage="publish_signal", regime=regime, patterns=patterns, strategy_id=strat_id, confidence=intent.get("confidence"), order_id=intent.get("order_id"), intent=intent, direct_intents_enabled=direct_enabled, direct_block_reason=direct_reason)
                if direct_enabled:
                    publish("muscle.order.intent", intent)
                else:
                    publish("market.signal.candidate", {**intent, "blocked_reason": direct_reason, "quote_age_sec": tick.get("quote_age_sec")})

        time.sleep(5)


if __name__ == "__main__":
    run()
