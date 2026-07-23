"""MCP tools for the session domain.

Wraps the in-memory session stub (:mod:`xijian_api.stubs.sessions`) as
MCP tools registered with :mod:`xijian_api.mcp.registry`.  A "session"
is a per-conversation message log keyed by session id.

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stub's own
input validation.  The session stub exposes no ``list_all`` helper, so
``session_list`` reads the ``state.sessions`` container directly.

Tools registered
----------------

* ``session_create``         — create a session
* ``session_get``            — fetch a session by id
* ``session_list``           — list every session
* ``session_append_message`` — append a message to a session
* ``session_list_messages``  — list messages in a session
* ``session_delete``         — delete a session
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import sessions as sessions_stub
from xijian_api.stubs import state


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _session_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    payload: dict[str, Any] = {}
    for key in ("title", "character_id", "world_id"):
        if key in args and args[key] is not None:
            payload[key] = args[key]
    return sessions_stub.create(payload)


def _session_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    session_id = args.get("session_id")
    if not session_id:
        raise ToolError("session_id is required")
    record = sessions_stub.get(session_id)
    if record is None:
        raise ToolError(f"session {session_id!r} not found")
    return record


def _session_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    return list(state.sessions.values())


def _session_append_message(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    session_id = args.get("session_id")
    if not session_id:
        raise ToolError("session_id is required")
    role = args.get("role")
    if not role:
        raise ToolError("role is required")
    content = args.get("content")
    if content is None:
        raise ToolError("content is required")
    payload: dict[str, Any] = {"role": role, "content": content}
    if "name" in args and args["name"] is not None:
        payload["name"] = args["name"]
    message = sessions_stub.append_message(session_id, payload)
    if message is None:
        raise ToolError(f"session {session_id!r} not found")
    return message


def _session_list_messages(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    session_id = args.get("session_id")
    if not session_id:
        raise ToolError("session_id is required")
    messages = sessions_stub.list_messages(session_id)
    if messages is None:
        raise ToolError(f"session {session_id!r} not found")
    return messages


def _session_delete(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    session_id = args.get("session_id")
    if not session_id:
        raise ToolError("session_id is required")
    if not sessions_stub.delete(session_id):
        raise ToolError(f"session {session_id!r} not found")
    return {"deleted": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


register_tool(
    name="session_create",
    description="Create a new session (per-conversation message log).",
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Session title (defaults to '新会话')."},
            "character_id": {"type": "string", "description": "Optional character id to associate."},
            "world_id": {"type": "string", "description": "Optional world id to associate."},
        },
        "required": [],
    },
    handler=_session_create,
    action_kind=None,
)


register_tool(
    name="session_get",
    description="Fetch a single session by id.",
    input_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "The session id to fetch."},
        },
        "required": ["session_id"],
    },
    handler=_session_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="session_list",
    description="List every session record.",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_session_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="session_append_message",
    description="Append a message (role/content) to a session's message log.",
    input_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "The session id to append to."},
            "role": {"type": "string", "description": "Message role (e.g. 'user', 'assistant')."},
            "content": {"type": "string", "description": "Message content text."},
            "name": {"type": "string", "description": "Optional sender name."},
        },
        "required": ["session_id", "role", "content"],
    },
    handler=_session_append_message,
    action_kind=None,
)


register_tool(
    name="session_list_messages",
    description="List every message in a session, oldest-first.",
    input_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "The session id to list messages for."},
        },
        "required": ["session_id"],
    },
    handler=_session_list_messages,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="session_delete",
    description="Delete a session by id.",
    input_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "The session id to delete."},
        },
        "required": ["session_id"],
    },
    handler=_session_delete,
    action_kind=None,
    annotations={"destructiveHint": True},
)
