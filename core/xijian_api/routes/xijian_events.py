"""``/v1/xijian/events/*`` and ``/v1/xijian/worlds/<wid>/event*`` routes.

A4.1 world-event CRUD + scheduling.  The legacy per-world event
endpoints (``POST /v1/xijian/worlds/<wid>/event`` in
:xijian_api.routes.xijian_worlds) are kept for backward compatibility
— they append a free-form event blob to the world's ``events`` list.
The A4.1 endpoints below model events as first-class resources with
typed trigger configs, cooldown tracking and per-world category
toggles.

Endpoints
=========

Event definition CRUD (cross-world)::

    GET    /v1/xijian/events                    — list (filters: world_id, kind, enabled_only)
    POST   /v1/xijian/events                    — create
    GET    /v1/xijian/events/<event_id>         — read
    PATCH  /v1/xijian/events/<event_id>         — update mutable fields
    DELETE /v1/xijian/events/<event_id>         — delete

Fired instance access::

    GET    /v1/xijian/events/instances          — list (filters: world_id, event_id, limit)
    GET    /v1/xijian/events/instances/<id>     — read
    POST   /v1/xijian/events/instances/<id>/resolve  — mark resolved

Category toggles (per world)::

    GET    /v1/xijian/worlds/<wid>/event-categories          — list disabled
    PUT    /v1/xijian/worlds/<wid>/event-categories/<kind>   — toggle disabled

Scheduler control::

    GET    /v1/xijian/events/scheduler         — status
    POST   /v1/xijian/events/scheduler/start   — start background tick
    POST   /v1/xijian/events/scheduler/stop    — stop background tick
    POST   /v1/xijian/events/scheduler/tick    — manual single tick (dev only)

Summary::

    GET    /v1/xijian/worlds/<wid>/events/summary  — aggregate snapshot
"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import events as events_stub
from xijian_api.stubs import worlds as worlds_stub


bp = Blueprint("xijian_events", __name__)


# ---------------------------------------------------------------------------
# Event definition CRUD
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/events")
def list_events():
    args = request.args
    world_id = args.get("world_id")
    kind = args.get("kind")
    enabled_only = args.get("enabled_only", "").lower() in {"1", "true", "yes"}
    records = events_stub.list_events(
        world_id=world_id,
        kind=kind,
        enabled_only=enabled_only,
    )
    return jsonify(paginate(records).to_dict())


@bp.post("/v1/xijian/events")
def create_event():
    payload = request.get_json(silent=True) or {}
    required = ("world_id", "kind", "name", "trigger_config")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ApiError(
            400,
            f"missing required fields: {', '.join(missing)}",
            "invalid_request_error",
            code="missing_event_fields",
            param=",".join(missing),
        )
    if worlds_stub.get(payload["world_id"]) is None:
        raise ApiError(
            404,
            "world not found",
            "not_found_error",
            code="world_not_found",
        )
    try:
        record = events_stub.create_event(**payload)
    except events_stub.EventError as exc:
        raise ApiError(
            400,
            str(exc),
            "invalid_request_error",
            code="invalid_event",
        ) from exc
    return jsonify(record), 201


@bp.get("/v1/xijian/events/<event_id>")
def get_event(event_id: str):
    record = events_stub.get_event(event_id)
    if record is None:
        raise ApiError(404, "event not found", "not_found_error", code="event_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/events/<event_id>")
def patch_event(event_id: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not payload:
        raise ApiError(
            400,
            "request body must be a non-empty JSON object",
            "invalid_request_error",
            code="empty_patch",
        )
    try:
        record = events_stub.update_event(event_id, payload)
    except events_stub.EventError as exc:
        raise ApiError(
            400,
            str(exc),
            "invalid_request_error",
            code="invalid_event",
        ) from exc
    if record is None:
        raise ApiError(404, "event not found", "not_found_error", code="event_not_found")
    return jsonify(record)


@bp.delete("/v1/xijian/events/<event_id>")
def delete_event(event_id: str):
    if not events_stub.delete_event(event_id):
        raise ApiError(404, "event not found", "not_found_error", code="event_not_found")
    return ("", 204)


# ---------------------------------------------------------------------------
# Fired instance access
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/events/instances")
def list_instances():
    args = request.args
    world_id = args.get("world_id")
    event_id = args.get("event_id")
    try:
        limit = int(args.get("limit", "50"))
    except ValueError:
        raise ApiError(
            400,
            "`limit` must be an integer",
            "invalid_request_error",
            code="invalid_limit",
            param="limit",
        )
    records = events_stub.list_instances(
        world_id=world_id,
        event_id=event_id,
        limit=limit,
    )
    return jsonify({"data": records, "object": "list"})


@bp.get("/v1/xijian/events/instances/<instance_id>")
def get_instance(instance_id: str):
    record = events_stub.get_instance(instance_id)
    if record is None:
        raise ApiError(
            404,
            "event instance not found",
            "not_found_error",
            code="event_instance_not_found",
        )
    return jsonify(record)


@bp.post("/v1/xijian/events/instances/<instance_id>/resolve")
def resolve_instance(instance_id: str):
    record = events_stub.resolve_instance(instance_id)
    if record is None:
        raise ApiError(
            404,
            "event instance not found",
            "not_found_error",
            code="event_instance_not_found",
        )
    return jsonify(record)


# ---------------------------------------------------------------------------
# Per-world category toggles (US-A4.1-02)
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/worlds/<world_id>/event-categories")
def list_disabled_categories(world_id: str):
    if worlds_stub.get(world_id) is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify({"world_id": world_id, "disabled": events_stub.list_disabled_categories(world_id)})


@bp.put("/v1/xijian/worlds/<world_id>/event-categories/<category>")
def set_category_disabled(world_id: str, category: str):
    if worlds_stub.get(world_id) is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    payload = request.get_json(silent=True) or {}
    if "disabled" not in payload:
        raise ApiError(
            400,
            "`disabled` (bool) is required",
            "invalid_request_error",
            code="missing_disabled_flag",
            param="disabled",
        )
    disabled = bool(payload["disabled"])
    try:
        current = events_stub.set_category_disabled(world_id, category, disabled)
    except events_stub.EventError as exc:
        raise ApiError(
            400,
            str(exc),
            "invalid_request_error",
            code="invalid_category",
        ) from exc
    return jsonify(
        {
            "world_id": world_id,
            "category": category,
            "disabled": disabled,
            "all_disabled": sorted(current),
        }
    )


# ---------------------------------------------------------------------------
# Scheduler control
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/events/scheduler")
def scheduler_status():
    return jsonify(events_stub.scheduler_status())


@bp.post("/v1/xijian/events/scheduler/start")
def scheduler_start():
    return jsonify(events_stub.start_scheduler())


@bp.post("/v1/xijian/events/scheduler/stop")
def scheduler_stop():
    return jsonify(events_stub.stop_scheduler())


@bp.post("/v1/xijian/events/scheduler/tick")
def scheduler_tick():
    """Run a single scheduler pass; dev-only (``XIJIAN_DEV=1``).

    Production deployments rely on the background thread started by
    :func:`xijian_api.stubs.events.seed_default`; exposing a manual
    tick in prod would let clients amplify scheduling work.
    """
    if os.environ.get("XIJIAN_DEV") != "1":
        raise ApiError(404, "not found", "not_found_error", code="route_not_found")
    payload = request.get_json(silent=True) or {}
    world_id = payload.get("world_id")
    if world_id is not None:
        if worlds_stub.get(world_id) is None:
            raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
        fired = events_stub.tick_world(world_id)
        return jsonify({"world_id": world_id, "fired": fired})
    fired_by_world = events_stub.tick_all()
    return jsonify({"fired_by_world": fired_by_world})


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/worlds/<world_id>/events/summary")
def world_events_summary(world_id: str):
    if worlds_stub.get(world_id) is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(events_stub.summary(world_id))


__all__ = ["bp"]