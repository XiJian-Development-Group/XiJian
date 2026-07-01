"""``/v1/xijian/characters/*`` routes.

A3.2 state endpoints layered on top of the existing character CRUD.
The legacy ``GET/POST /<id>/state`` endpoints are kept for
backward compatibility — they expose the v1 fields (``affection`` /
``mood`` / ``recent_memory_summary``) and now also surface the A3.2
numeric fields (``hunger`` / ``thirst`` / ``health`` / ``mood`` /
``status`` / ``can_dialogue`` / ``active_behavior``).

New A3.2 endpoints:

* ``GET  /<id>/state/config``        — read decay / threshold / binding config
* ``PATCH /<id>/state/config``       — update decay rates, thresholds, bindings
* ``GET  /<id>/state/log``           — read the append-only change log
* ``POST /<id>/state/tick``          — manually trigger a tick (dev only)
* ``POST /<id>/state/recover``       — admin force-recover from Critical
* ``POST /<id>/state/recovering``    — event-driven Sick → Recovering
* ``PUT  /<id>/state/modifier``      — set runtime time / activity / world modifier
* ``DELETE /<id>/state/modifier``    — clear a runtime modifier
"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import character_state as cs_stub
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


# ---------------------------------------------------------------------------
# A3.2 — state config
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/characters/<character_id>/state/config")
def get_character_state_config(character_id: str):
    if chars_stub.get(character_id) is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    cfg = cs_stub.get_or_init_config(character_id)
    return jsonify(cfg)


@bp.patch("/v1/xijian/characters/<character_id>/state/config")
def patch_character_state_config(character_id: str):
    if chars_stub.get(character_id) is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    if not prot_stub.is_enabled():
        raise ApiError(403, "protection system is disabled", "protection_error", code="protection_disabled")
    payload = request.get_json(silent=True) or {}
    cfg = cs_stub.get_or_init_config(character_id)
    # Whitelist the keys we accept.  ``behavior_bindings`` is a
    # nested dict that we replace wholesale — partial deep-merge
    # would hide a typo and silently miss a binding update.
    for key in ("decay_per_hour", "thresholds", "recovery_thresholds", "transition_dwell_seconds"):
        if key in payload and isinstance(payload[key], dict):
            cfg[key] = dict(payload[key])
    if "behavior_bindings" in payload and isinstance(payload["behavior_bindings"], dict):
        # Merge per binding name so callers can update one binding
        # without re-sending the whole table.
        merged = dict(cfg.get("behavior_bindings") or {})
        for name, value in payload["behavior_bindings"].items():
            if isinstance(value, dict):
                merged[name] = {**merged.get(name, {}), **value}
        cfg["behavior_bindings"] = merged
    return jsonify(cfg)


# ---------------------------------------------------------------------------
# A3.2 — log
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/characters/<character_id>/state/log")
def get_character_state_log(character_id: str):
    if chars_stub.get(character_id) is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError as exc:
        raise ApiError(400, "limit must be an integer", "invalid_request_error", code="bad_limit") from exc
    if limit < 1 or limit > 500:
        raise ApiError(400, "limit must be in [1, 500]", "invalid_request_error", code="bad_limit")
    return jsonify({"entries": cs_stub.list_log(character_id, limit=limit)})


# ---------------------------------------------------------------------------
# A3.2 — tick + recover + modifiers
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/characters/<character_id>/state/tick")
def tick_character_state(character_id: str):
    if os.environ.get("XIJIAN_DEV") != "1":
        raise ApiError(404, "not found", "not_found_error", code="route_not_found")
    if chars_stub.get(character_id) is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    payload = request.get_json(silent=True) or {}
    if "field" in payload and "value" in payload:
        # Dev hook: apply a single field change so it's easy to seed
        # specific values for manual UI tests.
        try:
            value = float(payload["value"])
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "value must be numeric", "invalid_request_error", code="bad_value") from exc
        record = cs_stub.apply_field_change(
            character_id, payload["field"], value, reason="manual"
        )
        return jsonify({"applied": record})
    return jsonify({"tick": cs_stub.tick_character(character_id)})


@bp.post("/v1/xijian/characters/<character_id>/state/recover")
def recover_character(character_id: str):
    if chars_stub.get(character_id) is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    payload = request.get_json(silent=True) or {}
    record = cs_stub.force_recover(
        character_id,
        reason=payload.get("reason", "admin_recover"),
        ref_id=payload.get("ref_id"),
    )
    return jsonify(record)


@bp.post("/v1/xijian/characters/<character_id>/state/recovering")
def enter_recovering(character_id: str):
    if chars_stub.get(character_id) is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    payload = request.get_json(silent=True) or {}
    record = cs_stub.enter_recovering(
        character_id,
        reason=payload.get("reason", "world_recover"),
        ref_id=payload.get("ref_id"),
    )
    return jsonify(record)


@bp.put("/v1/xijian/characters/<character_id>/state/modifier")
def put_character_state_modifier(character_id: str):
    if chars_stub.get(character_id) is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    payload = request.get_json(silent=True) or {}
    modifiers = cs_stub.set_modifier(character_id, payload)
    return jsonify({"modifiers": modifiers})


@bp.delete("/v1/xijian/characters/<character_id>/state/modifier")
def delete_character_state_modifier(character_id: str):
    if chars_stub.get(character_id) is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    keys_param = request.args.get("keys", "")
    keys = tuple(k.strip() for k in keys_param.split(",") if k.strip())
    modifiers = cs_stub.clear_modifier(character_id, *keys)
    return jsonify({"modifiers": modifiers})


# ---------------------------------------------------------------------------
# A3.2 — active behaviour (used by the UI to pick motion / lines)
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/characters/<character_id>/state/behavior")
def get_character_state_behavior(character_id: str):
    if chars_stub.get(character_id) is None:
        raise ApiError(404, "character not found", "not_found_error", code="character_not_found")
    return jsonify({"behavior": cs_stub.get_active_behavior(character_id)})


__all__ = ["bp"]
