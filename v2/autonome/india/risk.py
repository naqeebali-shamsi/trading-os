"""
autonome/india/risk.py  v1.0
India-specific risk management.

India market characteristics vs US:
- ~2x volatility (25-30% vs 15-20% annualized)
- Currency risk (INR depreciation)
- Political/execution risk (policy reversal, corruption)
- Lower liquidity in mid-caps
- Higher retail participation = more irrational moves
- Circuit breakers (10/15/20% daily limits)
- STCG tax (15% if held < 1yr), LTCG (10% if > 1 lakh gains)

Adapted rules:
- Position size: max 2.5% per stock (vs 2% US)
- Stop loss: wider (8-12% vs 5% US)
- Heat map: max 15% total portfolio (vs 5% US)
- Cash buffer: 20% minimum for averaging on dips
- Sector concentration: max 20% per sector
- Re-evaluate fundamentals monthly (not daily)
- Tax-aware: prefer > 1yr holds where possible
"""
from __future__ import annotations

import logging
from typing import Dict, Optional
from dataclasses import dataclass

log = logging.getLogger("india.risk")

# India heat limits
INDIA_MAX_HEAT = 15.0  # % of equity at risk
INDIA_MAX_POSITION = 2.5  # % per stock
INDIA_MAX_SECTOR = 20.0  # % per sector
INDIA_CASH_BUFFER_MIN = 20.0  # % cash always available
INDIA_MAX_POSITIONS = 8  # Active positions
INDIA_STOP_PCT = 0.10  # 10% default stop for India


@dataclass
class IndiaRiskResult:
    approved: bool
    size_shares: int
    size_value_inr: float
    stop_loss: float
    max_risk_inr: float
    reason: str
    regime: str  # AGGRESSIVE | BALANCED | CAUTIOUS | DEFENSE


class IndiaRiskManager:
    """Risk management adapted for Indian market conditions."""

    def __init__(self, equity_inr: float):
        self.equity = equity_inr
        self.heat = 0.0
        self.positions: Dict[str, float] = {}  # symbol -> value
        self.sector_exposure: Dict[str, float] = {}  # sector -> value

    def evaluate(
        self,
        symbol: str,
        price: float,
        sector: str,
        confidence: float,
        regime: str = "BALANCED",
        atr_pct: float = 2.5,
    ) -> IndiaRiskResult:
        """
        Evaluate a potential India position.
        """
        # Regime adjustment
        multiplier = 1.0
        if regime == "AGGRESSIVE":
            multiplier = 1.3
        elif regime == "CAUTIOUS":
            multiplier = 0.6
        elif regime == "DEFENSE":
            multiplier = 0.3

        base_pct = INDIA_MAX_POSITION * multiplier * confidence
        base_pct = min(base_pct, INDIA_MAX_POSITION)

        # Volatility adjustment (smaller for volatile)
        vol_factor = max(0.4, 1.0 - (atr_pct / 6.0))
        position_pct = base_pct * vol_factor

        # Check sector concentration
        sector_pct = self.sector_exposure.get(sector, 0) / self.equity * 100
        if sector_pct + position_pct > INDIA_MAX_SECTOR:
            max_add = INDIA_MAX_SECTOR - sector_pct
            if max_add < 0.5:
                return IndiaRiskResult(
                    approved=False,
                    size_shares=0, size_value_inr=0,
                    stop_loss=0, max_risk_inr=0,
                    reason=f"Sector {sector} at {sector_pct:.1f}% limit",
                    regime=regime,
                )
            position_pct = min(position_pct, max_add)

        # Check cash buffer
        invested = sum(self.positions.values())
        cash_pct = ((self.equity - invested) / self.equity) * 100 if self.equity else 100
        if cash_pct - (position_pct) < INDIA_CASH_BUFFER_MIN:
            position_pct = cash_pct - INDIA_CASH_BUFFER_MIN
            if position_pct < 0.5:
                return IndiaRiskResult(
                    approved=False,
                    size_shares=0, size_value_inr=0,
                    stop_loss=0, max_risk_inr=0,
                    reason=f"Cash buffer at limit ({cash_pct:.1f}%)",
                    regime=regime,
                )

        # Position size
        value = self.equity * (position_pct / 100)
        shares = int(value / price)

        if shares < 1:
            return IndiaRiskResult(
                approved=False,
                size_shares=0, size_value_inr=0,
                stop_loss=0, max_risk_inr=0,
                reason="Position too small (less than 1 share)",
                regime=regime,
            )

        # Stop loss based on volatility
        stop_pct = min(INDIA_STOP_PCT, max(0.06, atr_pct * 2.5 / 100))
        stop = price * (1 - stop_pct)

        # Max risk
        risk_per_share = price - stop
        max_risk = shares * risk_per_share

        # Update tracking
        self.heat += (max_risk / self.equity) * 100
        self.positions[symbol] = self.positions.get(symbol, 0) + value
        self.sector_exposure[sector] = self.sector_exposure.get(sector, 0) + value

        return IndiaRiskResult(
            approved=True,
            size_shares=shares,
            size_value_inr=round(value, 2),
            stop_loss=round(stop, 2),
            max_risk_inr=round(max_risk, 2),
            reason=f"Approved: {position_pct:.2f}% position, regime={regime}",
            regime=regime,
        )

    def exit_position(self, symbol: str, sector: str, exit_price: float, entry_price: float):
        """Update tracking on exit."""
        if symbol in self.positions:
            old_value = self.positions.pop(symbol)
            risk = abs(exit_price - entry_price) * (old_value / entry_price)
            self.heat = max(0, self.heat - (risk / self.equity) * 100)
        if sector in self.sector_exposure:
            self.sector_exposure[sector] = max(0, self.sector_exposure[sector] - old_value)
