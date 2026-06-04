"""
autonome/india/fundamentals.py  v2.0
Indian stock fundamental screener using yfinance.

Core thesis for India manufacturing boom:
- Buy the dip on fundamentally strong stocks
- Low debt (India leverage kills during shocks)
- Manufacturing + consumption beneficiaries
- Long position sizes, long hold times (buy-and-monitor)

Buy-the-dip criteria:
- P/E < 25 (vs India avg 25-30)
- ROE > 12%
- Debt/Equity < 0.5
- P/B reasonable (< sector norm)
- Market cap > 5000 Cr
- Consistent growth
"""
from __future__ import annotations

import json, logging, os
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import yfinance as yf

log = logging.getLogger("india.fundamentals")

# Mega-trend beneficiaries — manufacturing + consumption India
INDIA_UNIVERSE = {
    "large_cap": [
        "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
        "HINDUNILVR.NS", "ITC.NS", "KOTAKBANK.NS", "AXISBANK.NS", "LT.NS",
        "SBIN.NS", "BHARTIARTL.NS", "BAJFINANCE.NS", "HCLTECH.NS", "WIPRO.NS",
        "ASIANPAINT.NS", "MARUTI.NS", "TATAMOTORS.NS", "SUNPHARMA.NS", "TITAN.NS",
        "ULTRACEMCO.NS", "NESTLEIND.NS", "POWERGRID.NS", "NTPC.NS", "ADANIENT.NS",
    ],
    "manufacturing_pli": [
        "SIEMENS.NS", "ABB.NS", "LARSEN.NS", "THERMAX.NS", "CUMMINSIND.NS",
        "HAVELLS.NS", "CGPOWER.NS", "SCHAEFFLER.NS", "SKFINDIA.NS", "TIMKEN.NS",
        "BHEL.NS", "KIRLOSENG.NS", "GRASIM.NS", "ULTRACEMCO.NS", "DALBHAR.NS",
    ],
    "auto_ev": [
        "M&M.NS", "EICHERMOT.NS", "BAJAJ-AUTO.NS", "HEROMOTOCO.NS", "ASHOKLEY.NS",
        "TVSMOTOR.NS", "MOTHERSON.NS", "BOSCHLTD.NS", "BHARATFORG.NS", "APOLLOTYRE.NS",
        "MRF.NS", "UBL.NS",
    ],
    "chemicals": [
        "UPL.NS", "SRF.NS", "DEEPAKNTR.NS", "ATUL.NS", "AARTIIND.NS",
        "NAVINFLUOR.NS", "PIDILITIND.NS", "BALAMINES.NS", "GUJARATFLUORO.NS",
        "TATACHEM.NS", "LINDEINDIA.NS",
    ],
    "defence": [
        "HAL.NS", "BEL.NS", "COCHINSHIP.NS", "MAZDOCK.NS", "GRSE.NS",
        "DATAPATTNS.NS", "PARAS.NS", "SAKSOFT.NS",
    ],
    "renewables": [
        "ADANIGREEN.NS", "NTPC.NS", "TATAPOWER.NS", "JSWENERGY.NS",
        "NHPC.NS", "RECLTD.NS", "PFC.NS", "INOXWIND.NS",
    ],
    "it_digital": [
        "TECHM.NS", "LTIM.NS", "PERSISTENT.NS", "MPHASIS.NS", "COFORGE.NS",
        "TATAELXSI.NS", "OFSS.NS", "ZENSARTECH.NS", "SONATSOFTW.NS",
    ],
    "fmcg_rural": [
        "BRITANNIA.NS", "DABUR.NS", "COLPAL.NS", "GODREJCP.NS", "MARICO.NS",
        "VBL.NS", "TATACONSUM.NS", "JUBLFOOD.NS", "DEVYANI.NS", "VARUNBEV.NS",
    ],
    "pharma": [
        "DRREDDY.NS", "CIPLA.NS", "AUROPHARMA.NS", "TORNTPHARM.NS", "ZYDUSLIFE.NS",
        "LUPIN.NS", "ALKEM.NS", "JBMA.NS", "IPCALAB.NS", "GRANULES.NS",
    ],
    "finance": [
        "BAJAJFINSV.NS", "CHOLAFIN.NS", "POONAWALLA.NS", "RECLTD.NS", "PFC.NS",
        "CANBK.NS", "INDUSINDBK.NS", "BANKBARODA.NS", "UNIONBANK.NS", "IOB.NS",
    ],
}


def _safe_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _de_ratio(info: dict) -> Optional[float]:
    """
    Parse debt-to-equity from yfinance info dict.
    Yahoo reports D/E as a percentage string or raw number.
    Returns as a ratio (0.15 means 15% debt-to-equity).
    """
    raw = info.get("debtToEquity")
    if raw is None:
        return None
    val = _safe_float(raw)
    if val is None:
        return None
    # Yahoo sometimes returns as percentage (150.0 means 150% = 1.5)
    if val > 10:
        return val / 100.0
    return val


def _roe(info: dict) -> Optional[float]:
    """ROE as percentage (15.0 means 15%)."""
    raw = info.get("returnOnEquity")
    if raw is None:
        return None
    val = _safe_float(raw)
    if val is None:
        return None
    if val < 1.0:
        return val * 100.0
    return val


def _growth(info: dict, key: str) -> Optional[float]:
    """Growth rate as percentage."""
    raw = info.get(key)
    if raw is None:
        return None
    val = _safe_float(raw)
    if val is None:
        return None
    if val < 1.0:
        return val * 100.0
    return val


@dataclass
class IndianStock:
    symbol: str
    name: str
    sector: str
    industry: str
    market_cap_cr: float
    price: float
    pe_trailing: Optional[float]
    pe_forward: Optional[float]
    pb: Optional[float]
    ps: Optional[float]
    roe: Optional[float]
    roa: Optional[float]
    debt_to_equity: Optional[float]
    current_ratio: Optional[float]
    earnings_growth: Optional[float]
    revenue_growth: Optional[float]
    profit_margin: Optional[float]
    dividend_yield: Optional[float]
    fifty_day_avg: float
    two_hundred_day_avg: float
    fifty_two_week_low: float
    fifty_two_week_high: float

    @property
    def distance_from_52w_low(self) -> float:
        """0 = at 52w low, 1 = at 52w high."""
        r = self.fifty_two_week_high - self.fifty_two_week_low
        if r <= 0:
            return 0.5
        return (self.price - self.fifty_two_week_low) / r

    @property
    def is_in_dip(self) -> bool:
        return self.distance_from_52w_low < 0.30

    @property
    def is_near_high(self) -> bool:
        return self.distance_from_52w_low > 0.85

    def peg(self) -> Optional[float]:
        if self.pe_trailing and self.earnings_growth and self.earnings_growth > 0:
            return self.pe_trailing / self.earnings_growth
        return None

    def fundamental_score(self) -> float:
        score = 5.0
        if self.pe_trailing and self.pe_trailing < 20:  score += 1.0
        elif self.pe_trailing and self.pe_trailing < 30: score += 0.5

        if self.roe and self.roe > 15:                    score += 1.0
        elif self.roe and self.roe > 12:                  score += 0.5

        if self.debt_to_equity is not None and self.debt_to_equity < 0.3: score += 1.0
        elif self.debt_to_equity is not None and self.debt_to_equity < 0.6: score += 0.5

        if self.pb and self.pb < 2:                       score += 0.5
        if self.profit_margin and self.profit_margin > 10: score += 0.5
        if self.revenue_growth and self.revenue_growth > 10: score += 0.5
        if self.dividend_yield and self.dividend_yield > 1:   score += 0.5
        return min(10.0, score)

    def value_score(self) -> float:
        """Higher = better buying opportunity."""
        score = self.fundamental_score()
        if self.is_in_dip:
            score += 1.5
        elif self.distance_from_52w_low < 0.40:
            score += 0.8

        if self.is_near_high:
            score -= 2.0

        peg = self.peg()
        if peg and peg < 0.8:
            score += 1.0
        elif peg and peg < 1.2:
            score += 0.5

        return min(10.0, max(0.0, score))

    def dict(self) -> Dict:
        return {
            "symbol": self.symbol, "name": self.name, "sector": self.sector,
            "price": self.price, "market_cap_cr": round(self.market_cap_cr, 0),
            "pe": self.pe_trailing, "pb": self.pb, "roe": self.roe,
            "debt_equity": self.debt_to_equity,
            "distance_from_low": round(self.distance_from_52w_low, 2),
            "is_in_dip": self.is_in_dip,
            "fundamental_score": self.fundamental_score(),
            "value_score": self.value_score(),
        }


def screen_stock(symbol: str) -> Optional[IndianStock]:
    """Fetch fundamentals for an Indian stock via yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or "symbol" not in info:
            return None

        # Fix market cap
        mc = info.get("marketCap", 0)
        market_cap_cr = mc / 1e7 if mc else 0  # INR → Crores

        return IndianStock(
            symbol=symbol,
            name=info.get("longName", symbol),
            sector=info.get("sector", ""),
            industry=info.get("industry", ""),
            market_cap_cr=market_cap_cr,
            price=_safe_float(info.get("currentPrice") or info.get("regularMarketPrice")) or 0,
            pe_trailing=_safe_float(info.get("trailingPE")),
            pe_forward=_safe_float(info.get("forwardPE")),
            pb=_safe_float(info.get("priceToBook")),
            ps=_safe_float(info.get("priceToSalesTrailing12Months")),
            roe=_roe(info),
            roa=_safe_float(info.get("returnOnAssets")),
            debt_to_equity=_de_ratio(info),
            current_ratio=_safe_float(info.get("currentRatio")),
            earnings_growth=_growth(info, "earningsGrowth"),
            revenue_growth=_growth(info, "revenueGrowth"),
            profit_margin=_growth(info, "profitMargins"),
            dividend_yield=_safe_float(info.get("dividendYield")),
            fifty_day_avg=_safe_float(info.get("fiftyDayAverage")) or 0,
            two_hundred_day_avg=_safe_float(info.get("twoHundredDayAverage")) or 0,
            fifty_two_week_low=_safe_float(info.get("fiftyTwoWeekLow")) or 0,
            fifty_two_week_high=_safe_float(info.get("fiftyTwoWeekHigh")) or 0,
        )
    except Exception as e:
        log.warning("Screen failed for %s: %s", symbol, e)
        return None


def screen_universe(symbols: List[str]) -> List[IndianStock]:
    """Screen all symbols in a list."""
    results = []
    for sym in symbols:
        stock = screen_stock(sym)
        if stock and stock.price > 0:
            results.append(stock)
    results.sort(key=lambda s: s.value_score(), reverse=True)
    return results


def find_value_picks(
    symbols: List[str] = None,
    min_value_score: float = 6.0,
    max_pe: float = 30.0,
    min_roe: float = 10.0,
    max_debt: float = 1.0,
    min_market_cap_cr: float = 5000,
) -> List[IndianStock]:
    """
    Find strong fundamentals + buy-the-dip candidates.
    """
    symbols = symbols or INDIA_UNIVERSE["large_cap"]
    stocks = screen_universe(symbols)
    picks = []
    for s in stocks:
        if s.market_cap_cr < min_market_cap_cr:
            continue
        if s.pe_trailing and s.pe_trailing > max_pe:
            continue
        if s.roe and s.roe < min_roe:
            continue
        if s.debt_to_equity is not None and s.debt_to_equity > max_debt:
            continue
        if s.value_score() >= min_value_score:
            picks.append(s)
    return picks


# -- CLI test --
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for s in find_value_picks(min_value_score=5.0)[:10]:
        print(f"{s.symbol:18} VS={s.value_score():.1f} FS={s.fundamental_score():.1f} PE={s.pe_trailing} ROE={s.roe:.1f}% D/E={s.debt_to_equity} Price={s.price} DistLow={s.distance_from_52w_low:.2f}")
