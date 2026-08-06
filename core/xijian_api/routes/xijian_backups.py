"""``/v1/xijian/backups/*`` 路由 — A5.3。

快照 CRUD
==============

* ``GET    /v1/xijian/backups/snapshots``         — 列表
                                                     (?scope,
                                                     ?target_id,
                                                     ?reason,
                                                     ?limit)
* ``POST   /v1/xijian/backups/snapshots``         — 创建
* ``GET    /v1/xijian/backups/snapshots/<id>``    — 获取
* ``DELETE /v1/xijian/backups/snapshots/<id>``    — 删除
* ``POST   /v1/xijian/backups/snapshots/<id>/compress`` — 强制重新压缩

容量
========

* ``GET    /v1/xijian/backups/capacity``         — 当前总量 + 上限
* ``POST   /v1/xijian/backups/capacity/resolve``  — 操作员对提示记录的
                                                     响应 (压缩 / 丢弃 / 强制)

清理
=====

* ``POST   /v1/xijian/backups/prune``             — 丢弃过期快照
                                                     (?dry_run)

策略
======

* ``GET    /v1/xijian/backups/policy``            — 读取
* ``PUT    /v1/xijian/backups/policy``            — 设置
* ``DELETE /v1/xijian/backups/policy``            — 重置为默认值
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import snapshots as snap_stub


bp = Blueprint("xijian_backups", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.xijian_backups")


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


# ---------------------------------------------------------------------------
# 快照 CRUD
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/backups/snapshots")
def list_snapshots():
    args = request.args
    try:
        limit = int(args.get("limit", 50))
    except ValueError:
        limit = 50
    items = snap_stub.list_snapshots(
        scope=args.get("scope"),
        target_id=args.get("target_id"),
        reason=args.get("reason"),
        limit=limit,
    )
    return jsonify({"snapshots": items})


@bp.post("/v1/xijian/backups/snapshots")
def post_snapshot():
    body = _require_json()
    try:
        record = snap_stub.create_snapshot(
            scope=body.get("scope"),
            target_id=body.get("target_id"),
            payload=body.get("payload", {}),
            reason=body.get("reason", snap_stub.REASON_MANUAL),
            ref_id=body.get("ref_id"),
            expires_at=body.get("expires_at"),
            force=bool(body.get("force", False)),
        )
    except snap_stub.CapacityExceededError as exc:
        # 返回 409 + 提示体，供操作员决策。
        # 响应体包含 ``action: "prompt"`` 以及
        # current_total / ceiling / 最旧候选，
        # 使 UI 能直接据此渲染确认对话框。
        raise ApiError(
            409, str(exc), "invalid_request_error",
            code="capacity_exceeded", **exc.prompt,
        )
    except snap_stub.SnapshotError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="snapshot_error",
        )
    return jsonify(record), 201


@bp.get("/v1/xijian/backups/snapshots/<snapshot_id>")
def get_snapshot(snapshot_id: str):
    record = snap_stub.get_snapshot(snapshot_id)
    if record is None:
        raise ApiError(
            404, "snapshot not found", "not_found_error",
            code="snapshot_not_found",
        )
    return jsonify(record)


@bp.delete("/v1/xijian/backups/snapshots/<snapshot_id>")
def delete_snapshot(snapshot_id: str):
    if not snap_stub.delete_snapshot(snapshot_id):
        raise ApiError(
            404, "snapshot not found", "not_found_error",
            code="snapshot_not_found",
        )
    return jsonify({"deleted": True, "snapshot_id": snapshot_id})


@bp.post("/v1/xijian/backups/snapshots/<snapshot_id>/compress")
def compress_snapshot(snapshot_id: str):
    record = snap_stub.compress_snapshot(snapshot_id)
    if record is None:
        raise ApiError(
            404, "snapshot not found", "not_found_error",
            code="snapshot_not_found",
        )
    return jsonify(record)


# ---------------------------------------------------------------------------
# 容量
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/backups/capacity")
def get_capacity():
    policy = snap_stub.get_policy()
    return jsonify({
        "current_total": snap_stub.get_total_bytes(),
        "ceiling": int(policy["max_total_bytes"]),
        "policy_id": policy["id"],
    })


@bp.post("/v1/xijian/backups/capacity/resolve")
def resolve_capacity():
    body = _require_json()
    action = body.get("action")
    if action not in {"compress", "drop", "force"}:
        raise ApiError(
            400, "`action` must be one of compress / drop / force",
            "invalid_request_error", code="invalid_action",
            param="action",
        )
    try:
        summary = snap_stub.resolve_capacity(
            action=action,
            incoming_bytes=int(body.get("incoming_bytes", 0)),
        )
    except (snap_stub.SnapshotError, TypeError, ValueError) as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="capacity_error",
        )
    return jsonify(summary)


# ---------------------------------------------------------------------------
# 清理
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/backups/prune")
def prune():
    body = _require_json(silent=True) or {}
    dry_run = bool(body.get("dry_run", False))
    if dry_run:
        # 仅统计候选而不删除它们。
        import time
        moment = float(time.time())
        candidates = 0
        for record in snap_stub.list_snapshots(limit=10_000):
            expires_at = record.get("expires_at")
            if expires_at is not None and float(expires_at) <= moment:
                candidates += 1
        return jsonify({"dry_run": True, "would_drop": candidates})
    removed = snap_stub.prune_expired()
    return jsonify({"dry_run": False, "dropped": removed})


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/backups/policy")
def get_policy():
    return jsonify(snap_stub.get_policy())


@bp.put("/v1/xijian/backups/policy")
def set_policy():
    body = _require_json()
    try:
        record = snap_stub.set_policy(
            max_total_bytes=body.get("max_total_bytes"),
            auto_compress_enabled=body.get("auto_compress_enabled"),
            compression_target=body.get("compression_target"),
            backup_interval_seconds=body.get("backup_interval_seconds"),
        )
    except snap_stub.SnapshotError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="policy_error",
        )
    return jsonify(record)


@bp.delete("/v1/xijian/backups/policy")
def reset_policy():
    return jsonify(snap_stub.reset_policy())


__all__ = ["bp"]
