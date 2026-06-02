"""Trader-facing dashboard summaries with plain-language labels."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

# Live broker refresh can be slow or stall against a remote bridge. Cap the
# wait so /api/state always responds; a slow refresh keeps running in the
# background and warms the cache for the next poll.
PORTFOLIO_PANEL_TIMEOUT_SEC = 1.2
GATE_REPORT_PATH = ROOT / "intel" / "edge_gate_report.json"
STRATEGY_SEARCH_REPORT_PATH = ROOT / "intel" / "strategy_search_report.json"

STAGE_LABELS = {
    "pattern_scan": "Pattern scan",
    "strategy_selection": "Strategy selection",
    "market_snapshot": "Live price check",
    "quote_freshness": "Quote freshness",
    "instrument_validation": "Order validation",
    "confidence": "Confidence threshold",
    "macro_gate": "News & macro gate",
    "research_gate": "Research filter",
    "publish_signal": "Signal approved",
}

REASON_LABELS = {
    "below_min_confidence": "Setup confidence too low",
    "warming_up": "Warming up. Need more candles",
    "no_patterns": "No clear pattern",
    "timeframe_disabled": "Timeframe disabled",
    "symbol_disabled": "Symbol disabled",
    "macro_gate": "Blocked by news or macro risk",
    "research_gate_blocked": "Stock below research tier",
    "research_row_missing": "No research data for symbol",
    "no_fresh_tick": "No fresh price quote",
    "signal_emitted": "Signal passed all checks",
    "direct pattern intents disabled": "Pattern orders turned off",
    "stock direct intents disabled (FX/metals only)": "Stock orders turned off",
    "AI brain is HOLD": "AI recommends waiting",
}

TIER_LABELS = {
    "multibagger_candidate": "Multibagger candidate",
    "high_conviction": "High conviction",
    "accumulate": "Accumulate",
    "watch": "Watch",
}

READINESS_LABELS = {
    "READY": "Ready",
    "DISABLED": "Off",
    "BLOCKED_NO_CHART": "No chart",
    "BLOCKED_SESSION_CLOSED": "Market closed",
    "BLOCKED_SPREAD_TOO_WIDE": "Spread wide",
    "BLOCKED_QUOTE_STALE": "Quote stale",
    "BLOCKED_TICK_MISSING": "No price",
    "BLOCKED_BROKER_TRADE_DISABLED": "Broker trade off",
}

RECOMMENDATION_LABELS = {
    "proceed": "Normal trading",
    "reduce_size": "Reduce size",
    "halt_new": "Pause new trades",
    "halt_symbols": "Pause affected symbols",
    "hold": "Hold. No new trades",
    "block": "Blocked",
}


def human_stage(stage: Optional[str]) -> str:
    key = str(stage or "").strip()
    return STAGE_LABELS.get(key, key.replace("_", " ").title() or "Unknown")


def human_reason(reason: Optional[str]) -> str:
    key = str(reason or "").strip()
    if not key:
        return "Unknown"
    if key in REASON_LABELS:
        return REASON_LABELS[key]
    if key.startswith("BLOCKED_"):
        return READINESS_LABELS.get(key, key.replace("BLOCKED_", "").replace("_", " ").title())
    return key.replace("_", " ")


def human_tier(tier: Optional[str]) -> str:
    return TIER_LABELS.get(str(tier or ""), str(tier or "Watch"))


def human_recommendation(rec: Optional[str]) -> str:
    return RECOMMENDATION_LABELS.get(str(rec or "").lower(), str(rec or "Unknown"))


def _latest_by_topic(events: Iterable[dict], topic: str) -> Optional[dict]:
    for event in events:
        if event.get("topic") == topic:
            return event.get("payload") or {}
    return None


def _collect_topics(events: Iterable[dict], topics: set[str], limit: int = 20) -> List[dict]:
    rows = []
    for event in events:
        if event.get("topic") not in topics:
            continue
        rows.append(
            {
                "topic": event.get("topic"),
                "ts": event.get("ts"),
                "payload": event.get("payload") or {},
            }
        )
        if len(rows) >= limit:
            break
    return rows


def research_watchlist(limit: int = 10) -> Dict[str, Any]:
    try:
        from research.snapshot import load_snapshot, rank_research_rows
    except ImportError:
        return {"available": False, "message": "Research module unavailable", "picks": []}

    snapshot = load_snapshot()
    if not snapshot.get("available"):
        return {"available": False, "message": "Research scan has not run yet. Check back after the daily update.", "picks": []}

    rows: List[dict] = []
    seen = set()
    for bucket in ("multibagger_candidates", "top_picks"):
        for row in snapshot.get(bucket) or []:
            sym = str(row.get("symbol") or "").upper()
            if sym and sym not in seen:
                seen.add(sym)
                rows.append(row)
    ranked = rank_research_rows(rows)[:limit]
    picks = []
    for row in ranked:
        tags = row.get("thesis_tags") or []
        picks.append(
            {
                "symbol": row.get("symbol"),
                "tier": row.get("tier"),
                "tier_label": human_tier(row.get("tier")),
                "confidence": row.get("confidence"),
                "composite_score": row.get("composite_score"),
                "thesis": row.get("thesis") or "; ".join(tags) or "Screening in progress",
                "thesis_tags": tags,
            }
        )
    updated_ts = snapshot.get("ts") or snapshot.get("generated_ts")
    stale_after_sec = 86400 * 1.25
    try:
        from research.config import load_config

        stale_after_sec = float((load_config().get("run_interval_sec") or 86400)) * 1.25
    except Exception:
        pass
    age_sec = None
    is_stale = False
    if updated_ts is not None:
        try:
            age_sec = max(0.0, time.time() - float(updated_ts))
            is_stale = age_sec > stale_after_sec
        except (TypeError, ValueError):
            age_sec = None

    return {
        "available": True,
        "updated_ts": updated_ts,
        "age_sec": age_sec,
        "stale": is_stale,
        "stale_after_sec": stale_after_sec,
        "run_id": snapshot.get("run_id"),
        "picks": picks,
        "message": f"{len(picks)} names on today's watchlist" if picks else "No names cleared the confidence threshold",
    }


def signal_evaluation_drilldown(events: Iterable[dict], *, limit: int = 25) -> Dict[str, Any]:
    eval_rows = []
    for event in events:
        if event.get("topic") != "market.signal.evaluation":
            continue
        payload = event.get("payload") or {}
        eval_rows.append(
            {
                "ts": event.get("ts"),
                "symbol": payload.get("symbol"),
                "timeframe": payload.get("timeframe"),
                "status": payload.get("status"),
                "stage": payload.get("stage"),
                "stage_label": human_stage(payload.get("stage")),
                "reason": payload.get("reason"),
                "reason_label": human_reason(payload.get("reason")),
                "confidence": payload.get("confidence"),
                "min_confidence": payload.get("min_confidence"),
                "patterns": payload.get("patterns") or [],
                "research": payload.get("research") or payload.get("intent", {}).get("research"),
            }
        )
        if len(eval_rows) >= limit:
            break

    latest_by_symbol: Dict[str, dict] = {}
    for row in eval_rows:
        sym = str(row.get("symbol") or "").upper()
        if sym:
            latest_by_symbol[sym] = row

    blocked = [r for r in eval_rows if r.get("status") == "blocked"]
    passed = [r for r in eval_rows if r.get("status") == "passed"]

    headline = "No recent signal checks"
    if passed:
        last = passed[0]
        headline = f"Latest pass: {last.get('symbol')} cleared at {last.get('stage_label')}"
    elif blocked:
        last = blocked[0]
        headline = f"Latest block: {last.get('symbol')}. {last.get('reason_label')}"

    return {
        "headline": headline,
        "total": len(eval_rows),
        "blocked_count": len(blocked),
        "passed_count": len(passed),
        "latest_by_symbol": list(latest_by_symbol.values())[:12],
        "recent": eval_rows[:limit],
    }


def macro_news_impact(events: Iterable[dict]) -> Dict[str, Any]:
    decision = _latest_by_topic(events, "cortex.decision") or {}
    radar = _latest_by_topic(events, "macro.event_radar") or {}
    policy = _latest_by_topic(events, "risk.macro_policy") or {}

    headlines: List[dict] = []
    news_topics = {
        "macro.news.oil",
        "macro.news.tech",
        "macro.news.geopolitics",
        "macro.news.health",
        "macro.news.rates",
    }
    for row in _collect_topics(events, news_topics, limit=8):
        payload = row.get("payload") or {}
        headlines.append(
            {
                "route": payload.get("route") or row.get("topic", "").split(".")[-1],
                "title": payload.get("title") or payload.get("headline") or payload.get("summary") or "News item",
                "ts": row.get("ts"),
            }
        )

    affected = decision.get("affected_symbols") or {}
    if isinstance(affected, dict):
        affected_list = [
            {"symbol": sym, "relevance": rel}
            for sym, rel in sorted(affected.items(), key=lambda x: float(x[1] or 0), reverse=True)[:10]
        ]
    else:
        affected_list = [{"symbol": str(s), "relevance": 1.0} for s in (affected or [])[:10]]

    halt_symbols = decision.get("halt_symbols") or []
    recommendation = decision.get("recommendation") or radar.get("recommended_action") or "proceed"

    risk_level = "normal"
    if recommendation in {"halt_new", "halt_symbols", "hold", "block"}:
        risk_level = "elevated"
    elif recommendation == "reduce_size" or (decision.get("impact_score") or 0) > 0.55:
        risk_level = "caution"

    return {
        "risk_level": risk_level,
        "risk_label": {"normal": "Normal", "caution": "Caution", "elevated": "Elevated risk"}[risk_level],
        "recommendation": recommendation,
        "recommendation_label": human_recommendation(recommendation),
        "assessment": decision.get("assessment") or radar.get("primary_bias") or "neutral",
        "confidence": decision.get("confidence") or radar.get("confidence"),
        "impact_score": decision.get("impact_score"),
        "affected_symbols": affected_list,
        "halt_symbols": list(halt_symbols)[:12],
        "headline_count": decision.get("headline_count"),
        "event_radar": {
            "primary_bias": radar.get("primary_bias"),
            "categories": radar.get("category_scores") or radar.get("categories"),
            "blackout_recommended": radar.get("blackout_recommended"),
            "affected_symbols": radar.get("affected_symbols") or [],
        },
        "policy": {
            "size_multiplier": policy.get("size_multiplier"),
            "blackout_recommended": policy.get("blackout_recommended"),
        },
        "headlines": headlines,
        "top_keywords": (decision.get("top_keywords") or [])[:6],
    }


def portfolio_summary(*, refresh: bool = True) -> Dict[str, Any]:
    try:
        from muscle.portfolio_snapshot import build_portfolio_snapshot
    except ImportError:
        return {"available": False, "message": "Portfolio metrics unavailable"}
    return build_portfolio_snapshot(refresh_positions=refresh)


def positions_summary() -> Dict[str, Any]:
    try:
        from muscle import pnl_sync
    except ImportError:
        return {"available": False, "open_count": 0, "floating_pnl": 0.0, "positions": [], "message": "PnL module unavailable"}

    state = pnl_sync.load_state()
    positions_raw = state.get("positions") or {}
    positions = list(positions_raw.values()) if isinstance(positions_raw, dict) else []
    try:
        report = pnl_sync.reconcile_positions(positions, previous=positions_raw if isinstance(positions_raw, dict) else {}, publish_events=False)
        floating = float(report.get("floating_pnl") or 0.0)
    except Exception:
        floating = sum(float(p.get("profit") or 0) for p in positions)

    rows = []
    for pos in positions:
        rows.append(
            {
                "symbol": pos.get("symbol"),
                "side": pos.get("side"),
                "qty": pos.get("qty"),
                "open_price": pos.get("open_price"),
                "current_price": pos.get("current_price"),
                "profit": pos.get("profit"),
                "sl": pos.get("sl"),
                "tp": pos.get("tp"),
            }
        )

    return {
        "available": True,
        "open_count": len(rows),
        "floating_pnl": round(floating, 2),
        "positions": rows,
        "message": "No open positions" if not rows else f"{len(rows)} open position{'s' if len(rows) != 1 else ''}",
    }


def readiness_table(preflight: Optional[Mapping[str, Any]] = None, *, enabled_only: bool = True, limit: int = 40) -> Dict[str, Any]:
    if not preflight:
        return {"available": False, "rows": [], "ready_count": 0, "message": "Symbol readiness is not available. Restart the dashboard if this continues."}

    instruments = preflight.get("instruments") or {}
    if not isinstance(instruments, dict):
        return {"available": False, "rows": [], "ready_count": 0, "message": "No instrument data"}

    rows = []
    ready_count = 0
    for symbol, status in sorted(instruments.items()):
        if enabled_only and not status.get("enabled"):
            continue
        result = str(status.get("result") or "")
        ready = bool(status.get("ready"))
        if ready:
            ready_count += 1
        quote_age = status.get("quote_age_sec")
        quote_note = "OK"
        if status.get("quote_skipped"):
            quote_note = "Session closed"
        elif quote_age is not None:
            quote_note = f"{int(quote_age)}s old"
        rows.append(
            {
                "symbol": symbol,
                "asset_class": status.get("asset_class") or "fx",
                "ready": ready,
                "status_label": READINESS_LABELS.get(result, human_reason(result)),
                "session": "Open" if status.get("session_ok") else "Closed",
                "spread": "OK" if status.get("spread_ok") else "Wide",
                "quote": quote_note,
                "chart": "Connected" if status.get("chart_present") else "Missing",
            }
        )
        if len(rows) >= limit:
            break

    stale = bool(preflight.get("stale"))
    message = f"{ready_count} of {len(rows)} symbols ready to trade"
    if stale:
        message = f"{message} (readiness from cached probe — refresh may be in progress)"

    return {
        "available": True,
        "ready_count": ready_count,
        "total_shown": len(rows),
        "rows": rows,
        "stale": stale,
        "message": message,
    }


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


_TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "D": 86400,
    "W1": 604800,
}

_BULLISH_WORDS = {"up", "buy", "bullish", "long"}
_BEARISH_WORDS = {"down", "sell", "bearish", "short"}
_RESTRICTIVE_RECS = {"halt_symbols", "halt_new", "hold", "block", "reduce_size"}


def _timeframe_seconds(timeframe: Any) -> Optional[int]:
    return _TIMEFRAME_SECONDS.get(str(timeframe or "").upper().strip())


def _event_payload(event: Mapping[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _is_forecast_topic(topic: Any) -> bool:
    text = str(topic or "")
    return text == "market.forecast" or text.startswith("market.forecast.")


def _forecast_symbol(payload: Mapping[str, Any], topic: Any = "") -> Optional[str]:
    for key in ("symbol", "ticker", "instrument"):
        val = payload.get(key)
        if val:
            return str(val).upper().strip()
    text = str(topic or "")
    if text.startswith("market.forecast."):
        suffix = text.split("market.forecast.", 1)[1].strip()
        if suffix:
            return suffix.split(".")[0].upper()
    return None


def _forecast_timeframe(payload: Mapping[str, Any]) -> Optional[str]:
    tf = payload.get("timeframe") or payload.get("tf")
    if tf:
        return str(tf).upper().strip()
    return None


def _forecast_sort_key(event: Mapping[str, Any], index: int) -> Tuple[float, float, int]:
    payload = _event_payload(event)
    ts = _safe_float(event.get("ts"))
    if ts is None:
        ts = _safe_float(payload.get("ts"))
    seq = _safe_float(event.get("seq"))
    if seq is None:
        seq = 0.0
    if ts is None:
        ts = 0.0
    return (ts, seq, -index)


def _forecast_value(payload: Mapping[str, Any], *keys: str) -> Any:
    nested = payload.get("forecast")
    nested_dict = nested if isinstance(nested, dict) else {}
    for key in keys:
        if payload.get(key) not in (None, ""):
            val = payload.get(key)
            if key in ("predicted_close", "forecast_close") and isinstance(val, list) and val:
                return val[-1]
            return val
        if nested_dict.get(key) not in (None, ""):
            val = nested_dict.get(key)
            if key in ("predicted_close", "forecast_close") and isinstance(val, list) and val:
                return val[-1]
            return val
    return None


def _forecast_staleness(age_sec: Optional[float], timeframe: Any) -> Tuple[str, str]:
    """Classify a forecast by age relative to its timeframe cadence."""
    if age_sec is None:
        return "unknown", "Age unknown"
    bar = _timeframe_seconds(timeframe)
    if bar:
        if age_sec <= bar * 1.5:
            return "fresh", "Fresh"
        if age_sec <= bar * 4:
            return "aging", "Aging"
        return "stale", "Stale"
    if age_sec <= 300:
        return "fresh", "Fresh"
    if age_sec <= 1800:
        return "aging", "Aging"
    return "stale", "Stale"


def _direction_sign(direction: Any) -> int:
    value = str(direction or "").lower().strip()
    if value in _BULLISH_WORDS:
        return 1
    if value in _BEARISH_WORDS:
        return -1
    return 0


def _macro_pressure(macro: Optional[Mapping[str, Any]]) -> int:
    """Net macro/news pressure: +1 risk-on, -1 cautious/risk-off, 0 neutral."""
    if not macro:
        return 0
    if macro.get("halted"):
        return -1
    rec = str(macro.get("recommendation") or "").lower().strip()
    if rec in _RESTRICTIVE_RECS:
        return -1
    assessment = str(macro.get("assessment") or "").lower()
    if any(word in assessment for word in ("risk_off", "risk off", "bearish", "caution", "stress")):
        return -1
    if any(word in assessment for word in ("risk_on", "risk on", "bullish")):
        return 1
    return 0


def _forecast_macro_conflict(direction: Any, macro: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Flag when the forecast direction disagrees with macro/news pressure."""
    if not macro:
        return {"conflict": False, "label": "No macro context", "severity": "none"}
    if macro.get("halted"):
        return {"conflict": True, "label": "Macro halt on symbol", "severity": "high"}
    sign = _direction_sign(direction)
    pressure = _macro_pressure(macro)
    if sign > 0 and pressure < 0:
        return {"conflict": True, "label": "Bullish forecast vs macro caution", "severity": "medium"}
    if sign < 0 and pressure > 0:
        return {"conflict": True, "label": "Bearish forecast vs risk-on macro", "severity": "medium"}
    if sign != 0 and pressure != 0:
        return {"conflict": False, "label": "Aligned with macro", "severity": "none"}
    return {"conflict": False, "label": "No macro conflict", "severity": "none"}


def _compact_forecast_entry(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "ts": record.get("ts"),
        "direction": record.get("direction"),
        "confidence": record.get("confidence"),
        "predicted_close": record.get("predicted_close"),
    }


def _research_symbol_candidates(symbol: Any) -> List[str]:
    sym = str(symbol or "").upper().strip()
    return [sym] if sym else []


def _research_context_by_symbol() -> Dict[str, Dict[str, Any]]:
    try:
        from research.snapshot import load_snapshot
    except ImportError:
        return {}
    snapshot = load_snapshot()
    if not snapshot.get("available"):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for bucket in ("multibagger_candidates", "top_picks", "accumulate"):
        for row in snapshot.get(bucket) or []:
            sym = str(row.get("symbol") or "").upper()
            if sym and sym not in out:
                out[sym] = {
                    "tier": row.get("tier"),
                    "tier_label": human_tier(row.get("tier")),
                    "confidence": row.get("confidence"),
                    "thesis": row.get("thesis"),
                }
    return out


def _latest_symbol_decision_context(events: Iterable[dict]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for event in events:
        if event.get("topic") != "cortex.decision":
            continue
        payload = event.get("payload") or {}
        affected = payload.get("affected_symbols") or {}
        halt_symbols = {str(s).upper() for s in (payload.get("halt_symbols") or [])}
        rec = payload.get("recommendation")
        rec_label = human_recommendation(rec)
        if isinstance(affected, dict):
            for sym, rel in affected.items():
                s = str(sym).upper()
                out[s] = {
                    "recommendation": rec,
                    "recommendation_label": rec_label,
                    "assessment": payload.get("assessment"),
                    "relevance": rel,
                    "halted": s in halt_symbols,
                }
        for sym in halt_symbols:
            out.setdefault(
                sym,
                {
                    "recommendation": rec or "halt_symbols",
                    "recommendation_label": rec_label,
                    "assessment": payload.get("assessment"),
                    "relevance": 1.0,
                    "halted": True,
                },
            )
    return out


def forecast_thesis_panel(
    events: Iterable[dict],
    *,
    limit: int = 30,
    history_limit: int = 6,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    forecasts: Dict[Tuple[str, str], Dict[str, Any]] = {}
    history_map: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    event_list = [event for event in events if isinstance(event, Mapping)]
    reference_ts = float(now) if now is not None else time.time()
    macro_summary = macro_news_impact(event_list)

    for index, event in enumerate(event_list):
        if not _is_forecast_topic(event.get("topic")):
            continue
        payload = _event_payload(event)
        symbol = _forecast_symbol(payload, event.get("topic"))
        timeframe = _forecast_timeframe(payload)
        if not symbol or not timeframe:
            continue

        key = (symbol, timeframe)
        sort_key = _forecast_sort_key(event, index)
        record = {
            "_sort_key": sort_key,
            "ts": event.get("ts") if event.get("ts") is not None else payload.get("ts"),
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": _forecast_value(payload, "direction", "bias", "signal"),
            "confidence": _forecast_value(payload, "confidence"),
            "model": payload.get("model") or payload.get("model_name") or _forecast_value(payload, "source"),
            "last_close": _forecast_value(payload, "last_close", "close"),
            "predicted_close": _forecast_value(payload, "predicted_close", "forecast_close"),
            "advisory_only": bool(payload.get("advisory_only")),
            "ok": payload.get("ok"),
            "error": payload.get("error"),
        }
        history_map.setdefault(key, []).append(record)

        existing = forecasts.get(key)
        if existing and sort_key <= existing["_sort_key"]:
            continue
        forecasts[key] = record

    research = _research_context_by_symbol()
    decision_context = _latest_symbol_decision_context(event_list)
    rows = sorted(forecasts.values(), key=lambda row: row["_sort_key"], reverse=True)[:limit]
    conflict_count = 0
    stale_count = 0
    for row in rows:
        key = (row["symbol"], row["timeframe"])

        row_ts = _safe_float(row.get("ts"))
        age_sec = round(reference_ts - row_ts, 1) if row_ts is not None else None
        if age_sec is not None and age_sec < 0:
            age_sec = 0.0
        staleness, staleness_label = _forecast_staleness(age_sec, row.get("timeframe"))
        row["age_sec"] = age_sec
        row["staleness"] = staleness
        row["staleness_label"] = staleness_label
        if staleness == "stale":
            stale_count += 1

        for candidate in _research_symbol_candidates(row.get("symbol")):
            if candidate in research:
                row["research"] = research[candidate]
                break
        for candidate in _research_symbol_candidates(row.get("symbol")):
            if candidate in decision_context:
                row["macro_news"] = decision_context[candidate]
                row["macro"] = decision_context[candidate]
                break

        conflict = _forecast_macro_conflict(row.get("direction"), row.get("macro_news"))
        row["macro_conflict"] = conflict
        if conflict.get("conflict"):
            conflict_count += 1

        history = sorted(
            history_map.get(key, []),
            key=lambda item: item["_sort_key"],
            reverse=True,
        )[:history_limit]
        row["history"] = [_compact_forecast_entry(item) for item in history]
        row.pop("_sort_key", None)

    symbols = sorted({row["symbol"] for row in rows})
    if not rows:
        return {
            "available": False,
            "count": 0,
            "symbols": [],
            "rows": [],
            "macro_summary": macro_summary,
            "message": "No recent forecasts in the current window.",
        }

    advisory_values = [row.get("advisory_only") for row in rows]
    return {
        "available": True,
        "count": len(rows),
        "symbols": symbols,
        "latest_ts": rows[0]["ts"] if rows else None,
        "advisory_only": all(value is not False for value in advisory_values),
        "conflict_count": conflict_count,
        "stale_count": stale_count,
        "rows": rows,
        "macro_summary": macro_summary,
        "message": f"{len(rows)} latest forecast thesis row{'s' if len(rows) != 1 else ''}",
    }


def edge_validation_panel(*, path: Path = GATE_REPORT_PATH) -> Dict[str, Any]:
    empty = {
        "available": False,
        "candidate_count": 0,
        "label_count": 0,
        "group_count": 0,
        "promotable_count": 0,
        "groups": [],
        "message": "No edge gate report yet. Run the edge ledger daemon or scripts/edge_ledger_run.py.",
    }
    if not path.exists():
        return empty
    report = _read_json_file(path)
    if not report:
        out = dict(empty)
        out["message"] = "Edge gate report is unreadable."
        return out

    groups = []
    for group in report.get("groups") or []:
        groups.append(
            {
                "symbol": group.get("symbol"),
                "timeframe": group.get("timeframe"),
                "samples": group.get("samples", 0),
                "win_rate": group.get("win_rate"),
                "edge": group.get("edge"),
                "profit_factor": group.get("profit_factor"),
                "promotable": bool(group.get("promotable")),
                "reasons": list(group.get("reasons") or []),
            }
        )
    promotable = int(report.get("promotable_count") or 0)
    group_count = int(report.get("group_count") or len(groups))
    return {
        "available": True,
        "candidate_count": report.get("candidate_count", 0),
        "label_count": report.get("label_count", 0),
        "group_count": group_count,
        "promotable_count": promotable,
        "groups": groups,
        "message": f"{promotable} of {group_count} group(s) clear the promotion gate",
    }


def frontier_search_panel(*, path: Path = STRATEGY_SEARCH_REPORT_PATH) -> Dict[str, Any]:
    empty = {
        "available": False,
        "trials_run": 0,
        "survivor_count": 0,
        "validation_passed_count": 0,
        "symbol": None,
        "timeframe": None,
        "best_survivor": None,
        "survivors": [],
        "rejection_summary": {},
        "protocol": {},
        "message": "No strategy search report yet. Run scripts/run_strategy_search.py or wait for Dream Lab daily cycle.",
    }
    if not path.exists():
        return empty
    report = _read_json_file(path)
    if not report or not report.get("ok"):
        out = dict(empty)
        out["message"] = report.get("error") if report else "Strategy search report is unreadable."
        return out

    best = report.get("best_survivor")
    best_summary = None
    if best:
        spec = best.get("spec") or {}
        val = best.get("validation") or {}
        test = best.get("test") or {}
        best_summary = {
            "strategy_id": spec.get("strategy_id"),
            "family": spec.get("family"),
            "params": spec.get("params") or {},
            "validation_sharpe": val.get("sharpe_proxy"),
            "test_sharpe": test.get("sharpe_proxy"),
            "validation_trades": val.get("trades"),
            "test_trades": test.get("trades"),
        }

    survivors = []
    for row in report.get("survivors") or []:
        spec = row.get("spec") or {}
        survivors.append(
            {
                "strategy_id": spec.get("strategy_id"),
                "family": spec.get("family"),
                "validation_sharpe": (row.get("validation") or {}).get("sharpe_proxy"),
                "test_sharpe": (row.get("test") or {}).get("sharpe_proxy"),
            }
        )

    survivor_count = int(report.get("survivor_count") or 0)
    validation_passed = int(report.get("validation_passed_count") or 0)
    trials = int(report.get("trials_run") or 0)
    return {
        "available": True,
        "ts": report.get("ts"),
        "trials_run": trials,
        "survivor_count": survivor_count,
        "validation_passed_count": validation_passed,
        "symbol": report.get("symbol"),
        "timeframe": report.get("timeframe"),
        "protocol": report.get("protocol") or {},
        "rejection_summary": report.get("rejection_summary") or {},
        "best_survivor": best_summary,
        "survivors": survivors,
        "report_path": str(path),
        "message": (
            f"{survivor_count} survivor(s) from {trials} trials ({validation_passed} passed validation gates)"
            if trials
            else "Strategy search report loaded"
        ),
    }


class _PanelTimeout(Exception):
    """Raised when a panel builder exceeds its time budget."""


# Last successful result per panel name. A slow live dependency (broker
# refresh, readiness probe) should never leave the UI in a skeleton state;
# we serve the most recent good payload while the refresh keeps running in the
# background and warms this cache for the next request.
_PANEL_CACHE: Dict[str, Dict[str, Any]] = {}
_PANEL_CACHE_LOCK = threading.Lock()


def _store_panel_cache(name: str, result: Dict[str, Any]) -> None:
    if isinstance(result, dict) and result.get("available"):
        with _PANEL_CACHE_LOCK:
            _PANEL_CACHE[name] = dict(result)


def _read_panel_cache(name: str) -> Optional[Dict[str, Any]]:
    with _PANEL_CACHE_LOCK:
        cached = _PANEL_CACHE.get(name)
        return dict(cached) if cached is not None else None


def _run_panel_with_timeout(name: str, builder: Callable[[], Dict[str, Any]], timeout: float) -> Dict[str, Any]:
    """Run ``builder`` but give up after ``timeout`` seconds."""
    box: Dict[str, Any] = {}
    done = threading.Event()

    def worker() -> None:
        try:
            value = builder()
            box["value"] = value
            _store_panel_cache(name, value)
        except Exception as exc:  # noqa: BLE001 - surfaced via box["error"]
            box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=worker, name=f"panel-{name}", daemon=True)
    thread.start()
    if not done.wait(timeout):
        raise _PanelTimeout(name)
    if "error" in box:
        raise box["error"]
    return box["value"]


def _safe_panel(
    name: str,
    builder: Callable[[], Dict[str, Any]],
    fallback: Dict[str, Any],
    *,
    timeout: Optional[float] = None,
    cache: bool = False,
) -> Dict[str, Any]:
    try:
        if timeout is None:
            result = builder()
            if cache:
                _store_panel_cache(name, result)
            return result
        return _run_panel_with_timeout(name, builder, timeout)
    except _PanelTimeout:
        cached = _read_panel_cache(name) if cache else None
        if cached is not None:
            cached["stale"] = True
            base = cached.get("message") or name
            cached["message"] = f"{base} (showing cached data; live refresh is slow)"
            return cached
        out = dict(fallback)
        out["stale"] = True
        out["message"] = f"{out.get('message', name)} (live refresh timed out)"
        return out
    except Exception as exc:
        cached = _read_panel_cache(name) if cache else None
        if cached is not None:
            cached["stale"] = True
            base = cached.get("message") or name
            cached["message"] = f"{base} (showing cached data; refresh failed)"
            return cached
        out = dict(fallback)
        out["message"] = f"{out.get('message', name)} ({exc})"
        return out


def supervisor_layers_panel(
    events: Iterable[dict],
    health: Optional[Mapping[str, Any]] = None,
    *,
    stale_after_sec: float = 45.0,
) -> Dict[str, Any]:
    health = health or {}
    sup = health.get("supervisor") if isinstance(health.get("supervisor"), dict) else {}
    layers = list(sup.get("layers") or [])
    ts = sup.get("ts")
    age_sec = None
    stale = True
    if ts is not None:
        try:
            age_sec = max(0.0, time.time() - float(ts))
            stale = age_sec > stale_after_sec
        except (TypeError, ValueError):
            age_sec = None

    restarts: List[dict] = []
    for event in events:
        if event.get("topic") != "ops.layer.restarted":
            continue
        payload = event.get("payload") or {}
        restarts.append(
            {
                "ts": event.get("ts"),
                "layer": payload.get("layer"),
                "exit_code": payload.get("exit_code"),
                "pid": payload.get("pid"),
            }
        )
        if len(restarts) >= 8:
            break

    if not layers:
        return {
            "available": False,
            "layers": [],
            "restarts": restarts,
            "stale": True,
            "message": "Supervisor layer status not available. Start the stack with kernel/supervisor.py.",
        }

    running = sum(1 for row in layers if row.get("running"))
    down = [row for row in layers if not row.get("running")]
    headline = f"{running} of {len(layers)} layers running"
    if stale:
        headline = f"{headline} (status stale)"
    if down:
        names = ", ".join(str(row.get("layer") or "?") for row in down[:4])
        if len(down) > 4:
            names = f"{names}, +{len(down) - 4} more"
        headline = f"{headline} — down: {names}"

    return {
        "available": True,
        "stale": stale,
        "age_sec": age_sec,
        "supervisor_pid": sup.get("pid"),
        "layer_count": len(layers),
        "running_count": running,
        "all_running": bool(sup.get("all_running")),
        "layers": layers,
        "restarts": restarts,
        "message": headline,
    }


def build_trader_panels(
    events: Iterable[dict],
    *,
    preflight: Optional[Mapping[str, Any]] = None,
    health: Optional[Mapping[str, Any]] = None,
    max_heartbeat_age: float = 30.0,
) -> Dict[str, Any]:
    event_list = list(events)
    return {
        "ts": time.time(),
        "research_watchlist": _safe_panel(
            "research_watchlist",
            research_watchlist,
            {"available": False, "message": "Research watchlist unavailable", "picks": []},
        ),
        "portfolio_pnl": _safe_panel(
            "portfolio_pnl",
            lambda: portfolio_summary(refresh=True),
            {"available": False, "message": "Portfolio metrics unavailable"},
            timeout=PORTFOLIO_PANEL_TIMEOUT_SEC,
            cache=True,
        ),
        "signal_drilldown": _safe_panel(
            "signal_drilldown",
            lambda: signal_evaluation_drilldown(event_list),
            {"available": False, "message": "Signal drilldown unavailable"},
        ),
        "macro_news": _safe_panel(
            "macro_news",
            lambda: macro_news_impact(event_list),
            {"available": False, "message": "Macro news unavailable"},
        ),
        "positions": _safe_panel(
            "positions",
            positions_summary,
            {"available": False, "open_count": 0, "floating_pnl": 0.0, "positions": [], "message": "Positions unavailable"},
        ),
        "readiness": _safe_panel(
            "readiness",
            lambda: readiness_table(preflight),
            {"available": False, "rows": [], "ready_count": 0, "message": "Readiness unavailable"},
        ),
        "dream_lab": _safe_panel(
            "dream_lab",
            lambda: dream_lab_summary(event_list),
            {"available": False, "message": "Dream Lab status unavailable"},
        ),
        "pending_promotions": _safe_panel(
            "pending_promotions",
            pending_promotions_panel,
            {"available": False, "items": [], "message": "Promotion queue unavailable"},
        ),
        "forecast_thesis": _safe_panel(
            "forecast_thesis",
            lambda: forecast_thesis_panel(event_list),
            {"available": False, "rows": [], "message": "Forecast thesis unavailable"},
        ),
        "edge_validation": _safe_panel(
            "edge_validation",
            edge_validation_panel,
            {"available": False, "groups": [], "message": "Edge validation unavailable"},
        ),
        "frontier_search": _safe_panel(
            "frontier_search",
            frontier_search_panel,
            {"available": False, "survivors": [], "message": "Frontier strategy search unavailable"},
        ),
        "supervisor_layers": _safe_panel(
            "supervisor_layers",
            lambda: supervisor_layers_panel(event_list, health),
            {"available": False, "layers": [], "restarts": [], "message": "Supervisor layers unavailable"},
        ),
    }


def dream_lab_summary(events: Iterable[dict]) -> Dict[str, Any]:
    """Summarize Dream Lab scheduler cycles from recent bus events."""
    rd_events = [
        ev for ev in events
        if str(ev.get("topic") or "").startswith("rd.")
    ]
    last_cycle = next(
        (ev for ev in rd_events if ev.get("topic") == "rd.dream.cycle.complete"),
        None,
    )
    last_proposal = next(
        (ev for ev in rd_events if ev.get("topic") == "rd.promotion.proposed"),
        None,
    )
    state = _read_json_file(ROOT / "intel" / "dream_lab_state.json")
    try:
        from cortex.live_policy import policy_summary

        policy = policy_summary()
    except ImportError:
        policy = {}

    return {
        "available": True,
        "last_cycle": {
            "topic": last_cycle.get("topic") if last_cycle else None,
            "ts": last_cycle.get("ts") if last_cycle else None,
            "payload": (last_cycle or {}).get("payload") or {},
        },
        "last_proposal": {
            "ts": last_proposal.get("ts") if last_proposal else None,
            "payload": (last_proposal or {}).get("payload") or {},
        },
        "state": {
            "last_hourly_ts": state.get("last_hourly_ts"),
            "last_six_hour_ts": state.get("last_six_hour_ts"),
            "last_daily_ts": state.get("last_daily_ts"),
        },
        "live_policy": policy,
        "message": "Dream Lab is running in the background" if last_cycle else "Waiting for first Dream Lab cycle",
    }


def pending_promotions_panel(*, limit: int = 20) -> Dict[str, Any]:
    try:
        from rd import promotions
    except ImportError:
        return {"available": False, "items": [], "message": "Dream Lab promotions unavailable"}

    pending = promotions.list_promotions(status="pending", limit=limit)
    items = []
    for row in pending:
        items.append(
            {
                "id": row.get("id"),
                "type": row.get("type"),
                "summary": row.get("summary"),
                "risk": row.get("risk"),
                "agent": row.get("agent"),
                "created_ts": row.get("created_ts"),
                "evidence_keys": sorted((row.get("evidence") or {}).keys()),
            }
        )
    return {
        "available": True,
        "count": len(items),
        "items": items,
        "message": "No pending improvements" if not items else f"{len(items)} improvement(s) awaiting review",
    }


def _read_json_file(path: Path) -> Dict[str, Any]:
    import json

    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
