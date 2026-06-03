"""
autonome/backtest/engine.py  v1.0
Event-driven backtest engine for momentum breakout strategy.
Simulates fill at next-bar open with slippage + commission.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Callable

from autonome.data.bars import Bar
from autonome.strategy.momentum_breakout import MomentumBreakout, Signal
from autonome.risk.risk_manager import RiskManager, RiskDecision

log = logging.getLogger("backtest")


@dataclass
class TradeResult:
    """Result of a single simulated trade."""
    symbol: str
    direction: str
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    qty: float
    pnl: float          # realized P&L
    pnl_pct: float      # P&L as % of entry notional
    exit_reason: str    # "stop", "target", "end_of_data", "strategy_exit"
    max_drawdown: float # max adverse excursion as %
    bars_held: int


@dataclass
class BacktestResult:
    """Complete backtest output."""
    trades: List[TradeResult] = field(default_factory=list)
    equity_curve: List[tuple[datetime, float]] = field(default_factory=list)
    signals_generated: int = 0
    signals_rejected: int = 0
    signals_risk_rejected: int = 0
    initial_equity: float = 100000.0
    commission_rate: float = 0.0
    slippage: float = 0.0


def _ema(values: List[float], period: int) -> List[float]:
    """Simple EMA series."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    ema = [sum(values[:period]) / period]
    for v in values[period:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


class BacktestEngine:
    """
    Walk-forward backtest engine.

    Parameters
    ----------
    strategy : MomentumBreakout
        Strategy instance to backtest.
    risk_manager : RiskManager
        Risk manager for sizing decisions.
    initial_equity : float
        Starting capital.
    commission_rate : float
        Commission as fraction of notional (e.g. 0.0005 for 0.05%).
    slippage : float
        Slippage per side as fraction of price (e.g. 0.0005 for 5bps).
    entry_at : str
        "next_open" or "limit".  How entry fills are modelled.
    """

    def __init__(
        self,
        strategy: MomentumBreakout,
        risk_manager: RiskManager,
        initial_equity: float = 100000.0,
        commission_rate: float = 0.0005,
        slippage: float = 0.0005,
        entry_at: str = "next_open",
    ):
        self.strategy = strategy
        self.risk = risk_manager
        self.initial_equity = initial_equity
        self.commission_rate = commission_rate
        self.slippage = slippage
        self.entry_at = entry_at

    def _slipped_price(self, price: float, direction: str, is_entry: bool) -> float:
        """Apply slippage against the trader."""
        slip = price * self.slippage
        if direction == "LONG":
            return price + slip if is_entry else price - slip
        else:  # SHORT
            return price - slip if is_entry else price + slip

    def run(self, bars: List[Bar]) -> BacktestResult:
        """
        Run backtest on a chronological list of bars for a single symbol.
        """
        if not bars:
            return BacktestResult(initial_equity=self.initial_equity)

        result = BacktestResult(
            initial_equity=self.initial_equity,
            commission_rate=self.commission_rate,
            slippage=self.slippage,
        )

        equity = self.initial_equity
        result.equity_curve.append((bars[0].t, equity))

        # Track open position
        open_trade: Optional[dict] = None  # {signal, entry_price, qty, entry_time, bars_held, max_price, min_price}

        for i in range(len(bars)):
            bar = bars[i]

            # Check exit for open position
            if open_trade is not None:
                ot = open_trade
                ot["bars_held"] += 1
                ot["max_price"] = max(ot["max_price"], bar.high)
                ot["min_price"] = min(ot["min_price"], bar.low)

                direction = ot["signal"].direction
                sl = ot["signal"].stop_loss
                tp = ot["signal"].take_profit

                exited = False
                exit_price = None
                exit_reason = None

                if direction == "LONG":
                    # Stop hit first (intrabar: assume worst case = low)
                    if bar.low <= sl:
                        exited = True
                        exit_price = min(bar.open, sl)  # worst of open or stop
                        exit_reason = "stop"
                    elif bar.high >= tp:
                        exited = True
                        exit_price = max(bar.open, tp)
                        exit_reason = "target"
                else:  # SHORT
                    if bar.high >= sl:
                        exited = True
                        exit_price = max(bar.open, sl)
                        exit_reason = "stop"
                    elif bar.low <= tp:
                        exited = True
                        exit_price = min(bar.open, tp)
                        exit_reason = "target"

                if exited:
                    exit_price = self._slipped_price(exit_price, direction, is_entry=False)
                    entry_slip = self._slipped_price(ot["entry_price"], direction, is_entry=True)

                    if direction == "LONG":
                        raw_pnl = (exit_price - entry_slip) * ot["qty"]
                    else:
                        raw_pnl = (entry_slip - exit_price) * ot["qty"]

                    notional = entry_slip * ot["qty"]
                    commission = notional * self.commission_rate * 2  # in + out
                    pnl = raw_pnl - commission
                    pnl_pct = pnl / notional if notional > 0 else 0.0

                    # Max drawdown
                    if direction == "LONG":
                        mae = (entry_slip - ot["min_price"]) / entry_slip
                    else:
                        mae = (ot["max_price"] - entry_slip) / entry_slip

                    result.trades.append(TradeResult(
                        symbol=bar.symbol,
                        direction=direction,
                        entry_time=ot["entry_time"],
                        exit_time=bar.t,
                        entry_price=entry_slip,
                        exit_price=exit_price,
                        qty=ot["qty"],
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        exit_reason=exit_reason,
                        max_drawdown=mae,
                        bars_held=ot["bars_held"],
                    ))

                    equity += pnl
                    result.equity_curve.append((bar.t, equity))
                    open_trade = None
                    continue  # skip signal generation on exit bar

            # No open position — generate signals
            if open_trade is not None:
                continue

            # Need enough bars for strategy
            if i < 30:
                continue

            # Build BarStore from history up to this bar
            hist = bars[:i+1]
            # Use a temporary store without DB (we pass db_path to avoid locks)
            from autonome.data.bars import BarStore
            store = BarStore([bar.symbol], maxlen=200, db_path=None)
            for b in hist:
                store.buffers[bar.symbol].append(b)

            sig = self.strategy.scan(bar.symbol, store, global_bar_idx=i)
            result.signals_generated += 1

            if sig is None:
                continue

            # Risk evaluation (mock account)
            from types import SimpleNamespace
            mock_acc = SimpleNamespace(
                equity=equity,
                buying_power=equity,
                cash=equity,
                daytrade_count=0,
                status="ACTIVE",
            )

            class MockPosition:
                def __init__(self, s, q, e):
                    self.symbol = s
                    self.qty = q
                    self.avg_entry_price = e
                    self.current_price = e
                    self.unrealized_pl = 0.0
                    self.unrealized_plpc = 0.0

            positions = []
            rd = self.risk.evaluate(
                mock_acc, positions, sig.confidence,
                sig.entry_price, sig.stop_loss, sig.take_profit,
                bar.symbol, sig.direction,
            )

            if not rd.approved:
                result.signals_risk_rejected += 1
                continue

            # Entry fill
            if self.entry_at == "next_open":
                if i + 1 >= len(bars):
                    # No next bar — skip
                    continue
                fill_price = self._slipped_price(bars[i+1].open, sig.direction, is_entry=True)
                fill_time = bars[i+1].t
            else:
                fill_price = self._slipped_price(bar.close, sig.direction, is_entry=True)
                fill_time = bar.t

            open_trade = {
                "signal": sig,
                "entry_price": fill_price,
                "qty": rd.qty,
                "entry_time": fill_time,
                "bars_held": 0,
                "max_price": fill_price,
                "min_price": fill_price,
            }

        # Close any open position at last bar
        if open_trade is not None:
            ot = open_trade
            last_bar = bars[-1]
            direction = ot["signal"].direction
            exit_price = self._slipped_price(last_bar.close, direction, is_entry=False)
            entry_slip = self._slipped_price(ot["entry_price"], direction, is_entry=True)

            if direction == "LONG":
                raw_pnl = (exit_price - entry_slip) * ot["qty"]
            else:
                raw_pnl = (entry_slip - exit_price) * ot["qty"]

            notional = entry_slip * ot["qty"]
            commission = notional * self.commission_rate * 2
            pnl = raw_pnl - commission
            pnl_pct = pnl / notional if notional > 0 else 0.0

            if direction == "LONG":
                mae = (entry_slip - ot["min_price"]) / entry_slip
            else:
                mae = (ot["max_price"] - entry_slip) / entry_slip

            result.trades.append(TradeResult(
                symbol=last_bar.symbol,
                direction=direction,
                entry_time=ot["entry_time"],
                exit_time=last_bar.t,
                entry_price=entry_slip,
                exit_price=exit_price,
                qty=ot["qty"],
                pnl=pnl,
                pnl_pct=pnl_pct,
                exit_reason="end_of_data",
                max_drawdown=mae,
                bars_held=ot["bars_held"],
            ))
            equity += pnl
            result.equity_curve.append((last_bar.t, equity))

        return result
