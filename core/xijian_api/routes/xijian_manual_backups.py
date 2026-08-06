"""``/v1/backups`` + ``/v1/protected-modules`` 路由 — A1.1。

按 ``docs/Dev. Function List功能清单v2.md`` §A1.1 的 A1.1 手动备份 / 恢复 / 受保护模块端点：

* ``POST /v1/backups``            — 触发手动备份
                                    （按角色、带版本）
* ``GET  /v1/backups``            — 列出备份 (?character_id, ?limit)
* ``GET  /v1/backups/<bid>``      — 获取单个备份
* ``POST /v1/backups/<bid>/restore`` — 恢复（可选 ``scope`` /
                                    ``target_character_id``）
* ``DELETE /v1/backups/<bid>``    — 删除备份
* ``GET  /v1/protected-modules``  — 受保护模块注册表
                                    (?character_id → 按角色关联)
* ``GET  /v1/characters/<cid>/protected-modules`` — 按角色视图
* ``PATCH /v1/characters/<cid>/protected-modules`` — 切换 auto_backup

错误风格与其他 xijian 路由模块一致（带机器可读 ``code`` 的 ApiError）。
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import manual_backups as mb_stub


bp = Blueprint("xijian_manual_backups", __name__)


def _require_json() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(
            400, "request body must be a JSON object",
            "invalid_request_error", code="invalid_body",
        )
    return body


# ---------------------------------------------------------------------------
# 受保护模块（AC-1）
# ---------------------------------------------------------------------------


@bp.get("/v1/protected-modules")
def list_protected_modules():
    character_id = request.args.get("character_id")
    modules = mb_stub.list_protected_modules(character_id=character_id)
    return jsonify(paginate(modules).to_dict())


@bp.get("/v1/characters/<character_id>/protected-modules")
def get_character_protection(character_id: str):
    return jsonify(mb_stub.get_character_protection(character_id))


@bp.patch("/v1/characters/<character_id>/protected-modules")
def patch_character_protection(character_id: str):
    body = _require_json()
    module_name = body.get("module_name")
    enabled = body.get("enabled", body.get("auto_backup", True))
    if not module_name:
        raise ApiError(
            400, "`module_name` is required",
            "invalid_request_error", code="missing_module_name",
            param="module_name",
        )
    try:
        record = mb_stub.set_auto_backup(
            character_id, module_name, bool(enabled)
        )
    except ValueError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error",
            code="unknown_protected_module",
            param="module_name",
        ) from exc
    return jsonify(record)


# ---------------------------------------------------------------------------
# 手动备份（AC-2 / AC-3）
# ---------------------------------------------------------------------------


@bp.post("/v1/backups")
def create_backup():
    body = _require_json()
    character_id = body.get("character_id")
    if not character_id:
        raise ApiError(
            400, "`character_id` is required",
            "invalid_request_error", code="missing_character_id",
            param="character_id",
        )
    scope = body.get("scope", mb_stub.SCOPE_ALL)
    created_by = body.get("created_by", "user")
    if created_by not in {"user", "system"}:
        raise ApiError(
            400, "`created_by` must be 'user' or 'system'",
            "invalid_request_error", code="invalid_created_by",
            param="created_by",
        )
    try:
        record = mb_stub.create_backup(
            character_id, scope=scope, created_by=created_by
        )
    except ValueError as exc:
        code = "invalid_scope" if "scope" in str(exc) else "character_not_found"
        status = 400 if code == "invalid_scope" else 404
        raise ApiError(
            status, str(exc),
            "invalid_request_error" if status == 400 else "not_found_error",
            code=code,
            param="scope" if code == "invalid_scope" else "character_id",
        ) from exc
    return jsonify(record), 201


@bp.get("/v1/backups")
def list_backups():
    character_id = request.args.get("character_id")
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    items = mb_stub.list_backups(character_id=character_id, limit=limit)
    return jsonify(paginate(items).to_dict())


@bp.get("/v1/backups/<backup_id>")
def get_backup(backup_id: str):
    record = mb_stub.get_backup(backup_id)
    if record is None:
        raise ApiError(
            404, "backup not found", "not_found_error",
            code="backup_not_found",
        )
    return jsonify(record)


@bp.delete("/v1/backups/<backup_id>")
def delete_backup(backup_id: str):
    if not mb_stub.delete_backup(backup_id):
        raise ApiError(
            404, "backup not found", "not_found_error",
            code="backup_not_found",
        )
    return jsonify({"deleted": True, "backup_id": backup_id})


@bp.post("/v1/backups/<backup_id>/restore")
def restore_backup(backup_id: str):
    body = _require_json()
    scope = body.get("scope")
    target_character_id = body.get("target_character_id")
    try:
        summary = mb_stub.restore_backup(
            backup_id,
            scope=scope,
            target_character_id=target_character_id,
        )
    except KeyError as exc:
        raise ApiError(
            404, str(exc), "not_found_error", code="backup_not_found",
        ) from exc
    except ValueError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error",
            code="invalid_scope", param="scope",
        ) from exc
    return jsonify(summary)


__all__ = ["bp"]
