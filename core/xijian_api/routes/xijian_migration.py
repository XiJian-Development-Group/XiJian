"""``/v1/xijian/migration/*`` routes — legacy data migration.

旧数据迁移路由 — 查询迁移状态、冲突清单并解决冲突。

Endpoints
---------
* ``GET  /v1/xijian/migration/status``   — overall migration status
* ``GET  /v1/xijian/migration/conflicts`` — pending conflict list
* ``POST /v1/xijian/migration/resolve``   — resolve one conflict
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import migration as migration_stub

bp = Blueprint("xijian_migration", __name__)


@bp.get("/v1/xijian/migration/status")
def migration_status():
    """Return the migration status (legacy_exists / migrated / items /
    conflicts / error).

    返回迁移状态（legacy_exists / migrated / items / conflicts / error）。
    """
    return jsonify(migration_stub.get_migration_status())


@bp.get("/v1/xijian/migration/conflicts")
def migration_conflicts():
    """Return the recorded conflict list.

    返回已记录的冲突清单。
    """
    status = migration_stub.get_migration_status()
    return jsonify({"conflicts": status.get("conflicts", [])})


@bp.post("/v1/xijian/migration/resolve")
def migration_resolve():
    """Resolve a migration conflict.

    Body: ``{"conflict_id": "...", "keep": "legacy" | "new"}``

    解决一条迁移冲突。
    """
    body = request.get_json(silent=True) or {}
    conflict_id = body.get("conflict_id")
    keep = body.get("keep")
    if not conflict_id:
        raise ApiError(
            400,
            "`conflict_id` is required",
            "invalid_request_error",
            code="missing_conflict_id",
            param="conflict_id",
        )
    if keep not in ("legacy", "new"):
        raise ApiError(
            400,
            "`keep` must be one of 'legacy' | 'new'",
            "invalid_request_error",
            code="invalid_keep",
            param="keep",
        )
    result = migration_stub.resolve_conflict(conflict_id, keep)
    if not result.get("ok"):
        # Stub error strings are mapped to stable machine-readable codes;
        # free-form errors (e.g. "mark unreadable: ...") fall back to a
        # generic code instead of leaking the raw message.
        # 将存根错误字符串映射为稳定的机器可读 code；自由格式错误
        # （如 "mark unreadable: ..."）回退到通用 code，避免泄漏原文。
        raw_error = result.get("error", "resolve_failed")
        code = {
            "migration_mark_missing": "migration_mark_missing",
            "conflict_not_found": "conflict_not_found",
            "invalid_keep": "invalid_keep",
        }.get(raw_error, "resolve_failed")
        raise ApiError(
            404 if raw_error == "conflict_not_found" else 400,
            raw_error,
            "invalid_request_error",
            code=code,
        )
    return jsonify(result)


__all__ = ["bp"]
