#!/usr/bin/env python3
"""Write install-time configuration for Trading OS (called by install wizard)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.setup_bridge import setup_bridge  # noqa: E402

try:
    from installer.secrets_store import delete_secret, load_secret, store_secret  # noqa: E402
except ImportError:
    from secrets_store import delete_secret, load_secret, store_secret  # noqa: E402


def _program_data_config_dir() -> Path:
    override = os.environ.get("TRADING_OS_PROGRAMDATA", "").strip()
    if override:
        base = Path(override)
    else:
        base = Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "TradingOS"
    base.mkdir(parents=True, exist_ok=True)
    return base


def read_key_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        key = path.read_text(encoding="utf-8-sig").strip()
    finally:
        path.unlink(missing_ok=True)
    return key


def report_progress(progress_file: Path | None, step: str, detail: str = "") -> None:
    if not progress_file:
        return
    try:
        progress_file.write_text(
            json.dumps({"step": step, "detail": detail}),
            encoding="utf-8",
        )
    except OSError:
        pass


def write_config_env(
    *,
    install_root: Path,
    trading_mode: str,
    llm_decision_mode: str,
    openrouter_key: str = "",
    observe_only: bool = False,
    dashboard_port: int = 8765,
) -> Path:
    cfg_dir = _program_data_config_dir()
    path = cfg_dir / "config.env"
    lines = [
        f"TRADING_OS_ROOT={install_root}",
        f"TRADING_OS_IPC={install_root / 'ipc'}",
        f"TRADING_OS_MODE={trading_mode.upper()}",
        f"TRADING_OS_LLM_DECISION_MODE={llm_decision_mode.upper()}",
        f"TRADING_OS_DASHBOARD_PORT={dashboard_port}",
        f"TRADING_OS_DASHBOARD_URL=http://127.0.0.1:{dashboard_port}/ui",
    ]
    if observe_only:
        lines.append("TRADING_OS_LLM_DISABLED=1")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    (install_root / "config.env").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def write_secrets_yaml(*, install_root: Path, openrouter_key: str = "", mt5_account: str = "", mt5_password: str = "", mt5_server: str = "") -> Path:
    secrets_path = install_root / "config" / "secrets.yaml"
    template_path = install_root / "config" / "secrets.yaml.template"
    if not secrets_path.exists() and template_path.exists():
        secrets_path.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")

    if not secrets_path.exists():
        secrets_path.parent.mkdir(parents=True, exist_ok=True)
        secrets_path.write_text(
            "mt5:\n  account: \"\"\n  password: \"\"\n  server: \"\"\nopenrouter: \"\"\nopenai: \"\"\n",
            encoding="utf-8",
        )

    text = secrets_path.read_text(encoding="utf-8")
    if openrouter_key.strip():
        if "openrouter:" in text:
            lines = []
            for line in text.splitlines():
                if line.startswith("openrouter:"):
                    lines.append(f'openrouter: "{openrouter_key.strip()}"')
                else:
                    lines.append(line)
            text = "\n".join(lines) + "\n"
        else:
            text += f'\nopenrouter: "{openrouter_key.strip()}"\n'

    if mt5_account or mt5_password or mt5_server:
        replacements = {
            'account: "your_account_number"': f'account: "{mt5_account}"',
            'password: "your_password"': f'password: "{mt5_password}"',
            'server: "your_broker_demo_server"': f'server: "{mt5_server}"',
        }
        for old, new in replacements.items():
            value_part = new.split(": ", 1)[1].strip().strip('"')
            if old in text and value_part:
                text = text.replace(old, new)

    secrets_path.write_text(text, encoding="utf-8")
    return secrets_path


def resolve_bootstrap_python(install_root: Path) -> Path:
    bundled = install_root / "runtime" / "python" / "python.exe"
    if bundled.exists():
        return bundled
    return Path(sys.executable)


def _run_pip(pip: Path, cmd: list[str], *, cwd: Path) -> None:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise RuntimeError(f"pip install failed (exit {proc.returncode}): {detail}")


def create_venv(install_root: Path, *, progress_file: Path | None = None) -> Path:
    venv_dir = install_root / "venv"
    venv_py = venv_dir / "Scripts" / "python.exe"
    marker = install_root / ".install-complete"

    if venv_py.exists() and marker.exists():
        report_progress(progress_file, "Python environment", "already configured")
        return venv_py

    report_progress(progress_file, "Python environment", "creating virtual environment")
    if venv_dir.exists():
        shutil.rmtree(venv_dir, ignore_errors=True)

    bootstrap = resolve_bootstrap_python(install_root)
    if not bootstrap.exists():
        raise FileNotFoundError(f"Bootstrap Python not found: {bootstrap}")

    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    runtime_root = install_root / "runtime" / "python"
    if bootstrap.resolve() == (runtime_root / "python.exe").resolve():
        env["PYTHONHOME"] = str(runtime_root)

    subprocess.run([str(bootstrap), "-m", "venv", str(venv_dir)], check=True, cwd=str(install_root), env=env)
    pip = venv_dir / "Scripts" / "pip.exe"
    req = install_root / "requirements.txt"
    if not req.exists():
        req = ROOT / "requirements.txt"

    wheelhouse = install_root / "wheelhouse"
    offline_cmd = [
        str(pip), "install", "-q", "--disable-pip-version-check",
        "--no-index", "--find-links", str(wheelhouse), "-r", str(req),
    ]
    online_cmd = [str(pip), "install", "-q", "--disable-pip-version-check", "-r", str(req)]

    report_progress(progress_file, "Python packages", "installing dependencies (1-2 min)")
    if wheelhouse.exists() and any(wheelhouse.glob("*.whl")):
        try:
            _run_pip(pip, offline_cmd, cwd=install_root)
        except RuntimeError:
            report_progress(progress_file, "Python packages", "retrying with online download")
            _run_pip(pip, online_cmd, cwd=install_root)
    else:
        _run_pip(pip, online_cmd, cwd=install_root)

    report_progress(progress_file, "Python environment", "ready")
    return venv_py


def ensure_runtime_dirs(install_root: Path) -> None:
    for name in ("ipc", "logs", "nervous", "config", "consciousness/traces", "nervous/topics", "intel"):
        (install_root / name).mkdir(parents=True, exist_ok=True)


def write_install_marker(install_root: Path) -> Path:
    marker = install_root / ".install-complete"
    marker.write_text("ok\n", encoding="utf-8")
    return marker


def existing_llm_key(install_root: Path) -> bool:
    try:
        if load_secret("openrouter_api_key"):
            return True
    except Exception:
        pass
    secrets_yaml = install_root / "config" / "secrets.yaml"
    if not secrets_yaml.exists():
        return False
    text = secrets_yaml.read_text(encoding="utf-8")
    return "openrouter:" in text and 'openrouter: ""' not in text


def run_install(
    *,
    install_root: Path,
    trading_mode: str,
    llm_decision_mode: str,
    openrouter_key: str,
    observe_only: bool,
    setup_bridge_flag: bool,
    mt5_account: str = "",
    mt5_password: str = "",
    mt5_server: str = "",
    progress_file: Path | None = None,
) -> dict:
    install_root = install_root.resolve()
    os.environ["TRADING_OS_ROOT"] = str(install_root)

    report_progress(progress_file, "Validation", "checking settings")
    if not observe_only and not openrouter_key.strip() and not existing_llm_key(install_root):
        return {
            "ok": False,
            "install_ok": False,
            "error": "OpenRouter API key required unless observe-only mode is selected.",
            "install_root": str(install_root),
        }

    if observe_only and trading_mode.upper() == "LIVE":
        return {
            "ok": False,
            "install_ok": False,
            "error": "LIVE trading requires an API key and LLM brain. Use SIMULATION for observe-only.",
            "install_root": str(install_root),
        }

    ensure_runtime_dirs(install_root)
    report_progress(progress_file, "Configuration", "creating folders")
    python_exe = create_venv(install_root, progress_file=progress_file)
    report_progress(progress_file, "Configuration", "writing config")
    config_path = write_config_env(
        install_root=install_root,
        trading_mode=trading_mode,
        llm_decision_mode=llm_decision_mode,
        openrouter_key=openrouter_key,
        observe_only=observe_only,
    )
    secret_store_path = None
    yaml_key = ""
    if openrouter_key.strip() and not observe_only:
        try:
            secret_store_path = store_secret("openrouter_api_key", openrouter_key.strip())
        except Exception:
            yaml_key = openrouter_key
    elif observe_only:
        try:
            delete_secret("openrouter_api_key")
        except Exception:
            pass

    secrets_path = write_secrets_yaml(
        install_root=install_root,
        openrouter_key=yaml_key,
        mt5_account=mt5_account,
        mt5_password=mt5_password,
        mt5_server=mt5_server,
    )

    bridge_status = None
    bridge_error = None
    if setup_bridge_flag:
        report_progress(progress_file, "MT5 bridge", "copying EA and creating IPC junction")
        try:
            bridge_status = setup_bridge(root=install_root)
            if not bridge_status.get("ok", False):
                bridge_error = "Bridge setup returned failure"
        except Exception as exc:
            bridge_error = str(exc)
    elif trading_mode.upper() == "LIVE":
        bridge_error = "Bridge setup was skipped; MT5 bridge is required for LIVE mode."

    install_ok = True
    error_msg = None
    if trading_mode.upper() == "LIVE" and setup_bridge_flag and bridge_error:
        install_ok = False
        error_msg = f"MT5 bridge setup failed: {bridge_error}"

    marker_path = None
    if install_ok:
        marker_path = write_install_marker(install_root)
        report_progress(progress_file, "Complete", "Trading OS is configured")
    else:
        report_progress(progress_file, "Failed", error_msg or "Installation failed")

    return {
        "ok": install_ok,
        "install_ok": install_ok,
        "error": error_msg,
        "install_root": str(install_root),
        "python": str(python_exe),
        "config_env": str(config_path),
        "secrets": str(secrets_path),
        "secret_store": str(secret_store_path) if secret_store_path else None,
        "install_marker": str(marker_path) if marker_path else None,
        "observe_only": observe_only,
        "bridge": bridge_status,
        "bridge_error": bridge_error,
    }


def load_bootstrap(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_result(result: dict, *, result_file: Path | None) -> None:
    payload = json.dumps(result, indent=2)
    if result_file:
        result_file.write_text(payload, encoding="utf-8")
    print(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trading OS install configuration writer")
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--bootstrap", default="")
    parser.add_argument("--mode", default="SIMULATION", choices=["SIMULATION", "LIVE"])
    parser.add_argument("--llm-decision-mode", default="ADVISORY", choices=["ADVISORY", "LIVE"])
    parser.add_argument("--openrouter-key", default="")
    parser.add_argument("--key-file", default="")
    parser.add_argument("--observe-only", action="store_true")
    parser.add_argument("--setup-bridge", action="store_true")
    parser.add_argument("--mt5-account", default="")
    parser.add_argument("--mt5-password", default="")
    parser.add_argument("--mt5-server", default="")
    parser.add_argument("--result-file", default="")
    parser.add_argument("--progress-file", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    bootstrap = load_bootstrap(Path(args.bootstrap)) if args.bootstrap else {}
    key_file = args.key_file or bootstrap.get("key_file", "")
    openrouter_key = args.openrouter_key or bootstrap.get("openrouter_key", "")
    if key_file:
        openrouter_key = read_key_file(Path(key_file)) or openrouter_key

    trading_mode = (bootstrap.get("mode") or args.mode).upper()
    llm_mode = (bootstrap.get("llm_decision_mode") or args.llm_decision_mode).upper()
    observe_only = args.observe_only or bool(bootstrap.get("observe_only"))
    setup_bridge_flag = args.setup_bridge or bool(bootstrap.get("setup_bridge"))
    result_file = Path(args.result_file) if args.result_file else None
    progress_file = Path(args.progress_file) if args.progress_file else None

    try:
        result = run_install(
            install_root=Path(args.install_root),
            trading_mode=trading_mode,
            llm_decision_mode=llm_mode,
            openrouter_key=str(openrouter_key or ""),
            observe_only=observe_only,
            setup_bridge_flag=setup_bridge_flag,
            mt5_account=args.mt5_account or str(bootstrap.get("mt5_account", "")),
            mt5_password=args.mt5_password or str(bootstrap.get("mt5_password", "")),
            mt5_server=args.mt5_server or str(bootstrap.get("mt5_server", "")),
            progress_file=progress_file,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "install_ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "install_root": str(Path(args.install_root).resolve()),
        }

    bootstrap_path = Path(args.bootstrap) if args.bootstrap else None
    if bootstrap_path and bootstrap_path.exists():
        bootstrap_path.unlink(missing_ok=True)

    write_result(result, result_file=result_file)
    return 0 if result.get("install_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
