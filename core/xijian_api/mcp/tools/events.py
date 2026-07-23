"""MCP tools for the world-event domain.

Wraps the in-memory event scheduler stub (:mod:`xijian_api.stubs.events`)
as MCP tools registered with :mod:`xijian_api.mcp.registry`.  An event
definition carries a trigger config (``time`` / ``interval`` /
``probability`` / ``condition``); the scheduler fires instances when
triggers match, subject to per-event cooldowns and a per-world storm
throttle.

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stub's own
input validation.

Tools registered
----------------

* ``event_create``         — create a world event definition
* ``event_list``           — list event definitions for a world
* ``event_get``            — fetch an event definition by id
* ``event_trigger``        — fire an event instance manually
* ``event_list_instances`` — list fired event instances
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import events as events_stub


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _event_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    kind = args.get("kind")
    if not kind:
        raise ToolError("kind is required")
    name = args.get("name")
    if not name:
        raise ToolError("name is required")
    trigger_config = args.get("trigger_config")
    if trigger_config is None:
        raise ToolError("trigger_config is required")
    kwargs: dict[str, Any] = {
        "world_id": world_id,
        "kind": kind,
        "name": name,
        "trigger_config": trigger_config,
    }
    for key in (
        "description", "scene_ref_id", "priority",
        "is_enabled", "cooldown_until",
    ):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    try:
        return events_stub.create_event(**kwargs)
    except events_stub.EventError as exc:
        raise ToolError(str(exc)) from exc


def _event_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    kwargs: dict[str, Any] = {"world_id": world_id}
    if "kind" in args and args["kind"] is not None:
        kwargs["kind"] = args["kind"]
    if "enabled_only" in args and args["enabled_only"] is not None:
        kwargs["enabled_only"] = bool(args["enabled_only"])
    return events_stub.list_events(**kwargs)


def _event_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    event_id = args.get("event_id")
    if not event_id:
        raise ToolError("event_id is required")
    record = events_stub.get_event(event_id)
    if record is None:
        raise ToolError(f"event {event_id!r} not found")
    return record


def _event_trigger(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    event_id = args.get("event_id")
    if not event_id:
        raise ToolError("event_id is required")
    world_id = args.get("world_id")
    if world_id:
        # Soft validation: when the caller asserts a world, the event
        # must belong to it.  ``fire_event`` itself derives the world
        # from the event record, so this is just a guard.
        existing = events_stub.get_event(event_id)
        if existing is None:
            raise ToolError(f"event {event_id!r} not found")
        if existing.get("world_id") != world_id:
            raise ToolError(
                f"event {event_id!r} does not belong to world {world_id!r}"
            )
    kwargs: dict[str, Any] = {}
    if "payload" in args and args["payload"] is not None:
        kwargs["payload"] = args["payload"]
    if "affected_npcs" in args and args["affected_npcs"] is not None:
        kwargs["affected_npcs"] = args["affected_npcs"]
    if "affects_user" in args and args["affects_user"] is not None:
        kwargs["affects_user"] = bool(args["affects_user"])
    record = events_stub.fire_event(event_id, **kwargs)
    if record is None:
        raise ToolError(f"event {event_id!r} not found")
    return record


def _event_list_instances(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    if "world_id" in args and args["world_id"] is not None:
        kwargs["world_id"] = args["world_id"]
    if "event_id" in args and args["event_id"] is not None:
        kwargs["event_id"] = args["event_id"]
    if "limit" in args and args["limit"] is not None:
        kwargs["limit"] = int(args["limit"])
    return events_stub.list_instances(**kwargs)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


register_tool(
    name="event_create",
    description="Create a world event definition with a trigger config (time / interval / probability / condition).",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Owning world id."},
            "kind": {"type": "string", "description": "Event kind: common / custom / incident."},
            "name": {"type": "string", "description": "Human-readable event name."},
            "description": {"type": "string", "description": "Free-text description."},
            "trigger_config": {
                "type": "object",
                "description": "Trigger config; must include a 'type' (time/interval/probability/condition).",
            },
            "scene_ref_id": {"type": "string", "description": "Optional scene template ref; sets needs_scene on fired instances."},
            "priority": {"type": "integer", "description": "Higher priority wins ties under storm throttle."},
            "is_enabled": {"type": "boolean", "description": "Whether the scheduler considers this event (default true)."},
            "cooldown_until": {"type": "number", "description": "Unix timestamp; scheduler skips this event until then."},
        },
        "required": ["world_id", "kind", "name", "trigger_config"],
    },
    handler=_event_create,
    action_kind=None,
)


register_tool(
    name="event_list",
    description="List event definitions for a world, optionally filtered by kind and enabled status.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "World id to list events for."},
            "kind": {"type": "string", "description": "Optional kind filter: common / custom / incident."},
            "enabled_only": {"type": "boolean", "description": "If true, exclude disabled events."},
        },
        "required": ["world_id"],
    },
    handler=_event_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="event_get",
    description="Fetch a single event definition by id.",
    input_schema={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "The event id to fetch."},
        },
        "required": ["event_id"],
    },
    handler=_event_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="event_trigger",
    description="Manually fire an event instance, bypassing the scheduler. Returns the fired instance record.",
    input_schema={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "The event id to fire."},
            "world_id": {"type": "string", "description": "Optional world id assertion; the event must belong to this world."},
            "payload": {"type": "object", "description": "Optional payload overrides merged into the fired instance."},
            "affected_npcs": {"type": "array", "items": {"type": "string"}, "description": "Optional list of affected NPC ids."},
            "affects_user": {"type": "boolean", "description": "Whether the fired instance affects the user."},
        },
        "required": ["event_id"],
    },
    handler=_event_trigger,
    action_kind=None,
)


register_tool(
    name="event_list_instances",
    description="List fired event instances newest-first, optionally scoped by world or event id.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Optional world id filter."},
            "event_id": {"type": "string", "description": "Optional event id filter."},
            "limit": {"type": "integer", "description": "Max items to return (default 50)."},
        },
        "required": [],
    },
    handler=_event_list_instances,
    action_kind=None,
    annotations={"readOnlyHint": True},
)
