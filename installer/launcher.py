#!/usr/bin/env python3
"""
Trading OS desktop launcher — one-click start for the full supervisor stack.

Reads install config from:
  1. %ProgramData%\\TradingOS\\config.env (supplements missing keys)
  2. config.env beside TradingOS.exe / repo root (canonical for mode/ipc)

Starts kernel/supervisor.py via the install venv, waits for dashboard health,
then opens the web UI in the default browser.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

PID_FILE_NAME = "supervisor.pid"
CONFIG_DIR_NAME = "TradingOS"
DEFAULT_DASHBOARD = "http://127.0.0.1:8765/ui"
HEALTH_URL = "http://127.0.0.1:8765/api/events/health"
INSTALL_CANONICAL_KEYS = ("TRADING_OS_MODE", "TRADING_OS_IPC")

_logger: logging.Logger | None = None
_launcher_log_path: Path | None = None


def _install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _log_path(install_root: Path) -> Path:
    program_data = os.environ.get("ProgramData")
    if program_data:
        log_dir = Path(program_data) / CONFIG_DIR_NAME
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "launcher.log"
    log_dir = install_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "launcher.log"


def setup_logging(install_root: Path) -> Path:
    global _logger, _launcher_log_path
    log_path = _log_path(install_root)
    _launcher_log_path = log_path
    logger = logging.getLogger("trading_os.launcher")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    _logger = logger
    return log_path


def _log() -> logging.Logger:
    if _logger is None:
        return logging.getLogger("trading_os.launcher")
    return _logger


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def load_config_env(install_root: Path) -> dict[str, str]:
    install_config = install_root / "config.env"
    install_values = _parse_env_file(install_config) if install_config.exists() else {}

    program_data = os.environ.get("ProgramData")
    program_data_values: dict[str, str] = {}
    if program_data:
        pd_path = Path(program_data) / CONFIG_DIR_NAME / "config.env"
        if pd_path.exists():
            program_data_values = _parse_env_file(pd_path)

    values: dict[str, str] = {}
    values.update(program_data_values)
    values.update(install_values)

    if install_config.exists():
        for key in INSTALL_CANONICAL_KEYS:
            if key in install_values:
                values[key] = install_values[key]
            else:
                values.pop(key, None)

    values["TRADING_OS_ROOT"] = str(install_root)
    if "TRADING_OS_IPC" not in values:
        values["TRADING_OS_IPC"] = str(install_root / "ipc")
    return values


def _notify(title: str, message: str) -> None:
    if getattr(sys, "frozen", False) and os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
        except Exception:
            pass
    print(message)


def _preflight(install_root: Path) -> str | None:
    if not (install_root / ".install-complete").exists():
        return (
            "Trading OS is not configured yet.\n\n"
            "Run the setup wizard as Administrator:\n"
            f"  {install_root}\\installer\\install_wizard.ps1\n\n"
            "Or reinstall and complete the configuration step."
        )
    venv_py = install_root / "venv" / "Scripts" / "python.exe"
    if not venv_py.exists():
        return f"Missing virtual environment at {venv_py}. Re-run the setup wizard as Administrator."
    return None


def apply_config(values: dict[str, str]) -> None:
    for key, val in values.items():
        os.environ[key] = val


def apply_dpapi_secrets() -> None:
    if os.name != "nt":
        return
    try:
        if str(Path(__file__).resolve().parent) not in sys.path:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
        from secrets_store import load_secret

        if not os.environ.get("OPENROUTER_API_KEY"):
            key = load_secret("openrouter_api_key")
            if key:
                os.environ["OPENROUTER_API_KEY"] = key
                _log().info("Loaded OPENROUTER_API_KEY from DPAPI secrets store")
    except Exception as exc:
        _log().exception("Failed to load DPAPI secrets: %s", exc)


def resolve_python(install_root: Path) -> Path:
    venv_py = install_root / "venv" / "Scripts" / "pythonw.exe"
    if venv_py.exists():
        return venv_py
    venv_py_console = install_root / "venv" / "Scripts" / "python.exe"
    if venv_py_console.exists():
        return venv_py_console
    return Path(sys.executable)


def pid_file(install_root: Path) -> Path:
    program_data = os.environ.get("ProgramData")
    if program_data:
        base = Path(program_data) / CONFIG_DIR_NAME
        base.mkdir(parents=True, exist_ok=True)
        return base / PID_FILE_NAME
    return install_root / PID_FILE_NAME


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def existing_supervisor(install_root: Path) -> int | None:
    path = pid_file(install_root)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if _pid_running(pid):
        return pid
    path.unlink(missing_ok=True)
    return None


def stop_supervisor(install_root: Path, pid: int) -> bool:
    _log().warning("Stopping stale supervisor PID %s (dashboard unreachable)", pid)
    if not _pid_running(pid):
        pid_file(install_root).unlink(missing_ok=True)
        _log().info("Supervisor PID %s already exited", pid)
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        _log().error("Failed to stop supervisor PID %s: %s", pid, exc)
        return False

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _pid_running(pid):
            pid_file(install_root).unlink(missing_ok=True)
            _log().info("Stopped supervisor PID %s", pid)
            return True
        time.sleep(0.2)

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                check=False,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            _log().error("taskkill failed for supervisor PID %s: %s", pid, exc)
            return False
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as exc:
            _log().error("Failed to force-kill supervisor PID %s: %s", pid, exc)
            return False

    if not _pid_running(pid):
        pid_file(install_root).unlink(missing_ok=True)
        _log().info("Force-stopped supervisor PID %s", pid)
        return True
    _log().error("Supervisor PID %s still running after stop attempts", pid)
    return False


def _supervisor_boot_blocker(install_root: Path) -> str | None:
    """LIVE mode refuses to boot without MT5 bridge readiness."""
    if os.environ.get("TRADING_OS_MODE", "").upper() != "LIVE":
        return None
    if str(install_root) not in sys.path:
        sys.path.insert(0, str(install_root))
    try:
        from kernel.preflight import SupervisorPreflightError, run_supervisor_preflight

        run_supervisor_preflight(install_root, exit_on_failure=False)
    except SupervisorPreflightError:
        return (
            "Trading OS is in LIVE mode but MetaTrader 5 is not connected yet.\n\n"
            "The supervisor will not start until the bridge is ready:\n"
            "  1. Open MetaTrader 5 and log in to your broker\n"
            "  2. Drag FileBridgeEA_Windows onto a chart\n"
            "  3. Enable Algo Trading\n"
            "  4. Confirm heartbeat files under ipc\\\n\n"
            "Then launch Trading OS again.\n\n"
            "To explore the dashboard without MT5, run Configure Trading OS\n"
            "and choose SIMULATION mode instead of LIVE."
        )
    except Exception as exc:
        _log().exception("Unexpected error during supervisor preflight check: %s", exc)
        return None
    return None


def _supervisor_failed_quickly(proc: subprocess.Popen, install_root: Path) -> str | None:
    time.sleep(2.5)
    if proc.poll() is None:
        return None
    _log().error("Supervisor exited early with code %s", proc.returncode)
    health = install_root / "kernel" / "health.json"
    if health.exists():
        try:
            payload = json.loads(health.read_text(encoding="utf-8"))
            if payload.get("preflight_ok") is False:
                return _supervisor_boot_blocker(install_root) or (
                    "Supervisor exited during startup preflight.\n"
                    f"See {health} for details."
                )
        except json.JSONDecodeError:
            pass
    return (
        "Supervisor exited immediately after launch.\n\n"
        f"Check {install_root / 'logs'} and {install_root / 'kernel' / 'health.json'}"
    )


def _read_health_preflight(install_root: Path) -> bool | None:
    health = install_root / "kernel" / "health.json"
    if not health.exists():
        return None
    try:
        payload = json.loads(health.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    preflight = payload.get("preflight_ok")
    if isinstance(preflight, bool):
        return preflight
    return None


def _dashboard_http_ok() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return bool(body.get("ok"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False


def wait_for_real_health(install_root: Path, timeout_sec: float = 90.0) -> bool:
    live = os.environ.get("TRADING_OS_MODE", "").upper() == "LIVE"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        http_ok = _dashboard_http_ok()
        preflight_ok = _read_health_preflight(install_root)

        if live:
            if http_ok and preflight_ok is True:
                _log().info("Dashboard and LIVE preflight health are ready")
                return True
            if http_ok and preflight_ok is False:
                _log().warning("Dashboard responded but health.json preflight_ok=false in LIVE mode")
            elif preflight_ok is False:
                _log().debug("LIVE preflight not ready (preflight_ok=false)")
        elif http_ok:
            _log().info("Dashboard health endpoint responded")
            return True

        time.sleep(1.0)
    return False


def start_supervisor(install_root: Path, python_exe: Path) -> subprocess.Popen:
    supervisor = install_root / "kernel" / "supervisor.py"
    if not supervisor.exists():
        raise FileNotFoundError(f"Supervisor not found: {supervisor}")

    env = os.environ.copy()
    env.setdefault("TRADING_OS_ROOT", str(install_root))
    env.setdefault("TRADING_OS_MODE", "SIMULATION")
    env.setdefault("TRADING_OS_LLM_DECISION_MODE", "ADVISORY")

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    _log().info("Starting supervisor via %s", python_exe)
    proc = subprocess.Popen(
        [str(python_exe), str(supervisor)],
        cwd=str(install_root),
        env=env,
        creationflags=creationflags,
        close_fds=True,
    )
    pid_file(install_root).write_text(str(proc.pid), encoding="utf-8")
    _log().info("Supervisor started with PID %s", proc.pid)
    return proc


def main() -> int:
    install_root = Path(os.environ.get("TRADING_OS_ROOT") or _install_root()).resolve()
    log_path = setup_logging(install_root)
    _log().info("Launcher starting (install_root=%s, log=%s)", install_root, log_path)

    preflight_error = _preflight(install_root)
    if preflight_error:
        _log().error("Preflight failed: %s", preflight_error.splitlines()[0])
        _notify("Trading OS", preflight_error)
        return 1

    config = load_config_env(install_root)
    _log().info(
        "Loaded config (mode=%s, ipc=%s)",
        config.get("TRADING_OS_MODE", "unset"),
        config.get("TRADING_OS_IPC", "unset"),
    )
    apply_config(config)
    apply_dpapi_secrets()
    install_root = Path(os.environ["TRADING_OS_ROOT"]).resolve()

    dashboard_url = config.get("TRADING_OS_DASHBOARD_URL", DEFAULT_DASHBOARD)
    python_exe = resolve_python(install_root)
    _log().info("Using python executable: %s", python_exe)

    running = existing_supervisor(install_root)
    if running:
        _log().info("Existing supervisor PID %s found", running)
        if wait_for_real_health(install_root, timeout_sec=5):
            _log().info("Reusing existing supervisor; opening dashboard")
            webbrowser.open(dashboard_url)
            return 0
        _log().warning(
            "Supervisor PID %s is alive but dashboard health check failed; stopping stale process",
            running,
        )
        if not stop_supervisor(install_root, running):
            msg = (
                "Trading OS supervisor appears stuck and could not be stopped.\n\n"
                f"See {log_path} for details."
            )
            _notify("Trading OS", msg)
            return 1

    if not (install_root / "kernel" / "supervisor.py").exists():
        _log().error("Supervisor script missing under %s", install_root)
        _notify("Trading OS", f"Trading OS not found at {install_root}")
        return 1

    boot_block = _supervisor_boot_blocker(install_root)
    if boot_block:
        _log().error("Supervisor boot blocked by preflight")
        _notify("Trading OS", boot_block)
        return 1

    proc = start_supervisor(install_root, python_exe)
    early_fail = _supervisor_failed_quickly(proc, install_root)
    if early_fail:
        _notify("Trading OS", early_fail)
        return 1

    if wait_for_real_health(install_root):
        _log().info("Opening dashboard at %s", dashboard_url)
        webbrowser.open(dashboard_url)
        return 0

    msg = (
        "Trading OS started but dashboard did not respond in time.\n"
        f"Check logs in {install_root / 'logs'} and {log_path}"
    )
    _log().error("Dashboard health timeout")
    _notify("Trading OS", msg)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
