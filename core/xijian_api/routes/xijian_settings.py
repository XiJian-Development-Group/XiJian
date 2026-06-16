"""``/v1/xijian/settings`` + ``/permissions`` routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.stubs import settings as settings_stub


bp = Blueprint("xijian_settings", __name__)


@bp.get("/v1/xijian/settings")
def get_settings():
    return jsonify(settings_stub.get_settings())


@bp.patch("/v1/xijian/settings")
def patch_settings():
    return jsonify(settings_stub.patch_settings(request.get_json(silent=True) or {}))


@bp.get("/v1/xijian/settings/permissions")
def get_permissions():
    return jsonify(
        {
            "object": "list",
            "data": settings_stub.list_permissions(),
            "has_more": False,
            "first_id": None,
            "last_id": None,
        }
    )


__all__ = ["bp"]