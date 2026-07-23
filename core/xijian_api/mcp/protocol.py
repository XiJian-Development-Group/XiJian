"""JSON-RPC 2.0 protocol layer + MCP 1.0 method dispatcher.

This module is the single entry point for ``POST /v1/mcp``.  It
parses the incoming JSON-RPC 2.0 request (single or batch), routes
the method to the appropriate handler, and returns the JSON-RPC 2.0
response.

MCP 1.0 methods implemented
===========================

* ``initialize``           — handshake; returns server capabilities
* ``ping``                 — keepalive
* ``tools/list``           — list every registered tool
* ``tools/call``           — execute a tool (routes through A5.2 gate)
* ``resources/list``       — list read-only resources
* ``resources/read``       — read a resource by URI
* ``prompts/list``         — list prompt templates
* ``prompts/get``          — render a prompt by name

JSON-RPC 2.0 error codes
========================

Per the spec:

* ``-32700`` Parse error
* ``-32600`` Invalid request
* ``-32601`` Method not found
* ``-32602`` Invalid params
* ``-32603`` Internal error
"""

from __future__ import annotations

import logging
from typing import Any

from xijian_api.mcp.registry import (
    ToolError,
    ToolGateError,
    ToolNotFoundError,
    call_tool,
    list_tools,
)
from xijian_api.mcp.resources import list_resources, read_resource
from xijian_api.mcp.prompts import get_prompt, list_prompts


_LOGGER = logging.getLogger("xijian_api.mcp.protocol")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "2025-06-18"  # MCP spec version we follow
SERVER_NAME = "xijian-core"
SERVER_VERSION = "1.0.0"

# JSON-RPC 2.0 error codes
ERR_PARSE_ERROR = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def _make_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(
    req_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


# ---------------------------------------------------------------------------
# MCP method handlers
# ---------------------------------------------------------------------------


def _mcp_initialize(params: dict[str, Any]) -> dict[str, Any]:
    """Return server capabilities + protocol version."""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"listChanged": False, "subscribe": False},
            "prompts": {"listChanged": False},
            "logging": {},
        },
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        },
    }


def _mcp_ping(_params: dict[str, Any]) -> dict[str, Any]:
    return {}


def _mcp_tools_list(_params: dict[str, Any]) -> dict[str, Any]:
    return {"tools": list_tools()}


def _mcp_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise _RpcError(ERR_INVALID_PARAMS, "`name` is required")
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise _RpcError(ERR_INVALID_PARAMS, "`arguments` must be an object")
    world_id = params.get("world_id") or params.get("worldId")
    caller = params.get("_caller")
    try:
        return call_tool(name, arguments, world_id=world_id, caller=caller)
    except ToolNotFoundError:
        raise _RpcError(
            ERR_INVALID_PARAMS,
            "unknown tool: %s" % name,
        )
    except ToolGateError as exc:
        # Gate denials are returned as isError results, not JSON-RPC
        # errors — the model should see the denial and adjust.
        return {
            "content": [{"type": "text", "text": exc.message}],
            "isError": True,
            "_gate": exc.data,
        }
    except ToolError as exc:
        return {
            "content": [{"type": "text", "text": exc.message}],
            "isError": True,
            "_data": exc.data,
        }


def _mcp_resources_list(_params: dict[str, Any]) -> dict[str, Any]:
    return {"resources": list_resources()}


def _mcp_resources_read(params: dict[str, Any]) -> dict[str, Any]:
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri:
        raise _RpcError(ERR_INVALID_PARAMS, "`uri` is required")
    try:
        return read_resource(uri)
    except Exception as exc:  # noqa: BLE001
        raise _RpcError(ERR_INVALID_PARAMS, str(exc))


def _mcp_prompts_list(_params: dict[str, Any]) -> dict[str, Any]:
    return {"prompts": list_prompts()}


def _mcp_prompts_get(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise _RpcError(ERR_INVALID_PARAMS, "`name` is required")
    arguments = params.get("arguments") or {}
    try:
        return get_prompt(name, arguments)
    except Exception as exc:  # noqa: BLE001
        raise _RpcError(ERR_INVALID_PARAMS, str(exc))


#: Method dispatch table.  Each handler takes a params dict and
#: returns the ``result`` field of the JSON-RPC response, or raises
#: :class:`_RpcError` for protocol-level errors.
_METHODS: dict[str, Any] = {
    "initialize": _mcp_initialize,
    "ping": _mcp_ping,
    "tools/list": _mcp_tools_list,
    "tools/call": _mcp_tools_call,
    "resources/list": _mcp_resources_list,
    "resources/read": _mcp_resources_read,
    "prompts/list": _mcp_prompts_list,
    "prompts/get": _mcp_prompts_get,
}


# ---------------------------------------------------------------------------
# Internal RPC error
# ---------------------------------------------------------------------------


class _RpcError(Exception):
    """Protocol-level error that maps to a JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def handle_request(
    payload: Any,
    *,
    caller: str | None = None,
) -> dict[str, Any] | None:
    """Handle a single JSON-RPC 2.0 request dict.

    Returns the response dict, or ``None`` for notifications
    (requests without an ``id`` per JSON-RPC 2.0 §4).
    """
    if not isinstance(payload, dict):
        return _make_error(None, ERR_INVALID_REQUEST, "request must be an object")

    if payload.get("jsonrpc") != "2.0":
        return _make_error(
            payload.get("id"),
            ERR_INVALID_REQUEST,
            "`jsonrpc` must be \"2.0\"",
        )

    method = payload.get("method")
    req_id = payload.get("id")
    is_notification = "id" not in payload

    if not isinstance(method, str) or not method:
        if is_notification:
            return None
        return _make_error(req_id, ERR_INVALID_REQUEST, "`method` is required")

    handler = _METHODS.get(method)
    if handler is None:
        if is_notification:
            return None
        return _make_error(req_id, ERR_METHOD_NOT_FOUND, "method not found: %s" % method)

    params = payload.get("params") or {}
    if not isinstance(params, dict):
        if is_notification:
            return None
        return _make_error(req_id, ERR_INVALID_PARAMS, "`params` must be an object")

    # Inject caller info for tools/call.
    if isinstance(params, dict) and caller is not None:
        params = {**params, "_caller": caller}

    try:
        result = handler(params)
    except _RpcError as exc:
        if is_notification:
            return None
        return _make_error(req_id, exc.code, exc.message, exc.data)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("MCP method %r raised", method)
        if is_notification:
            return None
        return _make_error(
            req_id, ERR_INTERNAL_ERROR, "internal error: %s" % exc,
        )

    if is_notification:
        return None
    return _make_result(req_id, result)


def handle_batch(
    payload: Any,
    *,
    caller: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Handle a JSON-RPC 2.0 request (single or batch).

    * If ``payload`` is a dict → single request (delegates to
      :func:`handle_request`).
    * If ``payload`` is a list → batch request; each element is
      handled independently and non-None responses are collected.
    * If the batch is empty → returns a single error response
      (per JSON-RPC 2.0 §6).
    """
    if isinstance(payload, list):
        if not payload:
            return _make_error(None, ERR_INVALID_REQUEST, "batch must not be empty")
        responses: list[dict[str, Any]] = []
        for item in payload:
            resp = handle_request(item, caller=caller)
            if resp is not None:
                responses.append(resp)
        return responses if responses else None
    return handle_request(payload, caller=caller)


__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "handle_request",
    "handle_batch",
]
