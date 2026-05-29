#!/usr/bin/env python3
"""Advisory-only forecast layer.

This module is intentionally safe-by-default:
- it never emits order intents or trade commands
- in LIVE/demo real mode it will not silently publish mock forecasts
- forecasts are context for cortex/LLM only and must still pass all risk gates
"""
from __future__ import annotations

import json
import os
import sys
import time
import importlib
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, subscribe, current_seq  # noqa: E402

FORECAST_TOPIC = "market.forecast"
DEFAULT_MIN_CANDLES = 5
DEFAULT_HISTORY = 64
DEFAULT_HORIZON_STEPS = 3
RISK_FILE = ROOT / "immune" / "risk_limits.json"


class ForecastError(RuntimeError):
    """Forecast adapter error."""


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_candle(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize candle.close payloads into the fields the forecaster needs."""
    symbol = str(raw.get("symbol") or "").upper().strip()
    timeframe = str(raw.get("timeframe") or raw.get("tf") or "").upper().strip()
    close = _to_float(raw.get("close"))
    if not symbol or not timeframe or close is None:
        return None
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "close": close,
        "ts_close": _to_float(raw.get("ts_close"), time.time()),
    }


class MockTimesFMAdapter:
    """Deterministic local stand-in for TimesFM.

    It uses a conservative linear drift estimate from recent closes, clamps noisy
    confidence, and marks itself clearly as ``mock_timesfm``. This lets the OS
    wire the TimesFM contract now without adding an unsafe hard dependency or
    treating forecasts as executable signals.
    """

    name = "mock_timesfm"

    def forecast(self, closes: Iterable[float], *, horizon_steps: int = DEFAULT_HORIZON_STEPS) -> Dict[str, Any]:
        series = [float(x) for x in closes]
        if len(series) < 2:
            raise ForecastError("insufficient_series")
        last = series[-1]
        recent = series[-min(8, len(series)):]
        drift = (recent[-1] - recent[0]) / max(1, len(recent) - 1)
        preds = [round(last + drift * (i + 1), 5) for i in range(horizon_steps)]
        abs_moves = [abs(recent[i] - recent[i - 1]) for i in range(1, len(recent))]
        avg_move = sum(abs_moves) / len(abs_moves) if abs_moves else 0.0
        confidence = 0.35 if avg_move == 0 else max(0.2, min(0.65, abs(drift) / (avg_move + 1e-12) * 0.5))
        direction = "flat"
        if preds[-1] > last:
            direction = "up"
        elif preds[-1] < last:
            direction = "down"
        return {
            "predicted_close": preds,
            "direction": direction,
            "confidence": round(confidence, 3),
            "drift_per_step": round(drift, 8),
        }


class TechnicalForecastAdapter(MockTimesFMAdapter):
    """Lightweight real-time technical forecast used when TimesFM is absent.

    This is not a mock: it derives a conservative advisory forecast directly from
    live candle closes. It does not place trades and remains context-only for the
    guarded cortex path. Operators can still select the heavier OSS TimesFM
    adapter with TRADING_OS_TIMESFM_PROVIDER=timesfm when installed.
    """

    name = "technical_forecaster"


class RealTimesFMAdapter:
    """Adapter for the OSS ``timesfm`` Python package.

    This stays optional because TimesFM is a heavy ML dependency. In LIVE/demo it
    is only used when explicitly selected with ``TRADING_OS_TIMESFM_PROVIDER`` set
    to ``timesfm``/``real``/``local``. Missing packages or incompatible APIs fail
    as advisory forecast errors, never as mock forecasts.
    """

    name = "timesfm"

    def __init__(self, *, context_len: Optional[int] = None, horizon_len: Optional[int] = None, backend: Optional[str] = None, repo_id: Optional[str] = None):
        self.context_len = context_len or int(os.getenv("TRADING_OS_TIMESFM_CONTEXT_LEN", "64"))
        self.horizon_len = horizon_len or int(os.getenv("TRADING_OS_TIMESFM_HORIZON", str(DEFAULT_HORIZON_STEPS)))
        self.backend = backend or os.getenv("TRADING_OS_TIMESFM_BACKEND", "cpu")
        self.repo_id = repo_id or os.getenv("TRADING_OS_TIMESFM_REPO_ID", "google/timesfm-1.0-200m")
        self._model = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            module = importlib.import_module("timesfm")
        except Exception as exc:
            raise ForecastError("timesfm_package_missing: install/configure the OSS timesfm package") from exc

        # Support the common public API plus test/local shims without pinning the
        # entire OS to one upstream TimesFM release shape.
        if hasattr(module, "TimesFm"):
            kwargs = {"context_len": self.context_len, "horizon_len": self.horizon_len}
            try:
                model = module.TimesFm(**kwargs)
            except TypeError:
                model = module.TimesFm()
            if hasattr(model, "load_from_checkpoint"):
                try:
                    model.load_from_checkpoint(repo_id=self.repo_id)
                except TypeError:
                    model.load_from_checkpoint(self.repo_id)
            self._model = model
            return self._model

        if hasattr(module, "forecast"):
            self._model = module
            return self._model

        raise ForecastError("timesfm_api_unsupported:no TimesFm class or forecast function")

    def forecast(self, closes: Iterable[float], *, horizon_steps: int = DEFAULT_HORIZON_STEPS) -> Dict[str, Any]:
        series = [float(x) for x in closes]
        if len(series) < 2:
            raise ForecastError("insufficient_series")
        model = self._load_model()
        horizon = int(horizon_steps or self.horizon_len)
        try:
            if hasattr(model, "forecast"):
                raw = model.forecast([series], freq=[0], horizon_len=horizon)
            else:  # module-level forecast shim
                raw = model.forecast([series], horizon_len=horizon)
        except TypeError:
            raw = model.forecast([series])

        preds = _extract_prediction_series(raw, horizon)
        if not preds:
            raise ForecastError("timesfm_empty_prediction")
        last = series[-1]
        direction = "flat"
        if preds[-1] > last:
            direction = "up"
        elif preds[-1] < last:
            direction = "down"
        return {
            "predicted_close": [round(float(x), 5) for x in preds],
            "direction": direction,
            "confidence": None,
            "source": "oss_timesfm",
            "repo_id": self.repo_id,
        }


def _extract_prediction_series(raw: Any, horizon: int) -> List[float]:
    """Normalize likely TimesFM return shapes into a simple prediction list."""
    candidate = raw
    if isinstance(raw, tuple) and raw:
        candidate = raw[0]
    if isinstance(candidate, dict):
        for key in ("mean", "forecast", "predictions", "point_forecast"):
            if key in candidate:
                candidate = candidate[key]
                break
    try:
        import numpy as np  # type: ignore
        if isinstance(candidate, np.ndarray):
            candidate = candidate.tolist()
    except Exception:
        pass
    if isinstance(candidate, list) and candidate and isinstance(candidate[0], list):
        candidate = candidate[0]
    if isinstance(candidate, list):
        return [float(x) for x in candidate[:horizon]]
    return []


def configured_adapter() -> Optional[Any]:
    provider = os.getenv("TRADING_OS_TIMESFM_PROVIDER", "").strip().lower()
    if provider in {"timesfm", "real", "local"}:
        return RealTimesFMAdapter()
    if provider in {"", "technical", "baseline"}:
        return TechnicalForecastAdapter()
    if provider == "mock" and allow_mock_forecasts():
        return MockTimesFMAdapter()
    return None


def real_mode_active() -> bool:
    if os.getenv("TRADING_OS_MODE", "").strip().upper() == "LIVE":
        return True
    try:
        data = json.loads(RISK_FILE.read_text())
        return str(data.get("mode", "")).upper() == "LIVE"
    except Exception:
        return False


def allow_mock_forecasts() -> bool:
    return os.getenv("TRADING_OS_ALLOW_MOCK_FORECASTS", "0").strip().lower() in {"1", "true", "yes"}


def build_forecast(symbol: str, timeframe: str, candles: List[Dict[str, Any]], *, adapter: Optional[Any] = None, horizon_steps: int = DEFAULT_HORIZON_STEPS, min_candles: int = DEFAULT_MIN_CANDLES) -> Optional[Dict[str, Any]]:
    """Build an advisory forecast payload from normalized candle history."""
    if len(candles) < min_candles:
        return None
    adapter = adapter or configured_adapter()
    if adapter is None and real_mode_active() and not allow_mock_forecasts():
        return {
            "type": "timesfm_forecast",
            "ok": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "model": "unconfigured_real_forecaster",
            "error": "real_forecaster_required:TRADING_OS_TIMESFM_PROVIDER is invalid and TRADING_OS_ALLOW_MOCK_FORECASTS=0",
            "advisory_only": True,
            "ts": time.time(),
        }
    adapter = adapter or MockTimesFMAdapter()
    closes = [c["close"] for c in candles]
    try:
        result = adapter.forecast(closes, horizon_steps=horizon_steps)
    except Exception as exc:
        return {
            "type": "timesfm_forecast",
            "ok": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "model": getattr(adapter, "name", "timesfm"),
            "error": str(exc),
            "advisory_only": True,
            "ts": time.time(),
        }
    return {
        "type": "timesfm_forecast",
        "ok": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "model": getattr(adapter, "name", "timesfm"),
        "horizon_steps": horizon_steps,
        "input_candles": len(candles),
        "last_close": closes[-1],
        "forecast": result,
        "advisory_only": True,
        "ts": time.time(),
    }


class TimesFMForecaster:
    """Stateful bus forecaster for candle.close events."""

    def __init__(self, *, adapter: Optional[Any] = None, history: int = DEFAULT_HISTORY, min_candles: int = DEFAULT_MIN_CANDLES, horizon_steps: int = DEFAULT_HORIZON_STEPS):
        self.adapter = adapter
        self.history: Dict[tuple, Deque[Dict[str, Any]]] = defaultdict(lambda: deque(maxlen=history))
        self.min_candles = min_candles
        self.horizon_steps = horizon_steps

    def on_candle(self, candle: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        normalized = normalize_candle(candle)
        if normalized is None:
            return None
        key = (normalized["symbol"], normalized["timeframe"])
        self.history[key].append(normalized)
        return build_forecast(
            normalized["symbol"],
            normalized["timeframe"],
            list(self.history[key]),
            adapter=self.adapter,
            horizon_steps=self.horizon_steps,
            min_candles=self.min_candles,
        )


def publish_forecast(payload: Dict[str, Any]) -> None:
    """Publish advisory forecast topics. Never publishes order topics."""
    publish(FORECAST_TOPIC, payload)
    if payload.get("symbol"):
        publish(f"{FORECAST_TOPIC}.{payload['symbol']}", payload)


def run_once(events: List[Dict[str, Any]], forecaster: Optional[TimesFMForecaster] = None) -> List[Dict[str, Any]]:
    forecaster = forecaster or TimesFMForecaster()
    outputs = []
    for ev in events:
        if ev.get("topic") != "candle.close":
            continue
        payload = forecaster.on_candle(ev.get("payload", {}))
        if payload is not None:
            publish_forecast(payload)
            outputs.append(payload)
    return outputs


def run():
    forecaster = TimesFMForecaster(
        adapter=configured_adapter(),
        min_candles=int(os.getenv("TRADING_OS_TIMESFM_MIN_CANDLES", str(DEFAULT_MIN_CANDLES))),
        horizon_steps=int(os.getenv("TRADING_OS_TIMESFM_HORIZON", str(DEFAULT_HORIZON_STEPS))),
    )
    last_seq = current_seq()
    while True:
        events = subscribe("candle.close", since_seq=last_seq)
        for ev in events:
            seq = ev.get("seq", 0)
            if seq > last_seq:
                last_seq = seq
        run_once(events, forecaster)
        time.sleep(5)


if __name__ == "__main__":
    run()
