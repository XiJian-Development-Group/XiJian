"""MCP tools for the NPC domain.

Wraps the in-memory NPC stub (:mod:`xijian_api.stubs.npcs`) as MCP
tools registered with :mod:`xijian_api.mcp.registry`.  An NPC is a
world-scoped "background character" with an activity tier
(``high_active`` / ``low_active`` / ``idle``) and a per-NPC compute
budget; the scheduler (``tick_world``) promotes and demotes NPCs based
on budget pressure and idle time.

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stub's own
input validation.

Tools registered
----------------

* ``npc_create``      — create an NPC in a world
* ``npc_list``        — list NPCs for a world (filterable by tier / alive)
* ``npc_get``         — fetch an NPC by id
* ``npc_set_tier``    — change an NPC's activity tier (audit-logged)
* ``npc_tick_world``  — run one scheduler pass for a world
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import npcs as npcs_stub


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _npc_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    name = args.get("name")
    if not name:
        raise ToolError("name is required")
    kwargs: dict[str, Any] = {"world_id": world_id, "name": name}
    for key in (
        "persona_doc", "state_json", "compute_budget",
        "activity_tier", "importance", "npc_id", "is_alive",
    ):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    try:
        return npcs_stub.create(**kwargs)
    except npcs_stub.NPCError as exc:
        raise ToolError(str(exc)) from exc


def _npc_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    kwargs: dict[str, Any] = {}
    if "tier" in args and args["tier"] is not None:
        kwargs["tier"] = args["tier"]
    if "alive_only" in args and args["alive_only"] is not None:
        kwargs["alive_only"] = bool(args["alive_only"])
    return npcs_stub.list_for_world(world_id, **kwargs)


def _npc_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    npc_id = args.get("npc_id")
    if not npc_id:
        raise ToolError("npc_id is required")
    record = npcs_stub.get(npc_id)
    if record is None:
        raise ToolError(f"npc {npc_id!r} not found")
    return record


def _npc_set_tier(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    npc_id = args.get("npc_id")
    if not npc_id:
        raise ToolError("npc_id is required")
    tier = args.get("tier")
    if not tier:
        raise ToolError("tier is required")
    try:
        record = npcs_stub.set_tier(npc_id, tier)
    except npcs_stub.NPCError as exc:
        raise ToolError(str(exc)) from exc
    if record is None:
        raise ToolError(f"npc {npc_id!r} not found")
    return record


def _npc_tick_world(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    return npcs_stub.tick_world(world_id)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


register_tool(
    name="npc_create",
    description="Create a new NPC in a world with persona, tier, and compute budget.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Owning world id."},
            "name": {"type": "string", "description": "Human-readable NPC name."},
            "persona_doc": {"type": "string", "description": "Persona / background document text."},
            "state_json": {"type": "object", "description": "Free-form state payload (e.g. npc_kind tag)."},
            "compute_budget": {"type": "integer", "description": "Per-NPC token/min ceiling."},
            "activity_tier": {"type": "string", "description": "Initial tier: high_active / low_active / idle."},
            "importance": {"type": "number", "description": "Importance weight used by the demotion order."},
            "npc_id": {"type": "string", "description": "Optional explicit id; auto-generated when omitted."},
            "is_alive": {"type": "boolean", "description": "Whether the NPC is alive (default true)."},
        },
        "required": ["world_id", "name"],
    },
    handler=_npc_create,
    action_kind=None,
)


register_tool(
    name="npc_list",
    description="List NPCs in a world, optionally filtered by tier and alive status.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "World id to list NPCs for."},
            "tier": {"type": "string", "description": "Optional tier filter: high_active / low_active / idle."},
            "alive_only": {"type": "boolean", "description": "If true, exclude dead NPCs."},
        },
        "required": ["world_id"],
    },
    handler=_npc_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="npc_get",
    description="Fetch a single NPC by id.",
    input_schema={
        "type": "object",
        "properties": {
            "npc_id": {"type": "string", "description": "The NPC id to fetch."},
        },
        "required": ["npc_id"],
    },
    handler=_npc_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="npc_set_tier",
    description="Change an NPC's activity tier (writes an audit-log entry).",
    input_schema={
        "type": "object",
        "properties": {
            "npc_id": {"type": "string", "description": "The NPC id to update."},
            "tier": {"type": "string", "description": "Target tier: high_active / low_active / idle."},
        },
        "required": ["npc_id", "tier"],
    },
    handler=_npc_set_tier,
    action_kind=None,
)


register_tool(
    name="npc_tick_world",
    description="Run one NPC scheduler pass for a world: demote over-budget/idle NPCs and stamp last_think_at.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "World id to tick."},
        },
        "required": ["world_id"],
    },
    handler=_npc_tick_world,
    action_kind=None,
)
