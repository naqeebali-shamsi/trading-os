"""Supervisor boot preflight — fail closed before spawning trading layers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from runtime_safety import assert_runtime_safe, current_trading_mode

ROOT = Path(__file__).resolve().parent.parent


class SupervisorPreflightError(RuntimeError):
    """Raised when supervisor boot preflight fails."""


def run_supervisor_preflight(
    root: Path | None = None,
    *,
    require_live_bridge: bool | None = None,
    strict_instruments: bool | None = None,
    max_heartbeat_age: float | None = None,
    exit_on_failure: bool = True,
) -> dict:
    """Validate runtime safety and (for LIVE) full readiness before layer boot."""
    root = Path(root or ROOT)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "nervous"))

    assert_runtime_safe(root)

    from ipc_path import get_ipc_dir  # noqa: WPS433
    from ops.readiness_eval import ReadinessOptions, evaluate_readiness  # noqa: WPS433

    mode = current_trading_mode()
    live = mode == "LIVE"
    if require_live_bridge is None:
        require_live_bridge = live
    if strict_instruments is None:
        strict_instruments = False
    if max_heartbeat_age is None:
        max_heartbeat_age = float(__import__("os").environ.get("TRADING_OS_HEARTBEAT_STALE_SEC", "30"))

    opts = ReadinessOptions(
        live=bool(require_live_bridge),
        strict_instruments=bool(strict_instruments),
        max_heartbeat_age=max_heartbeat_age,
    )
    result = evaluate_readiness(root, opts, ipc_dir=Path(get_ipc_dir()))
    report = result.as_dict()
    report["trading_mode"] = mode

    health_path = root / "kernel" / "health.json"
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(
        json.dumps(
            {
                "preflight_ok": result.ok,
                "trading_mode": mode,
                "ipc_mode": result.ipc_mode,
                "fresh_chart_count": len(result.ipc_mode.get("fresh_charts") or []),
                "enabled_stocks": result.enabled_stocks,
                "readiness_policy": result.readiness_policy,
                "boot_deferred_instruments": result.boot_deferred_instruments,
                "llm": {
                    "last_ok": None,
                    "last_error_code": None,
                    "operator_message": "LLM status pending first brain cycle",
                },
                "ts": __import__("time").time(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if live and not result.ok:
        msg = f"supervisor preflight failed in LIVE mode ({len([c for c in result.checks if not c.get('ok')])} failed checks)"
        if exit_on_failure:
            print(msg, file=sys.stderr)
            print(json.dumps(report, indent=2), file=sys.stderr)
            raise SystemExit(result.exit_code)
        raise SupervisorPreflightError(msg)

    # SIMULATION: bridge offline is allowed but recorded
    if not live and require_live_bridge and not result.ok:
        print("[preflight] warning: bridge not ready in SIMULATION mode", file=sys.stderr)

    return report


def ensure_boot_safe(root: Path | None = None) -> dict:
    """Convenience alias used by supervisor.boot()."""
    return run_supervisor_preflight(root, exit_on_failure=True)
