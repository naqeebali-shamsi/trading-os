"""
autonome/longterm/us_screener.py  v1.0
US &amp; Canada Long-Term Value Gem Screener.

Philosophy: Buy wonderful businesses at fair prices.
- Quality metrics: ROIC, ROE, margin stability, low debt
- Growth metrics: Revenue CAGR, earnings CAGR
- Value metrics: P/E, P/FCF, EV/EBITDA, P/B
- Moat signals: High margins, stable market share, low capex intensity

Sources: Yahoo Finance (free, no key required)
Scoring: Composite Quality + Value + Growth - Risk
"""
import json, os, sys, math
from datetime import datetime, timezone
from typing import List, Optional, Dict

sys.path.insert(0, '/mnt/e/NomadCrew[GROWTH]/trading-os/v2')
import yfinance as yf
import numpy as np


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UNIVERSE — Quality US/Canada large-mid caps
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

US_LONGTERM_UNIVERSE = [
    # Tech
    "AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "AVGO", "ADBE", "CRM", "ORCL",
    "INTU", "NOW", "SNOW", "NET", "DDOG", "MDB", "PANW", "FTNT", "ZS", "CRWD",
    "PLTR", "ASML", "TSMC", "AMD", "QCOM", "TXN", "ADI", "LRCX", "AMAT", "KLAC",
    # Consumer
    "COST", "WMT", "TGT", "HD", "LOW", "NKE", "LULU", "SBUX", "MCD", "DPZ",
    "EL", "PG", "KO", "PEP", "GIS", "K", "CPB", "GIS", "MDLZ", "WM",
    # Healthcare
    "JNJ", "ABBV", "MRK", "PFE", "LLY", "NVO", "UNH", "CI", "HUM", "ANTM",
    "ISRG", "ZTS", "VRTX", "REGN", "GILD", "BIIB", "DHR", "SYK", "BSX", "RMD",
    # Financials
    "V", "MA", "AXP", "COF", "SYF", "ALLY", "JPM", "BAC", "WFC", "GS",
    "MS", "BLK", "BX", "KKR", "BAM", "TROW", "SCHW", "ICE", "CME", "SPGI",
    # Industrials
    " GE", "HON", "UNP", "UPS", "FDX", "CAT", "DE", "LMT", "NOC", "RTX",
    "TDG", "HEI", "CTAS", "EFX", "TRU", "EXPD", "GWW", "FAST", "MSM", "WCC",
    # Energy & Materials
    "XOM", "CVX", "COP", "EOG", "PXD", "OXY", "SLB", "HAL", "BKR", "NOV",
    "LIN", "APD", "SHW", "ECL", "NEM", "FMC", "MOS", "CF", "DOW", "LYB",
    # Communications & Media
    "NFLX", "DIS", "WBD", "PARA", "CHTR", "CMCSA", "T", "VZ", "TMUS", "CCI",
    "AMT", "SBAC", "EQIX", "DLR", "EXR", "PSA", "DOC", "VTR", "WELL", "SPG",
    # Canada
    "SHOP.TO", "TD.TO", "RY.TO", "BNS.TO", "CM.TO", "BMO.TO", "NA.TO",
    "CNQ.TO", "SU.TO", "IMO.TO", "CVE.TO", "ENB.TO", "TRP.TO", "PPL.TO",
    "FTS.TO", "BEP.UN", "BIP.UN", "CP.TO", "CNR.TO", "WN.TO", "L.TO",
    "DOL.TO", "ATD.TO", "MRU.TO", "GIB.A.TO", "CSU.TO", "TOI.TO", "KXS.TO",
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SCORING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class USLongTermGem:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.ticker = yf.Ticker(symbol)
        self.info = self.ticker.info
        self.hist = self.ticker.history(period="5y")
        self.scores = {}
        self.quality_score = 0
        self.value_score = 0
        self.growth_score = 0
        self.risk_score = 0
        self.total_score = 0

    # ── Helpers ─────────────────────────────────────────

    def _get(self, key: str, default=None):
        return self.info.get(key, default)

    def _safe_pe(self) -> float:
        pe = self._get("trailingPE")
        if pe and pe > 0 and pe < 200:
            return pe
        return 999

    def _safe_pb(self) -> float:
        pb = self._get("priceToBook")
        if pb and pb > 0 and pb < 50:
            return pb
        return 999

    def _safe_peg(self) -> float:
        peg = self._get("pegRatio")
        if peg and peg > 0 and peg < 10:
            return peg
        return 999

    def _roe(self) -> float:
        return self._get("returnOnEquity") or 0

    def _roa(self) -> float:
        return self._get("returnOnAssets") or 0

    def _margin(self) -> float:
        op_margin = self._get("operatingMargins")
        if op_margin:
            return op_margin
        return self._get("profitMargins") or 0

    def _debt_equity(self) -> float:
        de = self._get("debtToEquity")
        if de:
            return de / 100
        return 999

    def _fcf_yield(self) -> float:
        mkt_cap = self._get("marketCap")
        fcf = self._get("freeCashflow")
        if mkt_cap and fcf and mkt_cap > 0:
            return fcf / mkt_cap
        return 0

    def _price(self) -> float:
        return self._get("currentPrice") or self._get("regularMarketPrice") or 0

    def _52w_low(self) -> float:
        return self._get("fiftyTwoWeekLow") or 0

    def _52w_high(self) -> float:
        return self._get("fiftyTwoWeekHigh") or 0

    def _distance_from_low(self) -> float:
        low, high = self._52w_low(), self._52w_high()
        price = self._price()
        if low > 0 and high > low:
            return (price - low) / (high - low)
        return 0.5

    def _revenue_cagr(self) -> float:
        """Estimate 3-year revenue CAGR."""
        try:
            fin = self.ticker.financials
            if fin is not None and not fin.empty:
                rev = fin.loc["Total Revenue"] if "Total Revenue" in fin.index else None
                if rev is not None and len(rev) >= 3:
                    r0, r2 = rev.iloc[0], rev.iloc[2]
                    if r0 and r2 and r2 > 0:
                        years = 2
                        return (r0 / r2) ** (1 / years) - 1
        except Exception:
            pass
        return 0

    def _earnings_cagr(self) -> float:
        """Estimate 3-year earnings CAGR."""
        try:
            fin = self.ticker.financials
            if fin is not None and not fin.empty:
                net = fin.loc["Net Income"] if "Net Income" in fin.index else None
                if net is not None and len(net) >= 3:
                    n0, n2 = net.iloc[0], net.iloc[2]
                    if n0 and n2 and n2 > 0:
                        years = 2
                        return (n0 / n2) ** (1 / years) - 1
        except Exception:
            pass
        return 0

    # ── Scoring Functions ───────────────────────────────

    def _calc_quality(self) -> float:
        s = 0
        # ROE
        roe = self._roe()
        if roe > 0.20: s += 3
        elif roe > 0.15: s += 2
        elif roe > 0.10: s += 1

        # ROA
        roa = self._roa()
        if roa > 0.10: s += 2
        elif roa > 0.05: s += 1

        # Margins
        margin = self._margin()
        if margin > 0.20: s += 2
        elif margin > 0.10: s += 1

        # Low debt
        de = self._debt_equity()
        if de < 0.5: s += 2
        elif de < 1.0: s += 1

        # Consistent earnings (positive net income)
        try:
            fin = self.ticker.financials
            if fin is not None and not fin.empty and "Net Income" in fin.index:
                nets = fin.loc["Net Income"]
                if all(n is not None and n > 0 for n in nets[:3]):
                    s += 1
        except Exception:
            pass

        return min(s, 10)

    def _calc_value(self) -> float:
        s = 0
        pe = self._safe_pe()
        pb = self._safe_pb()
        peg = self._safe_peg()
        fcf_yield = self._fcf_yield()

        # PE < market average (~25 S&P500)
        if pe < 15: s += 3
        elif pe < 20: s += 2
        elif pe < 25: s += 1

        # PB
        if pb < 2: s += 2
        elif pb < 3: s += 1

        # PEG (growth-adjusted PE)
        if peg < 1.0: s += 2
        elif peg < 1.5: s += 1

        # FCF yield
        if fcf_yield > 0.05: s += 2
        elif fcf_yield > 0.03: s += 1

        # Near 52w low = more value
        dist = self._distance_from_low()
        if dist < 0.15: s += 1

        return min(s, 10)

    def _calc_growth(self) -> float:
        s = 0
        rev_cagr = self._revenue_cagr()
        earn_cagr = self._earnings_cagr()

        if rev_cagr > 0.15: s += 3
        elif rev_cagr > 0.10: s += 2
        elif rev_cagr > 0.05: s += 1

        if earn_cagr > 0.20: s += 3
        elif earn_cagr > 0.10: s += 2
        elif earn_cagr > 0.05: s += 1

        return min(s, 6)

    def _calc_risk(self) -> float:
        """Lower is better. Penalize high debt, high beta, small cap."""
        r = 0
        de = self._debt_equity()
        if de > 2.0: r += 3
        elif de > 1.0: r += 1

        beta = self._get("beta") or 1.0
        if beta > 2.0: r += 2
        elif beta > 1.5: r += 1

        mkt_cap = self._get("marketCap") or 0
        if mkt_cap < 5e9: r += 2  # Small cap penalty
        elif mkt_cap < 20e9: r += 1  # Mid cap

        return min(r, 6)

    def analyze(self):
        """Run full analysis."""
        self.quality_score = self._calc_quality()
        self.value_score = self._calc_value()
        self.growth_score = self._calc_growth()
        self.risk_score = self._calc_risk()
        self.total_score = self.quality_score + self.value_score + self.growth_score - self.risk_score

    def to_dict(self) -> dict:
        price = self._price()
        low, high = self._52w_low(), self._52w_high()
        mkt_cap = self._get("marketCap") or 0
        return {
            "symbol": self.symbol,
            "display_symbol": self.symbol.replace(".TO", "").replace(".UN", ""),
            "name": self._get("longName") or self._get("shortName") or self.symbol,
            "price": price,
            "price_fmt": f"${price:,.2f}",
            "pe": self._safe_pe(),
            "pe_fmt": f"{self._safe_pe():.1f}" if self._safe_pe() < 999 else "N/A",
            "pb": self._safe_pb(),
            "pb_fmt": f"{self._safe_pb():.1f}" if self._safe_pb() < 999 else "N/A",
            "peg": self._safe_peg(),
            "peg_fmt": f"{self._safe_peg():.2f}" if self._safe_peg() < 999 else "N/A",
            "roe": self._roe(),
            "roe_fmt": f"{self._roe()*100:.1f}%",
            "debt_equity": self._debt_equity(),
            "de_fmt": f"{self._debt_equity():.2f}x",
            "margin": self._margin(),
            "margin_fmt": f"{self._margin()*100:.1f}%",
            "fcf_yield": self._fcf_yield(),
            "fcf_yield_fmt": f"{self._fcf_yield()*100:.1f}%",
            "52w_low": low,
            "52w_high": high,
            "distance_from_low": self._distance_from_low(),
            "52w_range_fmt": f"{(self._distance_from_low()*100):.0f}%",
            "mkt_cap": mkt_cap,
            "mkt_cap_fmt": self._fmt_mktcap(mkt_cap),
            "revenue_cagr": self._revenue_cagr(),
            "earnings_cagr": self._earnings_cagr(),
            "quality_score": self.quality_score,
            "value_score": self.value_score,
            "growth_score": self.growth_score,
            "risk_score": self.risk_score,
            "total_score": self.total_score,
            "sector": self._get("sector") or "Unknown",
            "industry": self._get("industry") or "Unknown",
            "beta": self._get("beta") or 1.0,
            "dividend_yield": self._get("dividendYield") or 0,
        }

    @staticmethod
    def _fmt_mktcap(val: float) -> str:
        if val >= 1e12:
            return f"${val/1e12:.1f}T"
        if val >= 1e9:
            return f"${val/1e9:.0f}B"
        if val >= 1e6:
            return f"${val/1e6:.0f}M"
        return "N/A"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SCREENING ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def batch_analyze(symbols: List[str], delay: float = 0.3) -> List[USLongTermGem]:
    """Analyze a batch of symbols."""
    results = []
    for sym in symbols:
        try:
            gem = USLongTermGem(sym)
            gem.analyze()
            results.append(gem)
            time.sleep(delay)
        except Exception as e:
            print(f"  {sym}: ERROR {e}")
    return results


import time


def find_us_gems(min_total: int = 10, max_results: int = 30, sample_size: int = 100) -> List[dict]:
    """Find undervalued US/Canada gems."""
    # For speed, sample from universe
    import random
    sample = random.sample(US_LONGTERM_UNIVERSE, min(sample_size, len(US_LONGTERM_UNIVERSE)))
    print(f"Screening {len(sample)} US/Canada stocks for long-term value...")

    gems = batch_analyze(sample, delay=0.2)
    gems.sort(key=lambda g: g.total_score, reverse=True)

    top = [g.to_dict() for g in gems if g.total_score >= min_total][:max_results]
    return top


if __name__ == "__main__":
    gems = find_us_gems(min_total=8, max_results=20, sample_size=80)
    print(f"\n{'='*60}")
    print(f"TOP {len(gems)} US/CANADA LONG-TERM VALUE GEMS")
    print(f"{'='*60}")
    for g in gems:
        rec = "STRONG_BUY" if g['total_score'] >= 14 else "BUY" if g['total_score'] >= 10 else "HOLD"
        print(f"{rec:12} {g['display_symbol']:10} Q={g['quality_score']} V={g['value_score']} G={g['growth_score']} R={g['risk_score']} T={g['total_score']}")
        print(f"             PE={g['pe_fmt']} PB={g['pb_fmt']} ROE={g['roe_fmt']} D/E={g['de_fmt']} Marg={g['margin_fmt']}")
        print(f"             {g['price_fmt']} | 52w={g['52w_range_fmt']} | Cap={g['mkt_cap_fmt']}")
        print()