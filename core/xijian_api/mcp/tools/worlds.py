"""MCP tools for the world domain.

Wraps the in-memory world stub (:mod:`xijian_api.stubs.worlds`) as MCP
tools registered with :mod:`xijian_api.mcp.registry`.  A "world" is an
operator-curated sandbox with its own NPCs, environment state, and
compute config.

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stub's own
input validation.

Tools registered
----------------

World CRUD:

* ``world_create``         — create a world
* ``world_list``           — list every world
* ``world_get``            — fetch a world by id
* ``world_update``         — patch mutable world fields
* ``world_delete``         — delete a world
* ``world_switch_active``  — mark a world as the user's current world

State & views:

* ``world_get_state``      — combined world + environment + compute view
* ``world_summary``        — JSON-friendly overview of every world
* ``world_transition``     — legacy location-transition (audit-logged)

Two-step reset (AC-4):

* ``world_reset_preview``  — issue a reset token
* ``world_reset_confirm``  — confirm and execute the reset
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import worlds as worlds_stub


# ---------------------------------------------------------------------------
# World CRUD handlers
# ---------------------------------------------------------------------------


def _world_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    name = args.get("name")
    if not name:
        raise ToolError("name is required")
    kwargs: dict[str, Any] = {"name": name}
    for key in ("world_doc_path", "config_path", "state_doc_path", "world_id"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    if "is_active" in args and args["is_active"] is not None:
        kwargs["is_active"] = bool(args["is_active"])
    try:
        return worlds_stub.create(**kwargs)
    except worlds_stub.WorldError as exc:
        raise ToolError(str(exc)) from exc


def _world_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    return worlds_stub.list_all()


def _world_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    record = worlds_stub.get(world_id)
    if record is None:
        raise ToolError(f"world {world_id!r} not found")
    return record


_WORLD_PATCH_FIELDS = (
    "name", "world_doc_path", "config_path", "state_doc_path", "is_active",
)


def _world_update(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    patch = {key: args[key] for key in _WORLD_PATCH_FIELDS if key in args}
    try:
        record = worlds_stub.update(world_id, patch)
    except worlds_stub.WorldError as exc:
        raise ToolError(str(exc)) from exc
    if record is None:
        raise ToolError(f"world {world_id!r} not found")
    return record


def _world_delete(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    if not worlds_stub.delete(world_id):
        raise ToolError(f"world {world_id!r} not found")
    return {"deleted": True, "world_id": world_id}


def _world_switch_active(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    try:
        record = worlds_stub.switch_active(world_id)
    except worlds_stub.WorldError as exc:
        raise ToolError(str(exc)) from exc
    if record is None:
        raise ToolError(f"world {world_id!r} not found")
    return record


# ---------------------------------------------------------------------------
# State & views
# ---------------------------------------------------------------------------


def _world_get_state(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    record = worlds_stub.get_state(world_id)
    if record is None:
        raise ToolError(f"world {world_id!r} not found")
    return record


def _world_summary(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    return worlds_stub.summary()


def _world_transition(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    payload = {key: value for key, value in args.items() if key != "world_id"}
    record = worlds_stub.transition(world_id, payload)
    if record is None:
        raise ToolError(f"world {world_id!r} not found")
    return record


# ---------------------------------------------------------------------------
# Two-step reset (AC-4)
# ---------------------------------------------------------------------------


def _world_reset_preview(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    result = worlds_stub.preview_reset(world_id)
    if result is None:
        raise ToolError(f"world {world_id!r} not found")
    return result


def _world_reset_confirm(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    token = args.get("token")
    if not token:
        raise ToolError("token is required")
    result = worlds_stub.confirm_reset(world_id, token)
    if result is None:
        raise ToolError(f"world {world_id!r} not found")
    return result


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


register_tool(
    name="world_create",
    description="Create a new world with its lore, config, and state doc paths.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Operator-given display name."},
            "world_doc_path": {"type": "string", "description": "Path to the world's lore Markdown."},
            "config_path": {"type": "string", "description": "Path to the world's config file."},
            "state_doc_path": {"type": "string", "description": "Path to the world's persistent state file."},
            "world_id": {"type": "string", "description": "Optional explicit id; auto-generated when omitted."},
            "is_active": {"type": "boolean", "description": "Whether the world is in rotation (default true)."},
        },
        "required": ["name"],
    },
    handler=_world_create,
    action_kind=None,
)


register_tool(
    name="world_list",
    description="List every world (active first, then by name).",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_world_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="world_get",
    description="Fetch a single world by id.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to fetch."},
        },
        "required": ["world_id"],
    },
    handler=_world_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="world_update",
    description="Patch mutable world fields (name, doc paths, is_active).",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to update."},
            "name": {"type": "string"},
            "world_doc_path": {"type": "string"},
            "config_path": {"type": "string"},
            "state_doc_path": {"type": "string"},
            "is_active": {"type": "boolean"},
        },
        "required": ["world_id"],
    },
    handler=_world_update,
    action_kind=None,
)


register_tool(
    name="world_delete",
    description="Delete a world by id.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to delete."},
        },
        "required": ["world_id"],
    },
    handler=_world_delete,
    action_kind=None,
    annotations={"destructiveHint": True},
)


register_tool(
    name="world_switch_active",
    description="Mark a world as the user's current world and bump last_active_at.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to switch to."},
        },
        "required": ["world_id"],
    },
    handler=_world_switch_active,
    action_kind=None,
)


register_tool(
    name="world_get_state",
    description="Read a combined world view: record + environment + compute config + NPC count.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to read state for."},
        },
        "required": ["world_id"],
    },
    handler=_world_get_state,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="world_summary",
    description="Return a JSON-friendly overview of every world (counts + per-world snapshot).",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_world_summary,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="world_reset_preview",
    description="Begin the two-step world reset: returns a token to echo back via world_reset_confirm.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to reset."},
        },
        "required": ["world_id"],
    },
    handler=_world_reset_preview,
    action_kind=None,
)


register_tool(
    name="world_reset_confirm",
    description="Confirm and execute a world reset using the token from world_reset_preview. Wipes NPCs, environment, and compute config.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to reset."},
            "token": {"type": "string", "description": "Reset token returned by world_reset_preview."},
        },
        "required": ["world_id", "token"],
    },
    handler=_world_reset_confirm,
    action_kind=None,
    annotations={"destructiveHint": True},
)


register_tool(
    name="world_transition",
    description="Record a location transition for a world (audit-logged); updates last_transport.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to transition."},
            "to_location": {"type": "string", "description": "Destination location label."},
            "transport": {"type": "string", "description": "Transport method label."},
        },
        "required": ["world_id"],
    },
    handler=_world_transition,
    action_kind=None,
)
