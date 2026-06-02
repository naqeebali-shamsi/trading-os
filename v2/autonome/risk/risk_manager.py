"""
autonome/risk/risk_manager.py  v2.0
Kelly sizing + drawdown circuit breakers.  No voodoo.
"""
from __future__ import annotations

import os, logging, statistics
from dataclasses import dataclass
from typing import Optional

import yaml

from autonome.broker.alpaca_client import Account, Position

log = logging.getLogger("risk")


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    qty: float
    reason: str


class RiskManager:
    def __init__(self):
        cfg_path = os.path.join(os.path.dirname(__file__), "../../config/settings.yaml")
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        rc = cfg["risk"]
        self.acc_risk_pct = rc["account_risk_per_trade_pct"] / 100.0
        self.max_daily_loss_pct = rc["max_daily_loss_pct"] / 100.0
        self.max_drawdown_pct = rc["max_drawdown_pct"] / 100.0
        self.kelly_frac = rc["kelly_fraction"]
        self.vol_pause_annual = rc["volatility_pause_annual_pct"] / 100.0
        self.max_positions = cfg["system"]["max_concurrent_positions"]

        # mutable state (kept simple; persisted via journal)
        self.daily_loss_accum = 0.0      # reset at new day externally
        self.peak_equity = 0.0
        self.halted = False

    def reset_day(self, equity: float):
        self.daily_loss_accum = 0.0
        if equity > self.peak_equity:
            self.peak_equity = equity

    def update_peak(self, equity: float):
        if equity > self.peak_equity:
            self.peak_equity = equity

    def current_drawdown(self, equity: float) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - equity) / self.peak_equity

    def evaluate(self, account: Account, positions: list[Position],
                 signal_confidence: float, entry_price: float,
                 stop_loss: float, target_price: float,
                 symbol: str) -> RiskDecision:

        equity = account.equity
        self.update_peak(equity)

        # 1. halt gate
        if self.halted:
            return RiskDecision(False, 0.0, "global_halt_active")

        # 2. drawdown halt
        dd = self.current_drawdown(equity)
        if dd >= self.max_drawdown_pct:
            self.halted = True
            return RiskDecision(False, 0.0, f"drawdown_limit_{dd:.2%}")

        # 3. daily loss halt
        if self.daily_loss_accum >= equity * self.max_daily_loss_pct:
            return RiskDecision(False, 0.0, "daily_loss_limit")

        # 4. max positions
        if len(positions) >= self.max_positions:
            return RiskDecision(False, 0.0, "max_positions")

        # 5. already in symbol
        for p in positions:
            if p.symbol == symbol:
                return RiskDecision(False, 0.0, "already_in_symbol")

        # 6. expected value sanity
        if signal_confidence <= 0.0:
            return RiskDecision(False, 0.0, "zero_confidence")

        # 7. Kelly sizing
        # win rate estimated from confidence; payoff = R:R
        win_rate = signal_confidence
        risk = abs(entry_price - stop_loss)
        reward = abs(target_price - entry_price)
        if risk <= 0:
            return RiskDecision(False, 0.0, "invalid_stop")
        payoff = reward / risk
        kelly = win_rate - ((1 - win_rate) / payoff)
        if kelly <= 0:
            return RiskDecision(False, 0.0, "negative_kelly")
        frac = kelly * self.kelly_frac

        # dollar risk per trade
        dollar_risk = equity * self.acc_risk_pct
        shares = int(dollar_risk / risk)
        if shares < 1:
            return RiskDecision(False, 0.0, "risk_too_small_for_one_share")

        # apply Kelly fractional to shares
        shares = int(shares * frac)
        if shares < 1:
            shares = 1

        # 8. buying power guard
        notional = shares * entry_price
        if notional > account.buying_power * 0.95:
            shares = int((account.buying_power * 0.95) // entry_price)
            if shares < 1:
                return RiskDecision(False, 0.0, "insufficient_buying_power")

        return RiskDecision(True, float(shares), "approved")

    def record_loss(self, loss: float):
        self.daily_loss_accum += loss
        log.info("Daily loss now %.2f", self.daily_loss_accum)

    def record_win(self, win: float):
        # wins don't reduce daily loss accumulator; only losses matter for halt
        pass
