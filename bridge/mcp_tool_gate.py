#!/usr/bin/env python3
"""Central MCP tool authorization via kernel hook policies."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.mcp_protocol import tool_error  # noqa: E402
from kernel.hooks import HookResult, get_hook_manager  # noqa: E402

TRADE_TOOLS = frozenset({"place_order", "close_position", "close_all", "modify"})
MT5_READ_TOOLS = frozenset({"ping", "get_balance", "get_positions", "get_symbols", "get_rates"})
CONTEXT_TOOLS = frozenset(
    {
        "read_bus_tail",
        "read_health",
        "read_preflight",
        "read_instruments",
        "read_runtime_controls",
        "read_brain_latest",
        "read_agent_context",
    }
)
ALL_KNOWN_TOOLS = TRADE_TOOLS | MT5_READ_TOOLS | CONTEXT_TOOLS


def mcp_allow_trade() -> bool:
    return os.getenv("TRADING_OS_MCP_ALLOW_TRADE", "0").strip().lower() in {"1", "true", "yes", "on"}


def human_approved(arguments: Dict[str, Any]) -> bool:
    if bool(arguments.get("human_approved")):
        return True
    return os.getenv("TRADING_OS_HUMAN_APPROVED", "0").strip().lower() in {"1", "true", "yes", "approved"}


def risk_level_for(tool: str) -> str:
    if tool in TRADE_TOOLS:
        return "critical"
    if tool in CONTEXT_TOOLS or tool in MT5_READ_TOOLS:
        return "low"
    return "medium"


def authorize_mcp_tool(
    tool: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    actor: str = "mcp",
    server: str = "unknown",
) -> HookResult:
    """Run pre_tool_call hooks and MCP-specific trade gates."""
    args = dict(arguments or {})
    if tool in TRADE_TOOLS and not mcp_allow_trade():
        return HookResult(False, "mcp_trade_disabled", policy="mcp_trade_env_gate")

    payload = {
        "tool": tool,
        "tool_name": tool,
        "arguments": args,
        "server": server,
        "human_approved": human_approved(args),
    }
    return get_hook_manager().run(
        "pre_tool_call",
        payload,
        actor=actor,
        risk_level=risk_level_for(tool),
    )


def gate_mcp_tool_call(
    tool: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    actor: str = "mcp",
    server: str = "unknown",
) -> Optional[Dict[str, Any]]:
    """Return MCP error payload when blocked; None when allowed."""
    result = authorize_mcp_tool(tool, arguments, actor=actor, server=server)
    if result.allow:
        return None
    return tool_error(
        result.reason,
        tool=tool,
        policy=result.policy,
        permission=(result.payload_patch or {}).get("permission"),
        server=server,
    )


def visible_mt5_tools(all_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hide trade tools from tools/list unless explicitly enabled."""
    if mcp_allow_trade():
        return all_tools
    blocked = TRADE_TOOLS
    return [tool for tool in all_tools if tool.get("name") not in blocked]
