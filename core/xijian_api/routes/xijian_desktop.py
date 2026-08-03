"""``/v1/xijian/desktop/*`` + ``/v1/xijian/mcp/pending*`` routes — A8.

Desktop pets (US-A8-01 / US-A8-02)
==================================

* ``GET    /v1/xijian/desktop/pets``                     — list (?character_id, ?is_active)
* ``POST   /v1/xijian/desktop/pets``                     — create
* ``GET    /v1/xijian/desktop/pets/<pet_id>``            — get
* ``PATCH  /v1/xijian/desktop/pets/<pet_id>``            — patch
* ``DELETE /v1/xijian/desktop/pets/<pet_id>``            — delete
* ``POST   /v1/xijian/desktop/pets/<pet_id>/activate``   — show on desktop
* ``POST   /v1/xijian/desktop/pets/<pet_id>/deactivate`` — hide

Dynamic wallpapers (US-A8-03 / US-A8-04)
========================================

* ``GET    /v1/xijian/desktop/wallpapers``               — list (?character_id, ?is_active)
* ``POST   /v1/xijian/desktop/wallpapers``               — create
* ``GET    /v1/xijian/desktop/wallpapers/<wp_id>``       — get
* ``PATCH  /v1/xijian/desktop/wallpapers/<wp_id>``       — patch
* ``DELETE /v1/xijian/desktop/wallpapers/<wp_id>``       — delete
* ``POST   /v1/xijian/desktop/wallpapers/<wp_id>/activate``   — activating a
                                                                wallpaper
                                                                deactivates
                                                                the character's
                                                                pets (AC-4)
* ``POST   /v1/xijian/desktop/wallpapers/<wp_id>/deactivate``

Audit log (AC-2)
================

* ``GET  /v1/xijian/desktop/actions``                    — global pet-action log
* ``GET  /v1/xijian/desktop/pets/<pet_id>/actions``      — per-pet log
* ``POST /v1/xijian/desktop/pets/<pet_id>/actions``      — dispatch/record one
                                                          pet action

Desktop-client execution loop (the A5.2-flagged gap)
====================================================

* ``GET  /v1/xijian/mcp/pending``                        — poll pending actions
                                                          (?status, ?limit,
                                                          ?claim=1 to claim)
* ``GET  /v1/xijian/mcp/pending/<action_id>``            — get one
* ``POST /v1/xijian/mcp/pending/<action_id>/claim``      — claim for execution
* ``POST /v1/xijian/mcp/pending/<action_id>/result``     — write back
                                                          {status:
                                                           executed|failed,
                                                           result: {...},
                                                           pet_id?: ...}

WS push
=======

* ``desktop_pet.event`` / ``wallpaper.event`` / ``desktop_pet.action`` /
  ``desktop_pet.pending`` — best-effort broadcasts from the stub layer.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import desktop_pets as pets_stub
from xijian_api.utils.params import parse_float


bp = Blueprint("xijian_desktop", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.xijian_desktop")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_json() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(
            400,
            "request body must be a JSON object",
            "invalid_request_error",
            code="invalid_body",
        )
    return body


def _error(exc: Exception) -> ApiError:
    return ApiError(
        400, str(exc), "invalid_request_error", code="desktop_pet_error"
    )


def _pet_or_404(pet_id: str) -> dict:
    record = pets_stub.get_pet(pet_id)
    if record is None:
        raise ApiError(
            404, "desktop pet not found", "not_found_error",
            code="desktop_pet_not_found",
        )
    return record


def _wallpaper_or_404(wallpaper_id: str) -> dict:
    record = pets_stub.get_wallpaper(wallpaper_id)
    if record is None:
        raise ApiError(
            404, "dynamic wallpaper not found", "not_found_error",
            code="wallpaper_not_found",
        )
    return record


# ---------------------------------------------------------------------------
# Desktop pets — CRUD
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/desktop/pets")
def list_pets():
    args = request.args
    items = pets_stub.list_pets(
        character_id=args.get("character_id"),
        is_active=(
            args.get("is_active", "true").lower() in ("1", "true")
            if args.get("is_active") is not None else None
        ),
    )
    return jsonify(paginate(items).to_dict())


@bp.post("/v1/xijian/desktop/pets")
def create_pet():
    body = _require_json()
    character_id = body.get("character_id")
    if not isinstance(character_id, str) or not character_id:
        raise ApiError(
            400, "`character_id` is required", "invalid_request_error",
            code="missing_character_id", param="character_id",
        )
    try:
        record = pets_stub.create_pet(
            character_id=character_id,
            can_fly=bool(body.get("can_fly", False)),
            can_interact=bool(body.get("can_interact", False)),
            spawn_x=parse_float(body.get("spawn_x"), "spawn_x", 0.0),
            spawn_y=parse_float(body.get("spawn_y"), "spawn_y", 0.0),
            is_active=bool(body.get("is_active", True)),
            name=body.get("name"),
        )
    except pets_stub.DesktopPetError as exc:
        raise _error(exc)
    return jsonify(record), 201


@bp.get("/v1/xijian/desktop/pets/<pet_id>")
def get_pet(pet_id: str):
    return jsonify(_pet_or_404(pet_id))


@bp.patch("/v1/xijian/desktop/pets/<pet_id>")
def patch_pet(pet_id: str):
    _pet_or_404(pet_id)
    body = _require_json()
    try:
        record = pets_stub.update_pet(pet_id, body)
    except pets_stub.DesktopPetError as exc:
        raise _error(exc)
    return jsonify(record)


@bp.delete("/v1/xijian/desktop/pets/<pet_id>")
def delete_pet(pet_id: str):
    _pet_or_404(pet_id)
    pets_stub.delete_pet(pet_id)
    return jsonify({"deleted": True, "pet_id": pet_id})


@bp.post("/v1/xijian/desktop/pets/<pet_id>/activate")
def activate_pet(pet_id: str):
    _pet_or_404(pet_id)
    return jsonify(pets_stub.set_pet_active(pet_id, True))


@bp.post("/v1/xijian/desktop/pets/<pet_id>/deactivate")
def deactivate_pet(pet_id: str):
    _pet_or_404(pet_id)
    return jsonify(pets_stub.set_pet_active(pet_id, False))


# ---------------------------------------------------------------------------
# Dynamic wallpapers — CRUD
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/desktop/wallpapers")
def list_wallpapers():
    args = request.args
    items = pets_stub.list_wallpapers(
        character_id=args.get("character_id"),
        is_active=(
            args.get("is_active", "true").lower() in ("1", "true")
            if args.get("is_active") is not None else None
        ),
    )
    return jsonify(paginate(items).to_dict())


@bp.post("/v1/xijian/desktop/wallpapers")
def create_wallpaper():
    body = _require_json()
    character_id = body.get("character_id")
    if not isinstance(character_id, str) or not character_id:
        raise ApiError(
            400, "`character_id` is required", "invalid_request_error",
            code="missing_character_id", param="character_id",
        )
    try:
        record = pets_stub.create_wallpaper(
            character_id=character_id,
            world_id=body.get("world_id"),
            env_settings=body.get("env_settings"),
            can_layout=bool(body.get("can_layout", True)),
            is_active=bool(body.get("is_active", False)),
        )
    except pets_stub.DesktopPetError as exc:
        raise _error(exc)
    return jsonify(record), 201


@bp.get("/v1/xijian/desktop/wallpapers/<wallpaper_id>")
def get_wallpaper(wallpaper_id: str):
    return jsonify(_wallpaper_or_404(wallpaper_id))


@bp.patch("/v1/xijian/desktop/wallpapers/<wallpaper_id>")
def patch_wallpaper(wallpaper_id: str):
    _wallpaper_or_404(wallpaper_id)
    body = _require_json()
    try:
        record = pets_stub.update_wallpaper(wallpaper_id, body)
    except pets_stub.DesktopPetError as exc:
        raise _error(exc)
    return jsonify(record)


@bp.delete("/v1/xijian/desktop/wallpapers/<wallpaper_id>")
def delete_wallpaper(wallpaper_id: str):
    _wallpaper_or_404(wallpaper_id)
    pets_stub.delete_wallpaper(wallpaper_id)
    return jsonify({"deleted": True, "wallpaper_id": wallpaper_id})


@bp.post("/v1/xijian/desktop/wallpapers/<wallpaper_id>/activate")
def activate_wallpaper(wallpaper_id: str):
    _wallpaper_or_404(wallpaper_id)
    return jsonify(pets_stub.set_wallpaper_active(wallpaper_id, True))


@bp.post("/v1/xijian/desktop/wallpapers/<wallpaper_id>/deactivate")
def deactivate_wallpaper(wallpaper_id: str):
    _wallpaper_or_404(wallpaper_id)
    return jsonify(pets_stub.set_wallpaper_active(wallpaper_id, False))


# ---------------------------------------------------------------------------
# Audit log (AC-2)
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/desktop/actions")
def list_all_pet_actions():
    args = request.args
    try:
        limit = int(args.get("limit", 100))
    except ValueError:
        limit = 100
    return jsonify({
        "entries": pets_stub.list_pet_actions(
            None, action_kind=args.get("action_kind"), limit=limit
        ),
    })


@bp.get("/v1/xijian/desktop/pets/<pet_id>/actions")
def list_pet_actions(pet_id: str):
    _pet_or_404(pet_id)
    args = request.args
    try:
        limit = int(args.get("limit", 100))
    except ValueError:
        limit = 100
    return jsonify({
        "pet_id": pet_id,
        "entries": pets_stub.list_pet_actions(
            pet_id, action_kind=args.get("action_kind"), limit=limit
        ),
    })


@bp.post("/v1/xijian/desktop/pets/<pet_id>/actions")
def dispatch_pet_action(pet_id: str):
    """Dispatch / record one pet action (AC-2 audit entry)."""
    _pet_or_404(pet_id)
    body = _require_json()
    action_kind = body.get("action_kind")
    if not isinstance(action_kind, str) or not action_kind:
        raise ApiError(
            400, "`action_kind` is required", "invalid_request_error",
            code="missing_action_kind", param="action_kind",
        )
    try:
        entry = pets_stub.log_pet_action(
            pet_id, action_kind, body.get("payload") or {}
        )
    except pets_stub.DesktopPetError as exc:
        raise _error(exc)
    return jsonify(entry), 201


# ---------------------------------------------------------------------------
# Desktop-client execution loop — /v1/xijian/mcp/pending*
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/mcp/pending")
def list_pending_actions():
    """Poll the pending-action queue (desktop client loop).

    ``?claim=1`` 顺手认领返回的每一条（pending → claimed），减少
    一次往返。``?status=`` 过滤，``?limit=`` 限制条数。
    """
    args = request.args
    try:
        limit = int(args.get("limit", 50))
    except ValueError:
        limit = 50
    status = args.get("status")
    claim = args.get("claim", "0").lower() in ("1", "true")
    items = pets_stub.list_pending(status=status, limit=limit)
    if claim:
        for item in items:
            if item.get("status") == pets_stub.PENDING_STATUS_PENDING:
                pets_stub.claim_action(item["id"])
    return jsonify({
        "object": "list",
        "data": items,
        "count": len(items),
    })


@bp.get("/v1/xijian/mcp/pending/<action_id>")
def get_pending_action(action_id: str):
    record = pets_stub.get_pending(action_id)
    if record is None:
        raise ApiError(
            404, "pending action not found", "not_found_error",
            code="pending_action_not_found",
        )
    return jsonify(record)


@bp.post("/v1/xijian/mcp/pending/<action_id>/claim")
def claim_pending_action(action_id: str):
    try:
        record = pets_stub.claim_action(action_id)
    except pets_stub.DesktopPetError as exc:
        raise _error(exc)
    return jsonify(record)


@bp.post("/v1/xijian/mcp/pending/<action_id>/result")
def report_pending_result(action_id: str):
    """Write back the execution result.

    Body: ``{"status": "executed"|"failed", "result": {...},
    "pet_id": optional}``。AC-4 gate（壁纸模式禁写）在 stub 层强制。
    """
    body = _require_json()
    status = body.get("status")
    if status not in (pets_stub.PENDING_STATUS_EXECUTED, pets_stub.PENDING_STATUS_FAILED):
        raise ApiError(
            400,
            f"`status` must be one of "
            f"({pets_stub.PENDING_STATUS_EXECUTED!r}, "
            f"{pets_stub.PENDING_STATUS_FAILED!r})",
            "invalid_request_error",
            code="invalid_result_status", param="status",
        )
    try:
        record = pets_stub.report_result(
            action_id,
            status,
            body.get("result") or {},
            pet_id=body.get("pet_id"),
        )
    except pets_stub.DesktopPetError as exc:
        raise _error(exc)
    return jsonify(record)


__all__ = ["bp"]
