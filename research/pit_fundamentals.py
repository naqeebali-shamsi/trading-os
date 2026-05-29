"""Point-in-time fundamentals from quarterly financial statements.

Uses yfinance quarterly income/balance sheets with a reporting lag so walk-forward
validation does not peek at filings that would not yet be public at rebalance date.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

from research.stock_fundamentals import _safe_float, resolve_yfinance_ticker

DEFAULT_REPORTING_LAG_DAYS = 45

REVENUE_ROWS = ("Total Revenue", "Operating Revenue", "Revenue")
NET_INCOME_ROWS = ("Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operation Net Minority Interest")
GROSS_PROFIT_ROWS = ("Gross Profit",)
EQUITY_ROWS = (
    "Stockholders Equity",
    "Total Stockholder Equity",
    "Common Stock Equity",
    "Total Equity Gross Minority Interest",
)
DEBT_ROWS = ("Total Debt", "Long Term Debt And Capital Lease Obligation", "Long Term Debt")


@dataclass
class QuarterlySeries:
    """Newest quarter first."""

    periods: List[date]
    revenue: List[Optional[float]]
    net_income: List[Optional[float]]
    gross_profit: List[Optional[float]]
    equity: List[Optional[float]]
    debt: List[Optional[float]]


def _period_from_col(col: Any) -> Optional[date]:
    try:
        if hasattr(col, "date"):
            return col.date()
        if isinstance(col, date):
            return col
        ts = getattr(col, "to_pydatetime", None)
        if callable(ts):
            return ts().date()
    except Exception:
        return None
    return None


def _row_value(df: Any, row_names: Sequence[str], col: Any) -> Optional[float]:
    if df is None or getattr(df, "empty", True):
        return None
    index = getattr(df, "index", None)
    if index is None:
        return None
    for name in row_names:
        if name in index:
            try:
                return _safe_float(df.loc[name, col])
            except Exception:
                continue
    return None


def dataframe_to_quarterly_series(income_stmt: Any, balance_sheet: Any) -> QuarterlySeries:
    periods: List[date] = []
    if income_stmt is not None and not getattr(income_stmt, "empty", True):
        for col in income_stmt.columns:
            ped = _period_from_col(col)
            if ped is not None:
                periods.append(ped)
    periods = sorted(set(periods), reverse=True)

    revenue: List[Optional[float]] = []
    net_income: List[Optional[float]] = []
    gross_profit: List[Optional[float]] = []
    equity: List[Optional[float]] = []
    debt: List[Optional[float]] = []

    for ped in periods:
        col = None
        if income_stmt is not None and not getattr(income_stmt, "empty", True):
            for c in income_stmt.columns:
                if _period_from_col(c) == ped:
                    col = c
                    break
        bal_col = None
        if balance_sheet is not None and not getattr(balance_sheet, "empty", True):
            for c in balance_sheet.columns:
                if _period_from_col(c) == ped:
                    bal_col = c
                    break
        revenue.append(_row_value(income_stmt, REVENUE_ROWS, col) if col is not None else None)
        net_income.append(_row_value(income_stmt, NET_INCOME_ROWS, col) if col is not None else None)
        gross_profit.append(_row_value(income_stmt, GROSS_PROFIT_ROWS, col) if col is not None else None)
        equity.append(_row_value(balance_sheet, EQUITY_ROWS, bal_col) if bal_col is not None else None)
        debt.append(_row_value(balance_sheet, DEBT_ROWS, bal_col) if bal_col is not None else None)

    return QuarterlySeries(
        periods=periods,
        revenue=revenue,
        net_income=net_income,
        gross_profit=gross_profit,
        equity=equity,
        debt=debt,
    )


def _available_indices(series: QuarterlySeries, as_of: date, reporting_lag_days: int) -> List[int]:
    cutoff = as_of - timedelta(days=reporting_lag_days)
    out: List[int] = []
    for i, ped in enumerate(series.periods):
        if ped <= cutoff:
            out.append(i)
    return out


def _yoy_growth(values: Sequence[Optional[float]], idx: int, lag_quarters: int = 4) -> Optional[float]:
    if idx + lag_quarters >= len(values):
        return None
    cur = values[idx]
    prior = values[idx + lag_quarters]
    if cur is None or prior is None or prior == 0:
        return None
    return (float(cur) / float(prior)) - 1.0


def fundamentals_from_quarterly_series(
    series: QuarterlySeries,
    as_of: date,
    *,
    symbol: str = "",
    yfinance_ticker: str = "",
    reporting_lag_days: int = DEFAULT_REPORTING_LAG_DAYS,
    momentum_12_1: Optional[float] = None,
    price: Optional[float] = None,
    market_cap: Optional[float] = None,
) -> Dict[str, Any]:
    """Build normalized fundamental dict as-of rebalance date (no look-ahead)."""
    indices = _available_indices(series, as_of, reporting_lag_days)
    base: Dict[str, Any] = {
        "symbol": symbol,
        "yfinance_ticker": yfinance_ticker,
        "as_of": as_of.isoformat(),
        "reporting_lag_days": reporting_lag_days,
        "source": "yfinance_quarterly_pit",
        "ok": False,
    }
    if not indices:
        base["error"] = "no_quarters_before_cutoff"
        return base

    idx = indices[0]
    rev = series.revenue[idx]
    ni = series.net_income[idx]
    gp = series.gross_profit[idx]
    eq = series.equity[idx]
    debt = series.debt[idx]

    revenue_growth = _yoy_growth(series.revenue, idx)
    earnings_growth = _yoy_growth(series.net_income, idx)

    gross_margin = None
    if gp is not None and rev is not None and rev > 0:
        gross_margin = gp / rev
    profit_margin = None
    if ni is not None and rev is not None and rev > 0:
        profit_margin = ni / rev

    roe = None
    if ni is not None and eq is not None and eq > 0:
        roe = (ni * 4.0) / eq

    debt_to_equity = None
    if debt is not None and eq is not None and eq > 0:
        debt_to_equity = (debt / eq) * 100.0

    fields = {
        "price": price,
        "market_cap": market_cap,
        "revenue_growth": revenue_growth,
        "earnings_growth": earnings_growth,
        "profit_margin": profit_margin,
        "gross_margin": gross_margin,
        "roe": roe,
        "debt_to_equity": debt_to_equity,
        "momentum_12_1": momentum_12_1,
        "pe": None,
        "peg": None,
        "fcf_yield": None,
        "payout_ratio": None,
        "total_revenue": rev,
        "latest_quarter_end": series.periods[idx].isoformat(),
    }
    present = sum(1 for v in fields.values() if v is not None)
    base.update(fields)
    base.update(
        {
            "ok": present >= 4 and (revenue_growth is not None or momentum_12_1 is not None),
            "data_completeness": round(present / max(len(fields), 1), 3),
        }
    )
    return base


class QuarterlyFinancialCache:
    """Load and cache quarterly statements per yfinance ticker."""

    def __init__(self) -> None:
        self._series: Dict[str, QuarterlySeries] = {}
        self._errors: Dict[str, str] = {}

    def load(self, yfinance_ticker: str) -> Optional[QuarterlySeries]:
        key = str(yfinance_ticker or "").upper()
        if key in self._series:
            return self._series[key]
        if key in self._errors:
            return None
        if yf is None:
            self._errors[key] = "yfinance_not_installed"
            return None
        try:
            ticker = yf.Ticker(key)
            income = ticker.quarterly_income_stmt
            balance = ticker.quarterly_balance_sheet
            series = dataframe_to_quarterly_series(income, balance)
            if not series.periods:
                self._errors[key] = "empty_quarterly_statements"
                return None
            self._series[key] = series
            time.sleep(0.12)
            return series
        except Exception as exc:
            self._errors[key] = str(exc)
            return None

    def pit_snapshot(
        self,
        symbol: str,
        *,
        as_of: date,
        meta: Optional[Mapping[str, Any]] = None,
        reporting_lag_days: int = DEFAULT_REPORTING_LAG_DAYS,
        momentum_12_1: Optional[float] = None,
        price: Optional[float] = None,
        market_cap: Optional[float] = None,
    ) -> Dict[str, Any]:
        meta = meta or {}
        yf_ticker = str(meta.get("yfinance_ticker") or resolve_yfinance_ticker(
            symbol,
            region=str(meta.get("region") or "US"),
            exchange=str(meta.get("exchange") or ""),
        ))
        if symbol == "BRK.B":
            yf_ticker = "BRK-B"
        series = self.load(yf_ticker)
        if series is None:
            return {
                "symbol": symbol,
                "yfinance_ticker": yf_ticker,
                "as_of": as_of.isoformat(),
                "ok": False,
                "source": "yfinance_quarterly_pit",
                "error": self._errors.get(yf_ticker.upper(), "load_failed"),
            }
        return fundamentals_from_quarterly_series(
            series,
            as_of,
            symbol=symbol,
            yfinance_ticker=yf_ticker,
            reporting_lag_days=reporting_lag_days,
            momentum_12_1=momentum_12_1,
            price=price,
            market_cap=market_cap,
        )


def price_on_date(closes: Mapping[str, float], as_of: date) -> Optional[float]:
    """Last available monthly close on or before as_of."""
    eligible = [(d, v) for d, v in closes.items() if d <= as_of.isoformat()]
    if not eligible:
        return None
    eligible.sort(key=lambda x: x[0])
    return float(eligible[-1][1])
