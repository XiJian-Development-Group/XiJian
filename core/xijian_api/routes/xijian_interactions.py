"""``/v1/xijian/interactions/*`` routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import interactions as inter_stub


bp = Blueprint("xijian_interactions", __name__)


@bp.get("/v1/xijian/interactions")
def list_interactions():
    return jsonify(paginate(inter_stub.list_all()).to_dict())


@bp.post("/v1/xijian/interactions/<interaction_id>/trigger")
def trigger_interaction(interaction_id: str):
    payload = request.get_json(silent=True) or {}
    result = inter_stub.trigger(
        interaction_id,
        character_id=payload.get("character_id"),
        context=payload.get("context"),
        nsfw_allowed=bool(payload.get("nsfw_allowed", False)),
    )
    if not result.get("accepted") and result.get("reason") == "interaction_not_found":
        raise ApiError(404, "interaction not found", "not_found_error", code="interaction_not_found")
    return jsonify(result)


@bp.get("/v1/xijian/interactions/<interaction_id>/responses")
def list_responses(interaction_id: str):
    record = inter_stub.get(interaction_id)
    if record is None:
        raise ApiError(404, "interaction not found", "not_found_error", code="interaction_not_found")
    return jsonify(
        {
            "object": "list",
            "data": record.get("responses", []),
            "has_more": False,
            "first_id": None,
            "last_id": None,
        }
    )


__all__ = ["bp"]