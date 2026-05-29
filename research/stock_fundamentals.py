"""Fetch issuer fundamentals via yfinance (optional dependency)."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - optional on minimal hosts
    yf = None


def yfinance_available() -> bool:
    return yf is not None


def resolve_yfinance_ticker(symbol: str, *, region: str = "US", exchange: str = "") -> str:
    sym = str(symbol or "").upper().strip()
    if region == "IN" or exchange.upper() in {"NSE", "BSE"}:
        if sym.endswith(".NS") or sym.endswith(".BO"):
            return sym
        return f"{sym}.NS"
    return sym


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        out = float(value)
        if out != out:  # NaN
            return None
        return out
    except (TypeError, ValueError):
        return None


def fetch_fundamentals(
    symbol: str,
    *,
    region: str = "US",
    exchange: str = "",
    yfinance_ticker: Optional[str] = None,
) -> Dict[str, Any]:
    """Return normalized fundamental snapshot for one symbol."""
    ticker = yfinance_ticker or resolve_yfinance_ticker(symbol, region=region, exchange=exchange)
    base = {
        "symbol": symbol,
        "yfinance_ticker": ticker,
        "fetched_ts": time.time(),
        "ok": False,
        "source": "yfinance" if yf else "unavailable",
    }
    if yf is None:
        base["error"] = "yfinance_not_installed"
        return base

    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:
        base["error"] = str(exc)
        return base

    hist = None
    try:
        hist = yf.Ticker(ticker).history(period="13mo", interval="1mo")
    except Exception:
        hist = None

    price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    market_cap = _safe_float(info.get("marketCap"))
    pe = _safe_float(info.get("trailingPE") or info.get("forwardPE"))
    peg = _safe_float(info.get("pegRatio"))
    revenue_growth = _safe_float(info.get("revenueGrowth"))
    earnings_growth = _safe_float(info.get("earningsGrowth"))
    profit_margin = _safe_float(info.get("profitMargins"))
    gross_margin = _safe_float(info.get("grossMargins"))
    roe = _safe_float(info.get("returnOnEquity"))
    debt_to_equity = _safe_float(info.get("debtToEquity"))
    fcf = _safe_float(info.get("freeCashflow"))
    total_revenue = _safe_float(info.get("totalRevenue"))
    beta = _safe_float(info.get("beta"))
    sector = str(info.get("sector") or "")
    industry = str(info.get("industry") or "")

    momentum_12_1 = None
    if hist is not None and len(hist) >= 2:
        closes = hist["Close"].dropna()
        if len(closes) >= 2:
            start = float(closes.iloc[0])
            end = float(closes.iloc[-2]) if len(closes) >= 13 else float(closes.iloc[-1])
            if start > 0:
                momentum_12_1 = (end / start) - 1.0

    fcf_yield = None
    if fcf is not None and market_cap and market_cap > 0:
        fcf_yield = fcf / market_cap

    revenue_per_share_growth_proxy = revenue_growth
    payout_ratio = _safe_float(info.get("payoutRatio"))

    fields = {
        "price": price,
        "market_cap": market_cap,
        "pe": pe,
        "peg": peg,
        "revenue_growth": revenue_growth,
        "earnings_growth": earnings_growth,
        "profit_margin": profit_margin,
        "gross_margin": gross_margin,
        "roe": roe,
        "debt_to_equity": debt_to_equity,
        "fcf_yield": fcf_yield,
        "momentum_12_1": momentum_12_1,
        "beta": beta,
        "payout_ratio": payout_ratio,
        "total_revenue": total_revenue,
    }
    present = sum(1 for v in fields.values() if v is not None)

    base.update(fields)
    base.update(
        {
            "ok": present >= 5 and price is not None,
            "sector": sector,
            "industry": industry,
            "data_completeness": round(present / max(len(fields), 1), 3),
        }
    )
    return base


def fetch_universe(symbols: List[str], *, meta_by_symbol: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    meta_by_symbol = meta_by_symbol or {}
    out: Dict[str, Dict[str, Any]] = {}
    for symbol in symbols:
        meta = meta_by_symbol.get(symbol) or {}
        out[symbol] = fetch_fundamentals(
            symbol,
            region=str(meta.get("region") or "US"),
            exchange=str(meta.get("exchange") or ""),
            yfinance_ticker=meta.get("yfinance_ticker"),
        )
        time.sleep(0.15)
    return out
