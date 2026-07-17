"""``/v1/xijian/safety/*`` routes — A5.1.

Scan endpoints
==============

* ``POST   /v1/xijian/safety/scan/input``     — pre-screen user input
* ``POST   /v1/xijian/safety/scan/output``    — post-screen assistant output

Rules CRUD
==========

* ``GET    /v1/xijian/safety/rules``          — list (?active, ?rule_kind)
* ``POST   /v1/xijian/safety/rules``          — create
* ``GET    /v1/xijian/safety/rules/<rule_id>`` — get
* ``PATCH  /v1/xijian/safety/rules/<rule_id>`` — patch
* ``DELETE /v1/xijian/safety/rules/<rule_id>`` — delete

Audit query
===========

* ``GET    /v1/xijian/safety/audit``          — list (?world_id, ?character_id, ?stage, ?verdict, ?limit)
* ``GET    /v1/xijian/safety/audit/count``    — count (same filter args)

World policy
============

* ``GET    /v1/xijian/safety/policy/<wid>``   — read (threshold + is_dangerous)
* ``PUT    /v1/xijian/safety/policy/<wid>``   — set is_dangerous / threshold
* ``DELETE /v1/xijian/safety/policy/<wid>``   — reset to defaults

Dev-only
========

* ``POST   /v1/xijian/safety/dev/crash``      — force a scan-self-crash
                                                (XIJIAN_DEV=1) so tests
                                                can exercise the
                                                spec's "审查模块
                                                自身崩溃 → 降级为
                                                最严格档" path.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import safety as safety_stub
from xijian_api.stubs import safety_rules as rules_stub
from xijian_api.stubs import worlds as worlds_stub


bp = Blueprint("xijian_safety", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.xijian_safety")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_json() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
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


def _require_world(world_id: Any) -> str:
    if not isinstance(world_id, str) or not world_id:
        raise ApiError(
            400, "`world_id` is required", "invalid_request_error",
            code="missing_world_id", param="world_id",
        )
    if worlds_stub.get(world_id) is None:
        raise ApiError(
            404, "world not found", "not_found_error", code="world_not_found",
        )
    return world_id


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/safety/scan/input")
def scan_input_route():
    body = _require_json()
    text = body.get("text", "")
    if not isinstance(text, str):
        raise ApiError(
            400, "`text` must be a string", "invalid_request_error",
            code="invalid_text", param="text",
        )
    return jsonify(safety_stub.scan_input(
        text=text,
        character_id=body.get("character_id"),
        world_id=body.get("world_id"),
        event_tags=body.get("event_tags"),
    ))


@bp.post("/v1/xijian/safety/scan/output")
def scan_output_route():
    body = _require_json()
    text = body.get("text", "")
    if not isinstance(text, str):
        raise ApiError(
            400, "`text` must be a string", "invalid_request_error",
            code="invalid_text", param="text",
        )
    return jsonify(safety_stub.scan_output(
        text=text,
        character_id=body.get("character_id"),
        world_id=body.get("world_id"),
        event_tags=body.get("event_tags"),
    ))


# ---------------------------------------------------------------------------
# Rules CRUD
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/safety/rules")
def list_rules():
    active_only = request.args.get("active", "").lower() in ("1", "true", "yes")
    rule_kind = request.args.get("rule_kind")
    if rule_kind is not None and rule_kind not in rules_stub.VALID_KINDS:
        raise ApiError(
            400, "`rule_kind` is invalid", "invalid_request_error",
            code="invalid_rule_kind", param="rule_kind",
        )
    if active_only:
        return jsonify({"rules": rules_stub.list_active(rule_kind=rule_kind)})
    return jsonify(paginate(rules_stub.list_all()).to_dict())


@bp.post("/v1/xijian/safety/rules")
def create_rule():
    body = _require_json()
    try:
        record = rules_stub.create(
            rule_kind=body.get("rule_kind"),
            pattern=body.get("pattern", ""),
            severity=body.get("severity", rules_stub.DEFAULT_SEVERITY),
            is_active=bool(body.get("is_active", True)),
        )
    except rules_stub.SafetyRuleError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="rule_error",
        )
    return jsonify(record), 201


@bp.get("/v1/xijian/safety/rules/<rule_id>")
def get_rule(rule_id: str):
    record = rules_stub.get(rule_id)
    if record is None:
        raise ApiError(
            404, "rule not found", "not_found_error", code="rule_not_found",
        )
    return jsonify(record)


@bp.patch("/v1/xijian/safety/rules/<rule_id>")
def patch_rule(rule_id: str):
    body = _require_json()
    try:
        record = rules_stub.update(rule_id, body)
    except rules_stub.SafetyRuleError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="rule_error",
        )
    if record is None:
        raise ApiError(
            404, "rule not found", "not_found_error", code="rule_not_found",
        )
    return jsonify(record)


@bp.delete("/v1/xijian/safety/rules/<rule_id>")
def delete_rule(rule_id: str):
    if not rules_stub.delete(rule_id):
        raise ApiError(
            404, "rule not found", "not_found_error", code="rule_not_found",
        )
    return jsonify({"deleted": True, "rule_id": rule_id})


# ---------------------------------------------------------------------------
# Audit query
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/safety/audit")
def list_audit():
    args = request.args
    try:
        limit = int(args.get("limit", 50))
    except ValueError:
        limit = 50
    items = safety_stub.list_log(
        character_id=args.get("character_id"),
        world_id=args.get("world_id"),
        stage=args.get("stage"),
        verdict=args.get("verdict"),
        limit=limit,
    )
    return jsonify({"entries": items})


@bp.get("/v1/xijian/safety/audit/count")
def count_audit():
    args = request.args
    n = safety_stub.count_for(
        character_id=args.get("character_id"),
        world_id=args.get("world_id"),
        verdict=args.get("verdict"),
    )
    return jsonify({"count": n})


# ---------------------------------------------------------------------------
# World policy
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/safety/policy/<world_id>")
def get_policy(world_id: str):
    _require_world(world_id)
    return jsonify({
        "world_id": world_id,
        "is_dangerous": safety_stub.is_world_dangerous(world_id),
        "threshold": safety_stub.get_safety_threshold(world_id),
    })


@bp.put("/v1/xijian/safety/policy/<world_id>")
def set_policy(world_id: str):
    _require_world(world_id)
    body = _require_json()
    if "is_dangerous" in body:
        safety_stub.set_world_dangerous(world_id, bool(body["is_dangerous"]))
    if "threshold" in body:
        try:
            safety_stub.set_safety_threshold(world_id, body["threshold"])
        except safety_stub.SafetyError as exc:
            raise ApiError(
                400, str(exc), "invalid_request_error", code="policy_error",
            )
    return jsonify({
        "world_id": world_id,
        "is_dangerous": safety_stub.is_world_dangerous(world_id),
        "threshold": safety_stub.get_safety_threshold(world_id),
    })


@bp.delete("/v1/xijian/safety/policy/<world_id>")
def reset_policy(world_id: str):
    _require_world(world_id)
    removed = safety_stub.reset_world_policy(world_id)
    return jsonify({"reset": True, "removed_entries": removed, "world_id": world_id})


# ---------------------------------------------------------------------------
# Dev-only — exercise the self-crash fallback path
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/safety/dev/crash")
def dev_crash():
    _dev_only()
    # Patch the rulebook's :func:`match_active_rules` to raise so
    # the scan can demonstrate the spec's "审查模块自身崩溃 → 降级
    # 为最严格档" branch.  We restore the original after the call.
    original = rules_stub.match_active_rules

    def boom(text, *, rule_kind):
        raise RuntimeError("synthetic crash from dev/crash")

    rules_stub.match_active_rules = boom  # type: ignore[assignment]
    try:
        result_input = safety_stub.scan_input(text="hello", world_id="dev")
        result_output = safety_stub.scan_output(text="hello", world_id="dev")
    finally:
        rules_stub.match_active_rules = original  # type: ignore[assignment]
    return jsonify({
        "input": result_input,
        "output": result_output,
    })


__all__ = ["bp"]
