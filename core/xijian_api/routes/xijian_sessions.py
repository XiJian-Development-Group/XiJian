"""``/v1/xijian/sessions/*`` routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import sessions as sessions_stub


bp = Blueprint("xijian_sessions", __name__)


@bp.post("/v1/xijian/sessions")
def create_session():
    return jsonify(sessions_stub.create(request.get_json(silent=True) or {})), 201


@bp.post("/v1/xijian/sessions/<session_id>/messages")
def append_message(session_id: str):
    payload = request.get_json(silent=True) or {}
    if "content" not in payload:
        raise ApiError(400, "`content` is required", "invalid_request_error", code="missing_content", param="content")
    message = sessions_stub.append_message(session_id, payload)
    if message is None:
        raise ApiError(404, "session not found", "not_found_error", code="session_not_found")
    return jsonify(message), 201


@bp.get("/v1/xijian/sessions/<session_id>/messages")
def list_messages(session_id: str):
    messages = sessions_stub.list_messages(session_id)
    if messages is None:
        raise ApiError(404, "session not found", "not_found_error", code="session_not_found")
    return jsonify(
        {
            "object": "list",
            "data": messages,
            "has_more": False,
            "first_id": messages[0]["id"] if messages else None,
            "last_id": messages[-1]["id"] if messages else None,
        }
    )


@bp.delete("/v1/xijian/sessions/<session_id>")
def delete_session(session_id: str):
    if not sessions_stub.delete(session_id):
        raise ApiError(404, "session not found", "not_found_error", code="session_not_found")
    return ("", 204)


__all__ = ["bp"]