#!/usr/bin/env python3
"""Tests for canonical path resolution."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paths import archive_dir, ipc_dir, nervous_dir, repo_root, stop_trading_path, vault_dir  # noqa: E402


def test_repo_root_defaults_to_package_parent(tmp_path, monkeypatch):
    monkeypatch.delenv("TRADING_OS_ROOT", raising=False)
    assert repo_root() == ROOT.resolve()


def test_repo_root_honors_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_OS_ROOT", str(tmp_path / "clone"))
    assert repo_root() == (tmp_path / "clone").resolve()


def test_ipc_dir_defaults_under_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("TRADING_OS_IPC", raising=False)
    monkeypatch.delenv("TRADING_OS_ROOT", raising=False)
    assert ipc_dir() == (ROOT / "ipc").resolve()
    assert ipc_dir().is_dir()


def test_ipc_dir_honors_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom_ipc"
    monkeypatch.setenv("TRADING_OS_IPC", str(custom))
    assert ipc_dir() == custom.resolve()


def test_nervous_and_archive_dirs_follow_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_OS_ROOT", str(tmp_path / "os"))
    assert nervous_dir() == (tmp_path / "os" / "nervous").resolve()
    assert archive_dir() == (tmp_path / "os" / "logs" / "archive").resolve()


def test_vault_dir_defaults_to_sibling_of_repo_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_OS_ROOT", str(tmp_path / "trading-os"))
    assert vault_dir() == (tmp_path / "vault").resolve()


def test_vault_dir_honors_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "notes"
    monkeypatch.setenv("TRADING_OS_VAULT", str(custom))
    assert vault_dir() == custom.resolve()


def test_stop_trading_path_is_repo_local(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_OS_ROOT", str(tmp_path / "os"))
    assert stop_trading_path() == (tmp_path / "os" / "STOP_TRADING").resolve()
