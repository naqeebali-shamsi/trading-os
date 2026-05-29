#!/usr/bin/env python3
"""Macro lexicon loader — impact keywords, symbol relevance, category rules, sentiment terms."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
LEXICON_FILE = ROOT / "config" / "macro_lexicon.yaml"

_cache: Dict[str, Any] | None = None


def load_lexicon(force: bool = False) -> dict:
    """Load macro_lexicon.yaml with module-level caching."""
    global _cache
    if _cache is not None and not force:
        return _cache
    if not LEXICON_FILE.exists():
        _cache = {}
        return _cache
    with LEXICON_FILE.open("r", encoding="utf-8") as f:
        _cache = yaml.safe_load(f) or {}
    return _cache


def get_impact_keywords() -> Dict[str, float]:
    base = dict(load_lexicon().get("impact_keywords", {}))
    try:
        from cortex.live_policy import load_policy

        for keyword, weight in (load_policy().get("macro_lexicon") or {}).items():
            base[str(keyword)] = float(weight)
    except ImportError:
        pass
    return base


def get_symbol_relevance() -> Dict[str, Dict[str, float]]:
    return dict(load_lexicon().get("symbol_relevance", {}))


def get_category_rules() -> Dict[str, dict]:
    return dict(load_lexicon().get("category_rules", {}))


def get_sentiment_terms() -> Tuple[List[str], List[str]]:
    lex = load_lexicon()
    bullish = list(lex.get("bullish_terms", []))
    bearish = list(lex.get("bearish_terms", []))
    return bullish, bearish
