"""MCP tools for the world-event domain.
MCP 世界事件域工具。

Wraps the in-memory event scheduler stub (:mod:`xijian_api.stubs.events`)
as MCP tools registered with :mod:`xijian_api.mcp.registry`.  An event
definition carries a trigger config (``time`` / ``interval`` /
``probability`` / ``condition``); the scheduler fires instances when
triggers match, subject to per-event cooldowns and a per-world storm
throttle.
将内存事件调度器桩层封装为 MCP 工具。事件定义携带触发器配置
(``time`` / ``interval`` / ``probability`` / ``condition``)；
调度器在触发器匹配时触发实例，受限于每个事件的冷却时间和每个世界的风暴节流。

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stub's own
input validation.
这些是内部领域工具 (``action_kind=None``)：仅操作内存状态，绕过 A5.2 门禁。

Tools registered / 已注册工具
----------------

* ``event_create``         — create a world event definition / 创建世界事件定义
* ``event_list``           — list event definitions for a world / 列出世界的事件定义
* ``event_get``            — fetch an event definition by id / 按 ID 获取事件定义
* ``event_trigger``        — fire an event instance manually / 手动触发事件实例
* ``event_list_instances`` — list fired event instances / 列出已触发的事件实例
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import events as events_stub


# ---------------------------------------------------------------------------
# Handlers / 处理器
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
# Registration / 注册
# ---------------------------------------------------------------------------


register_tool(
    name="event_create",
    description="Create a world event definition with a trigger config (time / interval / probability / condition). / 创建带触发器配置（时间/间隔/概率/条件）的世界事件定义。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Owning world id. / 所属世界 ID。"},
            "kind": {"type": "string", "description": "Event kind: common / custom / incident. / 事件种类。"},
            "name": {"type": "string", "description": "Human-readable event name. / 人类可读的事件名称。"},
            "description": {"type": "string", "description": "Free-text description. / 自由文本描述。"},
            "trigger_config": {"type": "object", "description": "Trigger config / 触发器配置"},
            "scene_ref_id": {"type": "string", "description": "Optional scene template ref / 可选场景模板引用"},
            "priority": {"type": "integer", "description": "Higher priority wins ties under storm throttle. / 较高优先级在风暴节流下获胜。"},
            "is_enabled": {"type": "boolean", "description": "Whether the scheduler considers this event (default true). / 调度器是否考虑此事件。"},
            "cooldown_until": {"type": "number", "description": "Unix timestamp; scheduler skips this event until then. / Unix 时间戳；调度器在此之前跳过此事件。"},
        },
        "required": ["world_id", "kind", "name", "trigger_config"],
    },
    handler=_event_create,
    action_kind=None,
)


register_tool(
    name="event_list",
    description="List event definitions for a world, optionally filtered by kind and enabled status. / 列出世界的事件定义，可选按种类和启用状态筛选。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "World id to list events for. / 要列出事件的世界 ID。"},
            "kind": {"type": "string", "description": "Optional kind filter / 可选种类筛选"},
            "enabled_only": {"type": "boolean", "description": "If true, exclude disabled events. / 若为 true，排除已禁用事件。"},
        },
        "required": ["world_id"],
    },
    handler=_event_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="event_get",
    description="Fetch a single event definition by id. / 按 ID 获取单个事件定义。",
    input_schema={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "The event id to fetch. / 要获取的事件 ID。"},
        },
        "required": ["event_id"],
    },
    handler=_event_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="event_trigger",
    description="Manually fire an event instance, bypassing the scheduler. Returns the fired instance record. / 手动触发事件实例，绕过调度器。返回触发的实例记录。",
    input_schema={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "The event id to fire. / 要触发的事件 ID。"},
            "world_id": {"type": "string", "description": "Optional world id assertion / 可选的世界 ID 断言"},
            "payload": {"type": "object", "description": "Optional payload overrides / 可选的负载覆盖"},
            "affected_npcs": {"type": "array", "items": {"type": "string"}, "description": "Optional list of affected NPC ids / 可选的影响 NPC ID 列表"},
            "affects_user": {"type": "boolean", "description": "Whether the fired instance affects the user / 触发实例是否影响用户"},
        },
        "required": ["event_id"],
    },
    handler=_event_trigger,
    action_kind=None,
)


register_tool(
    name="event_list_instances",
    description="List fired event instances newest-first, optionally scoped by world or event id. / 列出已触发的事件实例（最新优先），可选按世界或事件 ID 限定范围。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Optional world id filter / 可选的世界 ID 筛选"},
            "event_id": {"type": "string", "description": "Optional event id filter / 可选的事件 ID 筛选"},
            "limit": {"type": "integer", "description": "Max items to return (default 50). / 最大返回条目数。"},
        },
        "required": [],
    },
    handler=_event_list_instances,
    action_kind=None,
    annotations={"readOnlyHint": True},
)
