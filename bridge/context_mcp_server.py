#!/usr/bin/env python3
"""
context_mcp_server.py — Read-only Trading OS context MCP server (stdio).

Exposes bus, health, readiness, instruments, runtime controls, and a unified
agent context bundle. All tools pass through kernel hook pre_tool_call policies.

Usage:
    python bridge/context_mcp_server.py
    python bridge/context_mcp_server.py --test
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))
sys.path.insert(0, str(ROOT / "bridge"))

from bridge.mcp_protocol import MCPServer, run_stdio_server, tool_result  # noqa: E402
from bridge.mcp_tool_gate import gate_mcp_tool_call  # noqa: E402
from ops.agent_context import build_agent_context, summarize_brain  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("context_mcp")


def _tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "read_bus_tail",
            "description": "Tail recent events from nervous/bus.jsonl (optional topic filter)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 20, "maximum": 200},
                    "topic": {"type": "string", "description": "Optional exact topic filter"},
                },
                "required": [],
            },
        },
        {
            "name": "read_health",
            "description": "Kernel health.json plus telemetry /health snapshot",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "read_preflight",
            "description": "Run readiness evaluation (bridge + instruments)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "live": {"type": "boolean", "default": True},
                    "strict_instruments": {"type": "boolean", "default": False},
                    "max_heartbeat_age": {"type": "number", "default": 30},
                },
                "required": [],
            },
        },
        {
            "name": "read_instruments",
            "description": "List enabled instruments from instrument registry",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "read_runtime_controls",
            "description": "Current hot-reloaded runtime controls",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "read_brain_latest",
            "description": "Latest cortex.brain.result summary from bus tail",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 80, "maximum": 400}},
                "required": [],
            },
        },
        {
            "name": "read_agent_context",
            "description": "Unified bundle for external agents (preflight, controls, brain, blockers)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "bus_limit": {"type": "integer", "default": 40, "maximum": 200},
                    "live_preflight": {"type": "boolean", "default": True},
                    "strict_instruments": {"type": "boolean", "default": False},
                },
                "required": [],
            },
        },
    ]


def _handlers() -> Dict[str, Any]:
    from bus import tail  # noqa: WPS433
    from cortex.instrument_registry import InstrumentRegistry  # noqa: WPS433
    from runtime_controls import load_controls  # noqa: WPS433

    def read_bus_tail(args: dict[str, Any]) -> dict[str, Any]:
        limit = min(int(args.get("limit") or 20), 200)
        topic = str(args.get("topic") or "").strip()
        if topic:
            events = [ev for ev in tail(limit * 4) if ev.get("topic") == topic][-limit:]
        else:
            events = tail(limit)
        return {"count": len(events), "events": events}

    def read_health(_args: dict[str, Any]) -> dict[str, Any]:
        ctx = build_agent_context(ROOT, bus_limit=5, live_preflight=False)
        return {"health": ctx.get("health"), "telemetry": ctx.get("telemetry")}

    def read_preflight(args: dict[str, Any]) -> dict[str, Any]:
        ctx = build_agent_context(
            ROOT,
            bus_limit=5,
            live_preflight=bool(args.get("live", True)),
            strict_instruments=bool(args.get("strict_instruments", False)),
            max_heartbeat_age=float(args.get("max_heartbeat_age") or 30),
        )
        return ctx.get("preflight") or {}

    def read_instruments(_args: dict[str, Any]) -> dict[str, Any]:
        registry = InstrumentRegistry()
        rows = []
        for symbol in registry.enabled_symbols():
            meta = registry.get(symbol) or {}
            rows.append(
                {
                    "symbol": symbol,
                    "asset_class": meta.get("asset_class"),
                    "strategies": meta.get("strategies"),
                    "chart": meta.get("chart"),
                }
            )
        return {"count": len(rows), "instruments": rows}

    def read_runtime_controls(_args: dict[str, Any]) -> dict[str, Any]:
        return load_controls()

    def read_brain_latest(args: dict[str, Any]) -> dict[str, Any]:
        limit = min(int(args.get("limit") or 80), 400)
        return summarize_brain(tail(limit))

    def read_agent_context(args: dict[str, Any]) -> dict[str, Any]:
        return build_agent_context(
            ROOT,
            bus_limit=min(int(args.get("bus_limit") or 40), 200),
            live_preflight=bool(args.get("live_preflight", True)),
            strict_instruments=bool(args.get("strict_instruments", False)),
        )

    return {
        "read_bus_tail": read_bus_tail,
        "read_health": read_health,
        "read_preflight": read_preflight,
        "read_instruments": read_instruments,
        "read_runtime_controls": read_runtime_controls,
        "read_brain_latest": read_brain_latest,
        "read_agent_context": read_agent_context,
    }


def _resource_handlers() -> Dict[str, Any]:
    from bus import tail
    from cortex.agent_schemas import export_json_schemas

    root = ROOT / "nervous"

    def read_topic(uri: str) -> dict[str, Any]:
        topic = uri.rsplit("/", 1)[-1].replace(".jsonl", "")
        path = root / "topics" / f"{topic}.jsonl"
        if not path.exists():
            return {"topic": topic, "events": [], "missing": True}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]
        events = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return {"topic": topic, "count": len(events), "events": events}

    return {
        "trading-os://schemas/agent": lambda _uri: export_json_schemas(),
        "trading-os://bus/topic/cortex.brain.result.jsonl": read_topic,
        "trading-os://bus/topic/risk.macro_policy.jsonl": read_topic,
        "trading-os://bus/topic/muscle.order.intent.jsonl": read_topic,
    }


def _resource_schemas() -> list[dict[str, Any]]:
    return [
        {"uri": "trading-os://schemas/agent", "name": "agent_schemas", "mimeType": "application/json"},
        {"uri": "trading-os://bus/topic/cortex.brain.result.jsonl", "name": "brain_results", "mimeType": "application/json"},
        {"uri": "trading-os://bus/topic/risk.macro_policy.jsonl", "name": "macro_policy", "mimeType": "application/json"},
        {"uri": "trading-os://bus/topic/muscle.order.intent.jsonl", "name": "order_intents", "mimeType": "application/json"},
    ]


def build_server() -> MCPServer:
    gate = lambda tool, args: gate_mcp_tool_call(tool, args, actor="mcp.context", server="context")
    handlers = _resource_handlers()
    return MCPServer(
        name="trading-os-context",
        version="1.1.0",
        tools=_tool_schemas(),
        handlers=_handlers(),
        gate=gate,
        resources=_resource_schemas(),
        resource_handlers=handlers,
    )


def run_self_test() -> None:
    server = build_server()
    print("=== Context MCP Self-Test ===")
    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    print("initialize:", init["result"]["serverInfo"]["name"])
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    print("tools:", [t["name"] for t in tools["result"]["tools"]])
    for tool in ("read_runtime_controls", "read_agent_context"):
        resp = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": tool, "arguments": {"bus_limit": 5, "live_preflight": False}},
            }
        )
        body = resp["result"]["content"][0]["text"]
        print(f"{tool}:", body[:240], "...")
    print("=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trading OS read-only context MCP server")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    if args.test:
        run_self_test()
    else:
        logger.info("Context MCP server ready")
        run_stdio_server(build_server())
