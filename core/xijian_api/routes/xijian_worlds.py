"""``/v1/xijian/worlds/*`` 路由 — A4.2。

CRUD
====

* ``GET    /v1/xijian/worlds``                       — 列表
* ``POST   /v1/xijian/worlds``                       — 创建
* ``GET    /v1/xijian/worlds/<wid>``                 — 获取
* ``PATCH  /v1/xijian/worlds/<wid>``                 — 修改可变字段
* ``DELETE /v1/xijian/worlds/<wid>``                 — 删除

生命周期
=========

* ``POST   /v1/xijian/worlds/<wid>/switch``          — 标记为活动
* ``POST   /v1/xijian/worlds/<wid>/reset/preview``   — 启动 AC-4 重置握手
* ``POST   /v1/xijian/worlds/<wid>/reset/confirm``  — 完成重置
* ``POST   /v1/xijian/worlds/<wid>/reset/cancel``   — 丢弃待处理令牌

状态与视图
=============

* ``GET    /v1/xijian/worlds/<wid>/state``           — 组合视图
* ``PATCH  /v1/xijian/worlds/<wid>/state``           — 白名单状态补丁
* ``POST   /v1/xijian/worlds/<wid>/state/doc``       — 仅操作员的路径
                                                       更新
* ``GET    /v1/xijian/worlds/<wid>/summary``         — 同 get_state，
                                                       为测试保留别名
* ``GET    /v1/xijian/worlds/summary``               — 全局摘要

跨模块
============

* ``GET    /v1/xijian/worlds/<wid>/compute``         — 计算配置
* ``PATCH  /v1/xijian/worlds/<wid>/compute``         — 修改计算配置
* ``POST   /v1/xijian/worlds/<wid>/compute/tier``    — 切换活动 tier
                                                       (AC-6 二元开关)
* ``GET    /v1/xijian/worlds/<wid>/environment``     — 环境状态
* ``PATCH  /v1/xijian/worlds/<wid>/environment``     — 修改环境
* ``GET    /v1/xijian/worlds/<wid>/audit``           — 按世界审计日志

旧版别名（pre-A4.2）
=========================

* ``POST   /v1/xijian/worlds/<wid>/transition``      — 旧版位置
                                                       转换（保留）
* ``POST   /v1/xijian/worlds/<wid>/event``           — 旧版按世界
                                                       事件日志（保留）
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import npcs as npcs_stub
from xijian_api.stubs import safety as prot_stub
from xijian_api.stubs import world_audit as audit_stub
from xijian_api.stubs import world_compute_config as wcc_stub
from xijian_api.stubs import world_environment as env_stub
from xijian_api.stubs import worlds as worlds_stub


bp = Blueprint("xijian_worlds", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.xijian_worlds")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _require_json() -> dict:
    """返回解析后的 JSON body，否则抛出 400。"""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(
            400,
            "request body must be a JSON object",
            "invalid_request_error",
            code="invalid_body",
        )
    return body


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/worlds")
def list_worlds():
    return jsonify(paginate(worlds_stub.list_all()).to_dict())


@bp.post("/v1/xijian/worlds")
def create_world():
    body = _require_json()
    name = body.get("name")
    if not isinstance(name, str) or not name:
        raise ApiError(400, "`name` is required", "invalid_request_error",
                       code="missing_name", param="name")
    try:
        record = worlds_stub.create(
            name=name,
            world_doc_path=body.get("world_doc_path", ""),
            config_path=body.get("config_path", ""),
            state_doc_path=body.get("state_doc_path", ""),
            world_id=body.get("world_id"),
            is_active=bool(body.get("is_active", True)),
        )
    except worlds_stub.WorldError as exc:
        raise ApiError(400, str(exc), "invalid_request_error", code="world_error")
    return jsonify(record), 201


@bp.get("/v1/xijian/worlds/summary")
def worlds_summary():
    return jsonify(worlds_stub.summary())


@bp.get("/v1/xijian/worlds/<world_id>")
def get_world(world_id: str):
    record = worlds_stub.get(world_id)
    if record is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/worlds/<world_id>")
def patch_world(world_id: str):
    body = _require_json()
    try:
        record = worlds_stub.update(world_id, body)
    except worlds_stub.WorldError as exc:
        raise ApiError(400, str(exc), "invalid_request_error", code="world_error")
    if record is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(record)


@bp.delete("/v1/xijian/worlds/<world_id>")
def delete_world(world_id: str):
    if not worlds_stub.delete(world_id):
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify({"deleted": True, "world_id": world_id})


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/worlds/<world_id>/switch")
def switch_active(world_id: str):
    try:
        record = worlds_stub.switch_active(world_id)
    except worlds_stub.WorldError as exc:
        raise ApiError(409, str(exc), "world_error", code="world_inactive")
    if record is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(record)


@bp.post("/v1/xijian/worlds/<world_id>/reset/preview")
def reset_preview(world_id: str):
    out = worlds_stub.preview_reset(world_id)
    if out is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(out)


@bp.post("/v1/xijian/worlds/<world_id>/reset/confirm")
def reset_confirm(world_id: str):
    body = _require_json()
    token = body.get("reset_token")
    if not isinstance(token, str) or not token:
        raise ApiError(
            400, "`reset_token` is required", "invalid_request_error",
            code="missing_reset_token", param="reset_token",
        )
    out = worlds_stub.confirm_reset(world_id, token)
    if out is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    if not out.get("ok"):
        # 按规范的双重确认模式将错误映射为 HTTP 状态码。
        err = out.get("error")
        if err == "token_expired":
            raise ApiError(
                408, "reset token expired; start over",
                "world_error", code="reset_token_expired",
            )
        if err == "token_mismatch":
            raise ApiError(
                403, "reset token does not match",
                "world_error", code="reset_token_mismatch",
            )
        if err == "no_pending_reset":
            raise ApiError(
                409, "no pending reset for this world",
                "world_error", code="no_pending_reset",
            )
        raise ApiError(400, err or "reset failed", "world_error", code="reset_failed")
    return jsonify(out["world"])


@bp.post("/v1/xijian/worlds/<world_id>/reset/cancel")
def reset_cancel(world_id: str):
    return jsonify(worlds_stub.cancel_reset(world_id))


# ---------------------------------------------------------------------------
# 状态与视图
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/worlds/<world_id>/state")
def get_world_state(world_id: str):
    record = worlds_stub.get_state(world_id)
    if record is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/worlds/<world_id>/state")
def patch_world_state(world_id: str):
    body = _require_json()
    state_blob, error_key = worlds_stub.update_state(
        world_id, body, protection_enabled=prot_stub.is_enabled()
    )
    if error_key == "not_found":
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    if error_key == "protection_disabled":
        raise ApiError(
            403, "protection system is disabled",
            "protection_error", code="protection_disabled",
        )
    return jsonify({"world_id": world_id, "state": state_blob})


@bp.post("/v1/xijian/worlds/<world_id>/state/doc")
def patch_state_doc(world_id: str):
    body = _require_json()
    record = worlds_stub.patch_state_doc(
        world_id,
        world_doc_path=body.get("world_doc_path"),
        config_path=body.get("config_path"),
        state_doc_path=body.get("state_doc_path"),
    )
    if record is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(record)


@bp.get("/v1/xijian/worlds/<world_id>/summary")
def world_summary(world_id: str):
    return get_world_state(world_id)


# ---------------------------------------------------------------------------
# 跨模块视图
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/worlds/<world_id>/compute")
def get_compute(world_id: str):
    cfg = wcc_stub.summary(world_id)
    if cfg is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(cfg)


@bp.patch("/v1/xijian/worlds/<world_id>/compute")
def patch_compute(world_id: str):
    body = _require_json()
    try:
        cfg = wcc_stub.update(world_id, body)
    except wcc_stub.ComputeConfigError as exc:
        raise ApiError(400, str(exc), "invalid_request_error", code="compute_error")
    if cfg is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(cfg)


@bp.post("/v1/xijian/worlds/<world_id>/compute/tier")
def flip_compute_tier(world_id: str):
    body = _require_json()
    tier = body.get("active_tier")
    if not isinstance(tier, str):
        raise ApiError(
            400, "`active_tier` is required", "invalid_request_error",
            code="missing_active_tier", param="active_tier",
        )
    try:
        cfg = wcc_stub.set_tier(world_id, tier)
    except wcc_stub.ComputeConfigError as exc:
        raise ApiError(400, str(exc), "invalid_request_error", code="compute_error")
    if cfg is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(cfg)


@bp.get("/v1/xijian/worlds/<world_id>/environment")
def get_environment(world_id: str):
    env = env_stub.get(world_id)
    if env is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(env)


@bp.patch("/v1/xijian/worlds/<world_id>/environment")
def patch_environment(world_id: str):
    body = _require_json()
    if worlds_stub.get(world_id) is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    record = env_stub.patch_environment(world_id, body)
    return jsonify(record)


@bp.get("/v1/xijian/worlds/<world_id>/audit")
def get_world_audit(world_id: str):
    if worlds_stub.get(world_id) is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    body = request.args
    action = body.get("action")
    try:
        limit = int(body.get("limit", 50))
    except ValueError:
        limit = 50
    return jsonify({"world_id": world_id, "entries": audit_stub.list_log(
        world_id=world_id, action=action, limit=limit
    )})


# ---------------------------------------------------------------------------
# NPC 便捷入口 — 从世界层面列出 NPC
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/worlds/<world_id>/npcs")
def list_world_npcs(world_id: str):
    if worlds_stub.get(world_id) is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    tier = request.args.get("tier")
    alive_only = request.args.get("alive_only", "false").lower() in ("1", "true")
    return jsonify({
        "world_id": world_id,
        "npcs": npcs_stub.list_for_world(world_id, tier=tier, alive_only=alive_only),
    })


@bp.get("/v1/xijian/worlds/<world_id>/compute/summary")
def world_compute_summary(world_id: str):
    if worlds_stub.get(world_id) is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(npcs_stub.compute_world_budget(world_id))


# ---------------------------------------------------------------------------
# 旧版别名（pre-A4.2 兼容）
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/worlds/<world_id>/transition")
def transition(world_id: str):
    body = _require_json()
    if "to_location" not in body:
        raise ApiError(
            400, "`to_location` is required", "invalid_request_error",
            code="missing_to_location", param="to_location",
        )
    record = worlds_stub.transition(world_id, body)
    if record is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(record)


@bp.post("/v1/xijian/worlds/<world_id>/event")
def add_event(world_id: str):
    body = _require_json()
    event = worlds_stub.add_event(world_id, body)
    if event is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(event), 201


__all__ = ["bp"]
