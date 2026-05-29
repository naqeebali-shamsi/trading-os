#!/usr/bin/env python3
"""Shared MCP 1.0 (JSON-RPC 2.0 newline-delimited stdio) server primitives."""
from __future__ import annotations

import json
import logging
import sys
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("mcp_protocol")

ToolHandler = Callable[[Dict[str, Any]], Any]
ResourceHandler = Callable[[str], Any]
GateFn = Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]
PreflightFn = Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]


class MCPError(Exception):
    def __init__(self, code: int, message: str, data: dict | None = None):
        self.code = code
        self.message = message
        self.data = data or {}


def tool_result(data: Any, *, is_error: bool = False) -> Dict[str, Any]:
    if isinstance(data, str):
        text = data
    else:
        text = json.dumps(data, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


def tool_error(message: str, **extra: Any) -> Dict[str, Any]:
    payload = {"error": message, **extra}
    return tool_result(payload, is_error=True)


class MCPServer:
    """Minimal MCP server: initialize, tools/list, tools/call, optional resources."""

    def __init__(
        self,
        *,
        name: str,
        version: str,
        tools: List[Dict[str, Any]],
        handlers: Dict[str, ToolHandler],
        gate: GateFn | None = None,
        preflight: PreflightFn | None = None,
        resources: List[Dict[str, Any]] | None = None,
        resource_handlers: Dict[str, ResourceHandler] | None = None,
    ):
        self.name = name
        self.version = version
        self.tools = tools
        self.handlers = handlers
        self.gate = gate
        self.preflight = preflight
        self.resources = resources or []
        self.resource_handlers = resource_handlers or {}
        self.initialized = False

    def handle(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = msg.get("method", "")
        msg_id = msg.get("id")

        if msg_id is None:
            if method == "notifications/initialized":
                self.initialized = True
            return None

        try:
            if method == "initialize":
                result = self._on_initialize(msg.get("params", {}))
            elif method == "tools/list":
                result = self._on_tools_list()
            elif method == "tools/call":
                result = self._on_tools_call(msg.get("params", {}))
            elif method == "resources/list":
                result = self._on_resources_list()
            elif method == "resources/read":
                result = self._on_resources_read(msg.get("params", {}))
            else:
                raise MCPError(-32601, f"Method not found: {method}", {"method": method})
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except MCPError as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": exc.code, "message": exc.message, "data": exc.data},
            }
        except Exception as exc:
            logger.exception("Unhandled MCP error in %s", method)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": f"Internal error: {exc}"},
            }

    def _on_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        _ = params.get("protocolVersion", "2024-11-05")
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False, "subscribe": False},
            },
            "serverInfo": {"name": self.name, "version": self.version},
        }

    def _on_tools_list(self) -> Dict[str, Any]:
        return {"tools": self.tools}

    def _on_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = str(params.get("name") or "")
        arguments = dict(params.get("arguments") or {})
        logger.info("TOOLS/CALL %s args=%s", tool_name, arguments)

        if tool_name not in self.handlers:
            return tool_error(f"Unknown tool: {tool_name}", tool=tool_name)

        if self.gate is not None:
            blocked = self.gate(tool_name, arguments)
            if blocked is not None:
                return blocked

        if self.preflight is not None:
            failed = self.preflight(tool_name, arguments)
            if failed is not None:
                return failed

        try:
            raw = self.handlers[tool_name](arguments)
            return tool_result(raw)
        except Exception as exc:
            logger.exception("Tool %s failed", tool_name)
            return tool_error(str(exc), tool=tool_name)

    def _on_resources_list(self) -> Dict[str, Any]:
        return {"resources": self.resources}

    def _on_resources_read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        uri = str(params.get("uri") or "")
        handler = self.resource_handlers.get(uri)
        if handler is None:
            return tool_error(f"Unknown resource: {uri}", uri=uri)
        try:
            body = handler(uri)
            text = body if isinstance(body, str) else json.dumps(body, indent=2, default=str)
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
        except Exception as exc:
            logger.exception("Resource read failed for %s", uri)
            return tool_error(str(exc), uri=uri)


def run_stdio_server(server: MCPServer) -> None:
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}), flush=True)
                continue
            response = server.handle(msg)
            if response is not None:
                print(json.dumps(response), flush=True)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
