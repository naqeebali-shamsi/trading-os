"""Configurable readiness / preflight policy loaded from instruments.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Set

INSTRUMENT_GATES = frozenset({"off", "all_enabled", "per_asset_class"})
CHART_GATES = frozenset({"off", "all_present", "enabled_symbols"})


@dataclass(frozen=True)
class ReadinessPolicy:
    instrument_gate: str
    chart_gate: str
    default_boot_required: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument_gate": self.instrument_gate,
            "chart_gate": self.chart_gate,
            "default_boot_required": self.default_boot_required,
        }


def _normalize_gate(value: str, allowed: frozenset[str], field: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise ValueError(f"Invalid {field}={value!r}; allowed: {sorted(allowed)}")
    return normalized


def load_readiness_policy(registry, overrides: Optional[dict[str, Any]] = None) -> ReadinessPolicy:
    """Build effective readiness policy from registry defaults, env, and caller overrides."""
    cfg = dict((getattr(registry, "defaults", None) or {}).get("readiness") or {})
    overrides = overrides or {}

    instrument_gate = (
        overrides.get("instrument_gate")
        or os.environ.get("TRADING_OS_READINESS_INSTRUMENT_GATE", "").strip()
        or cfg.get("instrument_gate")
        or "per_asset_class"
    )
    chart_gate = (
        overrides.get("chart_gate")
        or os.environ.get("TRADING_OS_READINESS_CHART_GATE", "").strip()
        or cfg.get("chart_gate")
        or "enabled_symbols"
    )
    default_boot_required = overrides.get("default_boot_required")
    if default_boot_required is None:
        default_boot_required = cfg.get("default_boot_required", False)

    return ReadinessPolicy(
        instrument_gate=_normalize_gate(instrument_gate, INSTRUMENT_GATES, "instrument_gate"),
        chart_gate=_normalize_gate(chart_gate, CHART_GATES, "chart_gate"),
        default_boot_required=bool(default_boot_required),
    )


def enabled_chart_labels(registry) -> Set[str]:
    labels: Set[str] = set()
    for symbol in registry.enabled_symbols():
        cfg = registry.get(symbol) or {}
        broker = str(cfg.get("broker_symbol") or symbol).strip().upper()
        labels.add(f"chart_{broker}")
    return labels


def chart_in_scope(policy: ReadinessPolicy, chart_label: str, enabled_labels: Iterable[str]) -> bool:
    if policy.chart_gate == "off":
        return False
    if policy.chart_gate == "all_present":
        return True
    return chart_label in set(enabled_labels)


def instrument_blocks_boot(
    policy: ReadinessPolicy,
    registry,
    *,
    enabled: bool,
    ready: bool,
    symbol: str,
) -> bool:
    if not enabled or ready:
        return False
    if policy.instrument_gate == "off":
        return False
    if policy.instrument_gate == "all_enabled":
        return True
    return bool(registry.boot_required(symbol))
