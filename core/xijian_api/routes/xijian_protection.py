"""``/v1/xijian/protection/*`` routes — backward-compatible aliases.

The legacy ``protection`` module has been merged into
:mod:`xijian_api.stubs.safety`.  These routes keep the old
``/v1/xijian/protection/*`` paths alive so existing clients
don't break, but delegate every call to the merged
:mod:`xijian_api.stubs.safety` stub.

New code should prefer the unified ``/v1/xijian/safety/*``
endpoints (see :mod:`xijian_api.routes.xijian_safety`).
"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import safety as safety_stub


bp = Blueprint("xijian_protection", __name__)


@bp.get("/v1/xijian/protection/status")
def protection_status():
    return jsonify(safety_stub.status())


@bp.post("/v1/xijian/protection/enable")
def protection_enable():
    return jsonify(safety_stub.enable())


@bp.post("/v1/xijian/protection/disable")
def protection_disable():
    payload = request.get_json(silent=True) or {}
    # Step 1 — only confirmation; step 2 — challenge_id + phrase.
    if "challenge_id" in payload and "phrase" in payload:
        return jsonify(safety_stub.confirm_disable(payload))
    return jsonify(safety_stub.start_disable(payload))


@bp.get("/v1/xijian/protection/snapshots")
def snapshots_list():
    return jsonify(paginate(safety_stub.list_snapshots()).to_dict())


@bp.get("/v1/xijian/protection/snapshots/<snapshot_id>")
def snapshot_get(snapshot_id: str):
    record = safety_stub.get_snapshot(snapshot_id)
    if record is None:
        raise ApiError(404, "snapshot not found", "not_found_error", code="snapshot_not_found")
    return jsonify(record)


@bp.post("/v1/xijian/protection/rollback")
def rollback():
    payload = request.get_json(silent=True) or {}
    if "snapshot_id" not in payload:
        raise ApiError(400, "`snapshot_id` is required", "invalid_request_error", code="missing_snapshot_id", param="snapshot_id")
    return jsonify(safety_stub.rollback(payload))


@bp.post("/v1/xijian/protection/guard/preview")
def guard_preview():
    payload = request.get_json(silent=True) or {}
    direction = payload.get("direction", "input")
    text = payload.get("text", "")
    context = payload.get("context")
    return jsonify(safety_stub.guard_preview(direction, text, context=context))


@bp.get("/v1/xijian/protection/audit")
def audit_list():
    return jsonify(paginate(safety_stub.list_audit()).to_dict())


@bp.post("/v1/xijian/protection/audit/export")
def audit_export():
    return jsonify(safety_stub.export_audit())


# ---- dev-only WS event injector -------------------------------------------

@bp.post("/v1/xijian/_test/emit")
def dev_emit():
    """Dev-only endpoint to publish a fake WS event.

    Guarded by ``XIJIAN_DEV=1`` so it never ships in production.
    """
    if os.environ.get("XIJIAN_DEV") != "1":
        raise ApiError(404, "not found", "not_found_error", code="route_not_found")
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("type", "ping")
    data = payload.get("data", {})
    # Lazy import — avoids a hard dependency between routes and ws.
    from xijian_api.routes.ws_routes import publish_event
    publish_event(event_type, data)
    return jsonify({"published": True, "type": event_type})


__all__ = ["bp"]
