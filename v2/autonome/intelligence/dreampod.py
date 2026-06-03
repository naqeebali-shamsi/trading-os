"""
autonome/intelligence/dreampod.py  v2.1
DreamPod: Overnight/pre-market analysis engine.
Runs when markets are CLOSED to build strategic positioning for the next session.

Outputs:
- data/dreampod_briefing.json  (signal context for supervisor)
- data/dreampod_memo.md        (human-readable intelligence memo)
- Updates playbook.md with overnight discoveries
"""
from __future__ import annotations

import os
import sys
import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from pathlib import Path

import requests
import yaml

log = logging.getLogger("dreampod")

# Add parent paths
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from autonome.broker.alpaca_client import AlpacaClient
from autonome.data.bars import AlpacaDataFeed, BarStore


@dataclass
class TechnicalProfile:
    symbol: str
    timeframe: str
    trend: str       # bullish | bearish | ranging
    trend_strength: float  # 0.0-1.0
    support_levels: List[float]
    resistance_levels: List[float]
    atr_14: Optional[float] = None
    rsi_14: Optional[float] = None
    volume_trend: str = "normal"  # increasing | decreasing | normal
    key_event: Optional[str] = None


@dataclass
class OvernightBriefing:
    timestamp: str
    market_open_utc: str
    regime: str
    priority_symbols: List[str]
    avoid_symbols: List[str]
    new_candidates: List[Dict[str, Any]]
    technical_profiles: List[TechnicalProfile]
    macro_headlines: List[str]
    playbook_updates: List[str]
    position_recommendations: List[Dict[str, Any]]


class DreamPod:
    """
    Overnight intelligence analyst. Runs at ~4 AM UTC (pre-market).
    Gathers multi-timeframe context, news, earnings, macro.
    Produces briefing consumed by supervisor on market open.
    """

    def __init__(self):
        cfg_path = ROOT / "config" / "settings.yaml"
        sec_path = ROOT / "config" / "secrets.yaml"

        with open(cfg_path) as f:
            self.cfg = yaml.safe_load(f)
        with open(sec_path) as f:
            secrets = yaml.safe_load(f)

        self.mode = self.cfg["system"]["mode"]
        self.client = AlpacaClient(mode=self.mode)
        self.feed = AlpacaDataFeed()
        self.store = BarStore(self.cfg["data"]["symbols"], maxlen=500)

        intel = self.cfg.get("intelligence") or {}
        self.news_api_key = (secrets.get("newsapi") or {}).get("api_key", "")
        self.openrouter_key = (secrets.get("openrouter") or {}).get("api_key", "")
        self.enable_macro_llm = intel.get("dreampod_macro_llm", True)
        self.discovery_enabled = intel.get("discovery_enabled", True)

        self.db_path = ROOT / "data" / "journal.sqlite"
        self.out_briefing = ROOT / "data" / "dreampod_briefing.json"
        self.out_memo = ROOT / "data" / "dreampod_memo.md"
        self.playbook_path = ROOT / "config" / "playbook.md"

    # ── helpers ──────────────────────────────────────────────────────────────

    def _warm_all(self):
        self.feed.warm_store(self.store)

    def _compute_rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        if len(prices) < period + 1:
            return None
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-period:]]
        if not losses or sum(losses) == 0:
            return 100.0
        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)
        rs = avg_gain / avg_loss if avg_loss > 0 else 999
        return 100.0 - (100.0 / (1 + rs))

    def _compute_atr(self, bars: List, period: int = 14) -> Optional[float]:
        if len(bars) < period + 1:
            return None
        trs = []
        for i in range(1, len(bars)):
            b = bars[i]
            prev = bars[i-1]
            tr = max(
                b.high - b.low,
                abs(b.high - prev.close),
                abs(b.low - prev.close)
            )
            trs.append(tr)
        if not trs:
            return None
        return sum(trs[-period:]) / len(trs[-period:])

    def _compute_ema(self, values: List[float], period: int) -> List[float]:
        if len(values) < period:
            return []
        ema = [sum(values[:period]) / period]
        multiplier = 2.0 / (period + 1)
        for v in values[period:]:
            ema.append((v - ema[-1]) * multiplier + ema[-1])
        return ema

    def _detect_trend(self, bars) -> tuple:
        closes = [b.close for b in bars]
        if len(closes) < 21:
            return "unknown", 0.0

        ema_9 = self._compute_ema(closes, 9)
        ema_21 = self._compute_ema(closes, 21)
        if not ema_9 or not ema_21:
            return "unknown", 0.0

        short = ema_9[-1]
        long = ema_21[-1]
        spread = abs(short - long) / long if long > 0 else 0

        if short > long * 1.001:
            strength = min(1.0, spread * 20)
            return "bullish", strength
        elif short < long * 0.999:
            strength = min(1.0, spread * 20)
            return "bearish", strength
        return "ranging", 0.0

    def _find_levels(self, bars) -> tuple:
        """Simple support/resistance from swing highs/lows."""
        if len(bars) < 20:
            return [], []
        highs = [b.high for b in bars[-50:]]
        lows = [b.low for b in bars[-50:]]
        # Cluster them
        def cluster(values, tolerance=0.02):
            vals = sorted(values)
            clusters = []
            current = [vals[0]]
            for v in vals[1:]:
                if abs(v - sum(current)/len(current)) / (sum(current)/len(current)) < tolerance:
                    current.append(v)
                else:
                    clusters.append(sum(current)/len(current))
                    current = [v]
            if current:
                clusters.append(sum(current)/len(current))
            return clusters[-3:]  # top 3

        return cluster(lows), cluster(highs)

    # ── multi-timeframe analysis ─────────────────────────────────────────────

    def analyze_symbol(self, symbol: str) -> TechnicalProfile:
        daily_bars = self.feed.fetch_history(symbol, limit=200)
        for b in daily_bars:
            self.store.ingest(b)

        bars = self.store.history(symbol, 500)
        if not bars:
            return TechnicalProfile(symbol, "1Day", "unknown", 0, [], [])

        trend, strength = self._detect_trend(bars)
        support, resistance = self._find_levels(bars)
        closes = [b.close for b in bars]
        rsi = self._compute_rsi(closes)
        atr = self._compute_atr(bars)

        vol_recent = sum(b.volume for b in bars[-5:]) / 5 if bars else 0
        vol_old = sum(b.volume for b in bars[-15:-5]) / 10 if len(bars) >= 15 else vol_recent
        vol_trend = "increasing" if vol_recent > vol_old * 1.2 else "decreasing" if vol_recent < vol_old * 0.8 else "normal"

        return TechnicalProfile(
            symbol=symbol,
            timeframe="1Day",
            trend=trend,
            trend_strength=strength,
            support_levels=support,
            resistance_levels=resistance,
            atr_14=atr,
            rsi_14=rsi,
            volume_trend=vol_trend,
        )

    # ── news & discovery ─────────────────────────────────────────────────────

    def fetch_news(self, query: str = "stock market earnings", max_results: int = 10) -> List[str]:
        if not self.news_api_key:
            return ["No NEWS_API key configured"]
        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": query,
                "sortBy": "publishedAt",
                "language": "en",
                "pageSize": max_results,
                "apiKey": self.news_api_key,
            }
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            return [f"{a['title']} | {a['source']['name']}" for a in data.get("articles", [])]
        except Exception as e:
            log.error("News fetch failed: %s", e)
            return [f"news_fetch_error: {e}"]

    def macro_llm_briefing(self, headlines: List[str], profiles: List[TechnicalProfile]) -> str:
        if not self.enable_macro_llm or not self.openrouter_key:
            return "LLM macro briefing disabled (no key)"

        tech_summary = "\n".join([
            f"{p.symbol}: {p.trend} (strength={p.trend_strength:.2f}, RSI={p.rsi_14:.1f if p.rsi_14 else 'N/A'})"
            for p in profiles[:10]
        ])

        prompt = f"""You are a macro strategist. Given overnight technical profiles and headlines, write a concise pre-market briefing (5-7 bullet points) covering:
1. Overall market tone (risk-on/off, rotation)
2. Key sectors/themes in play
3. Specific symbols to prioritize or avoid
4. Any geopolitical or macro risks
5. Positioning recommendation for the session

## Overnight Headlines
{chr(10).join(headlines[:8])}

## Technical Summary
{tech_summary}

Write ONLY the briefing bullets. No preamble."""

        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 600,
        }
        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return f"Macro LLM error: {e}"

    # ── portfolio & positioning ──────────────────────────────────────────────

    def _portfolio_snapshot(self) -> Dict[str, Any]:
        try:
            acc = self.client.fetch_account()
            positions = self.client.list_positions()
            return {
                "equity": acc.equity,
                "cash": acc.cash,
                "buying_power": acc.buying_power,
                "open_positions": len(positions),
                "positions": [{"sym": p.symbol, "qty": p.qty, "unrealized": p.unrealized_pl} for p in positions],
                "daytrades": acc.daytrade_count,
            }
        except Exception as e:
            return {"error": str(e)}

    def generate_position_recommendations(self, profiles: List[TechnicalProfile], portfolio: Dict) -> List[Dict]:
        recs = []
        held_symbols = {p["sym"]: p for p in portfolio.get("positions", [])}

        for p in profiles:
            if p.symbol in held_symbols:
                pos = held_symbols[p.symbol]
                # Exit logic
                if p.trend == "bearish" and p.trend_strength > 0.6:
                    recs.append({
                        "symbol": p.symbol,
                        "action": "REDUCE",
                        "reason": f"trend reversal bearish (strength={p.trend_strength:.2f})",
                        "qty_pct": 0.5,
                    })
                elif p.rsi_14 and p.rsi_14 > 75:
                    recs.append({
                        "symbol": p.symbol,
                        "action": "TRIM",
                        "reason": f"overbought RSI={p.rsi_14:.1f}",
                        "qty_pct": 0.3,
                    })
                elif p.rsi_14 and p.rsi_14 < 30 and p.trend == "bullish":
                    recs.append({
                        "symbol": p.symbol,
                        "action": "ADD",
                        "reason": f"oversold pullback in bullish trend RSI={p.rsi_14:.1f}",
                        "qty_pct": 0.3,
                    })
            else:
                # Entry logic
                if p.trend == "bullish" and p.trend_strength > 0.5 and p.volume_trend == "increasing":
                    recs.append({
                        "symbol": p.symbol,
                        "action": "BUILD",
                        "reason": f"strong bullish momentum with volume surge (strength={p.trend_strength:.2f})",
                        "entry_zone": f"near ${p.support_levels[-1]:.2f}" if p.support_levels else "market",
                    })
                elif p.trend == "bearish" and p.trend_strength > 0.6:
                    # Short signals (if broker supports)
                    recs.append({
                        "symbol": p.symbol,
                        "action": "WATCH_SHORT",
                        "reason": f"strong bearish trend, wait for setup",
                    })

        return recs[:20]

    # ── main run ─────────────────────────────────────────────────────────────

    def run(self) -> OvernightBriefing:
        log.info("DreamPod starting overnight analysis...")
        self._warm_all()

        symbols = self.cfg["data"]["symbols"]
        profiles = []
        for sym in symbols:
            try:
                p = self.analyze_symbol(sym)
                profiles.append(p)
                log.info("%s: %s (strength=%.2f, RSI=%s)", sym, p.trend, p.trend_strength,
                         f"{p.rsi_14:.1f}" if p.rsi_14 else "N/A")
            except Exception as e:
                log.error("Analysis failed for %s: %s", sym, e)

        # Macro context
        headlines = self.fetch_news("stock market OR earnings OR fed OR tariff", max_results=15)
        log.info("Fetched %d headlines", len(headlines))

        macro_brief = self.macro_llm_briefing(headlines, profiles)

        # Portfolio
        portfolio = self._portfolio_snapshot()

        # Recommendations
        recs = self.generate_position_recommendations(profiles, portfolio)

        # Determine regime
        bullish_count = sum(1 for p in profiles if p.trend == "bullish")
        bearish_count = sum(1 for p in profiles if p.trend == "bearish")
        total = len(profiles)
        if total > 0:
            if bullish_count / total > 0.6:
                regime = "strong_uptrend"
            elif bullish_count / total > 0.4:
                regime = "uptrend"
            elif bearish_count / total > 0.6:
                regime = "strong_downtrend"
            elif bearish_count / total > 0.4:
                regime = "downtrend"
            else:
                regime = "ranging"
        else:
            regime = "unknown"

        priority_symbols = [p.symbol for p in profiles if p.trend == "bullish" and p.trend_strength > 0.5][:5]
        avoid_symbols = [p.symbol for p in profiles if p.trend == "bearish" and p.trend_strength > 0.5][:5]

        # Build briefing
        now = datetime.now(timezone.utc)
        briefing = OvernightBriefing(
            timestamp=now.isoformat(),
            market_open_utc=(now + timedelta(hours=4)).isoformat(),  # rough estimate
            regime=regime,
            priority_symbols=priority_symbols,
            avoid_symbols=avoid_symbols,
            new_candidates=[],
            technical_profiles=profiles,
            macro_headlines=headlines[:8],
            playbook_updates=[
                f"Regime updated to {regime}",
                f"Bullish symbols: {', '.join(priority_symbols)}",
                f"Bearish symbols: {', '.join(avoid_symbols)}",
            ],
            position_recommendations=recs,
        )

        self._persist(briefing, macro_brief)
        self._update_playbook(briefing)

        log.info("DreamPod complete. Regime=%s | Priority=%s | Avoid=%s",
                 regime, briefing.priority_symbols, briefing.avoid_symbols)

        return briefing

    def _persist(self, briefing: OvernightBriefing, macro_memo: str) -> None:
        self.out_briefing.parent.mkdir(parents=True, exist_ok=True)
        with open(self.out_briefing, "w", encoding="utf-8") as f:
            json.dump(asdict(briefing), f, indent=2, default=str)

        # Write human-readable memo
        lines = [
            "# DreamPod Pre-Market Briefing",
            f"Generated: {briefing.timestamp}",
            f"Regime: **{briefing.regime}**",
            "",
            "## Macro Context",
            macro_memo,
            "",
            "## Priority Symbols",
            ", ".join(briefing.priority_symbols) or "None",
            "",
            "## Avoid Symbols",
            ", ".join(briefing.avoid_symbols) or "None",
            "",
            "## Position Recommendations",
        ]
        for r in briefing.position_recommendations[:15]:
            lines.append(f"- **{r['symbol']}**: {r['action']} — {r['reason']}")

        lines.extend(["", "## Technical Snapshots"])
        for p in briefing.technical_profiles[:10]:
            lines.append(
                f"- {p.symbol}: {p.trend} st={p.trend_strength:.2f} "
                f"RSI={p.rsi_14:.1f if p.rsi_14 else 'N/A'} "
                f"vol={p.volume_trend}"
            )

        self.out_memo.write_text("\n".join(lines), encoding="utf-8")

    def _update_playbook(self, briefing: OvernightBriefing) -> None:
        if not self.playbook_path.exists():
            log.warning("playbook.md not found; skipping update")
            return

        # Find and update the regime line
        content = self.playbook_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        new_lines = []
        in_regime = False
        for line in lines:
            if line.strip().startswith("## Current Market Regime"):
                in_regime = True
                new_lines.append(line)
                new_lines.append(f"- Regime: {briefing.regime}")
                new_lines.append(f"- Last updated: {briefing.timestamp}")
                continue
            if in_regime and line.startswith("## "):
                in_regime = False
            if in_regime:
                continue
            new_lines.append(line)

        # Append priority/avoid if not in watch/avoid list
        new_lines.append(f"\n## DreamPod Auto-Generated {briefing.timestamp[:10]}")
        new_lines.append(f"Priority: {', '.join(briefing.priority_symbols)}")
        new_lines.append(f"Avoid: {', '.join(briefing.avoid_symbols)}")

        self.playbook_path.write_text("\n".join(new_lines), encoding="utf-8")
        log.info("playbook.md updated with DreamPod findings")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-16s | %(levelname)-7s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    pod = DreamPod()
    pod.run()


if __name__ == "__main__":
    main()
