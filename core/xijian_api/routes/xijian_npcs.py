"""``/v1/xijian/npcs/*`` routes — A4.2.

NPC CRUD
========

* ``GET    /v1/xijian/npcs``                          — list (optional ?world_id)
* ``POST   /v1/xijian/npcs``                          — create (50-cap enforced)
* ``GET    /v1/xijian/npcs/<npc_id>``                 — get
* ``PATCH  /v1/xijian/npcs/<npc_id>``                 — patch (no tier change)
* ``DELETE /v1/xijian/npcs/<npc_id>``                 — delete

Tier transitions
================

* ``PUT    /v1/xijian/npcs/<npc_id>/tier``            — set tier (logs)
* ``PUT    /v1/xijian/npcs/<npc_id>/state``          — patch state_json
                                                       (NPC internal state)

Scheduling
==========

* ``GET    /v1/xijian/npcs/scheduling/log``           — global log (filtered)
* ``GET    /v1/xijian/npcs/<npc_id>/scheduling/log``  — per-NPC log
* ``GET    /v1/xijian/npcs/scheduling/summary``       — per-world budgets
* ``POST   /v1/xijian/npcs/scheduling/tick``          — dev-only (XIJIAN_DEV=1)
* ``POST   /v1/xijian/npcs/scheduling/tick/all``      — dev-only
* ``GET    /v1/xijian/npcs/scheduling/status``        — tick lifecycle
* ``POST   /v1/xijian/npcs/scheduling/resume``        — wake from overload
                                                       (manual override)

A4.1 cross-link
===============

* ``POST   /v1/xijian/npcs/affected/preview``        — preview the
                                                       affected-NPC selector
                                                       (for A4.1 event authors)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import npcs as npcs_stub
from xijian_api.stubs import worlds as worlds_stub


bp = Blueprint("xijian_npcs", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.xijian_npcs")


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
    """Block dev-only routes unless ``XIJIAN_DEV=1`` is set."""
    if os.environ.get("XIJIAN_DEV") != "1":
        raise ApiError(
            403, "dev-only endpoint", "forbidden_error", code="dev_only",
        )


# ---------------------------------------------------------------------------
# NPC CRUD
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/npcs")
def list_npcs():
    world_id = request.args.get("world_id")
    if world_id is not None:
        tier = request.args.get("tier")
        alive_only = request.args.get("alive_only", "false").lower() in ("1", "true")
        return jsonify({
            "world_id": world_id,
            "npcs": npcs_stub.list_for_world(world_id, tier=tier, alive_only=alive_only),
        })
    return jsonify(paginate(npcs_stub.list_all()).to_dict())


@bp.post("/v1/xijian/npcs")
def create_npc():
    body = _require_json()
    world_id = body.get("world_id")
    if not isinstance(world_id, str) or not world_id:
        raise ApiError(
            400, "`world_id` is required", "invalid_request_error",
            code="missing_world_id", param="world_id",
        )
    name = body.get("name")
    if not isinstance(name, str) or not name:
        raise ApiError(
            400, "`name` is required", "invalid_request_error",
            code="missing_name", param="name",
        )
    try:
        record = npcs_stub.create(
            world_id=world_id,
            name=name,
            persona_doc=body.get("persona_doc", ""),
            state_json=body.get("state_json") or {},
            compute_budget=body.get("compute_budget", npcs_stub.DEFAULT_NPC_COMPUTE_BUDGET),
            activity_tier=body.get("activity_tier", npcs_stub.TIER_LOW_ACTIVE),
            importance=body.get("importance", 1.0),
            npc_id=body.get("npc_id"),
            is_alive=bool(body.get("is_alive", True)),
        )
    except npcs_stub.NPCError as exc:
        # 50-cap or unknown-world → 409 / 404.
        msg = str(exc)
        if "does not exist" in msg:
            raise ApiError(404, msg, "not_found_error", code="world_not_found")
        if "hard cap" in msg:
            raise ApiError(409, msg, "world_error", code="npc_cap_exceeded")
        if "already exists" in msg:
            raise ApiError(409, msg, "world_error", code="npc_id_conflict")
        raise ApiError(400, msg, "invalid_request_error", code="npc_error")
    return jsonify(record), 201


@bp.get("/v1/xijian/npcs/<npc_id>")
def get_npc(npc_id: str):
    record = npcs_stub.get(npc_id)
    if record is None:
        raise ApiError(404, "npc not found", "not_found_error", code="npc_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/npcs/<npc_id>")
def patch_npc(npc_id: str):
    body = _require_json()
    try:
        record = npcs_stub.update(npc_id, body)
    except npcs_stub.NPCError as exc:
        raise ApiError(400, str(exc), "invalid_request_error", code="npc_error")
    if record is None:
        raise ApiError(404, "npc not found", "not_found_error", code="npc_not_found")
    return jsonify(record)


@bp.delete("/v1/xijian/npcs/<npc_id>")
def delete_npc(npc_id: str):
    if not npcs_stub.delete(npc_id):
        raise ApiError(404, "npc not found", "not_found_error", code="npc_not_found")
    return jsonify({"deleted": True, "npc_id": npc_id})


# ---------------------------------------------------------------------------
# Tier transitions
# ---------------------------------------------------------------------------


@bp.put("/v1/xijian/npcs/<npc_id>/tier")
def set_npc_tier(npc_id: str):
    body = _require_json()
    tier = body.get("activity_tier")
    if not isinstance(tier, str):
        raise ApiError(
            400, "`activity_tier` is required", "invalid_request_error",
            code="missing_activity_tier", param="activity_tier",
        )
    reason = body.get("reason", "manual")
    try:
        record = npcs_stub.set_tier(npc_id, tier, reason=reason)
    except npcs_stub.NPCError as exc:
        raise ApiError(400, str(exc), "invalid_request_error", code="npc_error")
    if record is None:
        raise ApiError(404, "npc not found", "not_found_error", code="npc_not_found")
    return jsonify(record)


@bp.put("/v1/xijian/npcs/<npc_id>/state")
def patch_npc_state(npc_id: str):
    body = _require_json()
    state_json = body.get("state_json")
    if not isinstance(state_json, dict):
        raise ApiError(
            400, "`state_json` must be a JSON object",
            "invalid_request_error", code="invalid_state_json",
        )
    try:
        record = npcs_stub.update(npc_id, {"state_json": state_json})
    except npcs_stub.NPCError as exc:
        raise ApiError(400, str(exc), "invalid_request_error", code="npc_error")
    if record is None:
        raise ApiError(404, "npc not found", "not_found_error", code="npc_not_found")
    return jsonify(record)


# ---------------------------------------------------------------------------
# Scheduling — log / summary
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/npcs/scheduling/log")
def global_scheduling_log():
    world_id = request.args.get("world_id")
    npc_id = request.args.get("npc_id")
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    out: list[dict] = []
    for entry in npcs_stub_state_values().values():
        if world_id is not None and entry.get("world_id") != world_id:
            continue
        if npc_id is not None and entry.get("npc_id") != npc_id:
            continue
        out.append(entry)
    out.sort(key=lambda e: e.get("created_at", 0.0), reverse=True)
    return jsonify({"entries": out[: max(1, limit)]})


def npcs_stub_state_values():
    """Lazy accessor for the log bucket to keep the route module
    decoupled from a direct state import."""
    from xijian_api.stubs import state
    return state.npc_scheduling_log


@bp.get("/v1/xijian/npcs/<npc_id>/scheduling/log")
def npc_scheduling_log(npc_id: str):
    if npcs_stub.get(npc_id) is None:
        raise ApiError(404, "npc not found", "not_found_error", code="npc_not_found")
    out = [
        e for e in npcs_stub_state_values().values()
        if e.get("npc_id") == npc_id
    ]
    out.sort(key=lambda e: e.get("created_at", 0.0), reverse=True)
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    return jsonify({"npc_id": npc_id, "entries": out[: max(1, limit)]})


@bp.get("/v1/xijian/npcs/scheduling/summary")
def scheduling_summary():
    return jsonify({"worlds": npcs_stub.compute_world_summary()})


@bp.post("/v1/xijian/npcs/scheduling/tick")
def dev_tick_world():
    _dev_only()
    body = _require_json()
    world_id = body.get("world_id")
    if not isinstance(world_id, str) or not world_id:
        raise ApiError(
            400, "`world_id` is required", "invalid_request_error",
            code="missing_world_id", param="world_id",
        )
    if worlds_stub.get(world_id) is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    queue_p99 = body.get("queue_p99_latency_s")
    out = npcs_stub.tick_world(world_id, queue_p99_latency_s=queue_p99)
    return jsonify(out)


@bp.post("/v1/xijian/npcs/scheduling/tick/all")
def dev_tick_all():
    _dev_only()
    out = npcs_stub.tick_all()
    return jsonify(out)


@bp.get("/v1/xijian/npcs/scheduling/status")
def tick_status():
    return jsonify(npcs_stub.tick_status())


@bp.post("/v1/xijian/npcs/scheduling/resume")
def resume_from_overload():
    return jsonify(npcs_stub.resume_from_overload())


# ---------------------------------------------------------------------------
# A4.1 cross-link — preview affected NPCs
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/npcs/affected/preview")
def preview_affected():
    """Preview which NPCs would be affected by a hypothetical event.

    Used by A4.1 event authors to see "if I fire this event, who
    gets tagged".  Body: ``{"world_id": "...", "event": {...}}``.
    """
    body = _require_json()
    world_id = body.get("world_id")
    if not isinstance(world_id, str) or not world_id:
        raise ApiError(
            400, "`world_id` is required", "invalid_request_error",
            code="missing_world_id", param="world_id",
        )
    if worlds_stub.get(world_id) is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    event = body.get("event") or {}
    world_record = worlds_stub.get(world_id) or {}
    affected = npcs_stub.select_affected_npcs(world_record, event)
    return jsonify({"world_id": world_id, "affected_npcs": affected})


__all__ = ["bp"]
