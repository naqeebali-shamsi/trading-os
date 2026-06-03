"""
autonome/intelligence/discovery.py  v2.1
Discovery Engine: News-driven thematic stock discovery.

Capabilities:
1. Scans web news for catalyst themes using RSS + NewsAPI
2. Matches themes to supply-chain maps (supply_chain_maps.json)
3. Applies corruption/geopolitical heuristics for edge in emerging markets
4. Discovers NEW stocks outside the default watchlist
5. Ranks candidates by catalyst strength + chain proximity + political nexus
6. Outputs: briefing.json + watchlist additions for playbook.md
"""
from __future__ import annotations

import os
import sys
import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set
from pathlib import Path

import requests
import yaml

try:
    import feedparser
except ImportError:
    feedparser = None  # type: ignore[assignment]

log = logging.getLogger("discovery")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


@dataclass
class CatalystEvent:
    headline: str
    source: str
    timestamp: str
    theme: Optional[str] = None
    countries: Optional[List[str]] = None
    sentiment: str = "neutral"
    entities: Optional[List[str]] = None


@dataclass
class DiscoveryCandidate:
    symbol: str
    name: str
    catalyst: str
    theme: str
    chain_position: str
    political_nexus: Optional[str] = None
    corruption_confidence: float = 0.0
    news_score: float = 0.0
    composite_score: float = 0.0
    thesis: str = ""
    exchange: Optional[str] = None
    is_new: bool = True


class DiscoveryEngine:
    """
    Multi-source stock discovery using thematic supply-chain mapping.
    """

    def __init__(self):
        cfg_path = ROOT / "config" / "settings.yaml"
        sec_path = ROOT / "config" / "secrets.yaml"

        with open(cfg_path) as f:
            self.cfg = yaml.safe_load(f)
        with open(sec_path) as f:
            self.secrets = yaml.safe_load(f)

        intel = self.cfg.get("intelligence") or {}
        self.news_api_key = (self.secrets.get("newsapi") or {}).get("api_key", "")
        self.openrouter_key = (self.secrets.get("openrouter") or {}).get("api_key", "")
        self.enable_llm = intel.get("discovery_llm", True)
        self.max_candidates = intel.get("discovery_max_candidates", 20)

        # Load supply chain maps
        sc_path = Path(__file__).resolve().parent / "supply_chain_maps.json"
        if sc_path.exists():
            with open(sc_path) as f:
                self.supply_chains = json.load(f).get("themes", {})
        else:
            self.supply_chains = {}

        # Known watchlist
        self.current_symbols = set(s.upper() for s in self.cfg["data"]["symbols"])

        # Discovery state
        self.seen_headlines: Set[str] = set()
        self.out_briefing = ROOT / "data" / "discovery_briefing.json"
        self.out_memo = ROOT / "data" / "discovery_memo.md"
        self.db_path = ROOT / "data" / "journal.sqlite"

    # ── news sources ─────────────────────────────────────────────────────────

    def fetch_newsapi(self, queries: List[str], per_query: int = 10) -> List[CatalystEvent]:
        if not self.news_api_key:
            log.warning("No NEWS_API key")
            return []

        events = []
        for query in queries:
            try:
                url = "https://newsapi.org/v2/everything"
                params = {
                    "q": query,
                    "sortBy": "publishedAt",
                    "language": "en",
                    "pageSize": per_query,
                    "from": (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d"),
                    "apiKey": self.news_api_key,
                }
                r = requests.get(url, params=params, timeout=20)
                r.raise_for_status()
                data = r.json()
                for a in data.get("articles", []):
                    headline = a["title"]
                    if headline in self.seen_headlines:
                        continue
                    self.seen_headlines.add(headline)
                    events.append(CatalystEvent(
                        headline=headline,
                        source=a["source"]["name"],
                        timestamp=a["publishedAt"],
                        sentiment=self._headline_sentiment(headline),
                        entities=self._extract_entities(headline + " " + (a.get("description") or "")),
                    ))
            except Exception as e:
                log.error("NewsAPI query '%s' failed: %s", query, e)
        return events

    def fetch_rss(self, feeds: List[str]) -> List[CatalystEvent]:
        if feedparser is None:
            return []
        events = []
        for feed_url in feeds:
            try:
                parsed = feedparser.parse(feed_url)
                for entry in parsed.entries[:15]:
                    headline = entry.get("title", "")
                    if headline in self.seen_headlines:
                        continue
                    self.seen_headlines.add(headline)
                    published = entry.get("published", datetime.now(timezone.utc).isoformat())
                    events.append(CatalystEvent(
                        headline=headline,
                        source=parsed.feed.get("title", feed_url),
                        timestamp=published,
                        sentiment=self._headline_sentiment(headline),
                        entities=self._extract_entities(headline + " " + entry.get("summary", "")),
                    ))
            except Exception as e:
                log.error("RSS feed '%s' failed: %s", feed_url, e)
        return events

    # ── text analysis ────────────────────────────────────────────────────────

    def _headline_sentiment(self, text: str) -> str:
        bull = ["approve", "surge", "boom", "rally", "growth", "expand", "invest",
                "fdi", "infrastructure", "tender", "contract", "deal", "partnership", "billions"]
        bear = ["ban", "probe", "crash", "collapse", "scandal", "fine", "investigation",
                "sell", "delay", "cancel", "halt", "closure"]
        text_l = text.lower()
        b_score = sum(1 for w in bull if w in text_l)
        s_score = sum(1 for w in bear if w in text_l)
        if b_score > s_score:
            return "bullish"
        elif s_score > b_score:
            return "bearish"
        return "neutral"

    def _extract_entities(self, text: str) -> List[str]:
        found = []
        text_l = text.lower()

        countries = {
            "india": "india", "china": "china", "us ": "usa", "united states": "usa",
            "japan": "japan", "germany": "germany", "saudi": "saudi", "uae": "uae",
            "brazil": "brazil", "vietnam": "vietnam", "taiwan": "taiwan"
        }
        for k, v in countries.items():
            if k in text_l:
                found.append(v)

        themes = {
            "data center": "data_center", "datacenter": "data_center",
            "semiconductor": "semiconductor", "chip": "semiconductor",
            "ai infrastructure": "ai_infra",
            "battery": "battery", "ev ": "ev",
            "defense": "defense", "solar": "solar", "lithium": "lithium",
            "fdi": "fdi", "nuclear": "nuclear",
            "tariff": "tariff", "trade war": "trade_war",
        }
        for k, v in themes.items():
            if k in text_l:
                found.append(v)

        movers = ["adani", "ambani", "gadkari", "bjp", "modi", "trump", "biden"]
        for m in movers:
            if m in text_l:
                found.append(f"mover_{m}")

        return list(set(found))

    def _match_theme(self, event: CatalystEvent) -> Optional[str]:
        entities = set(e.lower() for e in (event.entities or []))
        headline_l = event.headline.lower()

        if any(e in entities for e in ["india", "fdi", "data_center"]):
            if "india" in entities and ("data_center" in entities or "datacenter" in headline_l):
                return "india_data_center_fdi"

        if "india" in entities and "semiconductor" in entities:
            return "india_semiconductor_fab"

        if any(e in entities for e in ["usa", "ai_infra", "data_center"]):
            if any(w in headline_l for w in ["ai", "datacenter", "power", "nuclear"]):
                return "us_ai_infrastructure"

        if any(w in headline_l for w in ["pentagon", "defense", "military", "jadc2", "drone"]):
            return "us_defense_ai"

        if any(e in entities for e in ["battery", "ev", "lithium"]):
            return "global_battery_supply_chain"

        return None

    # ── supply chain scoring ─────────────────────────────────────────────────

    def _candidates_from_theme(self, theme: str, event: CatalystEvent) -> List[DiscoveryCandidate]:
        if theme not in self.supply_chains:
            return []

        theme_data = self.supply_chains[theme]
        candidates = []

        for tier_name, tier_key in [("primary", "primary_beneficiaries"), ("secondary", "secondary_beneficiaries")]:
            for sym in theme_data.get(tier_key, []):
                if sym == "NA":
                    continue
                name = sym
                chain_pos = tier_name
                found_chain = "general"
                for chain_name, chain_data in theme_data.get("supply_chain", {}).items():
                    if sym in chain_data.get("symbols", {}):
                        found_chain = chain_name
                        name = chain_data["symbols"][sym].get("link", sym)
                        break

                is_new = sym.upper() not in self.current_symbols

                political = None
                corruption_conf = 0.0
                corrupt = theme_data.get("corruption_nexus", [])
                for mover in corrupt:
                    if mover in event.headline.lower():
                        political = mover
                        corruption_conf = 0.85
                        break

                thesis_parts = [f"{theme} catalyst"]
                if political:
                    thesis_parts.append(f"political nexus: {political}")
                if is_new:
                    thesis_parts.append("NEW discovery")

                candidates.append(DiscoveryCandidate(
                    symbol=sym,
                    name=name,
                    catalyst=event.headline[:200],
                    theme=theme,
                    chain_position=found_chain,
                    political_nexus=political,
                    corruption_confidence=corruption_conf,
                    news_score=0.8 if tier_name == "primary" else 0.6,
                    thesis=" | ".join(thesis_parts),
                    is_new=is_new,
                ))

        return candidates

    # ── LLM deep analysis for unknown themes ────────────────────────────────

    def llm_discover(self, events: List[CatalystEvent]) -> List[DiscoveryCandidate]:
        if not self.enable_llm or not self.openrouter_key:
            return []

        unexplained = [e for e in events if not self._match_theme(e)]
        if not unexplained:
            return []

        headlines = "\n".join([f"- {e.headline} [{e.sentiment}]" for e in unexplained[:10]])

        prompt = f"""You are a thematic stock analyst. Given these news headlines, identify 3-5 tradable themes and the BEST stock tickers to play them.

## Headlines
{headlines}

## Your Task
For each identified theme, provide:
- Theme name
- Key tickers (with exchange suffixes like .NS for India, no suffix for US)
- Brief thesis (1 sentence each)
- Bullish/bearish/neutral sentiment

Respond ONLY as valid JSON:
{{
  "themes": [
    {{
      "theme": "theme_name",
      "tickers": ["SYMBOL1", "SYMBOL2.NS"],
      "thesis": "what the trade is",
      "sentiment": "bullish"
    }}
  ]
}}
If no clear actionable themes, return empty themes array."""

        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 800,
        }

        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
                if content.endswith("```"):
                    content = content[:-3].strip()
            elif content.startswith("```"):
                content = content[3:]
                if content.endswith("```"):
                    content = content[:-3].strip()

            parsed = json.loads(content)
            candidates = []
            for t in parsed.get("themes", []):
                for sym in t.get("tickers", []):
                    is_new = sym.upper() not in self.current_symbols
                    candidates.append(DiscoveryCandidate(
                        symbol=sym,
                        name=sym,
                        catalyst=unexplained[0].headline if unexplained else "news_driven",
                        theme=t["theme"],
                        chain_position="llm_inferred",
                        news_score=0.7 if t.get("sentiment") == "bullish" else 0.5,
                        thesis=t.get("thesis", "LLM inferred theme"),
                        is_new=is_new,
                    ))
            return candidates
        except Exception as e:
            log.error("LLM discovery failed: %s", e)
            return []

    # ── ranking ──────────────────────────────────────────────────────────────

    def rank_candidates(self, candidates: List[DiscoveryCandidate]) -> List[DiscoveryCandidate]:
        for c in candidates:
            score = c.news_score * 0.4
            if c.political_nexus:
                score += c.corruption_confidence * 0.3
            if c.is_new:
                score += 0.15
            if c.chain_position == "primary_beneficiaries":
                score += 0.10
            c.composite_score = round(min(1.0, score), 3)

        return sorted(candidates, key=lambda x: (x.composite_score, x.is_new), reverse=True)

    # ── persist ──────────────────────────────────────────────────────────────

    def persist(self, candidates: List[DiscoveryCandidate], events: List[CatalystEvent]) -> None:
        self.out_briefing.parent.mkdir(parents=True, exist_ok=True)

        briefing = {
            "t": datetime.now(timezone.utc).isoformat(),
            "event_count": len(events),
            "candidate_count": len(candidates),
            "themes_detected": list(set(c.theme for c in candidates)),
            "new_discoveries": [asdict(c) for c in candidates if c.is_new][:15],
            "all_candidates": [asdict(c) for c in candidates[:25]],
            "headlines": [asdict(e) for e in events[:20]],
            "recommended_additions": [c.symbol for c in candidates if c.is_new][:10],
        }

        with open(self.out_briefing, "w", encoding="utf-8") as f:
            json.dump(briefing, f, indent=2, default=str)

        lines = [
            f"# Discovery Engine Briefing — {datetime.now(timezone.utc).isoformat()[:19]}",
            f"Themes detected: {len(set(c.theme for c in candidates))}",
            f"Total candidates: {len(candidates)} | New discoveries: {sum(1 for c in candidates if c.is_new)}",
            "",
            "## Top New Discoveries",
        ]
        for c in candidates:
            if c.is_new:
                lines.append(f"- **{c.symbol}** ({c.name}) — {c.theme}")
                lines.append(f"  Score: {c.composite_score:.2f} | {c.thesis}")
                if c.political_nexus:
                    lines.append(f"  Political nexus: {c.political_nexus}")

        lines.extend(["", "## All Candidates (Top 15)"])
        for c in candidates[:15]:
            flag = " [NEW]" if c.is_new else ""
            lines.append(f"- **{c.symbol}**{flag} score={c.composite_score:.2f} | {c.theme} | {c.thesis[:100]}...")

        lines.extend(["", "## Catalyst Headlines"])
        for e in events[:10]:
            lines.append(f"- [{e.sentiment.upper()}] {e.headline} ({e.source})")

        self.out_memo.write_text("\n".join(lines), encoding="utf-8")

    # ── main run ─────────────────────────────────────────────────────────────

    def run(self) -> List[DiscoveryCandidate]:
        log.info("Discovery Engine starting scan...")

        queries = [
            "FDI data center India government approve",
            "India semiconductor fab PLI scheme",
            "AI datacenter power nuclear US",
            "Pentagon AI autonomous weapons budget",
            "EV battery lithium supply chain",
            "tariff trade war semiconductor",
            "infrastructure tender India Adani Ambani",
            "government contract corruption supply chain",
            "Bloomberg NEF renewable energy tender",
        ]
        events = self.fetch_newsapi(queries, per_query=10)
        log.info("NewsAPI: %d events", len(events))

        all_candidates: List[DiscoveryCandidate] = []
        for event in events:
            theme = self._match_theme(event)
            if theme:
                event.theme = theme
                candidates = self._candidates_from_theme(theme, event)
                all_candidates.extend(candidates)
                log.info("Theme match: %s -> %d candidates", theme, len(candidates))

        if self.enable_llm:
            llm_cands = self.llm_discover(events)
            all_candidates.extend(llm_cands)
            log.info("LLM discovery: %d candidates", len(llm_cands))

        seen = set()
        unique = []
        for c in all_candidates:
            if c.symbol not in seen:
                seen.add(c.symbol)
                unique.append(c)

        ranked = self.rank_candidates(unique)
        self.persist(ranked, events)

        new_count = sum(1 for c in ranked if c.is_new)
        log.info("Discovery complete: %d candidates, %d new", len(ranked), new_count)

        return ranked[:self.max_candidates]


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-16s | %(levelname)-7s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    engine = DiscoveryEngine()
    results = engine.run()
    print(f"\nTop 5 discoveries:")
    for r in results[:5]:
        print(f"  {r.symbol} ({r.name}) score={r.composite_score:.2f} [{r.theme}]")
        print(f"    {r.thesis}")


if __name__ == "__main__":
    main()
