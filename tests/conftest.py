#!/usr/bin/env python3
"""Shared pytest fixtures and platform markers for the trading-os test suite."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "unix_only: test relies on POSIX-only APIs (e.g. process groups) and is skipped elsewhere",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("unix_only") and os.name != "posix":
        pytest.skip("requires a POSIX platform")


@pytest.fixture
def ipc_root(tmp_path: Path) -> Path:
    """A TRADING_OS_IPC directory with one fresh chart heartbeat (EURUSD).

    NVDA is intentionally absent so bootstrap-gap evaluation reports it missing.
    """
    root = tmp_path / "ipc"
    eurusd_chart = root / "chart_EURUSD"
    eurusd_chart.mkdir(parents=True, exist_ok=True)
    (eurusd_chart / "heartbeat.txt").write_text(
        f"{int(time.time())}|alive\n", encoding="utf-8"
    )
    return root
