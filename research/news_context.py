"""Per-symbol news context for the stock research loop.

Builds a {SYMBOL: {catalyst_score, news_sentiment, source_quality}} map from the
recent raw headline cache so research packets can reflect catalysts and tone.
There is no dedicated per-stock sentiment engine in the system, so this matches
headlines to tickers/company names and reuses the existing news_orchestrator
lexicon scorers. Symbols with no headline match are intentionally omitted, which
lets derive_packet fall back to its neutral baseline.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

try:
    from paths import repo_root

    _ROOT = repo_root()
except Exception:  # pragma: no cover
    _ROOT = Path(__file__).resolve().parent.parent

RAW_HEADLINES = _ROOT / "intel" / "raw_headlines.jsonl"
DEFAULT_LOOKBACK_SEC = 172800.0  # 48h

# Company-name aliases for liquid names where the ticker alone rarely appears in
# free-text headlines. Kept small and explicit; extend as coverage grows.
COMPANY_ALIASES: Dict[str, List[str]] = {
    "AAPL": ["APPLE"],
    "MSFT": ["MICROSOFT"],
    "NVDA": ["NVIDIA"],
    "GOOGL": ["GOOGLE", "ALPHABET"],
    "GOOG": ["GOOGLE", "ALPHABET"],
    "AMZN": ["AMAZON"],
    "META": ["META", "FACEBOOK"],
    "TSLA": ["TESLA"],
    "NFLX": ["NETFLIX"],
    "AMD": ["AMD"],
    "AVGO": ["BROADCOM"],
    "INTC": ["INTEL"],
    "ORCL": ["ORACLE"],
    "CRM": ["SALESFORCE"],
    "ADBE": ["ADOBE"],
    "UBER": ["UBER"],
    "PLTR": ["PALANTIR"],
    "MU": ["MICRON"],
    "QCOM": ["QUALCOMM"],
    "CSCO": ["CISCO"],
    "BABA": ["ALIBABA"],
    "TSM": ["TSMC", "TAIWAN SEMICONDUCTOR"],
    "JPM": ["JPMORGAN"],
    "BAC": ["BANK OF AMERICA"],
    "GS": ["GOLDMAN SACHS"],
    "WFC": ["WELLS FARGO"],
    "PYPL": ["PAYPAL"],
    "COIN": ["COINBASE"],
    "UNH": ["UNITEDHEALTH"],
    "LLY": ["ELI LILLY"],
    "PFE": ["PFIZER"],
    "MRNA": ["MODERNA"],
    "XOM": ["EXXON"],
    "CVX": ["CHEVRON"],
    "BA": ["BOEING"],
    "WMT": ["WALMART"],
    "DIS": ["DISNEY"],
    "KO": ["COCA-COLA"],
    "PEP": ["PEPSI", "PEPSICO"],
}

# Bare tickers that are also common English words; matching them as standalone
# tokens in free-text headlines produces false positives, so we rely on their
# company-name aliases instead of the raw ticker.
NOISE_TICKERS = {
    "ALL", "ARM", "ANY", "ARE", "BE", "BIG", "CAT", "COST", "DD", "EAT",
    "FAST", "GE", "GOOD", "HD", "IT", "KEY", "LOW", "MA", "ON", "OR", "PG",
    "SO", "TM", "V", "WELL", "C", "F", "T",
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def load_recent_headlines(
    path: Path = RAW_HEADLINES,
    *,
    lookback_sec: float = DEFAULT_LOOKBACK_SEC,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Read recent, non-empty headlines from the raw headline cache."""
    if not Path(path).exists():
        return []
    reference = float(now) if now is not None else time.time()
    cutoff = reference - lookback_sec
    rows: List[Dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        fetched = row.get("fetched_ts")
        if fetched is not None:
            try:
                if float(fetched) < cutoff:
                    continue
            except (TypeError, ValueError):
                pass
        rows.append(row)
    return rows


def _aliases_for(symbol: str, extra_aliases: Optional[Mapping[str, Iterable[str]]]) -> List[str]:
    sym = symbol.upper()
    tokens = set()
    # The bare ticker is only a safe headline token when it is not a common word.
    if sym not in NOISE_TICKERS:
        tokens.add(sym)
    tokens.update(a.upper() for a in COMPANY_ALIASES.get(sym, []))
    if extra_aliases and sym in extra_aliases:
        tokens.update(str(a).upper() for a in (extra_aliases[sym] or []))
    # Tickers shorter than 2 chars are too noisy to match in free text.
    return [t for t in tokens if len(t) >= 2]


def _compile_patterns(tokens: Iterable[str]) -> List[re.Pattern]:
    return [re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE) for token in tokens]


def _signed_sentiment(result: Mapping[str, Any]) -> float:
    direction = result.get("direction")
    score = float(result.get("score") or 0.0)
    if direction == "bullish":
        return score
    if direction == "bearish":
        return -score
    return 0.0


def _default_scorers() -> tuple[Callable[[str], float], Callable[[str], float]]:
    """Reuse the orchestrator lexicon scorers when available, else neutral stubs."""
    try:
        from cortex.news_orchestrator import quick_sentiment, weighted_impact_score

        return (lambda text: _signed_sentiment(quick_sentiment(text)), weighted_impact_score)
    except Exception:  # pragma: no cover - cortex optional at import time
        return (lambda text: 0.0, lambda text: 0.0)


def build_news_context(
    symbols: Iterable[str],
    *,
    headlines: Optional[List[Mapping[str, Any]]] = None,
    headlines_path: Path = RAW_HEADLINES,
    aliases_by_symbol: Optional[Mapping[str, Iterable[str]]] = None,
    lookback_sec: float = DEFAULT_LOOKBACK_SEC,
    now: Optional[float] = None,
    sentiment_fn: Optional[Callable[[str], float]] = None,
    impact_fn: Optional[Callable[[str], float]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Map each symbol with matching headlines to news context for derive_packet.

    Returned values per symbol: catalyst_score (0..1), news_sentiment (-1..1),
    source_quality (0..1). Symbols without a headline match are omitted.
    """
    if headlines is None:
        headlines = load_recent_headlines(headlines_path, lookback_sec=lookback_sec, now=now)
    if not headlines:
        return {}

    if sentiment_fn is None or impact_fn is None:
        default_sentiment, default_impact = _default_scorers()
        sentiment_fn = sentiment_fn or default_sentiment
        impact_fn = impact_fn or default_impact

    titles = [str(h.get("title") or "") for h in headlines]

    context: Dict[str, Dict[str, Any]] = {}
    for symbol in symbols:
        sym = str(symbol or "").upper()
        if not sym:
            continue
        patterns = _compile_patterns(_aliases_for(sym, aliases_by_symbol))
        matched = [title for title in titles if any(p.search(title) for p in patterns)]
        if not matched:
            continue

        sentiments = [sentiment_fn(title) for title in matched]
        impacts = [impact_fn(title) for title in matched]
        news_sentiment = _clamp(sum(sentiments) / len(sentiments), -1.0, 1.0)
        catalyst_score = _clamp(max(impacts) if impacts else 0.0)
        # More corroborating headlines -> more confidence in the signal.
        source_quality = _clamp(0.5 + 0.1 * len(matched))

        context[sym] = {
            "catalyst_score": round(catalyst_score, 4),
            "news_sentiment": round(news_sentiment, 4),
            "source_quality": round(source_quality, 4),
            "headline_matches": len(matched),
        }
    return context
