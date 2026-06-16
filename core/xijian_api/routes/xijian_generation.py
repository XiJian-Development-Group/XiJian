"""``POST /v1/xijian/generation/abort`` — broad-scope abort."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api import abort as abort_registry
from xijian_api.errors import ApiError


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


__all__ = ["bp"]