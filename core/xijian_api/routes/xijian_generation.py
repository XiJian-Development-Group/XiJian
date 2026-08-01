"""``/v1/xijian/generation/*`` — generation-scope helpers for A4.1.

Two concerns live in this blueprint:

* ``POST /v1/xijian/generation/abort`` — broad-scope abort (original).
* ``GET  /v1/xijian/generation/scene/<instance_id>`` — read the scene
  record attached to a fired world-event instance (A4.1 US-A4.1-03).
* ``POST /v1/xijian/generation/scene/<instance_id>/generate`` —
  (re)generate the scene for an instance; when the core backend is
  unavailable it degrades to a placeholder (A4.1 AC-2).

The actual generation lives in :mod:`xijian_api.stubs.events`
(:func:`~xijian_api.stubs.events._ensure_scene_generated`) — the
route layer is a thin HTTP shell.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api import abort as abort_registry
from xijian_api.errors import ApiError
from xijian_api.stubs import events as events_stub


bp = Blueprint("xijian_generation", __name__)


@bp.post("/v1/xijian/generation/abort")
def generation_abort():
    payload = request.get_json(silent=True) or {}
    request_id = payload.get("request_id", "")
    if not request_id:
        raise ApiError(400, "`request_id` is required", "invalid_request_error", code="missing_request_id", param="request_id")
    scope = payload.get("scope", "all")
    signalled = abort_registry.abort(request_id)
    return jsonify(
        {
            "aborted": signalled,
            "request_id": request_id,
            "scope": scope,
        }
    )


@bp.get("/v1/xijian/generation/scene/<instance_id>")
def get_event_scene(instance_id: str):
    """Return the scene record attached to a fired event instance."""
    scene = events_stub.get_instance_scene(instance_id)
    if scene is None:
        raise ApiError(
            404,
            "event instance not found or it doesn't need a scene",
            "not_found_error",
            code="event_scene_not_found",
        )
    return jsonify({"instance_id": instance_id, "scene": scene})


@bp.post("/v1/xijian/generation/scene/<instance_id>/generate")
def generate_event_scene(instance_id: str):
    """(Re)generate the scene for a fired event instance.

    Best-effort: when the core image backend is unavailable the stub
    degrades to a placeholder scene (A4.1 AC-2) instead of failing.
    """
    scene = events_stub.ensure_scene_for_instance(instance_id)
    if scene is None:
        raise ApiError(
            404,
            "event instance not found or it doesn't need a scene",
            "not_found_error",
            code="event_scene_not_found",
        )
    return jsonify({"instance_id": instance_id, "scene": scene})


__all__ = ["bp"]
