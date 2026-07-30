"""MCP tools for the NPC domain.
MCP NPC 域工具。

Wraps the in-memory NPC stub (:mod:`xijian_api.stubs.npcs`) as MCP
tools registered with :mod:`xijian_api.mcp.registry`.  An NPC is a
world-scoped "background character" with an activity tier
(``high_active`` / ``low_active`` / ``idle``) and a per-NPC compute
budget; the scheduler (``tick_world``) promotes and demotes NPCs based
on budget pressure and idle time.
将内存 NPC 桩层封装为 MCP 工具。NPC 是限定到世界的"背景角色"，带有活动层级
和每个 NPC 的计算预算；调度器根据预算压力与空闲时间升降 NPC。

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stub's own
input validation.
内部领域工具，仅操作内存状态，绕过 A5.2 门禁。

Tools registered / 已注册工具
----------------

* ``npc_create``      — create an NPC in a world / 在世界中创建 NPC
* ``npc_list``        — list NPCs for a world (filterable by tier / alive) / 列出世界的 NPC
* ``npc_get``         — fetch an NPC by id / 按 ID 获取 NPC
* ``npc_set_tier``    — change an NPC's activity tier (audit-logged) / 更改 NPC 活动层级
* ``npc_tick_world``  — run one scheduler pass for a world / 为世界运行一次调度器
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import npcs as npcs_stub


# ---------------------------------------------------------------------------
# Handlers / 处理器
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
# Registration / 注册
# ---------------------------------------------------------------------------


register_tool(
    name="npc_create",
    description="Create a new NPC in a world with persona, tier, and compute budget. / 在世界中创建带人设、层级和计算预算的新 NPC。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Owning world id. / 所属世界 ID。"},
            "name": {"type": "string", "description": "Human-readable NPC name. / 人类可读的 NPC 名称。"},
            "persona_doc": {"type": "string", "description": "Persona / background document text. / 人设/背景文档。"},
            "state_json": {"type": "object", "description": "Free-form state payload. / 自由格式状态负载。"},
            "compute_budget": {"type": "integer", "description": "Per-NPC token/min ceiling. / 每 NPC 每分钟 Token 上限。"},
            "activity_tier": {"type": "string", "description": "Initial tier: high_active / low_active / idle. / 初始层级。"},
            "importance": {"type": "number", "description": "Importance weight used by the demotion order. / 降序排序中使用的权重。"},
            "npc_id": {"type": "string", "description": "Optional explicit id / 可选显式 ID"},
            "is_alive": {"type": "boolean", "description": "Whether the NPC is alive (default true). / NPC 是否存活。"},
        },
        "required": ["world_id", "name"],
    },
    handler=_npc_create,
    action_kind=None,
)


register_tool(
    name="npc_list",
    description="List NPCs in a world, optionally filtered by tier and alive status. / 列出世界中的 NPC，可选按层级和存活状态筛选。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "World id to list NPCs for. / 要列出 NPC 的世界 ID。"},
            "tier": {"type": "string", "description": "Optional tier filter / 可选层级筛选"},
            "alive_only": {"type": "boolean", "description": "If true, exclude dead NPCs. / 若为 true，排除已死亡 NPC。"},
        },
        "required": ["world_id"],
    },
    handler=_npc_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="npc_get",
    description="Fetch a single NPC by id. / 按 ID 获取单个 NPC。",
    input_schema={
        "type": "object",
        "properties": {
            "npc_id": {"type": "string", "description": "The NPC id to fetch. / 要获取的 NPC ID。"},
        },
        "required": ["npc_id"],
    },
    handler=_npc_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="npc_set_tier",
    description="Change an NPC's activity tier (writes an audit-log entry). / 更改 NPC 的活动层级（写入审计日志条目）。",
    input_schema={
        "type": "object",
        "properties": {
            "npc_id": {"type": "string", "description": "The NPC id to update. / 要更新的 NPC ID。"},
            "tier": {"type": "string", "description": "Target tier: high_active / low_active / idle. / 目标层级。"},
        },
        "required": ["npc_id", "tier"],
    },
    handler=_npc_set_tier,
    action_kind=None,
)


register_tool(
    name="npc_tick_world",
    description="Run one NPC scheduler pass for a world: demote over-budget/idle NPCs and stamp last_think_at. / 为世界运行一次 NPC 调度器：降级超预算/空闲 NPC 并更新 last_think_at。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "World id to tick. / 要调度的世界 ID。"},
        },
        "required": ["world_id"],
    },
    handler=_npc_tick_world,
    action_kind=None,
)
