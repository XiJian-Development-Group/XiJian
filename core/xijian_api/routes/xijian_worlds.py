"""``/v1/xijian/worlds/*`` routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import protection as prot_stub
from xijian_api.stubs import worlds as worlds_stub


bp = Blueprint("xijian_worlds", __name__)


@bp.get("/v1/xijian/worlds")
def list_worlds():
    return jsonify(paginate(worlds_stub.list_all()).to_dict())


@bp.post("/v1/xijian/worlds/<world_id>/transition")
def transition(world_id: str):
    payload = request.get_json(silent=True) or {}
    if "to_location" not in payload:
        raise ApiError(400, "`to_location` is required", "invalid_request_error", code="missing_to_location", param="to_location")
    record = worlds_stub.transition(world_id, payload)
    if record is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(record)


@bp.get("/v1/xijian/worlds/<world_id>/state")
def get_world_state(world_id: str):
    record = worlds_stub.get_state(world_id)
    if record is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/worlds/<world_id>/state")
def patch_world_state(world_id: str):
    payload = request.get_json(silent=True) or {}
    state, error_key = worlds_stub.update_state(
        world_id, payload, protection_enabled=prot_stub.is_enabled()
    )
    if error_key == "not_found":
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    if error_key == "protection_disabled":
        raise ApiError(403, "protection system is disabled", "protection_error", code="protection_disabled")
    return jsonify({"world_id": world_id, "state": state})


@bp.post("/v1/xijian/worlds/<world_id>/event")
def add_event(world_id: str):
    payload = request.get_json(silent=True) or {}
    event = worlds_stub.add_event(world_id, payload)
    if event is None:
        raise ApiError(404, "world not found", "not_found_error", code="world_not_found")
    return jsonify(event), 201


__all__ = ["bp"]