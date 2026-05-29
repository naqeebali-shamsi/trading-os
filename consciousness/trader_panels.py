"""Trader-facing dashboard summaries with plain-language labels."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent

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
    return {
        "available": True,
        "updated_ts": snapshot.get("ts") or snapshot.get("generated_ts"),
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

    return {
        "available": True,
        "ready_count": ready_count,
        "total_shown": len(rows),
        "rows": rows,
        "message": f"{ready_count} of {len(rows)} symbols ready to trade",
    }


def _safe_panel(name: str, builder, fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return builder()
    except Exception as exc:
        out = dict(fallback)
        out["message"] = f"{out.get('message', name)} ({exc})"
        return out


def build_trader_panels(
    events: Iterable[dict],
    *,
    preflight: Optional[Mapping[str, Any]] = None,
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
            lambda: portfolio_summary(refresh=False),
            {"available": False, "message": "Portfolio metrics unavailable"},
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
