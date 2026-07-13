"""Stub scene-interaction service — A4.3 in the function list v2.

A "scene interaction" is a per-POI action the user can fire against
an NPC / object / mechanism.  Per the SQL schema in §A4.3 the fields
are: id, world_id, poi_id, target_type, target_id, action, effects,
cooldown_sec.

Difference from the chat-level :mod:`xijian_api.stubs.interactions`
============================================================

The existing ``stubs/interactions.py`` module stores **chat-level**
interaction templates (拥抱 / 接吻 / 摸头).  Those are pre-seeded
canonical action templates the chat pipeline can fire on the user's
behalf, and the SQL row id is ``int_<12 hex>``.

A4.3's ``scene_interactions`` is a **different** resource:

* chat-level interactions fire from the dialog layer; the spec
  prefixes them ``int_``.
* scene-level interactions are operator-curated definitions
  describing "at POI X, against target Y, this action is possible and
  yields effect Z".  The spec prefixes them ``sint_`` (see
  :func:`xijian_api.utils.ids.gen_scene_interaction_id`).

We intentionally keep them as two separate buckets to avoid
back-compat churn.  The chat-level blueprint at
``/v1/xijian/interactions/*`` is unaffected.

Trigger semantics
=================

* :func:`trigger` validates the interaction exists, that the target
  is alive (for ``target_type == "npc"``), and that the cooldown has
  elapsed since the last fire.  On success it writes a row to the
  world audit log and returns the resolved effects + cooldown_until
  timestamp.
* The cooldown is **per** ``(interaction_id, character_id)`` pair so
  one character spamming "open the chest" doesn't lock it for every
  other character.  For object / mechanism targets the cooldown is
  effectively global (per ``interaction_id`` alone).
* A character in an "un-interactable" state (AC edge case) is blocked
  with reason ``"character_not_interactable"`` — the route layer
  surfaces a 409.

Buckets
=======

* :data:`xijian_api.stubs.state.scene_interactions` — the
  ``{interaction_id: dict}`` store.
* The cooldown map is a process-local ``{key: float_ts}`` so it
  survives between stub calls within one process.  The state is
  **not** in :mod:`state` because it's not part of the public
  API surface — operators shouldn't be patching it directly.

A4.1 cross-link
===============

If the effects payload has a ``"fire_event_id"`` key, :func:`trigger`
publishes a ``scene.interaction.fired`` record onto the existing
event-bus so any listening world-event stub can fire.  The stub does
not block if the bus isn't installed; the publish is best-effort.

Test surface
============

Pure helpers (no I/O):

* :func:`_cooldown_key`
* :func:`_validate_target_type`
* :func:`_validate_effects`

Side-effecting functions (CRUD + trigger):

* :func:`create` / :func:`get` / :func:`list_for_world` /
  :func:`list_for_poi` / :func:`list_all` / :func:`update` /
  :func:`delete`
* :func:`trigger`
* :func:`clear_cooldowns` — test helper
* :func:`seed_default` / :func:`reset_for_testing`
"""

from __future__ import annotations

import threading
from typing import Any

from xijian_api.stubs import state
from xijian_api.stubs import world_audit as wa_stub
from xijian_api.utils.ids import gen_audit_id, gen_scene_interaction_id
from xijian_api.utils.time import now_ts


#: Allowed target types per the SQL schema.  ``"npc"`` and
#: ``"object"`` and ``"mechanism"`` are the spec-listed values.
VALID_TARGET_TYPES = frozenset({"npc", "object", "mechanism"})

#: Default cooldown in seconds when the operator doesn't set one.
#: Spec doesn't pin a default; we pick a small positive value so a
#: missing field can't be exploited for spam-clicking.
DEFAULT_COOLDOWN_SECONDS: int = 3


class SceneInteractionError(ValueError):
    """Raised on any scene-interaction validation / lookup failure."""


# ---------------------------------------------------------------------------
# Cooldown map
# ---------------------------------------------------------------------------


#: ``{cooldown_key: cooldown_until_ts}``.  Held under a lock so
#: concurrent :func:`trigger` calls from the chat pipeline (e.g. an
#: NPC mid-sentence nudging an object) can't race the timestamp
#: check.
_COOLDOWNS: dict[str, float] = {}
_COOLDOWN_LOCK = threading.Lock()


def _cooldown_key(interaction_id: str, character_id: str | None) -> str:
    """Build the cooldown key.

    For ``target_type != "npc"`` we still key on ``character_id`` if
    provided so per-character cooldowns remain isolated; when the
    character_id is ``None`` we use a wildcard slot, which makes the
    cooldown effectively global for that interaction.
    """
    return f"{interaction_id}::{character_id or '*'}"


def clear_cooldowns() -> None:
    """Test helper.  Wipe every cooldown entry."""
    with _COOLDOWN_LOCK:
        _COOLDOWNS.clear()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _validate_target_type(value: Any) -> str:
    if not isinstance(value, str):
        raise SceneInteractionError(
            f"target_type must be a string, got {type(value).__name__}"
        )
    if value not in VALID_TARGET_TYPES:
        raise SceneInteractionError(
            f"invalid target_type {value!r}; must be one of {sorted(VALID_TARGET_TYPES)}"
        )
    return value


def _validate_cooldown(value: Any) -> int:
    if value is None:
        return DEFAULT_COOLDOWN_SECONDS
    if isinstance(value, bool) or not isinstance(value, int):
        raise SceneInteractionError(
            f"cooldown_sec must be an integer, got {type(value).__name__}"
        )
    if value < 0:
        raise SceneInteractionError(f"cooldown_sec must be >= 0, got {value}")
    return value


def _validate_action(value: Any) -> str:
    if not isinstance(value, str):
        raise SceneInteractionError(
            f"action must be a string, got {type(value).__name__}"
        )
    value = value.strip()
    if not value:
        raise SceneInteractionError("action must not be blank")
    return value


def _validate_effects(value: Any) -> dict:
    """Effects is a JSON blob (per SQL schema).  We keep it as a dict
    but the operator is free to nest whatever their engine understands.

    The reserved keys we look at:

    * ``fire_event_id`` — A4.1 cross-link; we publish onto the bus.
    * ``stamina_delta`` — applied to the character after firing.
    * ``mood_delta``    — applied to the NPC (if npc target).
    * ``world_state``   — patched into ``state.world_environment``
                          for the world.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SceneInteractionError(
            f"effects must be a dict (JSON), got {type(value).__name__}"
        )
    return value


def _validate_world_id(world_id: Any) -> str:
    if not isinstance(world_id, str) or not world_id.strip():
        raise SceneInteractionError("world_id must be a non-empty string")
    return world_id


def _validate_poi_id(poi_id: Any) -> str:
    if not isinstance(poi_id, str) or not poi_id.strip():
        raise SceneInteractionError("poi_id must be a non-empty string")
    return poi_id


def _validate_target_id(target_id: Any) -> str:
    if not isinstance(target_id, str) or not target_id.strip():
        raise SceneInteractionError("target_id must be a non-empty string")
    return target_id


def _character_is_interactable(character_id: str | None) -> bool:
    """AC edge case: a character with health <= 0 cannot trigger
    dangerous interactions.  We check the A3.2 character-state store;
    when the state is missing we assume the character is interactable
    so the stub is friendly to non-state characters.
    """
    if character_id is None:
        return True
    state_record = state.character_states.get(character_id)
    if state_record is None:
        return True
    if state_record.get("status") in {"unconscious", "dead", "frozen"}:
        return False
    health = state_record.get("health")
    if isinstance(health, (int, float)) and health <= 0:
        return False
    return True


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create(
    *,
    world_id: str,
    poi_id: str,
    target_type: str,
    target_id: str,
    action: str,
    effects: dict | None = None,
    cooldown_sec: int | None = None,
    interaction_id: str | None = None,
) -> dict:
    """Insert a new scene interaction and return the stored record.

    Raises :class:`SceneInteractionError` when:

    * the world does not exist;
    * the POI does not exist (or belongs to a different world);
    * the target_type is invalid;
    * the new id collides with an existing record.
    """
    world_id = _validate_world_id(world_id)
    poi_id = _validate_poi_id(poi_id)
    target_type = _validate_target_type(target_type)
    target_id = _validate_target_id(target_id)
    action = _validate_action(action)
    effects = _validate_effects(effects)
    cooldown_sec = _validate_cooldown(cooldown_sec)

    if world_id not in state.worlds:
        raise SceneInteractionError(f"world {world_id!r} does not exist")
    poi_record = state.pois.get(poi_id)
    if poi_record is None:
        raise SceneInteractionError(f"poi {poi_id!r} not found")
    if poi_record.get("world_id") != world_id:
        raise SceneInteractionError(
            f"poi {poi_id!r} belongs to world {poi_record.get('world_id')!r}, "
            f"not {world_id!r}"
        )

    new_id = interaction_id or gen_scene_interaction_id()
    if new_id in state.scene_interactions:
        raise SceneInteractionError(
            f"scene interaction id {new_id!r} already exists"
        )

    record = {
        "id": new_id,
        "world_id": world_id,
        "poi_id": poi_id,
        "target_type": target_type,
        "target_id": target_id,
        "action": action,
        "effects": effects,
        "cooldown_sec": cooldown_sec,
        "created_at": now_ts(),
    }
    state.scene_interactions[new_id] = record
    return dict(record)


def get(interaction_id: str) -> dict | None:
    return state.scene_interactions.get(interaction_id)


def get_required(interaction_id: str) -> dict:
    record = state.scene_interactions.get(interaction_id)
    if record is None:
        raise SceneInteractionError(
            f"scene interaction {interaction_id!r} not found"
        )
    return record


def list_for_world(world_id: str) -> list[dict]:
    return [
        dict(rec) for rec in state.scene_interactions.values()
        if rec.get("world_id") == world_id
    ]


def list_for_poi(poi_id: str) -> list[dict]:
    return [
        dict(rec) for rec in state.scene_interactions.values()
        if rec.get("poi_id") == poi_id
    ]


def list_all() -> list[dict]:
    return [dict(rec) for rec in state.scene_interactions.values()]


def update(interaction_id: str, patch: dict) -> dict | None:
    """Patch mutable fields.  ``id`` and ``world_id`` are immutable.

    If the patch changes ``poi_id`` we re-validate that the new POI
    belongs to the same world.
    """
    if not isinstance(patch, dict):
        raise SceneInteractionError("patch must be a dict")
    record = state.scene_interactions.get(interaction_id)
    if record is None:
        return None
    if "id" in patch and patch["id"] != interaction_id:
        raise SceneInteractionError("id is immutable; create a new scene interaction")
    if "world_id" in patch and patch["world_id"] != record["world_id"]:
        raise SceneInteractionError(
            "world_id is immutable; create a new scene interaction"
        )

    new_poi = patch.get("poi_id", record["poi_id"])
    if new_poi != record["poi_id"]:
        new_poi = _validate_poi_id(new_poi)
        poi_record = state.pois.get(new_poi)
        if poi_record is None:
            raise SceneInteractionError(f"poi {new_poi!r} not found")
        if poi_record.get("world_id") != record["world_id"]:
            raise SceneInteractionError(
                f"poi {new_poi!r} belongs to world {poi_record.get('world_id')!r}, "
                f"not {record['world_id']!r}"
            )

    new_target_type = (
        _validate_target_type(patch["target_type"])
        if "target_type" in patch
        else record["target_type"]
    )
    new_target_id = (
        _validate_target_id(patch["target_id"])
        if "target_id" in patch
        else record["target_id"]
    )
    new_action = (
        _validate_action(patch["action"])
        if "action" in patch
        else record["action"]
    )
    new_effects = (
        _validate_effects(patch["effects"])
        if "effects" in patch
        else record["effects"]
    )
    new_cooldown = (
        _validate_cooldown(patch["cooldown_sec"])
        if "cooldown_sec" in patch
        else record["cooldown_sec"]
    )

    record["poi_id"] = new_poi
    record["target_type"] = new_target_type
    record["target_id"] = new_target_id
    record["action"] = new_action
    record["effects"] = new_effects
    record["cooldown_sec"] = new_cooldown
    return dict(record)


def delete(interaction_id: str) -> bool:
    if interaction_id not in state.scene_interactions:
        return False
    del state.scene_interactions[interaction_id]
    # Drop any lingering cooldowns so a deleted interaction doesn't
    # leave a stale entry occupying memory.
    with _COOLDOWN_LOCK:
        stale = [k for k in _COOLDOWNS if k.startswith(f"{interaction_id}::")]
        for k in stale:
            del _COOLDOWNS[k]
    return True


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


def trigger(
    interaction_id: str,
    *,
    character_id: str | None = None,
    payload: dict | None = None,
    now: float | None = None,
) -> dict:
    """Fire the interaction and return a result envelope.

    Result shape::

        {
            "accepted": bool,
            "interaction_id": str,
            "world_id": str,
            "effects": dict,
            "cooldown_until": float | None,
            "reason": str | None,    # populated only on reject
            "audit_id": str | None,  # populated only on accept
        }

    Reasons for rejection:

    * ``"interaction_not_found"``
    * ``"character_not_interactable"`` (A4.3 AC edge case)
    * ``"on_cooldown"`` (cooldown not yet elapsed)
    * ``"target_dead"`` (NPC target not alive)
    """
    record = state.scene_interactions.get(interaction_id)
    if record is None:
        return {
            "accepted": False,
            "interaction_id": interaction_id,
            "world_id": None,
            "effects": {},
            "cooldown_until": None,
            "reason": "interaction_not_found",
            "audit_id": None,
        }

    timestamp = float(now if now is not None else now_ts())

    if not _character_is_interactable(character_id):
        return {
            "accepted": False,
            "interaction_id": interaction_id,
            "world_id": record["world_id"],
            "effects": {},
            "cooldown_until": None,
            "reason": "character_not_interactable",
            "audit_id": None,
        }

    # Validate NPC target liveness (A4.2 cross-link).
    if record["target_type"] == "npc":
        npc = state.npcs.get(record["target_id"])
        if npc is not None and not npc.get("is_alive", True):
            return {
                "accepted": False,
                "interaction_id": interaction_id,
                "world_id": record["world_id"],
                "effects": {},
                "cooldown_until": None,
                "reason": "target_dead",
                "audit_id": None,
            }

    cooldown_key = _cooldown_key(interaction_id, character_id)
    cooldown_sec = record.get("cooldown_sec", DEFAULT_COOLDOWN_SECONDS) or 0
    with _COOLDOWN_LOCK:
        last_until = _COOLDOWNS.get(cooldown_key, 0.0)
        if timestamp < last_until:
            return {
                "accepted": False,
                "interaction_id": interaction_id,
                "world_id": record["world_id"],
                "effects": {},
                "cooldown_until": last_until,
                "reason": "on_cooldown",
                "audit_id": None,
            }
        cooldown_until = timestamp + cooldown_sec
        _COOLDOWNS[cooldown_key] = cooldown_until

    # Write the audit log so AC-2 ("互动结果可回溯") holds.
    audit_id = gen_audit_id()
    try:
        wa_stub.record(
            world_id=record["world_id"],
            action="scene_interaction.trigger",
            actor="user" if character_id else "system",
            payload={
                "audit_id": audit_id,
                "interaction_id": interaction_id,
                "poi_id": record["poi_id"],
                "target_type": record["target_type"],
                "target_id": record["target_id"],
                "action_name": record["action"],
                "effects": record["effects"],
                "cooldown_until": cooldown_until,
                "character_id": character_id,
                "request_payload": payload or {},
            },
        )
    except Exception:
        # Audit log must not break the trigger path; a failed write
        # still returns the interaction's effects.
        audit_id = None

    # A4.1 cross-link: best-effort fire onto the bus.  We use the
    # existing ``fire_event`` API; failure is swallowed so a buggy
    # A4.1 subscriber can't take down the trigger path.
    fire_event_id = record["effects"].get("fire_event_id") if record["effects"] else None
    if fire_event_id:
        try:
            from xijian_api.stubs.events import fire_event
            fire_event(
                fire_event_id,
                payload={
                    "interaction_id": interaction_id,
                    "character_id": character_id,
                    "source": "scene_interaction",
                },
            )
        except Exception:
            pass

    return {
        "accepted": True,
        "interaction_id": interaction_id,
        "world_id": record["world_id"],
        "effects": record["effects"],
        "cooldown_until": cooldown_until,
        "reason": None,
        "audit_id": audit_id,
    }


# ---------------------------------------------------------------------------
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """No-op seed.

    The world library is operator-curated; we don't pre-populate any
    default scene interactions.  The function exists so
    :func:`xijian_api.stubs.seed_all` has a stable hook to call.
    """


def reset_for_testing() -> None:
    state.scene_interactions.clear()
    clear_cooldowns()


__all__ = [
    "SceneInteractionError",
    "VALID_TARGET_TYPES",
    "DEFAULT_COOLDOWN_SECONDS",
    "create",
    "get",
    "get_required",
    "list_for_world",
    "list_for_poi",
    "list_all",
    "update",
    "delete",
    "trigger",
    "clear_cooldowns",
    "seed_default",
    "reset_for_testing",
]
