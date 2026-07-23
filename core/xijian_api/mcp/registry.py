"""Tool registry and dispatcher for the MCP server.

Tools register themselves at import time via :func:`register_tool`.
The dispatcher :func:`call_tool` looks up the tool by name and, if
the tool declared an ``action_kind``, routes the call through the
A5.2 gate (:func:`xijian_api.stubs.mcp.check`) before executing the
handler.

Gate routing
============

The 8 A5.2 action_kinds
(``file_delete`` / ``file_write`` / ``file_read`` / ``shell`` /
``network`` / ``app_launch`` / ``settings_modify`` / ``system_cmd``)
are the ones the spec says must pass the blacklist/whitelist gate.
Internal domain tools (character CRUD, world management, memory
search, …) only touch in-memory state and therefore skip the gate —
they're protected by the API's own input validation.

A tool declares its gate relationship via ``action_kind``:

* ``action_kind=None``  → no gate (internal domain tool)
* ``action_kind="file_read"`` → gate with that kind; denied calls
  raise :class:`ToolGateError` instead of executing.

Tool spec shape
===============

Each tool is stored as::

    {
        "name": str,
        "description": str,
        "inputSchema": dict,       # JSON Schema for the tool's args
        "annotations": dict | None,  # readOnlyHint / destructiveHint / …
        "handler": callable,       # (args: dict, ctx: dict) -> dict
        "action_kind": str | None, # gate kind, or None to skip gate
    }

The ``handler`` receives the parsed arguments dict and a context
dict carrying ``world_id`` (optional) and ``caller`` info.  It
returns a result dict that the protocol layer wraps into the MCP
``tools/call`` response envelope::

    {"content": [{"type": "text", "text": "..."}], "isError": False}

Handlers may also raise :class:`ToolError` to signal a structured
error (the protocol layer turns it into an ``isError: true`` result
rather than a JSON-RPC error, per the MCP spec).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from xijian_api.stubs import mcp as mcp_stub
from xijian_api.stubs import mcp_rules as rules_stub


_LOGGER = logging.getLogger("xijian_api.mcp.registry")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """Raised by a tool handler to signal a structured error.

    The protocol layer wraps this into an MCP ``tools/call`` result
    with ``isError: true`` rather than a JSON-RPC error response,
    per the MCP spec ("tools/call errors are returned as results,
    not JSON-RPC errors, unless the error is a protocol-level
    mistake like unknown tool name").
    """

    def __init__(self, message: str, *, data: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.data = data


class ToolGateError(ToolError):
    """Raised when the A5.2 gate denies a tool call.

    Carries the gate verdict so the caller / model can understand
    *why* the call was blocked (blacklist hit, lockout, freeze, …).
    """


class ToolNotFoundError(KeyError):
    """Raised when ``call_tool`` is asked for an unknown tool name."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


#: Type alias for tool handler functions.
ToolHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


#: The registry itself.  Keyed by tool name.
_REGISTRY: dict[str, dict[str, Any]] = {}

#: Lock for registry mutations.  Registration happens at import time
#: (single-threaded) but ``call_tool`` may fire from request threads,
#: so we guard reads of the registry dict shape with the GIL + this
#: lock for compound operations.
_LOCK = threading.RLock()


def register_tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    handler: ToolHandler,
    *,
    action_kind: str | None = None,
    annotations: dict[str, Any] | None = None,
) -> None:
    """Register a tool in the MCP registry.

    Parameters
    ----------
    name:
        Dotted snake_case tool name (e.g. ``character_create``).
    description:
        Human-readable description shown to the model in
        ``tools/list``.  Keep it concise but actionable.
    input_schema:
        JSON Schema describing the tool's arguments.  Must be a
        ``{"type": "object", "properties": {...}, "required": [...]}``
        shape.
    handler:
        Callable ``(args: dict, ctx: dict) -> dict``.  The result
        dict should have the shape
        ``{"content": [{"type": "text", "text": "..."}], "isError": False}``.
    action_kind:
        If set, the dispatcher runs the A5.2 gate before calling
        ``handler``.  Must be one of
        :data:`xijian_api.stubs.mcp_rules.VALID_KINDS`.
    annotations:
        Optional MCP tool annotations
        (``readOnlyHint`` / ``destructiveHint`` / ``idempotentHint``
        / ``openWorldHint``).
    """
    if not isinstance(name, str) or not name:
        raise ValueError("tool name is required")
    if action_kind is not None and action_kind not in rules_stub.VALID_KINDS:
        raise ValueError(
            "action_kind must be one of %s, got %r"
            % (sorted(rules_stub.VALID_KINDS), action_kind)
        )
    with _LOCK:
        if name in _REGISTRY:
            _LOGGER.warning("overwriting already-registered tool %r", name)
        _REGISTRY[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
            "annotations": annotations,
            "handler": handler,
            "action_kind": action_kind,
        }
        _LOGGER.debug("registered MCP tool %r (action_kind=%s)", name, action_kind)


def unregister_tool(name: str) -> bool:
    """Remove a tool from the registry.  Returns True if it existed."""
    with _LOCK:
        return _REGISTRY.pop(name, None) is not None


def get_tool(name: str) -> dict[str, Any] | None:
    """Return the internal tool record (including handler) or None."""
    with _LOCK:
        return _REGISTRY.get(name)


def list_tools() -> list[dict[str, Any]]:
    """Return the public tool specs (no handler) for ``tools/list``."""
    with _LOCK:
        out: list[dict[str, Any]] = []
        for record in _REGISTRY.values():
            spec: dict[str, Any] = {
                "name": record["name"],
                "description": record["description"],
                "inputSchema": record["inputSchema"],
            }
            if record["annotations"] is not None:
                spec["annotations"] = record["annotations"]
            out.append(spec)
        # Sort by name for stable output.
        out.sort(key=lambda t: t["name"])
        return out


def list_tool_names() -> list[str]:
    """Return just the tool names, sorted."""
    with _LOCK:
        return sorted(_REGISTRY.keys())


def call_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    world_id: str | None = None,
    caller: str | None = None,
) -> dict[str, Any]:
    """Dispatch a tool call, routing through the A5.2 gate if needed.

    Returns the MCP ``tools/call`` result envelope::

        {"content": [...], "isError": False}

    Raises :class:`ToolNotFoundError` if the tool isn't registered,
    :class:`ToolGateError` if the A5.2 gate denies the call, and
    :class:`ToolError` if the handler raises a structured error.
    """
    arguments = arguments or {}
    with _LOCK:
        record = _REGISTRY.get(name)
    if record is None:
        raise ToolNotFoundError(name)

    ctx: dict[str, Any] = {
        "world_id": world_id,
        "caller": caller,
        "tool_name": name,
    }

    # A5.2 gate — only for tools that declare an action_kind.
    action_kind = record.get("action_kind")
    if action_kind is not None:
        gate_result = mcp_stub.check(
            action_kind=action_kind,
            args=arguments,
            world_id=world_id,
        )
        verdict = gate_result.get("verdict")
        if verdict != mcp_stub.VERDICT_ALLOWED:
            raise ToolGateError(
                "MCP gate denied the call (verdict=%s, blocked=%s)"
                % (verdict, gate_result.get("blocked")),
                data={
                    "verdict": verdict,
                    "blocked": gate_result.get("blocked"),
                    "matched_rule": gate_result.get("matched_rule"),
                    "audit_id": gate_result.get("audit_id"),
                },
            )

    handler: ToolHandler = record["handler"]
    try:
        result = handler(arguments, ctx)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 — wrap as ToolError
        _LOGGER.exception("tool %r handler raised", name)
        raise ToolError(
            "tool %r failed: %s" % (name, exc),
            data={"exception": type(exc).__name__},
        ) from exc

    # Normalise the result envelope.  Handlers may return a bare
    # dict / list / str — we wrap it into the MCP content shape.
    if not isinstance(result, dict) or "content" not in result:
        if isinstance(result, str):
            text = result
        else:
            import json
            text = json.dumps(result, ensure_ascii=False, default=str)
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    elif "isError" not in result:
        result["isError"] = False
    return result


def reset_registry() -> None:
    """Wipe every registered tool.  Used by tests."""
    with _LOCK:
        _REGISTRY.clear()


__all__ = [
    "ToolError",
    "ToolGateError",
    "ToolNotFoundError",
    "ToolHandler",
    "register_tool",
    "unregister_tool",
    "get_tool",
    "list_tools",
    "list_tool_names",
    "call_tool",
    "reset_registry",
]
