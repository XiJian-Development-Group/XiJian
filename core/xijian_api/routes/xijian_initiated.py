"""``/v1/xijian/initiated-actions/*`` 路由 — A7 主动联络。

动作
=======

* ``GET    /v1/xijian/initiated-actions``             — 列表
                                                       (?character_id, ?kind,
                                                        ?status, ?user_response)
* ``POST   /v1/xijian/initiated-actions``             — 创建（手动 /
                                                       开发 / 驱动流程的
                                                       调用方）
* ``GET    /v1/xijian/initiated-actions/<action_id>`` — 获取
* ``POST   /v1/xijian/initiated-actions/<action_id>/respond`` — {user_response:
                                                       accepted | declined |
                                                       ignored}
* ``POST   /v1/xijian/initiated-actions/scan``        — 按需运行一次
                                                       触发器扫描

通知权限（AC-3）
===============================

* ``GET    /v1/xijian/initiated-actions/notifications``            — 全局 +
                                                                    按角色
* ``PATCH  /v1/xijian/initiated-actions/notifications``            — 全局
                                                                    策略
* ``GET    /v1/xijian/initiated-actions/notifications/<character_id>`` — 按角色
* ``PATCH  /v1/xijian/initiated-actions/notifications/<character_id>`` — 按角色

WS 推送
=======

* ``character.initiated_action``   — 创建动作时（AC-1）
* ``character.initiated_response`` — 用户响应时

AC-2（拒绝 → 角色“理解”记忆回写）在
:func:`xijian_api.stubs.character_initiated_actions.respond` 内部发生 —
此蓝图只是转发用户的响应。
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import character_initiated_actions as init_stub


bp = Blueprint("xijian_initiated", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.xijian_initiated")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _require_json() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(
            400,
            "request body must be a JSON object",
            "invalid_request_error",
            code="invalid_body",
        )
    return body


def _error(exc: Exception) -> ApiError:
    return ApiError(
        400, str(exc), "invalid_request_error", code="initiated_action_error"
    )


def _get_or_404(action_id: str) -> dict:
    record = init_stub.get_action(action_id)
    if record is None:
        raise ApiError(
            404, "initiated action not found", "not_found_error",
            code="initiated_action_not_found",
        )
    return record


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/initiated-actions")
def list_initiated_actions():
    args = request.args
    items = init_stub.list_actions(
        character_id=args.get("character_id"),
        kind=args.get("kind"),
        status=args.get("status"),
        user_response=args.get("user_response"),
    )
    return jsonify(paginate(items).to_dict())


@bp.post("/v1/xijian/initiated-actions")
def create_initiated_action():
    body = _require_json()
    character_id = body.get("character_id")
    if not isinstance(character_id, str) or not character_id:
        raise ApiError(
            400, "`character_id` is required", "invalid_request_error",
            code="missing_character_id", param="character_id",
        )
    try:
        record = init_stub.create_action(
            character_id=character_id,
            kind=body.get("kind", init_stub.KIND_MESSAGE),
            payload=body.get("payload"),
        )
    except init_stub.InitiatedActionError as exc:
        raise _error(exc)
    return jsonify(record), 201


@bp.get("/v1/xijian/initiated-actions/<action_id>")
def get_initiated_action(action_id: str):
    return jsonify(_get_or_404(action_id))


@bp.post("/v1/xijian/initiated-actions/<action_id>/respond")
def respond_initiated_action(action_id: str):
    _get_or_404(action_id)
    body = _require_json()
    user_response = body.get("user_response")
    if not isinstance(user_response, str) or user_response not in init_stub.ALL_RESPONSES:
        raise ApiError(
            400,
            f"`user_response` must be one of {init_stub.ALL_RESPONSES}",
            "invalid_request_error",
            code="invalid_user_response", param="user_response",
        )
    try:
        record = init_stub.respond(action_id, user_response)
    except init_stub.InitiatedActionError as exc:
        raise _error(exc)
    return jsonify(record)


@bp.post("/v1/xijian/initiated-actions/scan")
def scan_initiated_actions():
    """按需运行一次触发器扫描（tick 线程也会运行）。"""
    created = init_stub.scan_for_actions()
    return jsonify({
        "scanned": True,
        "created_count": len(created),
        "created": created,
    })


# ---------------------------------------------------------------------------
# 通知权限（AC-3）
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/initiated-actions/notifications")
def get_notifications():
    return jsonify(init_stub.notifications_summary())


@bp.patch("/v1/xijian/initiated-actions/notifications")
def patch_notifications():
    body = _require_json()
    settings = init_stub.set_global_settings(body)
    return jsonify(settings)


@bp.get("/v1/xijian/initiated-actions/notifications/<character_id>")
def get_character_notification(character_id: str):
    return jsonify(init_stub.get_character_config(character_id))


@bp.patch("/v1/xijian/initiated-actions/notifications/<character_id>")
def patch_character_notification(character_id: str):
    body = _require_json()
    try:
        cfg = init_stub.set_character_config(character_id, body)
    except init_stub.InitiatedActionError as exc:
        raise _error(exc)
    return jsonify(cfg)


__all__ = ["bp"]
