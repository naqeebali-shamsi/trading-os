"""
autonome/india/signals.py  v1.0
India Manual Trading Signal Generator.

Combines fundamentals, technicals, and macro into BUY/SELL/HOLD directions
with explanations and confidence scores for manual execution.

Signal types:
    STRONG_BUY  — Fundamentals excellent + in dip + reversal candle
    BUY         — Fundamentals good + in dip
    HOLD        — Already own, fundamentals still ok
    REDUCE      — Near 52w high or fundamentals weakening
    SELL        — Fundamentals deteriorated or stop hit
    WATCH       — Interesting but not ready yet
"""
from __future__ import annotations

import json, logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from autonome.india.fundamentals import IndianStock, screen_stock, INDIA_UNIVERSE
from autonome.india.sentinel import IndiaSentinel
from autonome.data.yahoo_feed import fetch_history

log = logging.getLogger("india.signals")


@dataclass
class IndiaSignal:
    symbol: str
    action: str  # STRONG_BUY | BUY | HOLD | REDUCE | SELL | WATCH
    confidence: float  # 0.0 - 1.0
    price: float
    target: float
    stop: float
    thesis: str  # human-readable explanation
    rationale: List[str]  # bullet points
    sector: str
    fundamentals: Dict
    regime: str  # AGGRESSIVE | BALANCED | CAUTIOUS | DEFENSE


def check_candle_pattern(symbol: str) -> Tuple[str, float]:
    """
    Check recent candle patterns for reversal signals.
    Returns (pattern_description, confidence_boost).
    """
    from datetime import timedelta
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    bars = fetch_history(symbol, start=start, end=end, timeframe="1d")

    if len(bars) < 5:
        return "insufficient data", 0.0

    recent = bars[-5:]
    latest = bars[-1]
    prev = bars[-2]

    # Count red/green
    reds = sum(1 for b in recent if b.close < b.open)
    greens = 5 - reds

    # Strong green after reds
    if latest.close > latest.open and reds >= 3:
        body = latest.close - latest.open
        upper_wick = latest.high - latest.close
        lower_wick = min(latest.open, latest.close) - latest.low
        if body > upper_wick and lower_wick < body * 0.3:
            return "strong green reversal after decline", 0.15

    # Hammer
    range_ = latest.high - latest.low
    body = abs(latest.close - latest.open)
    lower_wick = min(latest.open, latest.close) - latest.low
    if range_ > 0 and lower_wick / range_ > 0.6 and body / range_ < 0.3:
        return "hammer candle (potential reversal)", 0.12

    # Doji at bottom
    if body < range_ * 0.1 and latest.close < prev.close * 1.02:
        return "doji near support", 0.08

    return f"{reds} red / {greens} green recent", 0.0


def generate_signals(portfolio: Dict[str, int] = None) -> List[IndiaSignal]:
    """
    Generate trading signals for all India universe stocks.

    portfolio: dict of symbol -> quantity owned (optional)
    """
    portfolio = portfolio or {}
    signals = []

    # Macro regime
    sentinel = IndiaSentinel()
    macro = sentinel.scan()
    regime = sentinel.recommend_regime()

    # Collect all universe symbols
    all_symbols = []
    for cat, syms in INDIA_UNIVERSE.items():
        all_symbols.extend(syms)
    all_symbols = list(set(all_symbols))

    log.info("Generating India signals for %d stocks | Regime: %s", len(all_symbols), regime)

    for sym in all_symbols:
        stock = screen_stock(sym)
        if not stock or stock.price <= 0:
            continue

        # Skip if market cap too small
        if stock.market_cap_cr < 1000:
            continue

        candle, candle_boost = check_candle_pattern(sym)
        owned = sym in portfolio and portfolio[sym] > 0
        qty = portfolio.get(sym, 0)
        invested_value = qty * stock.price

        # Default scores
        val_score = stock.value_score()
        fund_score = stock.fundamental_score()
        action = "WATCH"
        confidence = 0.5
        thesis_parts = []
        rationale = []

        # Determine action
        if owned:
            # EXIT conditions
            if fund_score < 4.0:
                action = "SELL"
                confidence = 0.75
                thesis_parts.append(f"Fundamentals weakened to {fund_score:.1f}")
                rationale.append("ROE or profitability declining")
            elif stock.is_near_high:
                action = "REDUCE"
                confidence = 0.65
                thesis_parts.append(f"Near 52-week high ({stock.distance_from_52w_low:.0%})")
                rationale.append("Take partial profits")
            elif stock.is_in_dip and val_score >= 6.5:
                action = "HOLD"
                confidence = 0.7
                thesis_parts.append(f"In dip but fundamentals strong (FS={fund_score})")
                rationale.append("Already own, hold for recovery")
            else:
                action = "HOLD"
                confidence = 0.55
                thesis_parts.append("Holding, no clear signal")
        else:
            # ENTRY conditions
            if val_score >= 8.0 and stock.is_in_dip and candle_boost >= 0.1:
                action = "STRONG_BUY"
                confidence = min(0.95, 0.7 + candle_boost)
                thesis_parts.append(f"Strong value score {val_score:.1f} + reversal candle")
                rationale.append(f"Near 52w low ({stock.distance_from_52w_low:.0%})")
                roe_str = f"{stock.roe:.1f}%" if stock.roe else "N/A"
                rationale.append(f"Solid fundamentals: ROE={roe_str}, PE={stock.pe_trailing}")
                rationale.append(f"Candle pattern: {candle}")
            elif val_score >= 6.5 and stock.is_in_dip:
                action = "BUY"
                confidence = min(0.85, 0.6 + candle_boost)
                thesis_parts.append(f"Value score {val_score:.1f}, in dip")
                rationale.append(f"52w range: {stock.distance_from_52w_low:.0%} from low")
                pe_str = f"PE={stock.pe_trailing}" if stock.pe_trailing else ""
                roe_str = f"ROE={stock.roe:.1f}%" if stock.roe else ""
                rationale.append(f"{pe_str} {roe_str}".strip())
            elif val_score >= 6.0 and not stock.is_near_high:
                action = "WATCH"
                confidence = 0.4
                thesis_parts.append(f"Good fundamentals (FS={fund_score:.1f}) but not in dip yet")
                rationale.append("Wait for price to come closer to 52w low")
            else:
                action = "SKIP"
                confidence = 0.0
                thesis_parts.append("Does not meet criteria")

        if action == "SKIP":
            continue

        # Calculate target/stop
        if action in ("STRONG_BUY", "BUY"):
            target = min(stock.fifty_two_week_high, stock.price * 1.18)
            stop = stock.price * 0.90  # 10% stop for India
        elif action == "REDUCE":
            target = 0
            stop = 0
        elif action == "SELL":
            target = 0
            stop = 0
        else:
            target = min(stock.fifty_two_week_high, stock.price * 1.15)
            stop = stock.price * 0.88

        # Apply regime penalty
        if regime == "DEFENSE" and action in ("STRONG_BUY", "BUY"):
            if not stock.is_in_dip or stock.fundamental_score() < 7.0:
                action = "WATCH"
                confidence = 0.35
                thesis_parts.append("DEFENSE regime: only highest conviction dips")
            else:
                confidence *= 0.85  # Reduce confidence in defense

        signals.append(IndiaSignal(
            symbol=stock.symbol,
            action=action,
            confidence=round(confidence, 2),
            price=round(stock.price, 2),
            target=round(target, 2),
            stop=round(stop, 2),
            thesis="; ".join(thesis_parts),
            rationale=rationale if rationale else [thesis_parts[0]],
            sector=stock.sector,
            fundamentals=stock.dict(),
            regime=regime,
        ))

    # Sort: STRONG_BUY first, then BUY, then others
    priority = {"STRONG_BUY": 4, "BUY": 3, "REDUCE": 2, "SELL": 2, "HOLD": 1, "WATCH": 0}
    signals.sort(key=lambda s: (priority.get(s.action, 0), s.confidence), reverse=True)
    return signals


def _fmt_price(val):
    if val is None or val == 0:
        return "N/A"
    if val >= 100000:
        return f"₹{val:,.0f}"
    if val >= 1000:
        return f"₹{val:,.0f}"
    return f"₹{val:,.2f}"


def _fmt_num(val, decimals=1):
    if val is None:
        return "N/A"
    return f"{val:,.{decimals}f}"


def _fmt_pct(val):
    if val is None:
        return "N/A"
    return f"{val:.1f}%"


def _fmt_cr(val):
    """Format crores with Indian comma style."""
    if val is None or val == 0:
        return "N/A"
    if val >= 100000:
        return f"{val/100000:.1f}L Cr"
    if val >= 1000:
        return f"{val/1000:.1f}K Cr"
    return f"{val:.0f} Cr"


def _fetch_sparkline(symbol: str, days: int = 20) -> List[float]:
    """Fetch recent closing prices for sparkline."""
    from datetime import timedelta
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 5)
        bars = fetch_history(symbol, start=start, end=end, timeframe="1d")
        return [b.close for b in bars[-days:]] if bars else []
    except Exception as e:
        log.warning("Sparkline fetch failed for %s: %s", symbol, e)
        return []


def write_signals_json(path: str = None) -> str:
    """Write signals to JSON file for dashboard with formatted numbers and sparklines."""
    if path is None:
        path = "/mnt/e/NomadCrew[GROWTH]/trading-os/v2/swarm/intel/india_signals.json"

    signals = generate_signals()

    # Enrich with formatted strings and sparklines
    enriched = []
    for s in signals:
        d = asdict(s)
        f = d["fundamentals"]

        # Enrich with formatted strings and sparklines
        d["display_symbol"] = s.symbol.replace(".NS", "")
        d["price_fmt"] = _fmt_price(d["price"])
        d["target_fmt"] = _fmt_price(d["target"])
        d["stop_fmt"] = _fmt_price(d["stop"])
        d["confidence_pct"] = f"{d['confidence']*100:.0f}%"

        # Format fundamentals
        f["pe_fmt"] = _fmt_num(f.get("pe"), 1)
        f["pb_fmt"] = _fmt_num(f.get("pb"), 1)
        f["roe_fmt"] = _fmt_pct(f.get("roe"))
        f["debt_equity_fmt"] = _fmt_num(f.get("debt_equity"), 2)
        f["market_cap_fmt"] = _fmt_cr(f.get("market_cap_cr"))
        f["distance_from_low_fmt"] = f"{f.get('distance_from_low', 0)*100:.0f}%"

        # Sparkline
        d["sparkline"] = _fetch_sparkline(s.symbol, days=20)

        enriched.append(d)

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(signals),
        "signals": enriched,
    }
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    log.info("Signals written: %s (%d signals)", path, len(signals))
    return path
