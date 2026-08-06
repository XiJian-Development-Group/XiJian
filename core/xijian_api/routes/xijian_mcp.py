"""``/v1/xijian/mcp/*`` 路由 — A5.2。

门禁（热路径）
===================

* ``POST   /v1/xijian/mcp/check``              — 在桌面客户端执行
                                                 工具调用前进行
                                                 预检

规则 CRUD
==========

* ``GET    /v1/xijian/mcp/rules``              — 列表 (?active, ?action_kind, ?mode)
* ``POST   /v1/xijian/mcp/rules``              — 创建
* ``GET    /v1/xijian/mcp/rules/<rule_id>``   — 获取
* ``PATCH  /v1/xijian/mcp/rules/<rule_id>``   — 修改
* ``DELETE /v1/xijian/mcp/rules/<rule_id>``   — 删除

审计查询
===========

* ``GET    /v1/xijian/mcp/audit``             — 列表 (?action_kind, ?world_id, ?verdict, ?limit)
* ``GET    /v1/xijian/mcp/audit/count``       — 计数（相同过滤参数）

世界策略
============

* ``GET    /v1/xijian/mcp/policy/<wid>``      — 读取
* ``PUT    /v1/xijian/mcp/policy/<wid>``      — 设置默认 / 清除锁定
* ``DELETE /v1/xijian/mcp/policy/<wid>``      — 重置为默认值

安全停止（冻结状态机）
=======================================

* ``POST   /v1/xijian/mcp/safety_stop``               — 发起（快捷键路径）
* ``GET    /v1/xijian/mcp/safety_stop``               — 列表
* ``GET    /v1/xijian/mcp/safety_stop/<freeze_id>``   — 获取
* ``POST   /v1/xijian/mcp/safety_stop/<freeze_id>/confirm`` — 用户选择“清理并恢复”
* ``POST   /v1/xijian/mcp/safety_stop/<freeze_id>/cancel``  — 用户选择“保持冻结”

快照（规范中“专用备份文件夹”的一半）
==================================================

* ``GET    /v1/xijian/mcp/snapshots``                — 列出摘要
* ``GET    /v1/xijian/mcp/snapshots/<snap_id>``     — 获取
* ``POST   /v1/xijian/mcp/snapshots``                — 显式转储
* ``POST   /v1/xijian/mcp/snapshots/<snap_id>/sanitize`` — 显式脱敏
* ``POST   /v1/xijian/mcp/snapshots/<snap_id>/restore``  — 显式恢复

仅限开发
========

* ``POST   /v1/xijian/mcp/dev/crash``         — 强制规则手册崩溃，
                                                 使测试可以演练规范中的
                                                 “审查模块自身崩溃 →
                                                 降级为最严格档”分支。
                                                 ``XIJIAN_DEV=1``。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import mcp as mcp_stub
from xijian_api.stubs import mcp_rules as rules_stub


bp = Blueprint("xijian_mcp", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.xijian_mcp")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _require_json(silent: bool = False) -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        if silent:
            return {}
        raise ApiError(
            400, "request body must be a JSON object",
            "invalid_request_error", code="invalid_body",
        )
    return body


def _dev_only() -> None:
    if os.environ.get("XIJIAN_DEV") != "1":
        raise ApiError(
            403, "dev-only endpoint", "forbidden_error", code="dev_only",
        )


# ---------------------------------------------------------------------------
# 门禁 — 热路径
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/mcp/check")
def check_route():
    body = _require_json()
    action_kind = body.get("action_kind")
    if not isinstance(action_kind, str) or not action_kind:
        raise ApiError(
            400, "`action_kind` is required", "invalid_request_error",
            code="missing_action_kind", param="action_kind",
        )
    if action_kind not in rules_stub.VALID_KINDS:
        raise ApiError(
            400, "`action_kind` is invalid", "invalid_request_error",
            code="invalid_action_kind", param="action_kind",
        )
    return jsonify(mcp_stub.check(
        action_kind=action_kind,
        args=body.get("args"),
        world_id=body.get("world_id"),
    ))


# ---------------------------------------------------------------------------
# 规则 CRUD
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/mcp/rules")
def list_rules():
    active_only = request.args.get("active", "").lower() in ("1", "true", "yes")
    action_kind = request.args.get("action_kind")
    mode = request.args.get("mode")
    if action_kind is not None and action_kind not in rules_stub.VALID_KINDS:
        raise ApiError(
            400, "`action_kind` is invalid", "invalid_request_error",
            code="invalid_action_kind", param="action_kind",
        )
    if mode is not None and mode not in rules_stub.VALID_MODES:
        raise ApiError(
            400, "`mode` is invalid", "invalid_request_error",
            code="invalid_mode", param="mode",
        )
    if active_only:
        return jsonify({"rules": rules_stub.list_active(
            action_kind=action_kind, mode=mode,
        )})
    return jsonify({"rules": rules_stub.list_all(
        action_kind=action_kind, mode=mode,
    )})


@bp.post("/v1/xijian/mcp/rules")
def create_rule():
    body = _require_json()
    try:
        record = rules_stub.create(
            action_kind=body.get("action_kind"),
            pattern=body.get("pattern", ""),
            mode=body.get("mode"),
            severity=body.get("severity", rules_stub.DEFAULT_SEVERITY),
            is_active=bool(body.get("is_active", True)),
        )
    except rules_stub.MCPRuleError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="rule_error",
        )
    return jsonify(record), 201


@bp.get("/v1/xijian/mcp/rules/<rule_id>")
def get_rule(rule_id: str):
    record = rules_stub.get(rule_id)
    if record is None:
        raise ApiError(
            404, "rule not found", "not_found_error", code="rule_not_found",
        )
    return jsonify(record)


@bp.patch("/v1/xijian/mcp/rules/<rule_id>")
def patch_rule(rule_id: str):
    body = _require_json()
    try:
        record = rules_stub.update(rule_id, body)
    except rules_stub.MCPRuleError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="rule_error",
        )
    if record is None:
        raise ApiError(
            404, "rule not found", "not_found_error", code="rule_not_found",
        )
    return jsonify(record)


@bp.delete("/v1/xijian/mcp/rules/<rule_id>")
def delete_rule(rule_id: str):
    if not rules_stub.delete(rule_id):
        raise ApiError(
            404, "rule not found", "not_found_error", code="rule_not_found",
        )
    return jsonify({"deleted": True, "rule_id": rule_id})


# ---------------------------------------------------------------------------
# 审计查询
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/mcp/audit")
def list_audit():
    args = request.args
    try:
        limit = int(args.get("limit", 50))
    except ValueError:
        limit = 50
    items = mcp_stub.list_audit(
        action_kind=args.get("action_kind"),
        world_id=args.get("world_id"),
        verdict=args.get("verdict"),
        limit=limit,
    )
    return jsonify({"entries": items})


@bp.get("/v1/xijian/mcp/audit/count")
def count_audit():
    args = request.args
    n = mcp_stub.count_audit(
        action_kind=args.get("action_kind"),
        world_id=args.get("world_id"),
        verdict=args.get("verdict"),
    )
    return jsonify({"count": n})


# ---------------------------------------------------------------------------
# 世界策略
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/mcp/policy/<world_id>")
def get_policy(world_id: str):
    return jsonify({
        "world_id": world_id,
        **mcp_stub.get_world_policy(world_id),
    })


@bp.put("/v1/xijian/mcp/policy/<world_id>")
def set_policy(world_id: str):
    body = _require_json()
    try:
        policy = mcp_stub.set_world_policy(
            world_id,
            default=body.get("default"),
            lockout_until=body.get("lockout_until"),
            clear_lockout=bool(body.get("clear_lockout", False)),
        )
    except mcp_stub.MCPError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="policy_error",
        )
    return jsonify({"world_id": world_id, **policy})


@bp.delete("/v1/xijian/mcp/policy/<world_id>")
def reset_policy(world_id: str):
    removed = mcp_stub.reset_world_policy(world_id)
    return jsonify({"reset": True, "removed_entries": removed, "world_id": world_id})


# ---------------------------------------------------------------------------
# 安全停止
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/mcp/safety_stop")
def post_safety_stop():
    body = _require_json()
    try:
        record = mcp_stub.safety_stop(
            reason=body.get("reason"),
            world_id=body.get("world_id"),
            source=body.get("source"),
        )
    except mcp_stub.MCPLockoutError as exc:
        raise ApiError(
            409, str(exc), "invalid_request_error", code="lockout_active",
        )
    except mcp_stub.MCPFrozenError as exc:
        raise ApiError(
            409, str(exc), "invalid_request_error", code="freeze_pending",
        )
    except mcp_stub.MCPError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="safety_stop_error",
        )
    return jsonify(record), 201


@bp.get("/v1/xijian/mcp/safety_stop")
def list_safety_stop():
    args = request.args
    try:
        limit = int(args.get("limit", 50))
    except ValueError:
        limit = 50
    items = mcp_stub.list_freezes(
        world_id=args.get("world_id"),
        status=args.get("status"),
        limit=limit,
    )
    return jsonify({"freezes": items})


@bp.get("/v1/xijian/mcp/safety_stop/<freeze_id>")
def get_safety_stop(freeze_id: str):
    record = mcp_stub.get_freeze(freeze_id)
    if record is None:
        raise ApiError(
            404, "freeze not found", "not_found_error", code="freeze_not_found",
        )
    return jsonify(record)


@bp.post("/v1/xijian/mcp/safety_stop/<freeze_id>/confirm")
def confirm_safety_stop(freeze_id: str):
    try:
        record = mcp_stub.confirm_safety_stop(freeze_id)
    except mcp_stub.MCPError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="safety_stop_error",
        )
    return jsonify(record)


@bp.post("/v1/xijian/mcp/safety_stop/<freeze_id>/cancel")
def cancel_safety_stop(freeze_id: str):
    body = _require_json(silent=True) or {}
    try:
        record = mcp_stub.cancel_safety_stop(
            freeze_id, reason=body.get("reason"),
        )
    except mcp_stub.MCPError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="safety_stop_error",
        )
    return jsonify(record)


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/mcp/snapshots")
def list_snapshots():
    args = request.args
    try:
        limit = int(args.get("limit", 50))
    except ValueError:
        limit = 50
    items = mcp_stub.list_snapshots(
        world_id=args.get("world_id"),
        reason=args.get("reason"),
        limit=limit,
    )
    return jsonify({"snapshots": items})


@bp.get("/v1/xijian/mcp/snapshots/<snapshot_id>")
def get_snapshot(snapshot_id: str):
    record = mcp_stub.get_snapshot(snapshot_id)
    if record is None:
        raise ApiError(
            404, "snapshot not found", "not_found_error", code="snapshot_not_found",
        )
    return jsonify(record)


@bp.post("/v1/xijian/mcp/snapshots")
def post_snapshot():
    body = _require_json()
    try:
        record = mcp_stub.dump_snapshot(
            world_id=body.get("world_id"),
            reason=body.get("reason", mcp_stub.SNAPSHOT_REASON_MANUAL),
        )
    except mcp_stub.MCPError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="snapshot_error",
        )
    return jsonify({k: v for k, v in record.items() if k != "payload"}), 201


@bp.post("/v1/xijian/mcp/snapshots/<snapshot_id>/sanitize")
def sanitize_snapshot(snapshot_id: str):
    try:
        record = mcp_stub.sanitize_snapshot(snapshot_id)
    except mcp_stub.MCPError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="snapshot_error",
        )
    return jsonify({k: v for k, v in record.items() if k != "payload"})


@bp.post("/v1/xijian/mcp/snapshots/<snapshot_id>/restore")
def restore_snapshot(snapshot_id: str):
    try:
        summary = mcp_stub.restore_snapshot(snapshot_id)
    except mcp_stub.MCPError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="snapshot_error",
        )
    return jsonify(summary)


# ---------------------------------------------------------------------------
# 仅限开发 — 演练自崩溃回退路径
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/mcp/dev/crash")
def dev_crash():
    _dev_only()
    # 修补规则手册的 :func:`match_action_rules` 使其抛出异常，
    # 让门禁能演示规范中“审查模块自身崩溃 → 降级为最严格档”
    # 的分支。调用后恢复原函数。
    original = rules_stub.match_action_rules

    def boom(action_kind, payload):
        raise RuntimeError("synthetic crash from dev/crash")

    rules_stub.match_action_rules = boom  # type: ignore[assignment]
    try:
        result = mcp_stub.check(
            action_kind=rules_stub.KIND_SHELL, args={"cmd": "ls"},
            world_id="dev",
        )
    finally:
        rules_stub.match_action_rules = original  # type: ignore[assignment]
    return jsonify(result)


__all__ = ["bp"]
