#!/usr/bin/env python3
"""Macro Event Radar advisory layer.

Classifies fresh headlines/news decisions into grounded macro event regimes and
publishes advisory-only context. It never emits orders and never enables symbols.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from bus import publish, subscribe  # noqa: E402
from cortex.macro_lexicon import get_category_rules  # noqa: E402
from cortex.macro_risk_policy import build_policy_from_radar  # noqa: E402

UPDATE_INTERVAL = int(os.getenv("EVENT_RADAR_INTERVAL", "120"))
NEWS_TOPICS = [
    "cortex.news",
    "market.news",
    "calendar.alert",
    "macro.news.oil",
    "macro.news.tech",
    "macro.news.geopolitics",
    "macro.news.health",
    "macro.news.rates",
]

CATEGORY_RULES = get_category_rules()

ACTION_BY_BIAS = {
    "risk_off": "prefer_defensive_or_hold",
    "inflation_risk": "watch_energy_gold_usd",
    "equity_volatility": "observe_tech_beta_only",
    "rates_volatility": "reduce_size_near_event",
}


def normalize_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(str(v) for v in value.values() if isinstance(v, (str, int, float)))
    if isinstance(value, (list, tuple)):
        return " ".join(normalize_text(v) for v in value)
    return ""


def score_category(text: str, rules: dict) -> Tuple[float, List[str]]:
    lower = text.lower()
    score = 0.0
    hits = []
    for keyword, weight in rules.get("keywords", {}).items():
        if re.search(r"\b" + re.escape(keyword.lower()) + r"\b", lower):
            score += float(weight)
            hits.append(keyword)
    return score, hits


def classify_event(texts: Iterable[str]) -> dict:
    text = "\n".join(t for t in texts if t).strip()
    category_scores = {}
    matched_keywords = {}
    for category, rules in CATEGORY_RULES.items():
        score, hits = score_category(text, rules)
        if score > 0:
            category_scores[category] = round(score, 2)
            matched_keywords[category] = hits

    if not category_scores:
        return {
            "source": "event_radar",
            "advisory_only": True,
            "category": "none",
            "bias": "neutral",
            "severity": "low",
            "confidence": 0.0,
            "candidate_symbols": [],
            "action_hint": "hold_or_ignore",
            "matched_keywords": {},
            "category_scores": {},
        }

    category, top_score = max(category_scores.items(), key=lambda item: item[1])
    confidence = min(0.95, round(top_score / 5.0, 2))
    severity = "high" if top_score >= 4.0 else "medium" if top_score >= 2.0 else "low"
    rules = CATEGORY_RULES[category]
    bias = rules["bias"]
    return {
        "source": "event_radar",
        "advisory_only": True,
        "category": category,
        "bias": bias,
        "severity": severity,
        "confidence": confidence,
        "candidate_symbols": list(rules["candidate_symbols"]),
        "action_hint": ACTION_BY_BIAS.get(bias, "hold_or_ignore"),
        "matched_keywords": matched_keywords,
        "category_scores": category_scores,
    }


def _texts_from_events(events):
    texts = []
    for ev in events:
        payload = ev.get("payload", {})
        if isinstance(payload, dict):
            if "headlines" in payload:
                texts.append(normalize_text(payload.get("headlines")))
            if "reason" in payload:
                texts.append(str(payload.get("reason")))
            texts.append(normalize_text(payload))
        else:
            texts.append(normalize_text(payload))
    return texts


def run_once(since_seq=0):
    topics = ["cortex.decision", *NEWS_TOPICS]
    events = []
    for topic in topics:
        events.extend(subscribe(topic, since_seq=since_seq, limit=25))
    events.sort(key=lambda e: e.get("seq", 0))
    if not events:
        return None, since_seq
    latest_seq = max(e.get("seq", since_seq) for e in events)
    texts = _texts_from_events(events[-25:])
    radar = classify_event(texts)
    radar["ts"] = time.time()
    radar["input_event_count"] = len(events)
    radar["source_topics"] = sorted(set(e.get("topic") for e in events))
    publish("macro.event_radar", radar)
    policy = build_policy_from_radar(radar)
    publish("risk.macro_policy", policy)
    if radar["category"] != "none" and radar["severity"] in {"medium", "high"}:
        publish("macro.event_radar.alert", radar)
    return radar, latest_seq


def run():
    last_seq = 0
    while True:
        try:
            radar, last_seq = run_once(last_seq)
            if radar:
                print(f"[event_radar] {radar['category']} severity={radar['severity']} confidence={radar['confidence']}", flush=True)
        except Exception as exc:
            publish("cortex.fallback", {"layer": "event_radar", "error": str(exc)})
        time.sleep(UPDATE_INTERVAL)


if __name__ == "__main__":
    run()
