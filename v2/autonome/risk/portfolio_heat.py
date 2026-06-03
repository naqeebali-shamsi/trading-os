"""
autonome/risk/portfolio_heat.py  v2.0
Tracks total portfolio heat (sum of individual position risks).
Prevents over-concentration. Supports conviction-weighted sizing.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from autonome.broker.alpaca_client import Position

log = logging.getLogger("risk.heat")


class PortfolioHeat:
    """
    Total heat = sum of (position_notional * position_risk_pct) for all open positions.
    Each position's risk_pct = distance to stop / entry_price.
    """

    def __init__(self, max_heat_pct: float = 5.0, max_heat_per_sector_pct: float = 3.0):
        self.max_heat_pct = max_heat_pct / 100.0
        self.max_heat_per_sector_pct = max_heat_per_sector_pct / 100.0
        # symbol -> heat data (from journal or broker)
        self._position_heat: dict[str, dict] = {}

    def register_position(self, symbol: str, entry_price: float, stop_loss: float,
                          qty: float, sector: Optional[str] = None,
                          conviction: Optional[float] = None):
        """Register a new position's risk parameters."""
        risk = abs(entry_price - stop_loss)
        risk_pct = risk / entry_price if entry_price > 0 else 0.0
        notional = qty * entry_price
        heat = notional * risk_pct
        self._position_heat[symbol] = {
            "entry": entry_price,
            "stop": stop_loss,
            "qty": qty,
            "sector": sector,
            "conviction": conviction or 0.5,
            "risk_pct": risk_pct,
            "notional": notional,
            "heat": heat,
        }

    def remove_position(self, symbol: str):
        self._position_heat.pop(symbol, None)

    def total_heat(self, equity: float) -> float:
        """Total portfolio heat as fraction of equity."""
        if equity <= 0:
            return 0.0
        total = sum(p["heat"] for p in self._position_heat.values())
        return total / equity

    def sector_heat(self, sector: str, equity: float) -> float:
        """Heat for a specific sector as fraction of equity."""
        if equity <= 0:
            return 0.0
        total = sum(p["heat"] for p in self._position_heat.values() if p.get("sector") == sector)
        return total / equity

    def remaining_heat(self, equity: float) -> float:
        """How much heat capacity remains (in $)."""
        used = sum(p["heat"] for p in self._position_heat.values())
        max_heat_dollar = equity * self.max_heat_pct
        return max(0.0, max_heat_dollar - used)

    def can_add_position(self, proposed_heat: float, equity: float,
                         sector: Optional[str] = None) -> tuple[bool, str]:
        """
        Check if adding proposed_heat would exceed limits.
        Returns (allowed, reason).
        """
        current_total = self.total_heat(equity)
        total_after = current_total + (proposed_heat / equity if equity > 0 else 0)

        if total_after > self.max_heat_pct:
            return False, (
                f"total_heat_limit: {total_after:.2%} > max {self.max_heat_pct:.2%} "
                f"(current={current_total:.2%}, proposed={proposed_heat/equity:.2%})"
            )

        if sector:
            current_sector = self.sector_heat(sector, equity)
            sector_after = current_sector + (proposed_heat / equity if equity > 0 else 0)
            if sector_after > self.max_heat_per_sector_pct:
                return False, (
                    f"sector_heat_limit: {sector_after:.2%} > max {self.max_heat_per_sector_pct:.2%} "
                    f"for {sector}"
                )

        return True, ""

    def unregister(self, symbol: str):
        """Remove a symbol from heat tracking (e.g., on failed fill)."""
        if symbol in self.positions:
            del self.positions[symbol]

    def conviction_weight(self, symbol: str, base_size: float) -> float:
        """Scale position by conviction relative to portfolio average."""
        if not self._position_heat:
            return base_size
        avg_conviction = sum(p["conviction"] for p in self._position_heat.values()) / len(self._position_heat)
        my_conviction = self._position_heat.get(symbol, {}).get("conviction", avg_conviction)
        if avg_conviction <= 0:
            return base_size
        ratio = my_conviction / avg_conviction
        # Cap at 2x and floor at 0.5x
        ratio = max(0.5, min(2.0, ratio))
        return base_size * ratio

    def summary(self, equity: float) -> dict:
        return {
            "total_heat_pct": self.total_heat(equity),
            "max_heat_pct": self.max_heat_pct,
            "positions_tracked": len(self._position_heat),
            "remaining_heat_usd": self.remaining_heat(equity),
            "sectors": {
                sector: self.sector_heat(sector, equity)
                for sector in set(p.get("sector") for p in self._position_heat.values() if p.get("sector"))
            }
        }
