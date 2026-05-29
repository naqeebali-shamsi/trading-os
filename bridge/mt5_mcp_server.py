#!/usr/bin/env python3
"""
mt5_mcp_server.py — Real stdio MCP 1.0 server wrapping MT5 IPC bridge.

Uses JSON-RPC 2.0 over stdio (newline-delimited) per the MCP spec.
Supports initialize, tools/list, tools/call, and notifications/initialized.

Trade tools (place_order, close_position, close_all, modify) require:
  TRADING_OS_MCP_ALLOW_TRADE=1
  hook approval via config/hooks.yaml tool_permissions
  human_approved in arguments OR TRADING_OS_HUMAN_APPROVED=1 for dangerous tools

Usage:
    python bridge/mt5_mcp_server.py
    python bridge/mt5_mcp_server.py --test
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bridge"))

from bridge.mcp_protocol import MCPServer, run_stdio_server, tool_error  # noqa: E402
from bridge.mcp_tool_gate import (  # noqa: E402
    TRADE_TOOLS,
    gate_mcp_tool_call,
    mcp_allow_trade,
    visible_mt5_tools,
)
from bridge.mcp_order_router import route_place_order, route_position_command  # noqa: E402
from mt5_ipc_engine import DEFAULT_WINE_PREFIX, DEFAULT_WORKSPACE, MT5IPCBridge  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("mt5_mcp")


def _tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "name": "ping",
            "description": "Check bridge + MT5 EA heartbeat",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "get_balance",
            "description": "Get account balance, equity, margin, free margin",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "get_positions",
            "description": "List open positions managed by the trading EA",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "get_symbols",
            "description": "Get list of available trading symbols",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "get_rates",
            "description": "Get OHLCV candlestick data for a symbol",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "e.g. EURUSD"},
                    "timeframe": {"type": "integer", "description": "MQL5 timeframe constant", "default": 1},
                    "count": {"type": "integer", "description": "Number of bars", "default": 10, "maximum": 1000},
                },
                "required": ["symbol"],
            },
        },
        {
            "name": "place_order",
            "description": "Place a market order (requires TRADING_OS_MCP_ALLOW_TRADE=1 + hook approval)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "volume": {"type": "number", "description": "Lot size (e.g. 0.01)"},
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "sl": {"type": "number", "description": "Stop loss price (optional)"},
                    "tp": {"type": "number", "description": "Take profit price (optional)"},
                    "comment": {"type": "string", "description": "Order comment", "default": "os_order"},
                    "human_approved": {"type": "boolean", "description": "Required for dangerous tools"},
                },
                "required": ["symbol", "volume", "side"],
            },
        },
        {
            "name": "close_position",
            "description": "Close an open position by ticket ID",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticket": {"type": "string", "description": "Position ticket number"},
                    "human_approved": {"type": "boolean"},
                },
                "required": ["ticket"],
            },
        },
        {
            "name": "close_all",
            "description": "Close all positions managed by the EA",
            "inputSchema": {
                "type": "object",
                "properties": {"human_approved": {"type": "boolean"}},
                "required": [],
            },
        },
        {
            "name": "modify",
            "description": "Modify SL/TP on an open position",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ticket": {"type": "string"},
                    "sl": {"type": "number", "description": "New stop loss price"},
                    "tp": {"type": "number", "description": "New take profit price"},
                    "human_approved": {"type": "boolean"},
                },
                "required": ["ticket"],
            },
        },
    ]


class MT5MCPServer(MCPServer):
    """MT5 MCP with trade tools hidden unless TRADING_OS_MCP_ALLOW_TRADE is set."""

    def _on_tools_list(self) -> Dict[str, Any]:
        return {"tools": visible_mt5_tools(self.tools)}


def build_server(bridge: MT5IPCBridge) -> MT5MCPServer:
    def gate(tool: str, args: dict[str, Any]):
        return gate_mcp_tool_call(tool, args, actor="mcp.mt5", server="mt5")

    def preflight(tool: str, _args: dict[str, Any]):
        if tool in TRADE_TOOLS:
            return None
        health = bridge.health()
        if not health.get("connected", False):
            return tool_error("MT5 not connected", health=health)
        return None

    handlers = {
        "ping": lambda _args: {"health": bridge.health(), "ping_response": bridge.ping()},
        "get_balance": lambda _args: bridge.get_balance(),
        "get_positions": lambda _args: bridge.get_positions(),
        "get_symbols": lambda _args: bridge.get_symbols(),
        "get_rates": lambda args: bridge.get_rates(
            args["symbol"],
            args.get("timeframe", 1),
            args.get("count", 10),
        ),
        "place_order": route_place_order,
        "close_position": lambda args: route_position_command("close_position", args),
        "close_all": lambda _args: route_position_command("close_all", {}),
        "modify": lambda args: route_position_command("modify", args),
    }

    return MT5MCPServer(
        name="mt5-mcp-server",
        version="2.1.0",
        tools=_tool_schemas(),
        handlers=handlers,
        gate=gate,
        preflight=preflight,
    )


def run_self_test(bridge: MT5IPCBridge) -> None:
    print("=== MT5 MCP Server Self-Test ===\n")
    print("Health:", json.dumps(bridge.health(), indent=2))
    server = build_server(bridge)
    init_resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    print("initialize:", init_resp["result"]["serverInfo"])
    list_resp = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = [t["name"] for t in list_resp["result"]["tools"]]
    print("tools/list:", names)
    assert "place_order" not in names or mcp_allow_trade()
    ping_resp = server.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ping", "arguments": {}}}
    )
    print("ping:", ping_resp["result"]["content"][0]["text"][:200])
    blocked = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "place_order",
                "arguments": {"symbol": "EURUSD", "volume": 0.01, "side": "buy"},
            },
        }
    )
    print("place_order gate:", blocked["result"]["content"][0]["text"])
    print("=== Test Complete ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MT5 MCP Server (stdio)")
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--wine-prefix", default=str(DEFAULT_WINE_PREFIX))
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    bridge = MT5IPCBridge(Path(args.workspace), Path(args.wine_prefix))
    if args.test:
        run_self_test(bridge)
    else:
        logger.info("MT5 MCP server ready (trade tools=%s)", mcp_allow_trade())
        run_stdio_server(build_server(bridge))
