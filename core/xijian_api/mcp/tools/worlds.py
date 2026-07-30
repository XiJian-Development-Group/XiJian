"""MCP tools for the world domain.
MCP 世界域工具。

Wraps the in-memory world stub (:mod:`xijian_api.stubs.worlds`) as MCP
tools registered with :mod:`xijian_api.mcp.registry`.  A "world" is an
operator-curated sandbox with its own NPCs, environment state, and
compute config.
将内存世界桩层封装为 MCP 工具。"世界"是经运维策划的沙盒，带自有 NPC、环境状态和计算配置。

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stub's own
input validation.
内部领域工具，仅操作内存状态，绕过 A5.2 门禁。

Tools registered / 已注册工具
----------------

World CRUD / 世界增删改查:

* ``world_create``         — create a world / 创建世界
* ``world_list``           — list every world / 列出所有世界
* ``world_get``            — fetch a world by id / 按 ID 获取世界
* ``world_update``         — patch mutable world fields / 修补可变世界字段
* ``world_delete``         — delete a world / 删除世界
* ``world_switch_active``  — mark a world as the user's current world / 将世界标记为用户当前世界

State & views / 状态与视图:

* ``world_get_state``      — combined world + environment + compute view / 组合的世界+环境+计算视图
* ``world_summary``        — JSON-friendly overview of every world / JSON 友好的所有世界概览
* ``world_transition``     — legacy location-transition (audit-logged) / 遗留位置变迁（审计记录）

Two-step reset (AC-4) / 两步重置 (AC-4):

* ``world_reset_preview``  — issue a reset token / 发出重置令牌
* ``world_reset_confirm``  — confirm and execute the reset / 确认并执行重置
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import worlds as worlds_stub


# ---------------------------------------------------------------------------
# World CRUD handlers / 世界增删改查处理器
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
# State & views / 状态与视图
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
# Two-step reset (AC-4) / 两步重置 (AC-4)
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
# Registration / 注册
# ---------------------------------------------------------------------------


register_tool(
    name="world_create",
    description="Create a new world with its lore, config, and state doc paths. / 创建带传说、配置和状态文档路径的新世界。",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Operator-given display name. / 运维指定的显示名称。"},
            "world_doc_path": {"type": "string", "description": "Path to the world's lore Markdown. / 世界传说的 Markdown 路径。"},
            "config_path": {"type": "string", "description": "Path to the world's config file. / 世界配置文件路径。"},
            "state_doc_path": {"type": "string", "description": "Path to the world's persistent state file. / 世界持久状态文件路径。"},
            "world_id": {"type": "string", "description": "Optional explicit id / 可选显式 ID"},
            "is_active": {"type": "boolean", "description": "Whether the world is in rotation (default true). / 世界是否在轮换中。"},
        },
        "required": ["name"],
    },
    handler=_world_create,
    action_kind=None,
)


register_tool(
    name="world_list",
    description="List every world (active first, then by name). / 列出每个世界（活跃优先，然后按名称排序）。",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_world_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="world_get",
    description="Fetch a single world by id. / 按 ID 获取单个世界。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to fetch. / 要获取的世界 ID。"},
        },
        "required": ["world_id"],
    },
    handler=_world_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="world_update",
    description="Patch mutable world fields (name, doc paths, is_active). / 修补可变世界字段（名称、文档路径、is_active）。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to update. / 要更新的世界 ID。"},
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
    description="Delete a world by id. / 按 ID 删除世界。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to delete. / 要删除的世界 ID。"},
        },
        "required": ["world_id"],
    },
    handler=_world_delete,
    action_kind=None,
    annotations={"destructiveHint": True},
)


register_tool(
    name="world_switch_active",
    description="Mark a world as the user's current world and bump last_active_at. / 将世界标记为用户当前世界并更新 last_active_at。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to switch to. / 要切换到的世界 ID。"},
        },
        "required": ["world_id"],
    },
    handler=_world_switch_active,
    action_kind=None,
)


register_tool(
    name="world_get_state",
    description="Read a combined world view: record + environment + compute config + NPC count. / 读取组合世界视图：记录+环境+计算配置+NPC 计数。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to read state for. / 要读取状态的世界 ID。"},
        },
        "required": ["world_id"],
    },
    handler=_world_get_state,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="world_summary",
    description="Return a JSON-friendly overview of every world (counts + per-world snapshot). / 返回 JSON 友好的所有世界概览（计数+每个世界快照）。",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_world_summary,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="world_reset_preview",
    description="Begin the two-step world reset: returns a token to echo back via world_reset_confirm. / 开始两步世界重置：返回要在 world_reset_confirm 中回传的令牌。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to reset. / 要重置的世界 ID。"},
        },
        "required": ["world_id"],
    },
    handler=_world_reset_preview,
    action_kind=None,
)


register_tool(
    name="world_reset_confirm",
    description="Confirm and execute a world reset using the token from world_reset_preview. Wipes NPCs, environment, and compute config. / 使用 world_reset_preview 的令牌确认并执行世界重置。清除 NPC、环境和计算配置。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to reset. / 要重置的世界 ID。"},
            "token": {"type": "string", "description": "Reset token returned by world_reset_preview. / 由 world_reset_preview 返回的重置令牌。"},
        },
        "required": ["world_id", "token"],
    },
    handler=_world_reset_confirm,
    action_kind=None,
    annotations={"destructiveHint": True},
)


register_tool(
    name="world_transition",
    description="Record a location transition for a world (audit-logged); updates last_transport. / 记录世界的位置变迁（审计记录）；更新 last_transport。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to transition. / 要变迁的世界 ID。"},
            "to_location": {"type": "string", "description": "Destination location label. / 目标位置标签。"},
            "transport": {"type": "string", "description": "Transport method label. / 交通方式标签。"},
        },
        "required": ["world_id"],
    },
    handler=_world_transition,
    action_kind=None,
)
