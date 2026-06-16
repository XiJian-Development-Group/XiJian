"""``/v1/xijian/characters/*`` routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import characters as chars_stub
from xijian_api.stubs import interactions as inter_stub
from xijian_api.stubs import protection as prot_stub


bp = Blueprint("xijian_characters", __name__)


@bp.post("/v1/xijian/characters")
def create_character():
    payload = request.get_json(silent=True) or {}
    record = chars_stub.create(payload)
    return jsonify(record), 201


@bp.get("/v1/xijian/characters")
def list_characters():
    return jsonify(paginate(chars_stub.list_all()).to_dict())


@bp.get("/v1/xijian/characters/<character_id>")
def get_character(character_id: str):
    record = chars_stub.get(character_id)
    if record is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/characters/<character_id>")
def patch_character(character_id: str):
    record = chars_stub.update(character_id, request.get_json(silent=True) or {})
    if record is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    return jsonify(record)


@bp.delete("/v1/xijian/characters/<character_id>")
def delete_character(character_id: str):
    if not chars_stub.delete(character_id):
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    return ("", 204)


@bp.post("/v1/xijian/characters/<character_id>/load")
def load_character(character_id: str):
    record = chars_stub.set_loaded(character_id, True)
    if record is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    return jsonify(record)


@bp.post("/v1/xijian/characters/<character_id>/unload")
def unload_character(character_id: str):
    record = chars_stub.set_loaded(character_id, False)
    if record is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    return jsonify(record)


@bp.post("/v1/xijian/characters/<character_id>/interact")
def interact(character_id: str):
    character = chars_stub.get(character_id)
    if character is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    payload = request.get_json(silent=True) or {}
    interaction_id = payload.get("interaction_id", "")
    if not interaction_id:
        raise ApiError(400, "`interaction_id` is required", "invalid_request_error", code="missing_interaction_id", param="interaction_id")
    nsfw_allowed = bool(payload.get("nsfw_allowed", False))
    result = inter_stub.trigger(
        interaction_id,
        character_id=character_id,
        context=payload.get("context"),
        nsfw_allowed=nsfw_allowed,
    )
    return jsonify(result)


@bp.get("/v1/xijian/characters/<character_id>/state")
def get_character_state(character_id: str):
    record = chars_stub.get_state(character_id)
    if record is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    return jsonify(record)


@bp.post("/v1/xijian/characters/<character_id>/state")
def update_character_state(character_id: str):
    payload = request.get_json(silent=True) or {}
    record, error_key = chars_stub.update_state(
        character_id, payload, protection_enabled=prot_stub.is_enabled()
    )
    if error_key == "not_found":
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    if error_key == "protection_disabled":
        raise ApiError(403, "protection system is disabled", "protection_error", code="protection_disabled")
    return jsonify(record)


__all__ = ["bp"]