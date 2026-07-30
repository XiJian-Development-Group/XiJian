"""MCP tools for the A5.2 MCP protection management surface.
MCP A5.2 保护管理面工具。

Wraps the rulebook stub (:mod:`xijian_api.stubs.mcp_rules`) and the
protection orchestrator stub (:mod:`xijian_api.stubs.mcp`) as MCP tools
registered with :mod:`xijian_api.mcp.registry`.
将规则书桩层和保护编排器桩层封装为 MCP 工具。

These are *management* tools — rule CRUD, world policy, safety-stop
lifecycle, audit queries, and snapshot dump/sanitize/restore.  They are
NOT the gate itself: the gate is :func:`xijian_api.stubs.mcp.check`,
which the registry runs automatically for any tool that declares an
``action_kind``.  Every tool here uses ``action_kind=None`` so the
management surface stays operable even while the gate is denying
desktop-control calls.
这些是*管理*工具 — 规则 CRUD、世界策略、安全停止生命周期、审计查询和快照转储/清理/恢复。
它们*不是*门禁本身：门禁是 :func:`xijian_api.stubs.mcp.check`，注册表自动为声明
``action_kind`` 的工具运行。此处所有工具使用 ``action_kind=None``，
使管理面在门禁拒绝桌面控制调用时仍保持可操作。

Tools registered / 已注册工具
----------------

Rules / 规则:

* ``mcp_rule_list``    — list rules (active or all) / 列出规则
* ``mcp_rule_create``  — create a rule / 创建规则
* ``mcp_rule_get``     — fetch a rule by id / 按 ID 获取规则
* ``mcp_rule_update``  — patch mutable rule fields / 修补可变规则字段
* ``mcp_rule_delete``  — delete a rule / 删除规则

World policy / 世界策略:

* ``mcp_policy_get``    — read the per-world MCP policy / 读取世界 MCP 策略
* ``mcp_policy_set``    — mutate the per-world policy / 修改世界策略
* ``mcp_policy_reset``  — drop the per-world policy entry / 删除世界策略条目

Audit / 审计:

* ``mcp_audit_list``   — list audit entries (filtered) / 列出审计条目
* ``mcp_audit_count``  — count audit entries (filtered) / 计数审计条目

Safety stop / 安全停止:

* ``mcp_safety_stop_initiate`` — initiate a safety stop / 发起安全停止
* ``mcp_safety_stop_list``     — list freeze records / 列出冻结记录
* ``mcp_safety_stop_get``      — fetch a freeze by id / 按 ID 获取冻结
* ``mcp_safety_stop_confirm``  — confirm (sanitize + restore) / 确认（清理+恢复）
* ``mcp_safety_stop_cancel``   — cancel a pending freeze / 取消待定冻结

Snapshots / 快照:

* ``mcp_snapshot_list``     — list snapshot summaries / 列出快照摘要
* ``mcp_snapshot_get``      — fetch a snapshot by id / 按 ID 获取快照
* ``mcp_snapshot_create``   — dump a new snapshot / 转储新快照
* ``mcp_snapshot_sanitize`` — sanitize a snapshot in place / 就地清理快照
* ``mcp_snapshot_restore``  — restore live state from a snapshot / 从快照恢复活动状态
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import mcp as mcp_stub
from xijian_api.stubs import mcp_rules as rules_stub


# ---------------------------------------------------------------------------
# Rule handlers / 规则处理器
# ---------------------------------------------------------------------------


def _mcp_rule_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    active_only = bool(args.get("active_only", False))
    action_kind = args.get("action_kind")
    mode = args.get("mode")
    if active_only:
        return rules_stub.list_active(action_kind=action_kind, mode=mode)
    return rules_stub.list_all(action_kind=action_kind, mode=mode)


def _mcp_rule_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    action_kind = args.get("action_kind")
    if not action_kind:
        raise ToolError("action_kind is required")
    pattern = args.get("pattern")
    if not pattern:
        raise ToolError("pattern is required")
    mode = args.get("mode")
    if not mode:
        raise ToolError("mode is required")
    kwargs: dict[str, Any] = {
        "action_kind": action_kind,
        "pattern": pattern,
        "mode": mode,
    }
    if "severity" in args and args["severity"] is not None:
        kwargs["severity"] = args["severity"]
    if "is_active" in args and args["is_active"] is not None:
        kwargs["is_active"] = bool(args["is_active"])
    return rules_stub.create(**kwargs)


def _mcp_rule_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    rule_id = args.get("rule_id")
    if not rule_id:
        raise ToolError("rule_id is required")
    record = rules_stub.get(rule_id)
    if record is None:
        raise ToolError(f"rule {rule_id!r} not found")
    return record


_RULE_PATCH_FIELDS = ("action_kind", "pattern", "mode", "severity", "is_active")


def _mcp_rule_update(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    rule_id = args.get("rule_id")
    if not rule_id:
        raise ToolError("rule_id is required")
    patch: dict[str, Any] = {}
    for key in _RULE_PATCH_FIELDS:
        if key in args and args[key] is not None:
            patch[key] = args[key]
    if not patch:
        raise ToolError("at least one patch field is required")
    record = rules_stub.update(rule_id, patch)
    if record is None:
        raise ToolError(f"rule {rule_id!r} not found")
    return record


def _mcp_rule_delete(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    rule_id = args.get("rule_id")
    if not rule_id:
        raise ToolError("rule_id is required")
    if not rules_stub.delete(rule_id):
        raise ToolError(f"rule {rule_id!r} not found")
    return {"deleted": True, "rule_id": rule_id}


# ---------------------------------------------------------------------------
# World policy handlers / 世界策略处理器
# ---------------------------------------------------------------------------


def _mcp_policy_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    return mcp_stub.get_world_policy(world_id)


def _mcp_policy_set(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    kwargs: dict[str, Any] = {}
    if "default" in args and args["default"] is not None:
        kwargs["default"] = args["default"]
    if "lockout_until" in args and args["lockout_until"] is not None:
        kwargs["lockout_until"] = args["lockout_until"]
    if "clear_lockout" in args and args["clear_lockout"] is not None:
        kwargs["clear_lockout"] = bool(args["clear_lockout"])
    return mcp_stub.set_world_policy(world_id, **kwargs)


def _mcp_policy_reset(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    dropped = mcp_stub.reset_world_policy(world_id)
    return {"reset": True, "world_id": world_id, "dropped": dropped}


# ---------------------------------------------------------------------------
# Audit handlers / 审计处理器
# ---------------------------------------------------------------------------


def _mcp_audit_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    for key in ("action_kind", "world_id", "verdict"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    if "limit" in args and args["limit"] is not None:
        kwargs["limit"] = int(args["limit"])
    return mcp_stub.list_audit(**kwargs)


def _mcp_audit_count(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    for key in ("action_kind", "world_id", "verdict"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    return {"count": mcp_stub.count_audit(**kwargs)}


# ---------------------------------------------------------------------------
# Safety-stop handlers / 安全停止处理器
# ---------------------------------------------------------------------------


def _mcp_safety_stop_initiate(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    for key in ("reason", "world_id", "source"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    return mcp_stub.safety_stop(**kwargs)


def _mcp_safety_stop_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    for key in ("world_id", "status"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    if "limit" in args and args["limit"] is not None:
        kwargs["limit"] = int(args["limit"])
    return mcp_stub.list_freezes(**kwargs)


def _mcp_safety_stop_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    freeze_id = args.get("freeze_id")
    if not freeze_id:
        raise ToolError("freeze_id is required")
    record = mcp_stub.get_freeze(freeze_id)
    if record is None:
        raise ToolError(f"freeze {freeze_id!r} not found")
    return record


def _mcp_safety_stop_confirm(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    freeze_id = args.get("freeze_id")
    if not freeze_id:
        raise ToolError("freeze_id is required")
    return mcp_stub.confirm_safety_stop(freeze_id)


def _mcp_safety_stop_cancel(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    freeze_id = args.get("freeze_id")
    if not freeze_id:
        raise ToolError("freeze_id is required")
    kwargs: dict[str, Any] = {}
    if "reason" in args and args["reason"] is not None:
        kwargs["reason"] = args["reason"]
    return mcp_stub.cancel_safety_stop(freeze_id, **kwargs)


# ---------------------------------------------------------------------------
# Snapshot handlers / 快照处理器
# ---------------------------------------------------------------------------


def _mcp_snapshot_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    for key in ("world_id", "reason"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    if "limit" in args and args["limit"] is not None:
        kwargs["limit"] = int(args["limit"])
    return mcp_stub.list_snapshots(**kwargs)


def _mcp_snapshot_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    snapshot_id = args.get("snapshot_id")
    if not snapshot_id:
        raise ToolError("snapshot_id is required")
    record = mcp_stub.get_snapshot(snapshot_id)
    if record is None:
        raise ToolError(f"snapshot {snapshot_id!r} not found")
    return record


def _mcp_snapshot_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    if "world_id" in args and args["world_id"] is not None:
        kwargs["world_id"] = args["world_id"]
    if "reason" in args and args["reason"] is not None:
        kwargs["reason"] = args["reason"]
    return mcp_stub.dump_snapshot(**kwargs)


def _mcp_snapshot_sanitize(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    snapshot_id = args.get("snapshot_id")
    if not snapshot_id:
        raise ToolError("snapshot_id is required")
    return mcp_stub.sanitize_snapshot(snapshot_id)


def _mcp_snapshot_restore(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    snapshot_id = args.get("snapshot_id")
    if not snapshot_id:
        raise ToolError("snapshot_id is required")
    return mcp_stub.restore_snapshot(snapshot_id)


# ---------------------------------------------------------------------------
# Registration — rules / 注册 — 规则
# ---------------------------------------------------------------------------


register_tool(
    name="mcp_rule_list",
    description="List MCP rules. Set active_only=true to return only active rules. / 列出 MCP 规则。设置 active_only=true 仅返回活跃规则。",
    input_schema={
        "type": "object",
        "properties": {
            "active_only": {"type": "boolean", "description": "If true, return only active rules. / 若为 true，仅返回活跃规则。"},
            "action_kind": {"type": "string", "description": "Filter by action kind / 按操作类型筛选"},
            "mode": {"type": "string", "description": "Filter by mode ('blacklist' or 'whitelist') / 按模式筛选"},
        },
        "required": [],
    },
    handler=_mcp_rule_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_rule_create",
    description="Create an MCP protection rule (blacklist/whitelist entry for the gate). / 创建 MCP 保护规则（门禁的黑名单/白名单条目）。",
    input_schema={
        "type": "object",
        "properties": {
            "action_kind": {"type": "string", "description": "One of the 8 A5.2 action kinds. / 8 种 A5.2 操作类型之一。"},
            "pattern": {"type": "string", "description": "Regex pattern / 正则表达式模式"},
            "mode": {"type": "string", "description": "'blacklist' (block on hit) or 'whitelist' (allow on hit). / 黑名单或白名单。"},
            "severity": {"type": "integer", "description": "1..5 (1 advisory, 5 hard block). Defaults to 3. / 严重级别。"},
            "is_active": {"type": "boolean", "description": "Whether the rule is active. Defaults to true. / 规则是否活跃。"},
        },
        "required": ["action_kind", "pattern", "mode"],
    },
    handler=_mcp_rule_create,
    action_kind=None,
)


register_tool(
    name="mcp_rule_get",
    description="Fetch a single MCP rule by id. / 按 ID 获取单个 MCP 规则。",
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {"type": "string", "description": "The rule id to fetch. / 要获取的规则 ID。"},
        },
        "required": ["rule_id"],
    },
    handler=_mcp_rule_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_rule_update",
    description="Patch mutable MCP rule fields (action_kind, pattern, mode, severity, is_active). / 修补可变 MCP 规则字段。",
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {"type": "string", "description": "The rule id to update. / 要更新的规则 ID。"},
            "action_kind": {"type": "string"},
            "pattern": {"type": "string"},
            "mode": {"type": "string"},
            "severity": {"type": "integer"},
            "is_active": {"type": "boolean"},
        },
        "required": ["rule_id"],
    },
    handler=_mcp_rule_update,
    action_kind=None,
)


register_tool(
    name="mcp_rule_delete",
    description="Delete an MCP rule by id. / 按 ID 删除 MCP 规则。",
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {"type": "string", "description": "The rule id to delete. / 要删除的规则 ID。"},
        },
        "required": ["rule_id"],
    },
    handler=_mcp_rule_delete,
    action_kind=None,
    annotations={"destructiveHint": True},
)


# ---------------------------------------------------------------------------
# Registration — world policy / 注册 — 世界策略
# ---------------------------------------------------------------------------


register_tool(
    name="mcp_policy_get",
    description="Read the per-world MCP policy (default verdict + lockout_until). / 读取世界 MCP 策略（默认判决 + 锁定截止时间）。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to read policy for. / 要读取策略的世界 ID。"},
        },
        "required": ["world_id"],
    },
    handler=_mcp_policy_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_policy_set",
    description="Mutate the per-world MCP policy. / 修改世界 MCP 策略。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to update policy for. / 要更新策略的世界 ID。"},
            "default": {"type": "string", "description": "Default verdict: 'allow' or 'deny'. / 默认判决。"},
            "lockout_until": {"type": "number", "description": "Unix ts until which the world is locked out. / 世界锁定截止的 Unix 时间戳。"},
            "clear_lockout": {"type": "boolean", "description": "If true, clear any active lockout. / 若为 true，清除任何活跃锁定。"},
        },
        "required": ["world_id"],
    },
    handler=_mcp_policy_set,
    action_kind=None,
)


register_tool(
    name="mcp_policy_reset",
    description="Drop the per-world MCP policy entry so the world starts from defaults. / 删除世界 MCP 策略条目，使世界从默认值开始。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "The world id to reset policy for. / 要重置策略的世界 ID。"},
        },
        "required": ["world_id"],
    },
    handler=_mcp_policy_reset,
    action_kind=None,
    annotations={"destructiveHint": True},
)


# ---------------------------------------------------------------------------
# Registration — audit / 注册 — 审计
# ---------------------------------------------------------------------------


register_tool(
    name="mcp_audit_list",
    description="List MCP audit entries (per-call verdicts), newest-first, with optional filters. / 列出 MCP 审计条目（每次调用的判决），最新优先，带可选筛选。",
    input_schema={
        "type": "object",
        "properties": {
            "action_kind": {"type": "string", "description": "Filter by action kind. / 按操作类型筛选。"},
            "world_id": {"type": "string", "description": "Filter by world id. / 按世界 ID 筛选。"},
            "verdict": {"type": "string", "description": "Filter by verdict. / 按判决筛选。"},
            "limit": {"type": "integer", "description": "Max entries to return (default 50). / 最大返回条目数。"},
        },
        "required": [],
    },
    handler=_mcp_audit_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_audit_count",
    description="Count MCP audit entries matching the given filters. / 计数匹配给定筛选条件的 MCP 审计条目。",
    input_schema={
        "type": "object",
        "properties": {
            "action_kind": {"type": "string", "description": "Filter by action kind. / 按操作类型筛选。"},
            "world_id": {"type": "string", "description": "Filter by world id. / 按世界 ID 筛选。"},
            "verdict": {"type": "string", "description": "Filter by verdict. / 按判决筛选。"},
        },
        "required": [],
    },
    handler=_mcp_audit_count,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


# ---------------------------------------------------------------------------
# Registration — safety stop / 注册 — 安全停止
# ---------------------------------------------------------------------------


register_tool(
    name="mcp_safety_stop_initiate",
    description="Initiate a safety stop (freeze MCP, dump snapshot, await confirm/cancel). / 发起安全停止（冻结 MCP、转储快照、等待确认/取消）。",
    input_schema={
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Reason for the safety stop. / 安全停止的原因。"},
            "world_id": {"type": "string", "description": "World id to freeze. / 要冻结的世界 ID。"},
            "source": {"type": "string", "description": "Source of the trigger. / 触发器来源。"},
        },
        "required": [],
    },
    handler=_mcp_safety_stop_initiate,
    action_kind=None,
)


register_tool(
    name="mcp_safety_stop_list",
    description="List safety-stop freeze records, newest-first, with optional filters. / 列出安全停止冻结记录，最新优先，带可选筛选。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Filter by world id. / 按世界 ID 筛选。"},
            "status": {"type": "string", "description": "Filter by status. / 按状态筛选。"},
            "limit": {"type": "integer", "description": "Max entries to return (default 50). / 最大返回条目数。"},
        },
        "required": [],
    },
    handler=_mcp_safety_stop_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_safety_stop_get",
    description="Fetch a single safety-stop freeze record by id. / 按 ID 获取单个安全停止冻结记录。",
    input_schema={
        "type": "object",
        "properties": {
            "freeze_id": {"type": "string", "description": "The freeze id to fetch. / 要获取的冻结 ID。"},
        },
        "required": ["freeze_id"],
    },
    handler=_mcp_safety_stop_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_safety_stop_confirm",
    description="Confirm a safety stop: sanitize the snapshot and restore live state from it. / 确认安全停止：清理快照并从快照恢复活动状态。",
    input_schema={
        "type": "object",
        "properties": {
            "freeze_id": {"type": "string", "description": "The freeze id to confirm. / 要确认的冻结 ID。"},
        },
        "required": ["freeze_id"],
    },
    handler=_mcp_safety_stop_confirm,
    action_kind=None,
)


register_tool(
    name="mcp_safety_stop_cancel",
    description="Cancel a pending safety stop (keeps the freeze on disk for inspection). / 取消待定安全停止（保留冻结在磁盘上供检查）。",
    input_schema={
        "type": "object",
        "properties": {
            "freeze_id": {"type": "string", "description": "The freeze id to cancel. / 要取消的冻结 ID。"},
            "reason": {"type": "string", "description": "Optional reason / 可选原因"},
        },
        "required": ["freeze_id"],
    },
    handler=_mcp_safety_stop_cancel,
    action_kind=None,
)


# ---------------------------------------------------------------------------
# Registration — snapshots / 注册 — 快照
# ---------------------------------------------------------------------------


register_tool(
    name="mcp_snapshot_list",
    description="List MCP snapshot summaries (without payload), newest-first, with optional filters. / 列出 MCP 快照摘要（不含负载），最新优先，带可选筛选。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Filter by world id. / 按世界 ID 筛选。"},
            "reason": {"type": "string", "description": "Filter by reason. / 按原因筛选。"},
            "limit": {"type": "integer", "description": "Max entries to return (default 50). / 最大返回条目数。"},
        },
        "required": [],
    },
    handler=_mcp_snapshot_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_snapshot_get",
    description="Fetch a single MCP snapshot by id (includes payload). / 按 ID 获取单个 MCP 快照（含负载）。",
    input_schema={
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "description": "The snapshot id to fetch. / 要获取的快照 ID。"},
        },
        "required": ["snapshot_id"],
    },
    handler=_mcp_snapshot_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="mcp_snapshot_create",
    description="Dump a new MCP snapshot of the protected state buckets. / 转储受保护状态桶的新 MCP 快照。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Optional world id to scope the snapshot. / 可选的范围限定世界 ID。"},
            "reason": {"type": "string", "description": "Reason tag / 原因标签"},
        },
        "required": [],
    },
    handler=_mcp_snapshot_create,
    action_kind=None,
)


register_tool(
    name="mcp_snapshot_sanitize",
    description="Sanitize a snapshot in place (strip A5.1 forbidden-word substrings from string leaves). / 就地清理快照（从字符串叶子节点移除 A5.1 禁用词子串）。",
    input_schema={
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "description": "The snapshot id to sanitize. / 要清理的快照 ID。"},
        },
        "required": ["snapshot_id"],
    },
    handler=_mcp_snapshot_sanitize,
    action_kind=None,
)


register_tool(
    name="mcp_snapshot_restore",
    description="Restore live state from a snapshot (sanitizes first if not already sanitized). / 从快照恢复活动状态（若尚未清理则先清理）。",
    input_schema={
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "description": "The snapshot id to restore from. / 要恢复的快照 ID。"},
        },
        "required": ["snapshot_id"],
    },
    handler=_mcp_snapshot_restore,
    action_kind=None,
    annotations={"destructiveHint": True},
)
