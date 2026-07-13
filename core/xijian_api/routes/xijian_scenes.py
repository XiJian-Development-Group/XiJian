"""``/v1/xijian/scenes/*`` routes — A4.3.

Three logical groups share the ``/v1/xijian/scenes`` namespace so the
URL surface stays easy to scan:

* **POI** (``/v1/xijian/scenes/pois/*``) — three-level map / region /
  POI tree.
* **Travel** (``/v1/xijian/scenes/travel-modes/*``) — per-world
  transport options.
* **Scene interactions** (``/v1/xijian/scenes/interactions/*``) —
  operator-curated "this action is possible at this POI against this
  target" definitions, with a ``POST .../trigger`` endpoint that
  honours the per-character cooldown.

POI endpoints
=============

* ``GET    /v1/xijian/scenes/pois``                 — list (optional ?world_id)
* ``POST   /v1/xijian/scenes/pois``                 — create
* ``GET    /v1/xijian/scenes/pois/<poi_id>``        — get
* ``PATCH  /v1/xijian/scenes/pois/<poi_id>``        — patch
* ``DELETE /v1/xijian/scenes/pois/<poi_id>``        — delete (no orphans)
* ``GET    /v1/xijian/scenes/pois/tree``            — nested tree (?world_id)
* ``GET    /v1/xijian/scenes/pois/<poi_id>/chain``  — ancestor chain
* ``GET    /v1/xijian/scenes/pois/<poi_id>/children``  — direct children
* ``GET    /v1/xijian/scenes/pois/<poi_id>/descendants`` — flat DFS

Travel endpoints
================

* ``GET    /v1/xijian/scenes/travel-modes``         — list (?world_id)
* ``POST   /v1/xijian/scenes/travel-modes``         — create
* ``GET    /v1/xijian/scenes/travel-modes/<id>``    — get
* ``PATCH  /v1/xijian/scenes/travel-modes/<id>``    — patch
* ``DELETE /v1/xijian/scenes/travel-modes/<id>``    — delete
* ``POST   /v1/xijian/scenes/travel-modes/<id>/estimate`` — cost preview

Scene-interaction endpoints
===========================

* ``GET    /v1/xijian/scenes/interactions``         — list (?world_id / ?poi_id)
* ``POST   /v1/xijian/scenes/interactions``         — create
* ``GET    /v1/xijian/scenes/interactions/<id>``    — get
* ``PATCH  /v1/xijian/scenes/interactions/<id>``    — patch
* ``DELETE /v1/xijian/scenes/interactions/<id>``    — delete
* ``POST   /v1/xijian/scenes/interactions/<id>/trigger`` — fire (cooldown-aware)
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import pois as pois_stub
from xijian_api.stubs import scene_interactions as si_stub
from xijian_api.stubs import travel_modes as tm_stub


bp = Blueprint("xijian_scenes", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.xijian_scenes")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_json(*, optional: bool = False) -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        if optional:
            return {}
        raise ApiError(
            400, "request body must be a JSON object",
            "invalid_request_error", code="invalid_body",
        )
    return body


def _err_from_stub(exc: Exception, *, default_code: str) -> "ApiError":
    """Map a stub exception to a 4xx ApiError.

    Stub exceptions are :class:`ValueError`-based with a string
    message; we don't introspect the message (it might contain user
    input) but we always preserve it on the wire.
    """
    return ApiError(
        400, str(exc), "invalid_request_error", code=default_code,
    )


# ===========================================================================
# POI
# ===========================================================================


@bp.get("/v1/xijian/scenes/pois")
def list_pois():
    world_id = request.args.get("world_id")
    if world_id is not None:
        return jsonify(paginate(pois_stub.list_for_world(world_id)).to_dict())
    return jsonify(paginate(pois_stub.list_all()).to_dict())


@bp.post("/v1/xijian/scenes/pois")
def create_poi():
    body = _require_json()
    try:
        record = pois_stub.create(
            world_id=body.get("world_id"),
            name=body.get("name"),
            kind=body.get("kind"),
            parent_id=body.get("parent_id"),
            coords=body.get("coords"),
            description=body.get("description", ""),
            poi_id=body.get("poi_id"),
        )
    except pois_stub.POIError as exc:
        raise _err_from_stub(exc, default_code="poi_error")
    return jsonify(record), 201


@bp.get("/v1/xijian/scenes/pois/tree")
def tree_pois():
    world_id = request.args.get("world_id")
    if not isinstance(world_id, str) or not world_id:
        raise ApiError(400, "`world_id` query param is required",
                       "invalid_request_error", code="missing_world_id")
    root_id = request.args.get("root_id")
    try:
        tree = pois_stub.get_tree(world_id, root_id=root_id)
    except pois_stub.POIError as exc:
        raise _err_from_stub(exc, default_code="poi_error")
    return jsonify({"world_id": world_id, "tree": tree})


@bp.get("/v1/xijian/scenes/pois/<poi_id>")
def get_poi(poi_id: str):
    record = pois_stub.get(poi_id)
    if record is None:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/scenes/pois/<poi_id>")
def patch_poi(poi_id: str):
    body = _require_json()
    try:
        record = pois_stub.update(poi_id, body)
    except pois_stub.POIError as exc:
        raise _err_from_stub(exc, default_code="poi_error")
    if record is None:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify(record)


@bp.delete("/v1/xijian/scenes/pois/<poi_id>")
def delete_poi(poi_id: str):
    try:
        removed = pois_stub.delete(poi_id)
    except pois_stub.POIError as exc:
        raise _err_from_stub(exc, default_code="poi_error")
    if not removed:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify({"deleted": poi_id})


@bp.get("/v1/xijian/scenes/pois/<poi_id>/chain")
def poi_chain(poi_id: str):
    chain = pois_stub.get_ancestor_chain(poi_id)
    if not chain:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify({"poi_id": poi_id, "chain": chain})


@bp.get("/v1/xijian/scenes/pois/<poi_id>/children")
def poi_children(poi_id: str):
    if pois_stub.get(poi_id) is None:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify({"poi_id": poi_id, "children": pois_stub.list_children(poi_id)})


@bp.get("/v1/xijian/scenes/pois/<poi_id>/descendants")
def poi_descendants(poi_id: str):
    if pois_stub.get(poi_id) is None:
        raise ApiError(404, f"poi {poi_id!r} not found",
                       "not_found_error", code="poi_not_found")
    return jsonify({
        "poi_id": poi_id,
        "descendants": pois_stub.get_descendants(poi_id),
    })


# ===========================================================================
# Travel modes
# ===========================================================================


@bp.get("/v1/xijian/scenes/travel-modes")
def list_travel_modes():
    world_id = request.args.get("world_id")
    if world_id is not None:
        return jsonify(paginate(tm_stub.list_for_world(world_id)).to_dict())
    return jsonify(paginate(tm_stub.list_all()).to_dict())


@bp.post("/v1/xijian/scenes/travel-modes")
def create_travel_mode():
    body = _require_json()
    try:
        record = tm_stub.create(
            world_id=body.get("world_id"),
            name=body.get("name"),
            speed_factor=body.get("speed_factor", 1.0),
            stamina_cost=body.get("stamina_cost", 0.0),
            event_chance=body.get("event_chance", 0.0),
            mode_id=body.get("mode_id"),
        )
    except tm_stub.TravelModeError as exc:
        raise _err_from_stub(exc, default_code="travel_mode_error")
    return jsonify(record), 201


@bp.get("/v1/xijian/scenes/travel-modes/<mode_id>")
def get_travel_mode(mode_id: str):
    record = tm_stub.get(mode_id)
    if record is None:
        raise ApiError(404, f"travel mode {mode_id!r} not found",
                       "not_found_error", code="travel_mode_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/scenes/travel-modes/<mode_id>")
def patch_travel_mode(mode_id: str):
    body = _require_json()
    try:
        record = tm_stub.update(mode_id, body)
    except tm_stub.TravelModeError as exc:
        raise _err_from_stub(exc, default_code="travel_mode_error")
    if record is None:
        raise ApiError(404, f"travel mode {mode_id!r} not found",
                       "not_found_error", code="travel_mode_not_found")
    return jsonify(record)


@bp.delete("/v1/xijian/scenes/travel-modes/<mode_id>")
def delete_travel_mode(mode_id: str):
    if not tm_stub.delete(mode_id):
        raise ApiError(404, f"travel mode {mode_id!r} not found",
                       "not_found_error", code="travel_mode_not_found")
    return jsonify({"deleted": mode_id})


@bp.post("/v1/xijian/scenes/travel-modes/<mode_id>/estimate")
def estimate_travel_mode(mode_id: str):
    body = _require_json(optional=True)
    record = tm_stub.get(mode_id)
    if record is None:
        raise ApiError(404, f"travel mode {mode_id!r} not found",
                       "not_found_error", code="travel_mode_not_found")
    try:
        preview = tm_stub.estimate_trip(
            record,
            base_seconds=float(body.get("base_seconds", tm_stub.DEFAULT_BASE_TRAVEL_SECONDS)),
            random_roll=body.get("random_roll"),
        )
    except tm_stub.TravelModeError as exc:
        raise _err_from_stub(exc, default_code="travel_mode_error")
    return jsonify({"mode_id": mode_id, "preview": preview})


# ===========================================================================
# Scene interactions
# ===========================================================================


@bp.get("/v1/xijian/scenes/interactions")
def list_scene_interactions():
    world_id = request.args.get("world_id")
    poi_id = request.args.get("poi_id")
    if poi_id is not None:
        return jsonify(paginate(si_stub.list_for_poi(poi_id)).to_dict())
    if world_id is not None:
        return jsonify(paginate(si_stub.list_for_world(world_id)).to_dict())
    return jsonify(paginate(si_stub.list_all()).to_dict())


@bp.post("/v1/xijian/scenes/interactions")
def create_scene_interaction():
    body = _require_json()
    try:
        record = si_stub.create(
            world_id=body.get("world_id"),
            poi_id=body.get("poi_id"),
            target_type=body.get("target_type"),
            target_id=body.get("target_id"),
            action=body.get("action"),
            effects=body.get("effects"),
            cooldown_sec=body.get("cooldown_sec"),
            interaction_id=body.get("interaction_id"),
        )
    except si_stub.SceneInteractionError as exc:
        raise _err_from_stub(exc, default_code="scene_interaction_error")
    return jsonify(record), 201


@bp.get("/v1/xijian/scenes/interactions/<interaction_id>")
def get_scene_interaction(interaction_id: str):
    record = si_stub.get(interaction_id)
    if record is None:
        raise ApiError(404, f"scene interaction {interaction_id!r} not found",
                       "not_found_error", code="scene_interaction_not_found")
    return jsonify(record)


@bp.patch("/v1/xijian/scenes/interactions/<interaction_id>")
def patch_scene_interaction(interaction_id: str):
    body = _require_json()
    try:
        record = si_stub.update(interaction_id, body)
    except si_stub.SceneInteractionError as exc:
        raise _err_from_stub(exc, default_code="scene_interaction_error")
    if record is None:
        raise ApiError(404, f"scene interaction {interaction_id!r} not found",
                       "not_found_error", code="scene_interaction_not_found")
    return jsonify(record)


@bp.delete("/v1/xijian/scenes/interactions/<interaction_id>")
def delete_scene_interaction(interaction_id: str):
    if not si_stub.delete(interaction_id):
        raise ApiError(404, f"scene interaction {interaction_id!r} not found",
                       "not_found_error", code="scene_interaction_not_found")
    return jsonify({"deleted": interaction_id})


@bp.post("/v1/xijian/scenes/interactions/<interaction_id>/trigger")
def trigger_scene_interaction(interaction_id: str):
    body = _require_json(optional=True)
    result = si_stub.trigger(
        interaction_id,
        character_id=body.get("character_id"),
        payload=body.get("payload"),
    )
    if not result.get("accepted"):
        reason = result.get("reason")
        # Differentiate "not found" (404) from semantic rejects (409).
        if reason == "interaction_not_found":
            raise ApiError(404, "scene interaction not found",
                           "not_found_error", code="scene_interaction_not_found")
        raise ApiError(409, reason or "rejected",
                       "invalid_request_error", code=reason or "rejected")
    return jsonify(result)


# ---------------------------------------------------------------------------
# Seed hook
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Empty seed.  Real worlds are operator-curated.

    The hook exists so ``xijian_api.stubs.seed_all`` has a stable
    call-site for the A4.3 buckets.
    """
