"""
autonome/india/strategy.py  v1.0
India-specific 'Buy the Dip on Strong Fundamentals' strategy.

India characteristics:
- Higher volatility than US (~25-30% vs ~15-20%)
- Political risk, corruption, sudden FII outflows
- Manufacturing boom = long-term structural tailwind
- Fundamentally strong stocks recover even if dip lasts months
- Position sizing: SMALLER positions, WIDER stops, LONGER holds

Signal logic:
1. Screen for value-score >= 6.0 (fundamentals + dip)
2. Rank by (lowest distance from 52w low) * (highest fundamental score)
3. Enter on first green day after a series of red days
4. Size based on volatility (higher vol = smaller size)
5. Hold until value_score drops OR 52w high reached OR 6+ months
"""
from __future__ import annotations

import statistics, logging
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from autonome.india.fundamentals import IndianStock, screen_stock, find_value_picks
from autonome.data.yahoo_feed import fetch_history
from autonome.data.bars import Bar

log = logging.getLogger("india.strategy")


@dataclass
class IndiaSignal:
    symbol: str
    action: str  # ENTER | ADD | HOLD | TRIM | EXIT
    trigger: str  # value_dip | momentum_break | stop_loss | target | reduce
    quantity: int  # number of shares
    confidence: float  # 0-1
    thesis: str
    max_position_pct: float  # of equity
    suggested_stop: float
    suggested_target: float
    timeframe: str = "3-6 months"


class IndiaValueStrategy:
    """
    Buy-the-dip on strong fundamentals.
    Designed for Indian market volatility and structural growth.
    """

    def __init__(self, equity: float = 1_000_000.0):  # Default to 10 lakh INR
        self.equity = equity
        self.max_positions = 8
        self.max_sector_pct = 25.0  # Don't concentrate more than 25% per sector
        self.position_size_base = 3.0  # 3% per position

    def _fetch_recent_bars(self, symbol: str, lookback: int = 20) -> List[Bar]:
        """Fetch recent daily bars."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback + 5)
        bars = fetch_history(symbol, start=start, end=end, timeframe="1d")
        return bars

    def _recent_candles(self, bars: List[Bar]) -> Tuple[bool, str]:
        """
        Check if recent candles show a potential reversal pattern.
        Returns (is_green_reversal, description).
        """
        if len(bars) < 5:
            return False, "insufficient data"

        # Last 5 candles
        recent = bars[-5:]
        red_count = sum(1 for b in recent if b.close < b.open)
        green_count = 5 - red_count

        latest = bars[-1]
        prev = bars[-2]

        # Strong green candle after reds
        if latest.close > latest.open and red_count >= 3:
            body = latest.close - latest.open
            wick = latest.high - latest.close
            if body > wick * 0.5:  # Decent body, not just a doji
                return True, f"green reversal after {red_count} red candles"

        # Hammer candle (long lower wick, small body)
        if latest.low < min(latest.open, latest.close):
            range_ = latest.high - latest.low
            body = abs(latest.close - latest.open)
            lower_wick = min(latest.open, latest.close) - latest.low
            if range_ > 0 and lower_wick / range_ > 0.6 and body / range_ < 0.3:
                return True, "hammer candle (reversal)"

        return False, f"{red_count} red / {green_count} green recent"

    def _atr_percent(self, bars: List[Bar], period: int = 14) -> float:
        """Average true range as % of last close."""
        if len(bars) < period + 1:
            return 2.0  # Default

        trs = []
        for i in range(1, min(period, len(bars))):
            b0, b1 = bars[-i], bars[-i-1]
            tr = max(
                b1.high - b1.low,
                abs(b1.high - b0.close),
                abs(b1.low - b0.close),
            )
            trs.append(tr)
        atr = statistics.mean(trs) if trs else bars[-1].close * 0.02
        return (atr / bars[-1].close) * 100

    def generate_signals(self, symbols: List[str] = None) -> List[IndiaSignal]:
        """
        Generate buy/hold/trim signals for Indian stocks.
        """
        signals = []
        picks = find_value_picks(symbols=symbols, min_value_score=6.0)

        for stock in picks[:self.max_positions]:
            bars = self._fetch_recent_bars(stock.symbol, 30)
            if len(bars) < 5:
                continue

            is_reversal, candle_desc = self._recent_candles(bars)
            atr_pct = self._atr_percent(bars)
            latest = bars[-1]

            # Position sizing: smaller for volatile stocks
            vol_factor = max(0.3, 1.0 - (atr_pct / 5.0))  # 5% ATR = 0.3x size
            pos_pct = self.position_size_base * vol_factor
            notional = self.equity * (pos_pct / 100)
            qty = int(notional / stock.price)

            # Stop loss: 2x ATR below entry for India volatility
            stop = latest.low * (1 - (atr_pct / 100) * 2)
            # Target: 52w high or 15-20% gain whichever is closer
            target = min(stock.fifty_two_week_high, stock.price * 1.18)

            if is_reversal:
                signals.append(IndiaSignal(
                    symbol=stock.symbol,
                    action="ENTER",
                    trigger="value_dip",
                    quantity=max(qty, 1),
                    confidence=min(0.85, stock.value_score() / 10),
                    thesis=f"Value buy: score={stock.value_score():.1f}, near 52w low ({stock.distance_from_52w_low:.0%}), {candle_desc}",
                    max_position_pct=pos_pct,
                    suggested_stop=round(stop, 2),
                    suggested_target=round(target, 2),
                ))
            elif stock.is_in_dip and not is_reversal:
                signals.append(IndiaSignal(
                    symbol=stock.symbol,
                    action="WATCH",
                    trigger="value_dip_pending",
                    quantity=0,
                    confidence=0.5,
                    thesis=f"Value candidate but no reversal yet: {candle_desc}. Wait for green candle.",
                    max_position_pct=pos_pct,
                    suggested_stop=round(stop, 2),
                    suggested_target=round(target, 2),
                ))

        return signals

    def rebalance_signals(self, holdings: Dict[str, int]) -> List[IndiaSignal]:
        """
        Check existing India positions for trim/add/exit.
        """
        signals = []
        for symbol, qty in holdings.items():
            stock = screen_stock(symbol)
            if not stock:
                continue

            current_value = qty * stock.price
            current_pct = (current_value / self.equity) * 100

            # Exit if fundamentals deteriorated
            if stock.value_score() < 4.0:
                signals.append(IndiaSignal(
                    symbol=symbol,
                    action="EXIT",
                    trigger="reduce",
                    quantity=qty,
                    confidence=0.7,
                    thesis=f"Fundamentals weakened: value_score={stock.value_score():.1f}",
                    max_position_pct=0,
                    suggested_stop=0,
                    suggested_target=0,
                ))
            # Trim if 52w high reached (take profit)
            elif stock.is_near_high:
                trim_qty = int(qty * 0.5)
                signals.append(IndiaSignal(
                    symbol=symbol,
                    action="TRIM",
                    trigger="target",
                    quantity=trim_qty,
                    confidence=0.6,
                    thesis=f"Near 52w high ({stock.distance_from_52w_low:.0%}), take partial profit",
                    max_position_pct=current_pct / 2,
                    suggested_stop=0,
                    suggested_target=0,
                ))
            # Add if still in dip but position too small
            elif stock.is_in_dip and current_pct < self.position_size_base:
                add_value = self.equity * (self.position_size_base / 100) - current_value
                add_qty = int(add_value / stock.price)
                if add_qty > 0:
                    signals.append(IndiaSignal(
                        symbol=symbol,
                        action="ADD",
                        trigger="value_dip",
                        quantity=add_qty,
                        confidence=0.6,
                        thesis=f"Still in dip, averaging down: value_score={stock.value_score():.1f}",
                        max_position_pct=self.position_size_base,
                        suggested_stop=stock.price * 0.92,
                        suggested_target=stock.fifty_two_week_high,
                    ))

        return signals
