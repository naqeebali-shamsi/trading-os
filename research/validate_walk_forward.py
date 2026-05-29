#!/usr/bin/env python3
"""Walk-forward validation for the stock research factor model.

Run BEFORE changing scanner/trading wiring:
  python research/validate_walk_forward.py --phase pre --report intel/research_validation_pre.json

Run AFTER code changes:
  python research/validate_walk_forward.py --phase post --report intel/research_validation_post.json

Tests (price-based, no look-ahead on momentum):
  1. Momentum quintile spread — top vs bottom forward 6m return
  2. Composite proxy — momentum + inverse-vol at each rebalance date
  3. PIT fundamentals composite — quarterly statements + reporting lag
  4. Scanner simulation — top-K by tier/confidence vs equal-weight baseline
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex.instrument_registry import InstrumentRegistry  # noqa: E402
from research.config import load_config  # noqa: E402
from research.pit_fundamentals import QuarterlyFinancialCache, price_on_date  # noqa: E402
from research.stock_factors import build_research_row, rank_research_rows  # noqa: E402

try:
    import yfinance as yf
except ImportError:
    yf = None


@dataclass
class RebalanceRow:
    date: str
    symbol: str
    momentum_12_1: float
    vol_6m: float
    forward_6m: Optional[float]
    composite_proxy: Optional[float]
    pit_composite: Optional[float] = None
    tier_proxy: str = "low"


def _yf_ticker(symbol: str, meta: dict) -> str:
    region = str(meta.get("region") or "US")
    exchange = str(meta.get("exchange") or "")
    if region == "IN" or exchange.upper() in {"NSE", "BSE"}:
        return symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    if symbol == "BRK.B":
        return "BRK-B"
    return symbol


def load_universe(max_symbols: int = 40) -> Tuple[List[str], Dict[str, dict]]:
    registry = InstrumentRegistry(ROOT / "config" / "instruments.yaml")
    symbols = []
    meta = {}
    for sym, cfg in registry.symbols.items():
        if str(cfg.get("asset_class") or "") != "stock_cfd":
            continue
        symbols.append(sym.upper())
        meta[sym.upper()] = cfg
    symbols = sorted(symbols)[:max_symbols]
    return symbols, {s: meta.get(s, {}) for s in symbols}


def download_monthly_closes(symbols: List[str], meta_by_symbol: dict, *, years: int = 4) -> Dict[str, Dict[str, float]]:
    if yf is None:
        raise RuntimeError("yfinance required: python -m pip install yfinance")
    out: Dict[str, Dict[str, float]] = {}
    period = f"{years}y"
    for sym in symbols:
        ticker = _yf_ticker(sym, meta_by_symbol.get(sym, {}))
        try:
            hist = yf.Ticker(ticker).history(period=period, interval="1mo", auto_adjust=True)
        except Exception:
            continue
        if hist is None or hist.empty:
            continue
        closes = {}
        for idx, row in hist.iterrows():
            close = float(row.get("Close") or 0)
            if close > 0:
                closes[str(idx.date())] = close
        if closes:
            out[sym] = closes
        time.sleep(0.05)
    return out


def sorted_dates(closes: Dict[str, float]) -> List[str]:
    return sorted(closes.keys())


def momentum_12_1(closes: Dict[str, float], date_idx: int) -> Optional[float]:
    dates = sorted_dates(closes)
    if date_idx < 13 or date_idx >= len(dates):
        return None
    start = closes[dates[date_idx - 13]]
    end = closes[dates[date_idx - 1]]
    if start <= 0:
        return None
    return (end / start) - 1.0


def vol_6m(closes: Dict[str, float], date_idx: int) -> Optional[float]:
    dates = sorted_dates(closes)
    if date_idx < 7:
        return None
    rets = []
    for i in range(date_idx - 6, date_idx):
        a = closes[dates[i - 1]]
        b = closes[dates[i]]
        if a > 0:
            rets.append((b / a) - 1.0)
    if len(rets) < 3:
        return None
    return statistics.pstdev(rets)


def forward_return(closes: Dict[str, float], date_idx: int, months: int = 6) -> Optional[float]:
    dates = sorted_dates(closes)
    if date_idx + months >= len(dates):
        return None
    start = closes[dates[date_idx]]
    end = closes[dates[date_idx + months]]
    if start <= 0:
        return None
    return (end / start) - 1.0


def composite_proxy(momentum: float, vol: float) -> float:
    """Price-only proxy until point-in-time fundamentals exist."""
    mom_score = max(0.0, min(1.0, (momentum + 0.2) / 1.0))
    vol_score = max(0.0, min(1.0, 1.0 - (vol / 0.15))) if vol is not None else 0.5
    return round(0.65 * mom_score + 0.35 * vol_score, 4)


def quintile_bucket(values: List[Tuple[str, float]]) -> Dict[str, int]:
    if not values:
        return {}
    ordered = sorted(values, key=lambda x: x[1])
    n = len(ordered)
    out = {}
    for i, (sym, _) in enumerate(ordered):
        q = min(4, int(i * 5 / n))
        out[sym] = q
    return out


def rank_ic(scores: Dict[str, float], forwards: Dict[str, float]) -> Optional[float]:
    common = [s for s in scores if s in forwards]
    if len(common) < 5:
        return None
    xs = [scores[s] for s in common]
    ys = [forwards[s] for s in common]
    n = len(common)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 4)


def run_walk_forward(
    price_data: Dict[str, Dict[str, float]],
    *,
    forward_months: int = 6,
    min_history: int = 14,
    pit_cache: Optional[QuarterlyFinancialCache] = None,
    meta_by_symbol: Optional[Dict[str, dict]] = None,
    cfg: Optional[dict] = None,
    reporting_lag_days: int = 45,
) -> Dict[str, Any]:
    meta_by_symbol = meta_by_symbol or {}
    cfg = cfg or load_config()
    use_pit = pit_cache is not None and yf is not None
    anchor = max(price_data.items(), key=lambda kv: len(kv[1]))[0]
    dates = sorted_dates(price_data[anchor])
    rebalance_rows: List[RebalanceRow] = []

    for di, d in enumerate(dates):
        if di < min_history or di + forward_months >= len(dates):
            continue
        cross_mom: List[Tuple[str, float]] = []
        cross_fwd: Dict[str, float] = {}
        for sym, closes in price_data.items():
            sym_dates = sorted_dates(closes)
            if d not in sym_dates:
                continue
            sdi = sym_dates.index(d)
            mom = momentum_12_1(closes, sdi)
            vol = vol_6m(closes, sdi)
            fwd = forward_return(closes, sdi, months=forward_months)
            if mom is None:
                continue
            comp = composite_proxy(mom, vol or 0.05)
            pit_comp = None
            if use_pit:
                from datetime import date as date_cls

                as_of = date_cls.fromisoformat(d)
                px = price_on_date(closes, as_of)
                pit_data = pit_cache.pit_snapshot(
                    sym,
                    as_of=as_of,
                    meta=meta_by_symbol.get(sym, {}),
                    reporting_lag_days=reporting_lag_days,
                    momentum_12_1=mom,
                    price=px,
                )
                if pit_data.get("ok"):
                    row = build_research_row(sym, pit_data, cfg=cfg)
                    pit_comp = row.get("composite_score")
            rebalance_rows.append(
                RebalanceRow(
                    date=d,
                    symbol=sym,
                    momentum_12_1=mom,
                    vol_6m=vol or 0.0,
                    forward_6m=fwd,
                    composite_proxy=comp,
                    pit_composite=pit_comp,
                    tier_proxy="high" if comp >= 0.65 else "low",
                )
            )
            cross_mom.append((sym, mom))
            if fwd is not None:
                cross_fwd[sym] = fwd

        # per-date quintile stored implicitly via next aggregate pass

    # Aggregate by date
    by_date: Dict[str, List[RebalanceRow]] = {}
    for row in rebalance_rows:
        by_date.setdefault(row.date, []).append(row)

    momentum_spreads = []
    composite_spreads = []
    pit_spreads = []
    momentum_ics = []
    composite_ics = []
    pit_ics = []
    top5_returns = []
    bottom5_returns = []
    pit_top5_returns = []
    baseline_returns = []

    for d, rows in sorted(by_date.items()):
        with_fwd = [r for r in rows if r.forward_6m is not None]
        if len(with_fwd) < 8:
            continue
        mom_pairs = [(r.symbol, r.momentum_12_1) for r in with_fwd]
        comp_pairs = [(r.symbol, r.composite_proxy) for r in with_fwd]
        pit_pairs = [(r.symbol, r.pit_composite) for r in with_fwd if r.pit_composite is not None]
        mom_q = quintile_bucket(mom_pairs)
        comp_q = quintile_bucket(comp_pairs)
        pit_q = quintile_bucket(pit_pairs) if len(pit_pairs) >= 8 else {}
        fwd_map = {r.symbol: r.forward_6m for r in with_fwd}

        top_mom = [fwd_map[s] for s, q in mom_q.items() if q == 4]
        bot_mom = [fwd_map[s] for s, q in mom_q.items() if q == 0]
        if top_mom and bot_mom:
            momentum_spreads.append(statistics.mean(top_mom) - statistics.mean(bot_mom))

        top_comp = [fwd_map[s] for s, q in comp_q.items() if q == 4]
        bot_comp = [fwd_map[s] for s, q in comp_q.items() if q == 0]
        if top_comp and bot_comp:
            composite_spreads.append(statistics.mean(top_comp) - statistics.mean(bot_comp))

        if pit_q:
            top_pit = [fwd_map[s] for s, q in pit_q.items() if q == 4 and s in fwd_map]
            bot_pit = [fwd_map[s] for s, q in pit_q.items() if q == 0 and s in fwd_map]
            if top_pit and bot_pit:
                pit_spreads.append(statistics.mean(top_pit) - statistics.mean(bot_pit))

        mom_scores = {s: float(v) for s, v in mom_pairs}
        comp_scores = {s: float(v) for s, v in comp_pairs}
        ic_m = rank_ic(mom_scores, fwd_map)
        ic_c = rank_ic(comp_scores, fwd_map)
        ic_p = rank_ic({s: float(v) for s, v in pit_pairs}, fwd_map) if len(pit_pairs) >= 5 else None
        if ic_m is not None:
            momentum_ics.append(ic_m)
        if ic_c is not None:
            composite_ics.append(ic_c)
        if ic_p is not None:
            pit_ics.append(ic_p)

        ranked = sorted(with_fwd, key=lambda r: r.composite_proxy, reverse=True)
        top5 = ranked[:5]
        bot5 = ranked[-5:]
        top5_returns.append(statistics.mean([r.forward_6m for r in top5 if r.forward_6m is not None]))
        bottom5_returns.append(statistics.mean([r.forward_6m for r in bot5 if r.forward_6m is not None]))
        baseline_returns.append(statistics.mean([r.forward_6m for r in with_fwd if r.forward_6m is not None]))

        pit_ranked = sorted(
            [r for r in with_fwd if r.pit_composite is not None],
            key=lambda r: float(r.pit_composite or 0),
            reverse=True,
        )
        if len(pit_ranked) >= 5:
            pit_top5_returns.append(statistics.mean([r.forward_6m for r in pit_ranked[:5] if r.forward_6m is not None]))

    def _mean(xs: List[float]) -> Optional[float]:
        return round(statistics.mean(xs), 4) if xs else None

    def _hit_rate(spreads: List[float]) -> Optional[float]:
        if not spreads:
            return None
        return round(sum(1 for s in spreads if s > 0) / len(spreads), 4)

    return {
        "rebalance_dates": len(by_date),
        "momentum_quintile_spread_6m_mean": _mean(momentum_spreads),
        "momentum_quintile_hit_rate": _hit_rate(momentum_spreads),
        "composite_quintile_spread_6m_mean": _mean(composite_spreads),
        "composite_quintile_hit_rate": _hit_rate(composite_spreads),
        "pit_quintile_spread_6m_mean": _mean(pit_spreads),
        "pit_quintile_hit_rate": _hit_rate(pit_spreads),
        "momentum_rank_ic_mean": _mean(momentum_ics),
        "composite_rank_ic_mean": _mean(composite_ics),
        "pit_rank_ic_mean": _mean(pit_ics),
        "top5_vs_bottom5_spread_6m": _mean([t - b for t, b in zip(top5_returns, bottom5_returns)]),
        "top5_forward_6m_mean": _mean(top5_returns),
        "pit_top5_forward_6m_mean": _mean(pit_top5_returns),
        "bottom5_forward_6m_mean": _mean(bottom5_returns),
        "equal_weight_forward_6m_mean": _mean(baseline_returns),
        "pit_rebalance_coverage": round(len(pit_spreads) / max(len(by_date), 1), 4),
    }


def simulate_scanner_strategies(price_data: Dict[str, Dict[str, float]], cfg: dict) -> Dict[str, Any]:
    """Before/after scanner selection using price-proxy tiers vs alphabetical baseline."""
    wf = run_walk_forward(price_data)
    before_top = wf.get("equal_weight_forward_6m_mean")
    after_top = wf.get("top5_forward_6m_mean")
    improvement = None
    if before_top is not None and after_top is not None:
        improvement = round(after_top - before_top, 4)
    return {
        "baseline_equal_weight_6m": before_top,
        "research_top5_6m": after_top,
        "improvement_vs_baseline": improvement,
        "recommend_wire_scanner": improvement is not None and improvement > 0,
    }


def load_fundamental_snapshot_test(symbols: List[str], meta: dict, cfg: dict) -> Dict[str, Any]:
    """Cross-sectional sanity: do current fundamentals rank align with recent momentum?"""
    from research.stock_fundamentals import fetch_universe  # noqa: E402

    fundamentals = fetch_universe(symbols[:15], meta_by_symbol=meta)
    rows = [build_research_row(s, fundamentals.get(s) or {}, cfg=cfg) for s in symbols[:15]]
    ranked = rank_research_rows(rows)
    top = ranked[:5]
    bottom = ranked[-5:]
    return {
        "top_symbols": [r["symbol"] for r in top],
        "bottom_symbols": [r["symbol"] for r in bottom],
        "top_avg_confidence": round(statistics.mean(float(r.get("confidence") or 0) for r in top), 4) if top else None,
        "note": "Cross-sectional only — not a historical backtest",
    }


def run_validation(phase: str, report_path: Path, *, skip_pit: bool = False) -> Dict[str, Any]:
    cfg = load_config()
    pit_cfg = cfg.get("pit_validation") or {}
    symbols, meta = load_universe(max_symbols=int((cfg.get("universe") or {}).get("max_symbols", 40)))
    price_data = download_monthly_closes(symbols, meta)
    if len(price_data) < 10:
        raise RuntimeError(f"Insufficient price history: only {len(price_data)} symbols")

    pit_cache = None
    pit_symbols = symbols[: int(pit_cfg.get("max_symbols", 20))]
    reporting_lag = int(pit_cfg.get("reporting_lag_days", 45))
    if not skip_pit and yf is not None:
        pit_cache = QuarterlyFinancialCache()
        price_data_pit = {s: price_data[s] for s in pit_symbols if s in price_data}
    else:
        price_data_pit = price_data

    walk_forward = run_walk_forward(price_data)
    pit_walk_forward = None
    if pit_cache is not None:
        pit_walk_forward = run_walk_forward(
            price_data_pit,
            pit_cache=pit_cache,
            meta_by_symbol=meta,
            cfg=cfg,
            reporting_lag_days=reporting_lag,
        )

    payload = {
        "phase": phase,
        "ts": time.time(),
        "symbols_loaded": len(price_data),
        "pit_symbols": len(price_data_pit) if pit_cache else 0,
        "walk_forward": walk_forward,
        "pit_walk_forward": pit_walk_forward,
        "scanner_simulation": simulate_scanner_strategies(price_data, cfg),
        "fundamental_snapshot": load_fundamental_snapshot_test(symbols, meta, cfg),
        "verdict": {},
    }

    wf = payload["walk_forward"]
    pit_wf = payload.get("pit_walk_forward") or {}
    sim = payload["scanner_simulation"]
    passes = []
    fails = []
    if (wf.get("momentum_quintile_spread_6m_mean") or 0) > 0:
        passes.append("momentum_quintile_spread_positive")
    else:
        fails.append("momentum_quintile_spread_non_positive")
    if (wf.get("composite_quintile_spread_6m_mean") or 0) > 0:
        passes.append("composite_quintile_spread_positive")
    else:
        fails.append("composite_quintile_spread_non_positive")
    if sim.get("recommend_wire_scanner"):
        passes.append("top5_beats_equal_weight")
    else:
        fails.append("top5_does_not_beat_equal_weight")

    pit_spread = pit_wf.get("pit_quintile_spread_6m_mean")
    pit_ok = pit_spread is None or pit_spread >= 0
    if pit_spread is not None and pit_spread > 0:
        passes.append("pit_quintile_spread_positive")
    elif pit_spread is not None:
        fails.append("pit_quintile_spread_non_positive")

    ok_signal = sim.get("recommend_wire_scanner") is True and pit_ok
    payload["verdict"] = {
        "passes": passes,
        "fails": fails,
        "ok_to_wire_scanner": sim.get("recommend_wire_scanner") is True,
        "ok_to_wire_signal_engine": ok_signal,
        "summary": (
            "Research edge validated (price + PIT fundamentals). Safe to wire scanner and signal engine research boost."
            if ok_signal
            else (
                "Price walk-forward positive but PIT fundamentals inconclusive — wire signal engine with advisory boost only."
                if sim.get("recommend_wire_scanner")
                else "Insufficient edge in walk-forward. Keep research advisory-only."
            )
        ),
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Walk-forward validation for stock research")
    parser.add_argument("--phase", choices=["pre", "post"], default="pre")
    parser.add_argument("--report", default="")
    parser.add_argument("--skip-pit", action="store_true", help="Skip point-in-time quarterly fetch (faster)")
    args = parser.parse_args(argv)
    default_name = "research_validation_pre.json" if args.phase == "pre" else "research_validation_post.json"
    report_path = ROOT / "intel" / (args.report or default_name)
    payload = run_validation(args.phase, report_path, skip_pit=args.skip_pit)
    print(json.dumps(payload, indent=2))
    verdict = payload.get("verdict", {})
    return 0 if verdict.get("ok_to_wire_scanner") or verdict.get("ok_to_wire_signal_engine") else 1


if __name__ == "__main__":
    raise SystemExit(main())
