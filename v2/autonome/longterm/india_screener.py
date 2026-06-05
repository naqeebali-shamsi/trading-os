"""
autonome/longterm/india_screener.py  v1.0
India Long-Term Value Gem Screener.

Philosophy: "NEVER LOSE MONEY" — Ben Graham meets Indian growth.
- Quality businesses with durable competitive advantage
- Reasonable valuations with margin of safety
- Long-term wealth compounding potential (3-7 years)
- Emphasis on: ROE, capital efficiency, low debt, consistent earnings

Sources: Yahoo Finance (free)
"""
import json, os, sys, math, random, time
from typing import List, Dict
from datetime import datetime, timezone

sys.path.insert(0, '/mnt/e/NomadCrew[GROWTH]/trading-os/v2')
import yfinance as yf

INDIA_LONGTERM_UNIVERSE = [
    # Financial Services
    "HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS", "SBIN.NS",
    "BAJFINANCE.NS", "CHOLAFIN.NS", "POONAWALLA.NS", "MUTHOOTFIN.NS", "BAJAJFINSV.NS",
    # Technology
    "TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS",
    "LTIM.NS", "PERSISTENT.NS", "MPHASIS.NS", "COFORGE.NS", "SONATSOFTW.NS",
    "TATAELXSI.NS", "ZENSARTECH.NS", "LTTS.NS",
    # Consumer
    "HINDUNILVR.NS", "ITC.NS", "BRITANNIA.NS", "DABUR.NS", "MARICO.NS",
    "NESTLEIND.NS", "GODREJCP.NS", "COLPAL.NS", "VBL.NS", "TATACONSUM.NS",
    "DABUR.NS", "EMAMILTD.NS",
    # Pharma
    "SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "LUPIN.NS", "AUROPHARMA.NS",
    "TORNTPHARM.NS", "IPCALAB.NS", "ALKEM.NS", "BIOCON.NS",
    # Industrials / Capital Goods
    "LT.NS", "SIEMENS.NS", "ABB.NS", "BEL.NS", "HAL.NS",
    "COCHINSHIP.NS", "MAZDOCK.NS", "GRSE.NS", "BHEL.NS",
    "THERMAX.NS", "SKFINDIA.NS", "SCHAEFFLER.NS", "TIMKEN.NS", "BOSCHLTD.NS",
    "EICHERMOT.NS", "MRF.NS", "ASHOKLEY.NS", "TATAMOTORS.NS",
    # Chemicals
    "PIDILITIND.NS", "SRF.NS", "DEEPAKNTR.NS", "AARTIIND.NS", "ATUL.NS",
    "LINDEINDIA.NS", "UPL.NS", "TATACHEM.NS",
    # Energy / Power
    "RELIANCE.NS", "POWERGRID.NS", "NTPC.NS", "NHPC.NS", "ADANIGREEN.NS",
    "JSWENERGY.NS", "TATAPOWER.NS", "TORRENTPOST.NS",
    # Other Quality
    "MARUTI.NS", "BAJAJ-AUTO.NS", "HEROMOTOCO.NS", "TVSMOTOR.NS",
    "M&M.NS", "TITAN.NS", "ASIANPAINT.NS", "BERGEPAINT.NS",
    "HDFCLIFE.NS", "SBILIFE.NS", "ICICIGI.NS",
]


class IndiaLongTermGem:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.ticker = yf.Ticker(symbol)
        self.info = self.ticker.info
        self.scores = {}
        self.quality_score = 0
        self.value_score = 0
        self.growth_score = 0
        self.risk_score = 0
        self.total_score = 0

    def _get(self, key, default=None):
        return self.info.get(key, default)

    def _safe_pe(self):
        pe = self._get("trailingPE")
        if pe and 0 < pe < 200:
            return pe
        return 999

    def _pb(self):
        pb = self._get("priceToBook")
        if pb and pb > 0:
            return pb
        return 999

    def _roe(self):
        return self._get("returnOnEquity") or 0

    def _roa(self):
        return self._get("returnOnAssets") or 0

    def _debt_equity(self):
        de = self._get("debtToEquity")
        if de:
            return de / 100
        return 999

    def _margin(self):
        op = self._get("operatingMargins")
        if op:
            return op
        return self._get("profitMargins") or 0

    def _price(self):
        return self._get("currentPrice") or self._get("regularMarketPrice") or 0

    def _52w_low(self):
        return self._get("fiftyTwoWeekLow") or 0

    def _52w_high(self):
        return self._get("fiftyTwoWeekHigh") or 0

    def _distance_from_low(self):
        low, high = self._52w_low(), self._52w_high()
        price = self._price()
        if low > 0 and high > low:
            return (price - low) / (high - low)
        return 0.5

    def _mkt_cap_cr(self):
        cap = self._get("marketCap") or 0
        return cap / 1e7  # INR crores

    def _revenue_cagr(self):
        try:
            fin = self.ticker.financials
            if fin is not None and "Total Revenue" in fin.index:
                rev = fin.loc["Total Revenue"]
                if len(rev) >= 3:
                    r0, r2 = rev.iloc[0], rev.iloc[2]
                    if r0 and r2 and r2 > 0:
                        return (r0 / r2) ** 0.5 - 1
        except:
            pass
        return 0

    def _earnings_cagr(self):
        try:
            fin = self.ticker.financials
            if fin is not None and "Net Income" in fin.index:
                net = fin.loc["Net Income"]
                if len(net) >= 3:
                    n0, n2 = net.iloc[0], net.iloc[2]
                    if n0 and n2 and n2 > 0:
                        return (n0 / n2) ** 0.5 - 1
        except:
            pass
        return 0

    # ── Scoring ──────────────────────────────────────────

    def _calc_quality(self):
        s = 0
        roe = self._roe()
        if roe > 0.20: s += 3
        elif roe > 0.15: s += 2
        elif roe > 0.10: s += 1

        roa = self._roa()
        if roa > 0.10: s += 2
        elif roa > 0.05: s += 1

        margin = self._margin()
        if margin > 0.20: s += 2
        elif margin > 0.10: s += 1

        de = self._debt_equity()
        if de < 0.3: s += 3
        elif de < 0.6: s += 2
        elif de < 1.0: s += 1

        # Consistent profits
        try:
            fin = self.ticker.financials
            if fin is not None and "Net Income" in fin.index:
                nets = fin.loc["Net Income"]
                if all(n is not None and n > 0 for n in nets[:3]):
                    s += 1
        except:
            pass

        # Large cap = stability
        cap_cr = self._mkt_cap_cr()
        if cap_cr > 50000: s += 1

        return min(s, 10)

    def _calc_value(self):
        s = 0
        pe = self._safe_pe()
        pb = self._pb()

        # PE (India avg ~25)
        if pe < 12: s += 3
        elif pe < 18: s += 2
        elif pe < 25: s += 1

        # PB
        if pb < 2: s += 2
        elif pb < 3: s += 1

        # Near 52w low
        dist = self._distance_from_low()
        if dist < 0.15: s += 2
        elif dist < 0.30: s += 1

        # Dividend
        div = self._get("dividendYield") or 0
        if div > 0.025: s += 1

        return min(s, 10)

    def _calc_growth(self):
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

    def _calc_risk(self):
        r = 0
        de = self._debt_equity()
        if de > 2.0: r += 3
        elif de > 1.0: r += 1

        beta = self._get("beta") or 1.0
        if beta > 1.5: r += 2
        elif beta > 1.2: r += 1

        # Small cap = more volatile
        cap_cr = self._mkt_cap_cr()
        if cap_cr < 5000: r += 2
        elif cap_cr < 20000: r += 1

        return min(r, 6)

    def analyze(self):
        self.quality_score = self._calc_quality()
        self.value_score = self._calc_value()
        self.growth_score = self._calc_growth()
        self.risk_score = self._calc_risk()
        self.total_score = self.quality_score + self.value_score + self.growth_score - self.risk_score

    def to_dict(self) -> dict:
        price = self._price()
        low, high = self._52w_low(), self._52w_high()
        cap_cr = self._mkt_cap_cr()
        return {
            "symbol": self.symbol,
            "display_symbol": self.symbol.replace(".NS", ""),
            "name": self._get("longName") or self._get("shortName") or self.symbol,
            "price": price,
            "price_fmt": f"₹{price:,.0f}" if price >= 1000 else f"₹{price:,.2f}",
            "pe": self._safe_pe(),
            "pe_fmt": f"{self._safe_pe():.1f}" if self._safe_pe() < 999 else "N/A",
            "pb": self._pb(),
            "pb_fmt": f"{self._pb():.1f}" if self._pb() < 999 else "N/A",
            "roe": self._roe(),
            "roe_fmt": f"{self._roe()*100:.1f}%",
            "debt_equity": self._debt_equity(),
            "de_fmt": f"{self._debt_equity():.2f}x",
            "margin": self._margin(),
            "margin_fmt": f"{self._margin()*100:.1f}%",
            "distance_from_low": self._distance_from_low(),
            "52w_range_fmt": f"{(self._distance_from_low()*100):.0f}%",
            "mkt_cap_cr": cap_cr,
            "mkt_cap_fmt": self._fmt_cap(cap_cr),
            "quality_score": self.quality_score,
            "value_score": self.value_score,
            "growth_score": self.growth_score,
            "risk_score": self.risk_score,
            "total_score": self.total_score,
            "sector": self._get("sector") or "Unknown",
            "dividend_yield": (self._get("dividendYield") or 0) * 100,
        }

    @staticmethod
    def _fmt_cap(cr: float) -> str:
        if cr >= 100000:
            return f"₹{cr/100000:.1f}L Cr"
        if cr >= 1000:
            return f"₹{cr/1000:.0f}K Cr"
        return f"₹{cr:.0f} Cr"


def batch_analyze_india(symbols: List[str], delay: float = 0.25) -> List[IndiaLongTermGem]:
    results = []
    for sym in symbols:
        try:
            gem = IndiaLongTermGem(sym)
            gem.analyze()
            results.append(gem)
            time.sleep(delay)
        except Exception as e:
            print(f"  {sym}: ERROR {e}")
    return results


def find_india_gems(min_total: int = 8, max_results: int = 25, sample_size: int = 80) -> List[dict]:
    sample = random.sample(INDIA_LONGTERM_UNIVERSE, min(sample_size, len(INDIA_LONGTERM_UNIVERSE)))
    print(f"Screening {len(sample)} Indian stocks for long-term value...")

    gems = batch_analyze_india(sample, delay=0.2)
    gems.sort(key=lambda g: g.total_score, reverse=True)

    top = [g.to_dict() for g in gems if g.total_score >= min_total][:max_results]
    return top


if __name__ == "__main__":
    gems = find_india_gems(min_total=8, max_results=20, sample_size=80)
    print(f"\n{'='*60}")
    print(f"TOP {len(gems)} INDIA LONG-TERM VALUE GEMS")
    print(f"{'='*60}")
    for g in gems:
        rec = "STRONG_BUY" if g['total_score'] >= 14 else "BUY" if g['total_score'] >= 10 else "HOLD"
        print(f"{rec:12} {g['display_symbol']:12} Q={g['quality_score']} V={g['value_score']} G={g['growth_score']} R={g['risk_score']} T={g['total_score']}")
        print(f"             PE={g['pe_fmt']} ROE={g['roe_fmt']} D/E={g['de_fmt']} Range={g['52w_range_fmt']}")
        print()