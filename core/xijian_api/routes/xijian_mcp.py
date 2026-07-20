"""``/v1/xijian/mcp/*`` routes — A5.2.

Gate (the hot path)
===================

* ``POST   /v1/xijian/mcp/check``              — pre-flight a
                                                  tool call
                                                  before the
                                                  desktop client
                                                  runs it

Rules CRUD
==========

* ``GET    /v1/xijian/mcp/rules``              — list (?active, ?action_kind, ?mode)
* ``POST   /v1/xijian/mcp/rules``              — create
* ``GET    /v1/xijian/mcp/rules/<rule_id>``   — get
* ``PATCH  /v1/xijian/mcp/rules/<rule_id>``   — patch
* ``DELETE /v1/xijian/mcp/rules/<rule_id>``   — delete

Audit query
===========

* ``GET    /v1/xijian/mcp/audit``             — list (?action_kind, ?world_id, ?verdict, ?limit)
* ``GET    /v1/xijian/mcp/audit/count``       — count (same filter args)

World policy
============

* ``GET    /v1/xijian/mcp/policy/<wid>``      — read
* ``PUT    /v1/xijian/mcp/policy/<wid>``      — set default / clear lockout
* ``DELETE /v1/xijian/mcp/policy/<wid>``      — reset to defaults

Safety-stop (the freeze state machine)
=======================================

* ``POST   /v1/xijian/mcp/safety_stop``               — initiate (the hotkey path)
* ``GET    /v1/xijian/mcp/safety_stop``               — list
* ``GET    /v1/xijian/mcp/safety_stop/<freeze_id>``   — get
* ``POST   /v1/xijian/mcp/safety_stop/<freeze_id>/confirm`` — user said "清理并恢复"
* ``POST   /v1/xijian/mcp/safety_stop/<freeze_id>/cancel``  — user said "保持冻结"

Snapshots (the "专用备份文件夹" half of the spec)
==================================================

* ``GET    /v1/xijian/mcp/snapshots``                — list summaries
* ``GET    /v1/xijian/mcp/snapshots/<snap_id>``     — get
* ``POST   /v1/xijian/mcp/snapshots``                — explicit dump
* ``POST   /v1/xijian/mcp/snapshots/<snap_id>/sanitize`` — explicit sanitize
* ``POST   /v1/xijian/mcp/snapshots/<snap_id>/restore``  — explicit restore

Dev-only
========

* ``POST   /v1/xijian/mcp/dev/crash``         — force a
                                                 rulebook
                                                 crash so tests
                                                 can exercise
                                                 the spec's
                                                 "审查模块自身
                                                 崩溃 → 降级为
                                                 最严格档"
                                                 branch.
                                                 ``XIJIAN_DEV=1``.
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
# Helpers
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
# Gate — the hot path
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
# Rules CRUD
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
    return jsonify({"rules": rules_stub.list_all()})


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
# Audit query
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
# World policy
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
# Safety-stop
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
# Snapshots
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
# Dev-only — exercise the self-crash fallback path
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/mcp/dev/crash")
def dev_crash():
    _dev_only()
    # Patch the rulebook's :func:`match_action_rules` to raise
    # so the gate can demonstrate the spec's "审查模块自身崩
    # 溃 → 降级为最严格档" branch.  We restore the original
    # after the call.
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
