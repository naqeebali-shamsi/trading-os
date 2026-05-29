#!/usr/bin/env python3
"""
cortex/news_orchestrator.py — LLM News Scraper + Decision (v2 Citadel)
----------------------------------------------------------------------
FIXES from Adversarial Review (v1):
- [CRITICAL-4] Content-hash deduplication — skips if headline set unchanged
- [CRITICAL-5] Persistent "last_successful_fetch" — alerts if all feeds stale >30min
- [HIGH-4] Weighted keyword scoring (compound keywords score higher)
- [HIGH-5] Per-symbol relevance scoring (weighted by base/quote currency)
- [HIGH-6] Retry with exponential backoff on LLM calls; fallback to fast mode
- [MEDIUM-4] Persistent news cache to intel/news_cache.jsonl (rotated daily)
- [MEDIUM-5] Parallel RSS fetch via ThreadPoolExecutor
"""
import json, os, time, sys, urllib.request, re, hashlib, html
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

try:
    import feedparser  # type: ignore
except Exception:  # pragma: no cover - deploy hosts may not have optional dev deps yet
    feedparser = None

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, subscribe  # noqa
from cortex.llm_client import complete_json  # noqa
from cortex.macro_lexicon import get_impact_keywords, get_sentiment_terms, get_symbol_relevance  # noqa
from cortex.news_macro_gate import annotate_decision  # noqa

MODE = os.getenv("NEWS_ORCHESTRATOR_MODE", "fast").lower()
NEWS_LLM_PROVIDER = os.getenv("NEWS_ORCHESTRATOR_LLM_PROVIDER") or os.getenv("TRADING_OS_LLM_PROVIDER")
NEWS_LLM_MODEL = os.getenv("NEWS_ORCHESTRATOR_MODEL")
UPDATE_INTERVAL = int(os.getenv("NEWS_ORCHESTRATOR_INTERVAL", "300"))
MAX_FEED_AGE_SEC = 1800  # 30 min before critical alert

RSS_FEEDS = [
    {"name": "forexlive", "url": "https://www.forexlive.com/feed/news", "tags": ["fx", "macro"]},
    {"name": "marketwatch", "url": "https://feeds.marketwatch.com/marketwatch/topstories/", "tags": ["stocks", "macro"]},
    {"name": "bbc_business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "tags": ["macro", "geopolitics"]},
    {"name": "investing", "url": "https://www.investing.com/rss/news.rss", "tags": ["macro", "commodities"]},
    {"name": "google_oil", "url": "https://news.google.com/rss/search?q=oil%20OR%20OPEC%20OR%20WTI%20when:1d&hl=en-US&gl=US&ceid=US:en", "tags": ["oil"]},
    {"name": "google_tech", "url": "https://news.google.com/rss/search?q=NVDA%20OR%20Nvidia%20OR%20semiconductor%20OR%20tech%20stocks%20when:1d&hl=en-US&gl=US&ceid=US:en", "tags": ["tech"]},
    {"name": "google_geopolitics", "url": "https://news.google.com/rss/search?q=geopolitics%20OR%20sanctions%20OR%20war%20OR%20ceasefire%20when:1d&hl=en-US&gl=US&ceid=US:en", "tags": ["geopolitics"]},
    {"name": "google_health", "url": "https://news.google.com/rss/search?q=hantavirus%20OR%20outbreak%20OR%20WHO%20health%20emergency%20when:7d&hl=en-US&gl=US&ceid=US:en", "tags": ["health"]},
]

IMPACT_KEYWORDS = get_impact_keywords()
SYMBOL_RELEVANCE = get_symbol_relevance()
BULLISH_TERMS, BEARISH_TERMS = get_sentiment_terms()

STATE = {
    "last_fetch": 0,
    "last_successful_fetch": 0,
    "headlines": [],
    "last_hash": "",
    "sources": {},
}

NEWS_CACHE = ROOT / "intel" / "news_cache.jsonl"
RAW_HEADLINE_CACHE = ROOT / "intel" / "raw_headlines.jsonl"
NEWS_CACHE.parent.mkdir(parents=True, exist_ok=True)

ROUTE_KEYWORDS = {
    "oil": ["oil", "crude", "brent", "wti", "opec", "tanker", "hormuz", "refinery"],
    "tech": ["nasdaq", "nvidia", "nvda", "semiconductor", "chip", "ai", "tech stocks", "earnings", "guidance"],
    "geopolitics": ["war", "sanctions", "missile", "ceasefire", "invasion", "geopolitics", "red sea", "attack"],
    "health": ["hantavirus", "virus", "outbreak", "pandemic", "who", "quarantine", "health emergency"],
    "rates": ["fed", "fomc", "cpi", "inflation", "interest rate", "rate cut", "rate hike", "yield", "powell"],
}


def feed_name(feed):
    return feed.get("name") if isinstance(feed, dict) else str(feed)


def feed_url(feed):
    return feed.get("url") if isinstance(feed, dict) else str(feed)


def feed_tags(feed):
    return list(feed.get("tags", [])) if isinstance(feed, dict) else []


def _cache_headlines(headlines, decision):
    """Append headlines to daily cache."""
    try:
        with open(NEWS_CACHE, "a") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "headline_count": len(headlines),
                "hash": STATE["last_hash"],
                "decision": decision,
                "source_health": source_health_snapshot(),
            }) + "\n")
    except Exception:
        pass


def _cache_raw_headlines(headlines):
    try:
        with open(RAW_HEADLINE_CACHE, "a") as f:
            for item in headlines:
                f.write(json.dumps(normalize_headline(item)) + "\n")
    except Exception:
        pass


def _clean_text(value) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_headline(item):
    if isinstance(item, dict):
        return {
            "title": _clean_text(item.get("title", "")),
            "published": item.get("published") or item.get("pubDate") or "",
            "source": item.get("source", "unknown"),
            "url": item.get("url", ""),
            "tags": list(item.get("tags", []) or []),
            "fetched_ts": float(item.get("fetched_ts") or time.time()),
        }
    title = item[0] if isinstance(item, (tuple, list)) and item else str(item)
    published = item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else ""
    return {"title": _clean_text(title), "published": published, "source": "unknown", "url": "", "tags": [], "fetched_ts": time.time()}


def _local_name(tag: str) -> str:
    return str(tag).split("}", 1)[-1].lower()


def _child_text(node, *names) -> str:
    wanted = {name.lower() for name in names}
    for child in list(node):
        if _local_name(child.tag) in wanted:
            return _clean_text("".join(child.itertext()))
    return ""


def _child_link(node) -> str:
    for child in list(node):
        if _local_name(child.tag) == "link":
            return _clean_text(child.attrib.get("href") or "".join(child.itertext()))
    return ""


def _headline_from_entry(title, published, source, url, tags, fetched_ts, link=""):
    title = _clean_text(title)
    if not title:
        return None
    return {
        "title": title,
        "published": _clean_text(published),
        "source": source,
        "url": _clean_text(link) or url,
        "tags": tags,
        "fetched_ts": fetched_ts,
    }


def parse_feed_entries(raw_feed, feed, limit=15) -> list:
    """Parse RSS/Atom with feedparser when available, otherwise stdlib XML.

    The previous regex parser was brittle for real feeds that use CDATA,
    escaped HTML, RDF, Atom entries, or omit pubDate. This adapter keeps the
    ingestion contract stable while allowing deploy checks to pass on hosts
    that have not installed optional dev dependencies yet.
    """
    name = feed_name(feed)
    url = feed_url(feed)
    tags = feed_tags(feed)
    fetched_ts = time.time()
    headlines = []

    if feedparser is not None:
        parsed = feedparser.parse(raw_feed)
        for entry in parsed.entries[:limit]:
            item = _headline_from_entry(
                entry.get("title", ""),
                entry.get("published") or entry.get("updated") or entry.get("created") or "",
                name,
                url,
                tags,
                fetched_ts,
                entry.get("link", ""),
            )
            if item:
                headlines.append(item)
        return headlines

    try:
        root = ET.fromstring(raw_feed)
    except Exception:
        return []
    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    for node in entries[:limit]:
        item = _headline_from_entry(
            _child_text(node, "title"),
            _child_text(node, "pubDate", "published", "updated", "dc:date"),
            name,
            url,
            tags,
            fetched_ts,
            _child_link(node),
        )
        if item:
            headlines.append(item)
    return headlines


def headline_title(item):
    return normalize_headline(item)["title"]


def headline_text(headlines):
    return " ".join(headline_title(h).lower() for h in headlines)


def update_source_health(name, *, ok, count=0, error=None):
    now = time.time()
    state = STATE.setdefault("sources", {}).setdefault(name, {"successes": 0, "failures": 0, "last_success": 0, "last_error": None, "last_count": 0})
    if ok:
        state["successes"] += 1
        state["last_success"] = now
        state["last_error"] = None
    else:
        state["failures"] += 1
        state["last_error"] = str(error or "unknown_error")[:240]
    state["last_fetch"] = now
    state["last_count"] = int(count or 0)
    return state


def source_health_snapshot(now=None):
    now = now or time.time()
    snapshot = {}
    for name, state in STATE.get("sources", {}).items():
        last_success = float(state.get("last_success") or 0)
        snapshot[name] = {
            **state,
            "stale_sec": None if not last_success else round(now - last_success, 1),
            "healthy": bool(last_success and now - last_success <= MAX_FEED_AGE_SEC),
        }
    return snapshot


def fetch_rss_single(feed) -> list:
    name = feed_name(feed)
    url = feed_url(feed)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        headlines = parse_feed_entries(raw, feed)
        update_source_health(name, ok=True, count=len(headlines))
        return headlines
    except Exception as e:
        update_source_health(name, ok=False, error=e)
        publish("cortex.fallback", {"layer": "news_orchestrator", "action": "rss_fetch_failed", "url": url, "error": str(e)})
        return []


def fetch_all_rss() -> list:
    """Fetch all RSS feeds in parallel."""
    all_hl = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(fetch_rss_single, feed) for feed in RSS_FEEDS]
        for f in futures:
            try:
                all_hl.extend(f.result(timeout=20))
            except Exception:
                pass
    return all_hl


def _content_hash(headlines: list) -> str:
    """Deterministic hash of all headlines (sorted, lowercase)."""
    texts = sorted(headline_title(h).lower() for h in headlines)
    return hashlib.sha256("|".join(texts).encode()).hexdigest()[:16]


def route_headlines(headlines):
    routed = {key: [] for key in ROUTE_KEYWORDS}
    for item in headlines:
        normalized = normalize_headline(item)
        text = (normalized["title"] + " " + " ".join(normalized.get("tags", []))).lower()
        for route, keywords in ROUTE_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                routed[route].append(normalized)
    return {route: items for route, items in routed.items() if items}


def publish_routed_headlines(headlines, content_hash):
    routed = route_headlines(headlines)
    for route, items in routed.items():
        payload = {
            "source": "news_orchestrator",
            "route": route,
            "content_hash": content_hash,
            "headline_count": len(items),
            "headlines": items[:20],
            "advisory_only": True,
        }
        publish(f"macro.news.{route}", payload)
    publish("macro.news.source_health", {"source": "news_orchestrator", "sources": source_health_snapshot(), "advisory_only": True})
    return routed


def weighted_impact_score(text: str) -> float:
    text_lower = text.lower()
    score = 0.0
    for kw, weight in IMPACT_KEYWORDS.items():
        if kw in text_lower:
            score += weight
    return min(1.0, score / 4.0)


def symbol_relevance_score(text: str, symbol: str) -> float:
    """How relevant is a block of text to a specific symbol."""
    if symbol not in SYMBOL_RELEVANCE:
        return 0.0
    text_lower = text.lower()
    score = 0.0
    for kw, weight in SYMBOL_RELEVANCE[symbol].items():
        if kw in text_lower:
            score += weight
    return min(1.0, score / 3.0)


def quick_sentiment(text: str) -> dict:
    text_lower = text.lower()
    bull = sum(1 for t in BULLISH_TERMS if t in text_lower)
    bear = sum(1 for t in BEARISH_TERMS if t in text_lower)
    if bull > bear:
        return {"direction": "bullish", "score": round(min(1.0, 0.5 + (bull - bear) * 0.1), 2)}
    elif bear > bull:
        return {"direction": "bearish", "score": round(min(1.0, 0.5 + (bear - bull) * 0.1), 2)}
    return {"direction": "neutral", "score": 0.0}


def enrich_with_llm(headlines: list, retries: int = 2) -> dict:
    prompt = (
        "You are a senior FX/macro analyst. Assess the market risk from these headlines.\n"
        "Respond ONLY with JSON: {\"assessment\":\"risk_off|risk_on|neutral\","
        "\"confidence\":0.0-1.0,\"affected_symbols\":[\"EURUSD\",\"XAUUSD\"],"
        "\"reason\":\"one sentence\"}\n\nHeadlines:\n"
        + "\n".join(f"- {h[0]}" for h in headlines[:10])
    )
    delay = 1.0
    for attempt in range(retries + 1):
        result = complete_json(
            prompt,
            system="You are a senior FX/macro analyst. Return valid JSON only. Do not recommend direct order execution.",
            provider=NEWS_LLM_PROVIDER,
            model=NEWS_LLM_MODEL,
            temperature=0.2,
            max_tokens=200,
        )
        publish("cortex.llm_call", {"layer": "news_orchestrator", "ok": result.ok, "provider": result.provider, "model": result.model, "error": result.error, "latency_ms": result.latency_ms})
        if result.ok:
            return result.parsed or {"error": "empty_parsed", "assessment": "neutral", "confidence": 0}
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
            continue
        publish("cortex.fallback", {"layer": "news_orchestrator", "action": "llm_failed", "error": result.error})
        return {"error": result.error, "assessment": "neutral", "confidence": 0}


def build_decision(headlines: list, fast_mode: bool) -> dict:
    texts = headline_text(headlines)

    # Per-symbol relevance
    impacted = {}
    for sym in SYMBOL_RELEVANCE:
        rel = symbol_relevance_score(texts, sym)
        if rel > 0.2:
            impacted[sym] = round(rel, 2)

    if fast_mode:
        sent = quick_sentiment(texts)
        impact = weighted_impact_score(texts)
        recommendation = "proceed"
        if impact > 0.55:
            recommendation = "reduce_size"
        # Extreme headline density may trigger symbol-scoped halts via annotate_decision().
        if impact > 0.92:
            recommendation = "halt_new"

        return {
            "source": "news_orchestrator",
            "mode": "fast",
            "assessment": sent["direction"] if impact > 0.3 else "neutral",
            "confidence": round(sent["score"] * impact, 2),
            "impact_score": round(impact, 3),
            "affected_symbols": impacted,
            "recommendation": recommendation,
            "headline_count": len(headlines),
            "top_keywords": [kw for kw, _ in sorted(
                IMPACT_KEYWORDS.items(), key=lambda x: x[1], reverse=True) if kw in texts][:5],
        }
    else:
        llm = enrich_with_llm(headlines)
        # If LLM failed, fallback to fast
        if "error" in llm:
            return build_decision(headlines, fast_mode=True)
        rec_map = {"risk_off": "reduce_size", "risk_on": "proceed", "neutral": "proceed"}
        return {
            "source": "news_orchestrator",
            "mode": "full",
            **llm,
            "recommendation": rec_map.get(llm.get("assessment", "neutral"), "proceed"),
            "headline_count": len(headlines),
        }


def run_cycle():
    all_headlines = fetch_all_rss()

    # [FIX CRITICAL-5] Check feed staleness
    now = time.time()
    if all_headlines:
        STATE["last_successful_fetch"] = now
    else:
        stale = now - STATE.get("last_successful_fetch", 0)
        if stale > MAX_FEED_AGE_SEC:
            publish("alert.routed", {
                "severity": "critical",
                "source": "news_orchestrator",
                "message": f"All news feeds stale for {int(stale/60)}min. Trading without news context.",
            })
        return  # nothing to process

    # [FIX CRITICAL-4] Deduplication by content hash
    content_hash = _content_hash(all_headlines)
    if content_hash == STATE["last_hash"]:
        return  # no new content
    STATE["last_hash"] = content_hash

    STATE["headlines"] = all_headlines
    decision = build_decision(all_headlines, fast_mode=(MODE != "full"))
    decision["content_hash"] = content_hash
    decision = annotate_decision(decision, now=now)

    publish("cortex.decision", decision)
    _cache_raw_headlines(all_headlines)
    publish_routed_headlines(all_headlines, content_hash)
    _cache_headlines(all_headlines, decision)

    if decision.get("confidence", 0) > 0.75:
        publish("alert.routed", {
            "severity": "high",
            "source": "news_orchestrator",
            "message": f"News impact {decision['assessment']} on {', '.join(decision.get('affected_symbols', {}).keys())}",
            "decision": decision,
        })


def run():
    print(f"[news_orchestrator] Mode={MODE} interval={UPDATE_INTERVAL}s")
    while True:
        try:
            run_cycle()
        except Exception as e:
            publish("cortex.fallback", {"layer": "news_orchestrator", "action": "crash", "error": str(e)})
        time.sleep(UPDATE_INTERVAL)


if __name__ == "__main__":
    run()
