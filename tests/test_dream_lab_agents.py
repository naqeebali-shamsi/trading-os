#!/usr/bin/env python3
"""Smoke tests for Dream Lab agents and scheduler."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rd.agents.historian import HistorianAgent  # noqa: E402
from rd.agents.trainer import TrainerAgent  # noqa: E402
from rd.agents.strategist import StrategistAgent  # noqa: E402
from rd.agents.backtester import BacktesterAgent  # noqa: E402
from rd.agents.news_lab import NewsLabAgent  # noqa: E402
from rd.agents.auditor import AuditorAgent  # noqa: E402
from rd import dream_scheduler  # noqa: E402
from rd import promotions  # noqa: E402
from research.dataset_builder import DATASET_VERSION  # noqa: E402


def _dataset_row(i: int, *, symbol: str = "EURUSD", timeframe: str = "M15") -> dict:
    ts = 1_700_000_000.0 + i * 900.0
    close = 1.10 + 0.002 * (i % 7) + 0.00005 * i
    return {
        "dataset_version": DATASET_VERSION,
        "series_id": f"{symbol}_{timeframe}",
        "symbol": symbol,
        "timeframe": timeframe,
        "ts_close": ts,
        "feature_cutoff_ts": ts,
        "stage": "publish_signal",
        "reason": "signal_emitted",
        "status": "passed",
        "order_id": f"o{i}",
        "close": close,
        "h1_complete": True,
        "h1_target_ts_close": ts + 900.0,
        "h3_complete": True,
        "h3_target_ts_close": ts + 2700.0,
    }


def test_agents_run_without_error():
    assert HistorianAgent().run()["agent"] == "historian"
    assert TrainerAgent().run()["agent"] == "trainer"
    assert StrategistAgent().run({"reason": "test"})["agent"] == "strategist"
    assert BacktesterAgent().run({"strategy_id": "TEST_STRAT"})["agent"] == "backtester"
    assert NewsLabAgent().run()["agent"] == "news_lab"
    assert AuditorAgent().run()["agent"] == "auditor"


def test_scheduler_hourly_cycle(tmp_path, monkeypatch):
    state_file = tmp_path / "dream_lab_state.json"
    queue_file = tmp_path / "promotion_queue.jsonl"
    monkeypatch.setattr(dream_scheduler, "STATE_FILE", state_file)
    monkeypatch.setattr(promotions, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(
        dream_scheduler,
        "_ready_symbol_count",
        lambda: 0,
    )
    result = dream_scheduler.run_cycle("hourly", state={})
    assert result["cycle"] == "hourly"
    assert "historian" in result["agents"]
    assert state_file.exists()


def test_trainer_proposes_with_labeled_rows(tmp_path, monkeypatch):
    training = tmp_path / "decision_training.jsonl"
    rows = []
    for i in range(60):
        label = 1 if i % 2 == 0 else 0
        rows.append(json.dumps({"confidence": 0.75 if label else 0.82, "label": label}))
    training.write_text("\n".join(rows) + "\n", encoding="utf-8")
    queue_file = tmp_path / "promotion_queue.jsonl"
    model_dir = tmp_path / "models"
    monkeypatch.setattr("rd.agents.trainer.TRAINING_FILE", training)
    monkeypatch.setattr("rd.agents.trainer.MODEL_DIR", model_dir)
    monkeypatch.setattr("rd.agents.trainer.MODEL_FILE", model_dir / "confidence_v1.json")
    monkeypatch.setattr(promotions, "QUEUE_FILE", queue_file)

    result = TrainerAgent().run()
    assert result.get("ok") is True
    assert result.get("row_count", 0) >= 50


def test_backtester_uses_walk_forward_splits_when_dataset_available(tmp_path, monkeypatch):
    dataset = tmp_path / "datasets" / f"{DATASET_VERSION}.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    rows = [_dataset_row(i) for i in range(40)]
    dataset.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")

    import data_lake

    monkeypatch.setattr(data_lake, "TRAINING_ROOT", tmp_path)

    result = BacktesterAgent().run({"symbol": "EURUSD", "timeframe": "M15", "strategy_id": "TEST_WF"})
    backtest = result["backtest"]
    assert backtest["source"] == "walk_forward_splits"
    assert set(backtest["splits"]) == {"train", "validation", "test"}
    assert sum(backtest["split_counts"].values()) >= 10
    assert backtest["split_counts"]["train"] > 0
    assert backtest["primary_split"] in {"train", "validation", "test"}


def test_backtester_falls_back_to_candle_lake_without_dataset(tmp_path, monkeypatch):
    import data_lake

    monkeypatch.setattr(data_lake, "TRAINING_ROOT", tmp_path / "missing_training")

    result = BacktesterAgent().run(
        {
            "symbol": "EURUSD",
            "strategy_id": "TEST_FALLBACK",
            "force_candle_lake": True,
            "bars": 120,
        }
    )
    backtest = result["backtest"]
    assert backtest["source"] in {"candle_lake", "synthetic_fallback"}
    assert "splits" not in backtest
