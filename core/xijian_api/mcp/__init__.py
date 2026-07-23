"""MCP (Model Context Protocol) server package — JSON-RPC 2.0 over HTTP.

Implements the MCP 1.0 spec as a lightweight JSON-RPC 2.0 handler
mounted on the existing Flask app at ``POST /v1/mcp``.  The official
MCP Python SDK is ASGI-oriented and can't mount on Flask (WSGI) without
an adapter; hand-rolling the protocol is lighter and more controllable.

Layout
======

* :mod:`xijian_api.mcp.protocol`  — JSON-RPC 2.0 envelope + MCP 1.0
  method dispatcher (initialize / ping / tools/list / tools/call /
  resources/list / resources/read / prompts/list / prompts/get).
* :mod:`xijian_api.mcp.registry`  — tool registry.  Tools register
  via :func:`~xijian_api.mcp.registry.register_tool`; the dispatcher
  routes desktop-control tools through the A5.2 gate
  (:func:`xijian_api.stubs.mcp.check`) before execution.
* :mod:`xijian_api.mcp.resources` — read-only resource views.
* :mod:`xijian_api.mcp.prompts`   — prompt templates.
* :mod:`xijian_api.mcp.tools`     — tool modules organised by domain
  (characters, worlds, memory, npcs, economy, events, sessions,
  settings, files, desktop, protection).

Design decisions
================

* **Single endpoint** — ``POST /v1/mcp`` accepts a JSON-RPC 2.0
  request (or a batch) and returns the matching response.  Stateless
  so it scales trivially.
* **A5.2 gate routing** — tools that touch the user's machine
  (file_read / file_write / file_delete / shell / app_launch / …)
  declare an ``action_kind``; the dispatcher runs
  :func:`mcp_stub.check` first and refuses the call on denial.
  Internal domain tools (character CRUD, world management, …) skip
  the gate — they're protected by the API's own validation and only
  mutate in-memory state.
* **Tool naming** — domain-prefixed snake_case
  (``character_create``, ``world_list``, ``memory_search``,
  ``file_read``, ``file_write``, ``file_list`` …) for discoverability.
"""

from __future__ import annotations

from xijian_api.mcp.protocol import handle_request, handle_batch
from xijian_api.mcp.registry import (
    call_tool,
    list_tools,
    register_tool,
    reset_registry,
)

# Import the tools package last so that every tool module registers
# itself via register_tool() at import time.  This side-effect import
# must happen after the registry is importable (it is — the line
# above already imported it).  Placed at the bottom to avoid
# circular-import issues: tools → registry → (no back-ref to __init__).
from xijian_api.mcp import tools as _tools  # noqa: F401

__all__ = [
    "handle_request",
    "handle_batch",
    "call_tool",
    "list_tools",
    "register_tool",
    "reset_registry",
]
