#!/usr/bin/env python3
"""Tests for portfolio snapshot aggregation."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from muscle import pnl_sync  # noqa: E402
from muscle import portfolio_snapshot as ps  # noqa: E402


def test_parse_account_snapshot():
    text = "ACCOUNT|10000.00|10125.50|250.00|9875.50|4050.20|5050115683|MetaQuotes-Demo\n"
    account = pnl_sync.parse_account_snapshot(text)
    assert account["balance"] == 10000.0
    assert account["equity"] == 10125.5
    assert account["margin_used"] == 250.0
    assert account["login"] == "5050115683"


def test_build_portfolio_snapshot_with_account_and_positions(tmp_path, monkeypatch):
    ipc = tmp_path / "ipc"
    ipc.mkdir()
    data_out = ipc / "data_out.txt"
    data_out.write_text(
        "ACCOUNT|5000|5120|100|5020|5120|1|Demo\n"
        "POSITION|1|EURUSD|0.10|buy|1.1000|1.1010|0|0|10.00|2026.05.09\n",
        encoding="utf-8",
    )
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        json.dumps({"type": "trade_closed", "ts": 1, "date": "2026-05-09", "pnl": 25.0}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(pnl_sync, "DATA_FILE", data_out)
    monkeypatch.setattr(pnl_sync, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(ps, "JOURNAL_FILE", journal)

    snap = ps.build_portfolio_snapshot(refresh_positions=False)
    assert snap["available"] is True
    assert snap["account"]["equity"] == 5120.0
    assert snap["pnl"]["floating_pnl"] == 10.0
    assert snap["pnl"]["realized_total"] == 25.0
    assert snap["exposure"]["open_count"] == 1
    assert snap["exposure"]["invested_notional"] == 0.11
    assert snap["equity_curve"]["available"] is True
    assert snap["equity_curve"]["points"][-1]["equity"] == 5120.0


def test_reconcile_publishes_portfolio_equity(monkeypatch):
    published = []

    def fake_publish(topic, payload):
        published.append((topic, payload))

    monkeypatch.setattr(pnl_sync, "publish", fake_publish)
    monkeypatch.setattr(
        pnl_sync,
        "parse_account_snapshot",
        lambda: {"equity": 5120.0, "balance": 5000.0},
    )
    positions = [
        {
            "ticket": "1",
            "symbol": "EURUSD",
            "profit": 10.0,
            "swap": 0.0,
            "commission": 0.0,
        }
    ]
    report = pnl_sync.reconcile_positions(positions, previous={}, publish_events=True)
    assert report["open_count"] == 1
    topics = [topic for topic, _ in published]
    assert "portfolio.equity" in topics
    assert "position.pnl" in topics
    equity_event = next(payload for topic, payload in published if topic == "portfolio.equity")
    assert equity_event["equity"] == 5120.0
