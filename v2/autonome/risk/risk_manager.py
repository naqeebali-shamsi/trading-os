"""
autonome/risk/risk_manager.py  v2.2
Kelly sizing + drawdown circuit breakers + volatility halt + fractional shares + PDT guard.
Anti-greed. Anti-forgetting.
"""
from __future__ import annotations

import os, logging, statistics, math, json
from dataclasses import dataclass
from typing import Optional, Dict, List

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

        # mutable state
        self.daily_loss_accum = 0.0
        self.peak_equity = 0.0
        self.halted = False
        self._load_halt_state()

    # ── halt state persistence ───────────────────────────────────────────────
    def _halt_file(self) -> str:
        return os.path.join(os.path.dirname(__file__), "../../data/halted.json")

    def _load_halt_state(self):
        hf = self._halt_file()
        try:
            with open(hf) as f:
                data = json.load(f)
            self.halted = bool(data.get("halted", False))
            self.peak_equity = float(data.get("peak_equity", 0.0))
            if self.halted:
                log.warning("Loaded HALTED state from disk — manual resume required")
        except (OSError, json.JSONDecodeError):
            pass

    def _save_halt_state(self):
        hf = self._halt_file()
        os.makedirs(os.path.dirname(hf) or ".", exist_ok=True)
        with open(hf, "w") as f:
            json.dump({"halted": self.halted, "peak_equity": self.peak_equity}, f)

    def _clear_halt(self):
        self.halted = False
        try:
            os.remove(self._halt_file())
        except OSError:
            pass

    # ── daily / peak tracking ────────────────────────────────────────────────
    def reset_day(self, equity: float):
        self.daily_loss_accum = 0.0
        if equity > self.peak_equity:
            self.peak_equity = equity
        self._save_halt_state()

    def update_peak(self, equity: float):
        if equity > self.peak_equity:
            self.peak_equity = equity

    def current_drawdown(self, equity: float) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - equity) / self.peak_equity

    # ── volatility estimate ──────────────────────────────────────────────────
    @staticmethod
    def realized_vol_annual(closes: List[float]) -> float:
        """Annualized realized vol from closing prices."""
        if len(closes) < 2:
            return 0.0
        log_returns = []
        for i in range(1, len(closes)):
            if closes[i-1] > 0:
                log_returns.append(math.log(closes[i] / closes[i-1]))
        if len(log_returns) < 2:
            return 0.0
        mean_lr = sum(log_returns) / len(log_returns)
        variance = sum((lr - mean_lr) ** 2 for lr in log_returns) / (len(log_returns) - 1)
        std_daily = math.sqrt(variance)
        # Annualize (252 trading days)
        return std_daily * math.sqrt(252)

    # ── main evaluate ────────────────────────────────────────────────────────
    def evaluate(self, account: Account, positions: list[Position],
                 signal_confidence: float, entry_price: float,
                 stop_loss: float, target_price: float,
                 symbol: str, direction: str,
                 closes: Optional[List[float]] = None,
                 sector: Optional[str] = None,
                 vix: Optional[float] = None) -> RiskDecision:

        equity = account.equity
        self.update_peak(equity)

        # 1. halt gate
        if self.halted:
            return RiskDecision(False, 0.0, "global_halt_active")

        # 2. drawdown halt (persistent)
        dd = self.current_drawdown(equity)
        if dd >= self.max_drawdown_pct:
            self.halted = True
            self._save_halt_state()
            return RiskDecision(False, 0.0, f"drawdown_limit_{dd:.2%}")

        # 3. daily loss halt
        if self.daily_loss_accum >= equity * self.max_daily_loss_pct:
            self.halted = True
            self._save_halt_state()
            return RiskDecision(False, 0.0, "daily_loss_limit")

        # 4. volatility halt
        if closes and len(closes) >= 20:
            vol = self.realized_vol_annual(closes)
            if vol >= self.vol_pause_annual:
                return RiskDecision(False, 0.0, f"volatility_halt_{vol:.1%}")

        # 4b. VIX-based volatility rules
        if vix is not None:
            if vix >= 40.0:
                return RiskDecision(False, 0.0, f"vix_extreme_{vix:.1f}")

        # 5. max positions
        if len(positions) >= self.max_positions:
            return RiskDecision(False, 0.0, "max_positions")

        # 6. already in symbol
        for p in positions:
            if p.symbol == symbol:
                return RiskDecision(False, 0.0, "already_in_symbol")

        # 7. PDT guard (if daytrade_count present)
        if hasattr(account, 'daytrade_count') and account.daytrade_count >= 3:
            return RiskDecision(False, 0.0, "pdt_limit")

        # 8. expected value + directional sanity
        if signal_confidence <= 0.0:
            return RiskDecision(False, 0.0, "zero_confidence")

        if direction == "LONG":
            if target_price <= entry_price:
                return RiskDecision(False, 0.0, "long_target_not_above_entry")
            if stop_loss >= entry_price:
                return RiskDecision(False, 0.0, "long_stop_not_below_entry")
        elif direction == "SHORT":
            if target_price >= entry_price:
                return RiskDecision(False, 0.0, "short_target_not_below_entry")
            if stop_loss <= entry_price:
                return RiskDecision(False, 0.0, "short_stop_not_above_entry")
        else:
            return RiskDecision(False, 0.0, f"unknown_direction_{direction}")

        # 9. Slippage-adjusted risk
        risk = abs(entry_price - stop_loss)
        slippage_buffer = risk * 0.10  # assume 10% extra slippage beyond stop
        adjusted_risk = risk + slippage_buffer

        # 10. Kelly sizing
        win_rate = signal_confidence
        reward = abs(target_price - entry_price)
        if adjusted_risk <= 0:
            return RiskDecision(False, 0.0, "invalid_stop")
        payoff = reward / adjusted_risk
        kelly = win_rate - ((1 - win_rate) / payoff)
        if kelly <= 0:
            return RiskDecision(False, 0.0, "negative_kelly")
        frac = kelly * self.kelly_frac

        # dollar risk per trade
        dollar_risk = equity * self.acc_risk_pct
        shares = dollar_risk / adjusted_risk
        if shares < 0.001:
            return RiskDecision(False, 0.0, "risk_too_small_for_min_qty")

        # apply Kelly fractional sizing — keep fractional (Alpaca supports it)
        shares = shares * frac

        # guard: min meaningful position
        if shares * entry_price < 1.0:
            return RiskDecision(False, 0.0, "min_notional_1usd")

        notional = shares * entry_price

        # 10.5 short margin requirement (1.5x buying power for shorts)
        if direction == "SHORT":
            if account.buying_power < notional * 1.5:
                return RiskDecision(False, 0.0, "short_margin_requirement_not_met")

        # 11. buying power guard
        if notional > account.buying_power * 0.95:
            shares = (account.buying_power * 0.95) / entry_price
            if shares * entry_price < 1.0:
                return RiskDecision(False, 0.0, "insufficient_buying_power")

        # VIX high: reduce approved size by 50%
        if vix is not None and vix >= 30.0:
            shares = shares * 0.5
            if shares * entry_price < 1.0:
                return RiskDecision(False, 0.0, "vix_high_reduced_below_min_notional")
            log.info("VIX high (%.1f) — position size halved to %.4f", vix, shares)

        return RiskDecision(True, round(shares, 6), "approved")

    def record_loss(self, loss: float):
        self.daily_loss_accum += loss
        log.info("Daily loss now %.2f", self.daily_loss_accum)

    def record_win(self, win: float):
        pass
