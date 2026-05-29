"""Research packet enrichment + dated memo writer.

A "research packet" is a ranked stock-research row enriched with news context
(catalysts + tone) and a few narrative fields, plus a single blended
``final_score`` used to order conviction. Enrichment is pure and deterministic so
the stock researcher can map ``derive_packet`` over its ranked rows, and
``write_memo`` persists the result as a dated JSON + Markdown memo for humans.

The blend keeps fundamentals (the factor-screen composite) as the dominant
signal and treats news as a bounded tilt; weights are explicit module constants.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

try:
    from paths import repo_root

    _ROOT = repo_root()
except Exception:  # pragma: no cover - paths optional at import time
    _ROOT = Path(__file__).resolve().parent.parent

MEMO_DIR = _ROOT / "intel" / "research_memos"

# Neutral baselines used when a symbol has no matching news context. Sentiment is
# expressed on a -1..1 scale (0 = neutral); the others are 0..1.
NEUTRAL_CATALYST = 0.0
NEUTRAL_SENTIMENT = 0.0
NEUTRAL_SOURCE_QUALITY = 0.5

# final_score blend. Fundamentals dominate; news is a bounded tilt. All component
# values are normalized to 0..1 before weighting, so final_score stays in 0..1.
FINAL_SCORE_WEIGHTS: Dict[str, float] = {
    "composite": 0.65,
    "catalyst": 0.20,
    "sentiment": 0.10,
    "source_quality": 0.05,
}

RATING_BY_TIER: Dict[str, str] = {
    "multibagger_candidate": "STRONG_BUY",
    "high_conviction": "BUY",
    "accumulate": "ACCUMULATE",
    "watch": "WATCH",
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _conviction(confidence: float) -> str:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.60:
        return "medium"
    return "low"


def _news_fields(news: Optional[Mapping[str, Any]]) -> Dict[str, float]:
    """Extract news context with neutral fallbacks, tolerant of missing keys."""
    if not news:
        return {
            "catalyst_score": NEUTRAL_CATALYST,
            "news_sentiment": NEUTRAL_SENTIMENT,
            "source_quality": NEUTRAL_SOURCE_QUALITY,
        }
    return {
        "catalyst_score": _clamp(_as_float(news.get("catalyst_score"), NEUTRAL_CATALYST)),
        "news_sentiment": _clamp(
            _as_float(news.get("news_sentiment"), NEUTRAL_SENTIMENT), -1.0, 1.0
        ),
        "source_quality": _clamp(
            _as_float(news.get("source_quality"), NEUTRAL_SOURCE_QUALITY)
        ),
    }


def _final_score(composite: float, news: Mapping[str, float]) -> float:
    # Map signed sentiment (-1..1) onto 0..1 so neutral tone is a mid-point.
    sentiment_component = _clamp((news["news_sentiment"] + 1.0) / 2.0)
    blended = (
        FINAL_SCORE_WEIGHTS["composite"] * _clamp(composite)
        + FINAL_SCORE_WEIGHTS["catalyst"] * news["catalyst_score"]
        + FINAL_SCORE_WEIGHTS["sentiment"] * sentiment_component
        + FINAL_SCORE_WEIGHTS["source_quality"] * news["source_quality"]
    )
    return round(_clamp(blended), 4)


def _thesis_headline(symbol: str, rating: str, tier: str, thesis: str, news: Mapping[str, float]) -> str:
    base = f"{symbol}: {rating} ({tier})"
    if thesis and thesis != "insufficient_signal":
        base += f" — {thesis.replace('; ', ', ')}"
    if news["catalyst_score"] >= 0.5:
        tone = "bullish" if news["news_sentiment"] > 0.1 else "bearish" if news["news_sentiment"] < -0.1 else "mixed"
        base += f"; active {tone} news catalyst"
    return base


def derive_packet(row: Dict[str, Any], news: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Enrich a ranked research row into a research packet.

    Copies the row, merges news context (neutral defaults when ``news`` is None),
    adds narrative fields, and computes a blended ``final_score``. Pure and
    tolerant of missing keys.
    """
    packet = dict(row or {})
    news_fields = _news_fields(news)

    symbol = str(packet.get("symbol") or "").upper()
    tier = str(packet.get("tier") or "watch")
    confidence = _clamp(_as_float(packet.get("confidence")))
    composite = _clamp(_as_float(packet.get("composite_score")))
    thesis = str(packet.get("thesis") or "")

    rating = RATING_BY_TIER.get(tier, "WATCH")
    conviction = _conviction(confidence)
    final_score = _final_score(composite, news_fields)

    packet.update(news_fields)
    packet["has_news"] = bool(news)
    packet["final_score"] = final_score
    packet["rating"] = rating
    packet["conviction"] = conviction
    packet["thesis_headline"] = _thesis_headline(symbol or str(packet.get("symbol") or ""), rating, tier, thesis, news_fields)
    return packet


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _render_markdown(packets: List[Dict[str, Any]], *, date_str: str) -> str:
    lines = [
        f"# Research Memo — {date_str}",
        "",
        "_Advisory only. Quant factor screen + news tilt. Not investment advice._",
        "",
        f"Total packets: {len(packets)}",
        "",
        "| Rank | Symbol | Rating | Tier | Final | Confidence | Conviction | Thesis |",
        "| ---: | :--- | :--- | :--- | ---: | ---: | :--- | :--- |",
    ]
    for i, p in enumerate(packets, start=1):
        lines.append(
            "| {rank} | {sym} | {rating} | {tier} | {final:.4f} | {conf} | {conv} | {thesis} |".format(
                rank=i,
                sym=p.get("symbol", ""),
                rating=p.get("rating", ""),
                tier=p.get("tier", ""),
                final=_as_float(p.get("final_score")),
                conf=p.get("confidence", ""),
                conv=p.get("conviction", ""),
                thesis=str(p.get("thesis_headline") or "").replace("|", "/"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_memo(packets: List[Dict[str, Any]], *, memo_dir: Path = MEMO_DIR) -> Dict[str, str]:
    """Write a dated research memo as JSON + Markdown, ranked best-first.

    Returns the written paths, e.g. ``{"json": ..., "md": ...}``.
    """
    ordered = sorted(
        list(packets or []),
        key=lambda p: _as_float(p.get("final_score")),
        reverse=True,
    )
    date_str = _today()
    memo_dir = Path(memo_dir)
    memo_dir.mkdir(parents=True, exist_ok=True)

    json_path = memo_dir / f"research_memo_{date_str}.json"
    md_path = memo_dir / f"research_memo_{date_str}.md"

    json_path.write_text(
        json.dumps(
            {"date": date_str, "count": len(ordered), "packets": ordered},
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
    md_path.write_text(_render_markdown(ordered, date_str=date_str), encoding="utf-8")

    return {"json": str(json_path), "md": str(md_path)}
