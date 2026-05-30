#!/usr/bin/env python3
"""Instrument Intelligence Registry.

Deny-by-default metadata and validation for symbols/instruments traded by the OS.
This module is deliberately dependency-light and returns structured validation
results so safety-critical callers can publish exact block reasons.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

try:
    from cortex.strategy_registry import normalize_strategy_id, validate_strategy_id
except ImportError:  # pragma: no cover - direct script execution path
    from strategy_registry import normalize_strategy_id, validate_strategy_id

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "instruments.yaml"
OVERLAY_DIR = ROOT / "config" / "instruments.d"
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
FOREX_24_5_SESSIONS = ("forex_24_5", "forex_24_5_friday", "forex_24_5_sunday")


@dataclass
class ValidationResult:
    ok: bool
    reason: str = "ok"
    symbol: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "symbol": self.symbol, **self.details}


class InstrumentRegistry:
    def __init__(self, config_path: Path = CONFIG_FILE):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.symbols: Dict[str, Dict[str, Any]] = self.config.get("symbols", {}) or {}
        self.sessions: Dict[str, Dict[str, Any]] = self.config.get("sessions", {}) or {}
        self.asset_classes: Dict[str, Dict[str, Any]] = self.config.get("asset_classes", {}) or {}
        self.defaults: Dict[str, Any] = self.config.get("defaults", {}) or {}
        self._alias_map = self._build_alias_map()
        self._validate_session_definitions()

    def _validate_session_definitions(self) -> None:
        for session_name, session in self.sessions.items():
            tz = str(session.get("timezone") or "UTC").strip().upper()
            if tz not in {"UTC", ""}:
                raise ValueError(
                    f"Session '{session_name}' uses timezone={session.get('timezone')!r}; "
                    "only UTC windows are supported by session_ok today."
                )
            days = session.get("days") or DAY_NAMES[:5]
            invalid_days = [day for day in days if day not in DAY_NAMES]
            if invalid_days:
                raise ValueError(f"Session '{session_name}' has invalid days: {invalid_days}")
            for idx, window in enumerate(session.get("windows") or []):
                if not isinstance(window, (list, tuple)) or len(window) != 2:
                    raise ValueError(f"Session '{session_name}' window[{idx}] must be [start, end]")
                for label in window:
                    self._parse_hhmm(str(label), session_name=session_name, window_index=idx)

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            base: Dict[str, Any] = {"symbols": {}, "sessions": {}, "asset_classes": {}, "defaults": {}}
        else:
            with self.config_path.open("r", encoding="utf-8") as f:
                base = yaml.safe_load(f) or {}
        base.setdefault("symbols", {})
        base.setdefault("sessions", {})
        base.setdefault("asset_classes", {})
        base.setdefault("defaults", {})
        if OVERLAY_DIR.is_dir():
            for overlay_path in sorted(OVERLAY_DIR.glob("*.yaml")):
                with overlay_path.open("r", encoding="utf-8") as f:
                    overlay = yaml.safe_load(f) or {}
                for section in ("sessions", "asset_classes", "defaults"):
                    if overlay.get(section):
                        base[section].update(overlay[section])
                if overlay.get("symbols"):
                    self._merge_overlay_symbols(base["symbols"], overlay["symbols"])
        return base

    @staticmethod
    def _merge_overlay_symbols(base_symbols: Dict[str, Any], overlay_symbols: Dict[str, Any]) -> None:
        """Layer overlay symbol metadata onto base symbols per-symbol.

        Overlays (e.g. the advisory research watchlist) carry partial metadata and
        must not clobber a base symbol's canonical fields. New symbols are added
        as-is (disabled by default via the enabled fallback); existing symbols are
        deep-merged while preserving the base's canonical ``enabled`` flag so an
        overlay can never silently enable or disable a tradable instrument.
        """
        for symbol, overlay_cfg in (overlay_symbols or {}).items():
            if not isinstance(overlay_cfg, dict):
                continue
            existing = base_symbols.get(symbol)
            if not isinstance(existing, dict):
                base_symbols[symbol] = overlay_cfg
                continue
            merged = {**existing, **overlay_cfg}
            if "enabled" in existing:
                merged["enabled"] = existing["enabled"]
            base_symbols[symbol] = merged

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """Conservative normalization for matching only, not execution."""
        if not symbol:
            return ""
        s = str(symbol).strip()
        s = re.sub(r"\.(micro|mini|pro)$", "", s, flags=re.I)
        s = re.sub(r"[.#]$", "", s)
        s = re.sub(r"[mM]$", "", s)
        return s.upper()

    def _build_alias_map(self) -> Dict[str, str]:
        aliases: Dict[str, str] = {}
        for canonical, cfg in self.symbols.items():
            canonical_u = canonical.upper()
            candidates = {canonical, canonical_u, cfg.get("broker_symbol", canonical)}
            candidates.update(cfg.get("aliases", []) or [])
            for candidate in candidates:
                aliases[str(candidate).upper()] = canonical_u
                aliases[self.normalize_symbol(str(candidate))] = canonical_u
        return aliases

    def resolve_symbol(self, symbol: str) -> Optional[str]:
        if not symbol:
            return None
        raw = str(symbol).strip().upper()
        if raw in self.symbols:
            return raw
        return self._alias_map.get(raw) or self._alias_map.get(self.normalize_symbol(raw))

    def get(self, symbol: str) -> Optional[Dict[str, Any]]:
        canonical = self.resolve_symbol(symbol)
        if not canonical:
            return None
        cfg = dict(self.symbols.get(canonical, {}))
        cfg["symbol"] = canonical
        cfg.setdefault("broker_symbol", canonical)
        if not cfg.get("strategies"):
            asset = self.asset_classes.get(str(cfg.get("asset_class") or ""), {}) or {}
            defaults = asset.get("default_strategies") or []
            if defaults:
                cfg["strategies"] = list(defaults)
        return cfg

    def symbols_matching(
        self,
        *,
        enabled_only: bool = False,
        asset_class: Optional[str] = None,
        region: Optional[str] = None,
    ) -> List[str]:
        matches: List[str] = []
        for symbol, cfg in self.symbols.items():
            if enabled_only and not cfg.get("enabled", False):
                continue
            if asset_class and str(cfg.get("asset_class") or "") != asset_class:
                continue
            if region and str(cfg.get("region") or "").upper() != str(region).upper():
                continue
            matches.append(symbol)
        return sorted(matches)

    def enabled_symbols(self, asset_class: Optional[str] = None, region: Optional[str] = None) -> List[str]:
        return self.symbols_matching(enabled_only=True, asset_class=asset_class, region=region)

    def boot_required(self, symbol: str) -> bool:
        """Whether an enabled symbol must be ready before supervisor LIVE boot."""
        cfg = self.get(symbol) or {}
        if "boot_required" in cfg:
            return bool(cfg.get("boot_required"))
        asset = self.asset_classes.get(str(cfg.get("asset_class") or ""), {}) or {}
        if "boot_required" in asset:
            return bool(asset.get("boot_required"))
        readiness = self.defaults.get("readiness") or {}
        return bool(readiness.get("default_boot_required", False))

    def enabled_chart_labels(self) -> List[str]:
        labels: List[str] = []
        for symbol in self.enabled_symbols():
            cfg = self.get(symbol) or {}
            broker = str(cfg.get("broker_symbol") or symbol).strip().upper()
            labels.append(f"chart_{broker}")
        return sorted(set(labels))

    def all_symbols(self) -> List[str]:
        return list(self.symbols.keys())

    def max_fresh_quote_sec(self, symbol: str) -> float:
        """Max allowed MT5 quote age (``quote_age_sec``) before treating tick as stale."""
        cfg = self.get(symbol) or {}
        asset = self.asset_classes.get(str(cfg.get("asset_class") or ""), {}) or {}
        for source in (cfg, asset, self.defaults):
            value = self._num(source.get("require_fresh_tick_sec"))
            if value is not None and value > 0:
                return float(value)
        return 30.0

    def tick_quote_ok(self, symbol: str, tick: Optional[Dict[str, Any]] = None) -> ValidationResult:
        """Validate broker quote timestamp freshness (distinct from IPC file updates)."""
        canonical = self.resolve_symbol(symbol)
        cfg = self.get(symbol)
        if not cfg or not canonical:
            return ValidationResult(False, "unknown_symbol", symbol)
        tick = tick or {}
        max_age = self.max_fresh_quote_sec(canonical)
        asset_class = str(cfg.get("asset_class") or "")
        quote_age = tick.get("quote_age_sec")
        if quote_age is None:
            if asset_class == "stock_cfd":
                return ValidationResult(False, "quote_time_missing", canonical, {"asset_class": asset_class})
            return ValidationResult(True, symbol=canonical, details={"note": "quote_time_not_provided", "max_quote_age_sec": max_age})
        quote_age_f = float(quote_age)
        if quote_age_f > max_age:
            return ValidationResult(False, "quote_stale", canonical, {
                "quote_age_sec": round(quote_age_f, 1),
                "max_quote_age_sec": max_age,
                "asset_class": asset_class,
            })
        return ValidationResult(True, symbol=canonical, details={
            "quote_age_sec": round(quote_age_f, 1),
            "max_quote_age_sec": max_age,
            "delayed_feed": quote_age_f > 60.0,
        })

    def is_enabled(self, symbol: str) -> bool:
        cfg = self.get(symbol)
        return bool(cfg and cfg.get("enabled", False))

    def resolve_broker_symbol(self, symbol: str) -> Optional[str]:
        cfg = self.get(symbol)
        return cfg.get("broker_symbol") if cfg else None

    def strategy_allowed(self, symbol: str, strategy_id: Optional[str]) -> ValidationResult:
        cfg = self.get(symbol)
        canonical = self.resolve_symbol(symbol)
        if not cfg:
            return ValidationResult(False, "unknown_symbol", symbol)
        if not strategy_id:
            return ValidationResult(True, symbol=canonical)
        ok, canonical_strategy_id, reason = validate_strategy_id(strategy_id)
        if not ok:
            return ValidationResult(False, reason, canonical, {"strategy_id": canonical_strategy_id or strategy_id})
        allowed = cfg.get("strategies", []) or []
        allowed = [normalize_strategy_id(s) for s in allowed]
        if canonical_strategy_id not in allowed:
            return ValidationResult(False, "strategy_not_allowed", canonical, {"strategy_id": canonical_strategy_id, "allowed": allowed})
        return ValidationResult(True, symbol=canonical, details={"strategy_id": canonical_strategy_id})

    def validate_symbol(self, symbol: str, require_enabled: bool = True) -> ValidationResult:
        canonical = self.resolve_symbol(symbol)
        if not canonical:
            return ValidationResult(False, "unknown_symbol", symbol)
        cfg = self.get(canonical)
        if require_enabled and not cfg.get("enabled", False):
            return ValidationResult(False, "symbol_disabled", canonical)
        return ValidationResult(True, symbol=canonical, details={"broker_symbol": cfg.get("broker_symbol", canonical)})

    @staticmethod
    def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
        if value in (None, "auto", ""):
            return default
        try:
            val = float(value)
            if math.isnan(val) or math.isinf(val):
                return default
            return val
        except (TypeError, ValueError):
            return default

    def round_lot(self, symbol: str, lots: float) -> Optional[float]:
        cfg = self.get(symbol)
        if not cfg:
            return None
        step = self._num(cfg.get("lot_step"), 0.01) or 0.01
        rounded = math.floor((float(lots) + 1e-12) / step) * step
        decimals = max(0, len(f"{step:.10f}".rstrip("0").split(".")[-1])) if step < 1 else 0
        return round(rounded, decimals)

    def validate_lot(self, symbol: str, lots: Any) -> ValidationResult:
        canonical = self.resolve_symbol(symbol)
        cfg = self.get(symbol)
        if not cfg or not canonical:
            return ValidationResult(False, "unknown_symbol", symbol)
        qty = self._num(lots)
        if qty is None or qty <= 0:
            return ValidationResult(False, "invalid_lot", canonical, {"qty": lots})
        min_lot = self._num(cfg.get("min_lot"), 0.01) or 0.01
        max_lot = self._num(cfg.get("max_lot"), float("inf")) or float("inf")
        rounded = self.round_lot(canonical, qty)
        if rounded is None or rounded <= 0:
            return ValidationResult(False, "invalid_lot", canonical, {"qty": lots})
        if rounded < min_lot:
            return ValidationResult(False, "lot_below_min", canonical, {"qty": qty, "rounded": rounded, "min_lot": min_lot})
        if rounded > max_lot:
            return ValidationResult(False, "lot_above_max", canonical, {"qty": qty, "rounded": rounded, "max_lot": max_lot})
        return ValidationResult(True, symbol=canonical, details={"qty": qty, "rounded_qty": rounded, "min_lot": min_lot, "max_lot": max_lot})

    def spread_ok(self, symbol: str, bid: Any, ask: Any) -> ValidationResult:
        canonical = self.resolve_symbol(symbol)
        cfg = self.get(symbol)
        if not cfg or not canonical:
            return ValidationResult(False, "unknown_symbol", symbol)
        bid_f, ask_f = self._num(bid), self._num(ask)
        if bid_f is None or ask_f is None or bid_f <= 0 or ask_f <= 0:
            return ValidationResult(False, "invalid_bid_ask", canonical, {"bid": bid, "ask": ask})
        if ask_f < bid_f:
            return ValidationResult(False, "ask_below_bid", canonical, {"bid": bid_f, "ask": ask_f})
        asset = self.asset_classes.get(cfg.get("asset_class", ""), {}) or {}
        spread = ask_f - bid_f
        pip_size = self._num(cfg.get("pip_size"))
        if "max_spread_pips" in cfg or "default_max_spread_pips" in asset:
            max_spread = self._num(cfg.get("max_spread_pips"), self._num(asset.get("default_max_spread_pips")))
            if not pip_size:
                return ValidationResult(False, "missing_pip_size", canonical)
            spread_units = spread / pip_size
            unit = "pips"
        else:
            point_size = self._num(cfg.get("point_size"), self._num(cfg.get("tick_size"), 0.01)) or 0.01
            max_spread = self._num(cfg.get("max_spread_points"), self._num(asset.get("default_max_spread_points")))
            spread_units = spread / point_size
            unit = "points"
        if max_spread is None:
            return ValidationResult(False, "missing_spread_limit", canonical)
        details = {"bid": bid_f, "ask": ask_f, "spread": spread, f"spread_{unit}": round(spread_units, 4), f"max_spread_{unit}": max_spread}
        if spread_units > max_spread:
            return ValidationResult(False, "spread_too_wide", canonical, details)
        return ValidationResult(True, symbol=canonical, details=details)

    @staticmethod
    def _parse_hhmm(value: str, *, session_name: str = "", window_index: int = 0) -> Tuple[int, int]:
        try:
            hour, minute = str(value).strip().split(":", 1)
            h, m = int(hour), int(minute)
        except (TypeError, ValueError, AttributeError) as exc:
            where = f" in session '{session_name}' window[{window_index}]" if session_name else ""
            raise ValueError(f"Invalid HH:MM time {value!r}{where}") from exc
        if not (0 <= h <= 23 and 0 <= m <= 59):
            where = f" in session '{session_name}' window[{window_index}]" if session_name else ""
            raise ValueError(f"Out-of-range HH:MM time {value!r}{where}")
        return h, m

    @staticmethod
    def _minutes_in_window(now_minutes: int, start_minutes: int, end_minutes: int) -> bool:
        if start_minutes <= end_minutes:
            return start_minutes <= now_minutes <= end_minutes
        return now_minutes >= start_minutes or now_minutes <= end_minutes

    def _resolve_symbol_sessions(self, cfg: Dict[str, Any]) -> List[str]:
        explicit = cfg.get("sessions")
        if explicit:
            return list(explicit)
        asset = self.asset_classes.get(str(cfg.get("asset_class") or ""), {}) or {}
        defaults = asset.get("default_sessions") or []
        return list(defaults)

    def session_ok(self, symbol: str, now: Optional[datetime] = None) -> ValidationResult:
        canonical = self.resolve_symbol(symbol)
        cfg = self.get(symbol)
        if not cfg or not canonical:
            return ValidationResult(False, "unknown_symbol", symbol)
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is not None:
            now = now.astimezone(timezone.utc).replace(tzinfo=None)
        day = DAY_NAMES[now.weekday()]
        configured_sessions = self._resolve_symbol_sessions(cfg)
        if not configured_sessions:
            return ValidationResult(False, "no_sessions_configured", canonical)
        now_minutes = now.hour * 60 + now.minute
        for session_name in configured_sessions:
            session = self.sessions.get(session_name, {}) or {}
            if not session:
                return ValidationResult(False, "unknown_session", canonical, {"session": session_name})
            days = session.get("days", DAY_NAMES[:5])
            if day not in days:
                continue
            for idx, window in enumerate(session.get("windows", []) or []):
                if not isinstance(window, (list, tuple)) or len(window) != 2:
                    continue
                sh, sm = self._parse_hhmm(str(window[0]), session_name=session_name, window_index=idx)
                eh, em = self._parse_hhmm(str(window[1]), session_name=session_name, window_index=idx)
                start_minutes = sh * 60 + sm
                end_minutes = eh * 60 + em
                if self._minutes_in_window(now_minutes, start_minutes, end_minutes):
                    return ValidationResult(
                        True,
                        symbol=canonical,
                        details={"session": session_name, "day": day, "time_utc": now.strftime("%H:%M")},
                    )
        return ValidationResult(
            False,
            "session_closed",
            canonical,
            {"day": day, "sessions": configured_sessions, "time_utc": now.strftime("%H:%M")},
        )

    def stop_distance_ok(self, symbol: str, side: str, entry: Any, sl: Any = None, tp: Any = None) -> ValidationResult:
        canonical = self.resolve_symbol(symbol)
        cfg = self.get(symbol)
        if not cfg or not canonical:
            return ValidationResult(False, "unknown_symbol", symbol)
        entry_f = self._num(entry)
        sl_f = self._num(sl)
        tp_f = self._num(tp)
        side_u = str(side).upper()
        if entry_f is None or entry_f <= 0:
            return ValidationResult(True, symbol=canonical, details={"note": "entry_missing_skip_directional_check"})
        if sl_f is None or sl_f <= 0:
            return ValidationResult(False, "no_stop_loss", canonical)
        if side_u == "BUY" and sl_f >= entry_f:
            return ValidationResult(False, "sl_above_entry_for_buy", canonical)
        if side_u == "SELL" and sl_f <= entry_f:
            return ValidationResult(False, "sl_below_entry_for_sell", canonical)
        if tp_f and side_u == "BUY" and tp_f <= entry_f:
            return ValidationResult(False, "tp_below_entry_for_buy", canonical)
        if tp_f and side_u == "SELL" and tp_f >= entry_f:
            return ValidationResult(False, "tp_above_entry_for_sell", canonical)
        return ValidationResult(True, symbol=canonical)

    def validate_order(
        self,
        intent: Dict[str, Any],
        market_snapshot: Optional[Dict[str, Any]] = None,
        require_enabled: bool = True,
        now: Optional[datetime] = None,
    ) -> ValidationResult:
        symbol = intent.get("symbol", "")
        symbol_result = self.validate_symbol(symbol, require_enabled=require_enabled)
        if not symbol_result.ok:
            return symbol_result
        canonical = symbol_result.symbol
        lot_result = self.validate_lot(canonical, intent.get("qty"))
        if not lot_result.ok:
            return lot_result
        strategy_result = self.strategy_allowed(canonical, intent.get("strategy_id"))
        if not strategy_result.ok:
            return strategy_result
        session_result = self.session_ok(canonical, now=now)
        if not session_result.ok:
            return session_result
        side = str(intent.get("side", "")).upper()
        cfg = self.get(canonical) or {}
        if side == "BUY" and not cfg.get("allow_long", True):
            return ValidationResult(False, "long_disabled", canonical)
        if side == "SELL" and not cfg.get("allow_short", True):
            return ValidationResult(False, "short_disabled", canonical)
        if market_snapshot and "bid" in market_snapshot and "ask" in market_snapshot:
            spread_result = self.spread_ok(canonical, market_snapshot.get("bid"), market_snapshot.get("ask"))
            if not spread_result.ok:
                return spread_result
            quote_result = self.tick_quote_ok(canonical, market_snapshot)
            if not quote_result.ok:
                return quote_result
        return ValidationResult(True, symbol=canonical, details={
            "rounded_qty": lot_result.details.get("rounded_qty"),
            "broker_symbol": self.resolve_broker_symbol(canonical),
            "strategy_id": strategy_result.details.get("strategy_id"),
        })

    def broker_trade_ok(self, symbol: str, tick: Optional[Dict[str, Any]] = None) -> ValidationResult:
        """Validate broker metadata for broker-hydrated symbols.

        Root-symbol hydration is intentionally read-only. When it is used in
        place of a dedicated chart, we must still fail closed on the broker's
        execution metadata: disabled/close-only trade modes and broker minimum
        lots that exceed our configured approval cap are not executable.
        """
        canonical = self.resolve_symbol(symbol)
        cfg = self.get(symbol)
        if not cfg or not canonical:
            return ValidationResult(False, "unknown_symbol", symbol)
        tick = tick or {}
        if tick.get("source") != "broker_symbol_info":
            return ValidationResult(True, symbol=canonical, details={"source": tick.get("source")})
        info = tick.get("broker_info") or {}
        if not info:
            return ValidationResult(False, "broker_info_missing", canonical)

        trade_mode_raw = info.get("trade_mode")
        try:
            trade_mode = int(trade_mode_raw)
        except (TypeError, ValueError):
            return ValidationResult(False, "broker_trade_mode_unknown", canonical, {"trade_mode": trade_mode_raw})
        # MT5 modes: 0 disabled, 1 long-only, 2 short-only, 3 close-only, 4 full.
        # Without a side at readiness time, allow only modes that can open risk.
        if trade_mode not in {1, 2, 4}:
            return ValidationResult(False, "broker_trade_disabled", canonical, {"trade_mode": trade_mode})

        broker_min_lot = self._num(info.get("min_lot"))
        broker_lot_step = self._num(info.get("lot_step"))
        configured_max_lot = self._num(cfg.get("max_lot"), float("inf")) or float("inf")
        configured_min_lot = self._num(cfg.get("min_lot"), 0.01) or 0.01
        if broker_min_lot is not None and broker_min_lot > configured_max_lot + 1e-12:
            return ValidationResult(False, "broker_min_lot_above_approval", canonical, {
                "broker_min_lot": broker_min_lot,
                "configured_max_lot": configured_max_lot,
            })
        if broker_lot_step is not None and broker_lot_step > configured_max_lot + 1e-12:
            return ValidationResult(False, "broker_lot_step_above_approval", canonical, {
                "broker_lot_step": broker_lot_step,
                "configured_max_lot": configured_max_lot,
            })
        return ValidationResult(True, symbol=canonical, details={
            "trade_mode": trade_mode,
            "broker_min_lot": broker_min_lot,
            "broker_lot_step": broker_lot_step,
            "configured_min_lot": configured_min_lot,
            "configured_max_lot": configured_max_lot,
        })

    def readiness_snapshot(self, charts: Iterable[str] = (), ticks: Optional[Dict[str, Dict[str, Any]]] = None, now: Optional[datetime] = None) -> Dict[str, Dict[str, Any]]:
        chart_set = set(charts)
        ticks = ticks or {}
        result: Dict[str, Dict[str, Any]] = {}
        for symbol in self.all_symbols():
            canonical = self.resolve_symbol(symbol) or symbol
            cfg = self.get(symbol) or {}
            chart = "chart_" + cfg.get("broker_symbol", symbol)
            enabled = bool(cfg.get("enabled", False))
            session = self.session_ok(symbol, now=now)
            tick = ticks.get(symbol) or ticks.get(cfg.get("broker_symbol", symbol)) or {}
            broker_hydrated = tick.get("source") == "broker_symbol_info"
            chart_present = chart in chart_set or broker_hydrated
            spread = self.spread_ok(symbol, tick.get("bid"), tick.get("ask")) if tick else ValidationResult(False, "tick_missing", symbol)
            quote_eval = self.tick_quote_ok(symbol, tick) if tick else ValidationResult(False, "tick_missing", symbol)
            if tick and not session.ok:
                # Off-hours: last quote can be hours old on delayed stock feeds; do not treat as bridge failure.
                quote = ValidationResult(True, symbol=canonical, details={
                    "skipped": "session_closed",
                    "quote_age_sec": tick.get("quote_age_sec"),
                    "would_block": not quote_eval.ok,
                    "quote_eval_reason": quote_eval.reason if not quote_eval.ok else None,
                })
            else:
                quote = quote_eval
            broker_trade = self.broker_trade_ok(symbol, tick) if tick else ValidationResult(False, "tick_missing", symbol)
            ready = enabled and chart_present and session.ok and spread.ok and quote.ok and broker_trade.ok
            result[symbol] = {
                "enabled": enabled,
                "broker_symbol": cfg.get("broker_symbol", symbol),
                "asset_class": cfg.get("asset_class"),
                "chart": chart,
                "chart_present": chart_present,
                "tick_source": tick.get("source") if tick else None,
                "quote_age_sec": tick.get("quote_age_sec") if tick else None,
                "max_quote_age_sec": self.max_fresh_quote_sec(symbol),
                "session_ok": session.ok,
                "session_reason": session.reason,
                "spread_ok": spread.ok,
                "spread_reason": spread.reason,
                "quote_ok": quote.ok,
                "quote_reason": quote.reason,
                "quote_details": quote.details,
                "quote_skipped": bool(quote.details.get("skipped") == "session_closed"),
                "broker_trade_ok": broker_trade.ok,
                "broker_trade_reason": broker_trade.reason,
                "broker_trade_details": broker_trade.details,
                "ready": ready,
                "result": "READY" if ready else self._readiness_reason(enabled, chart_present, session, spread, quote, broker_trade),
            }
        return result

    @staticmethod
    def _readiness_reason(
        enabled: bool,
        chart_present: bool,
        session: ValidationResult,
        spread: ValidationResult,
        quote: Optional[ValidationResult] = None,
        broker_trade: Optional[ValidationResult] = None,
    ) -> str:
        if not enabled:
            return "DISABLED"
        if not chart_present:
            return "BLOCKED_NO_CHART"
        if not session.ok:
            return f"BLOCKED_{session.reason.upper()}"
        if not spread.ok:
            return f"BLOCKED_{spread.reason.upper()}"
        if quote is not None and not quote.ok:
            return f"BLOCKED_{quote.reason.upper()}"
        if broker_trade is not None and not broker_trade.ok:
            return f"BLOCKED_{broker_trade.reason.upper()}"
        return "BLOCKED_UNKNOWN"


_DEFAULT_REGISTRY: Optional[InstrumentRegistry] = None


def load_registry(force: bool = False) -> InstrumentRegistry:
    global _DEFAULT_REGISTRY
    if force or _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = InstrumentRegistry()
    return _DEFAULT_REGISTRY


# Convenience functions for callers that do not need an explicit registry object.
def normalize_symbol(symbol: str) -> str:
    return InstrumentRegistry.normalize_symbol(symbol)


def enabled_symbols() -> List[str]:
    return load_registry().enabled_symbols()


def validate_order(
    intent: Dict[str, Any],
    market_snapshot: Optional[Dict[str, Any]] = None,
    require_enabled: bool = True,
    now: Optional[datetime] = None,
) -> ValidationResult:
    return load_registry().validate_order(intent, market_snapshot=market_snapshot, require_enabled=require_enabled, now=now)


if __name__ == "__main__":
    registry = load_registry(force=True)
    print(yaml.safe_dump(registry.readiness_snapshot(), sort_keys=True))
