"""Stub travel-modes service — A4.3 in the function list v2.

A "travel mode" is a per-world transport option (步行 / 载具 / 传送).
Per the SQL schema in §A4.3 the fields are: id, world_id, name,
speed_factor, stamina_cost, event_chance.

Semantics
=========

* ``speed_factor`` — multiplier on the base travel time.  ``1.0`` is
  "as the crow flies"; ``0.5`` means "twice as fast" (a mount);
  ``2.0`` means "half as fast" (a wounded crawl).  Must be ``> 0``.
* ``stamina_cost`` — flat per-trip deduction the character takes.
  ``0.0`` is free; ``100.0`` is "I need a sit-down after this".
  Must be ``>= 0``.
* ``event_chance`` — per-trip probability of a random event firing on
  the road.  ``0.0`` is "never"; ``1.0`` is "every time".  Must be in
  ``[0.0, 1.0]``.

These three numbers feed straight into the trip planner (A4.3 US-01 /
AC-3).  We keep the data model tiny so the engine code that uses it
stays simple.

Test surface
============

Pure helpers (no I/O):

* :func:`_validate_speed_factor`
* :func:`_validate_stamina_cost`
* :func:`_validate_event_chance`
* :func:`estimate_trip` — combines the three knobs to produce a
  pre-flight cost preview.

Side-effecting functions (CRUD):

* :func:`create` / :func:`get` / :func:`list_for_world` /
  :func:`list_all` / :func:`update` / :func:`delete`
* :func:`seed_default` / :func:`reset_for_testing`
"""

from __future__ import annotations

from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_travel_mode_id
from xijian_api.utils.time import now_ts


#: Default base travel time in seconds (one map-unit at speed 1.0).
#: The trip planner (A4.3) multiplies this by ``speed_factor``.
DEFAULT_BASE_TRAVEL_SECONDS: float = 60.0


class TravelModeError(ValueError):
    """Raised on any travel-mode validation / lookup failure."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _validate_speed_factor(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TravelModeError(
            f"speed_factor must be a number, got {type(value).__name__}"
        )
    value = float(value)
    if value <= 0:
        raise TravelModeError(f"speed_factor must be > 0, got {value}")
    return value


def _validate_stamina_cost(value: Any) -> float:
    if value is None:
        return 0.0
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TravelModeError(
            f"stamina_cost must be a number, got {type(value).__name__}"
        )
    value = float(value)
    if value < 0:
        raise TravelModeError(f"stamina_cost must be >= 0, got {value}")
    return value


def _validate_event_chance(value: Any) -> float:
    if value is None:
        return 0.0
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TravelModeError(
            f"event_chance must be a number, got {type(value).__name__}"
        )
    value = float(value)
    if value < 0.0 or value > 1.0:
        raise TravelModeError(
            f"event_chance must be in [0.0, 1.0], got {value}"
        )
    return value


def _validate_name(name: Any) -> str:
    if not isinstance(name, str):
        raise TravelModeError(
            f"name must be a string, got {type(name).__name__}"
        )
    name = name.strip()
    if not name:
        raise TravelModeError("name must not be blank")
    return name


def _validate_world_id(world_id: Any) -> str:
    if not isinstance(world_id, str) or not world_id.strip():
        raise TravelModeError("world_id must be a non-empty string")
    return world_id


def estimate_trip(
    mode: dict,
    *,
    base_seconds: float = DEFAULT_BASE_TRAVEL_SECONDS,
    random_roll: float | None = None,
) -> dict:
    """Return a pre-flight cost preview for ``mode``.

    The returned dict has:

    * ``duration_seconds``  — base × speed_factor.
    * ``stamina_cost``      — the mode's flat cost.
    * ``event_chance``      — pass-through; if ``random_roll`` is also
                              given (in [0, 1]) we additionally return
                              ``event_triggered`` (bool).

    The function is pure — it does **not** mutate the mode record and
    does **not** write to state — so the trip-planner route handler
    can safely call it on every trip preview without locking.
    """
    if not isinstance(mode, dict):
        raise TravelModeError("mode must be a dict")
    duration = base_seconds / _validate_speed_factor(mode.get("speed_factor", 1.0))
    stamina = _validate_stamina_cost(mode.get("stamina_cost", 0.0))
    chance = _validate_event_chance(mode.get("event_chance", 0.0))
    out: dict = {
        "duration_seconds": round(duration, 4),
        "stamina_cost": stamina,
        "event_chance": chance,
    }
    if random_roll is not None:
        if not isinstance(random_roll, (int, float)) or isinstance(random_roll, bool):
            raise TravelModeError("random_roll must be a number in [0, 1]")
        if not 0.0 <= float(random_roll) <= 1.0:
            raise TravelModeError("random_roll must be in [0, 1]")
        out["event_triggered"] = float(random_roll) < chance
    return out


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create(
    *,
    world_id: str,
    name: str,
    speed_factor: float = 1.0,
    stamina_cost: float = 0.0,
    event_chance: float = 0.0,
    mode_id: str | None = None,
) -> dict:
    """Insert a new travel mode and return the stored record.

    Raises :class:`TravelModeError` on validation failure or when the
    world does not exist.
    """
    world_id = _validate_world_id(world_id)
    name = _validate_name(name)
    speed_factor = _validate_speed_factor(speed_factor)
    stamina_cost = _validate_stamina_cost(stamina_cost)
    event_chance = _validate_event_chance(event_chance)

    if world_id not in state.worlds:
        raise TravelModeError(f"world {world_id!r} does not exist")

    new_id = mode_id or gen_travel_mode_id()
    if new_id in state.travel_modes:
        raise TravelModeError(f"travel mode id {new_id!r} already exists")

    record = {
        "id": new_id,
        "world_id": world_id,
        "name": name,
        "speed_factor": speed_factor,
        "stamina_cost": stamina_cost,
        "event_chance": event_chance,
        "created_at": now_ts(),
    }
    state.travel_modes[new_id] = record
    return dict(record)


def get(mode_id: str) -> dict | None:
    return state.travel_modes.get(mode_id)


def get_required(mode_id: str) -> dict:
    record = state.travel_modes.get(mode_id)
    if record is None:
        raise TravelModeError(f"travel mode {mode_id!r} not found")
    return record


def list_for_world(world_id: str) -> list[dict]:
    return [
        dict(rec) for rec in state.travel_modes.values()
        if rec.get("world_id") == world_id
    ]


def list_all() -> list[dict]:
    return [dict(rec) for rec in state.travel_modes.values()]


def update(mode_id: str, patch: dict) -> dict | None:
    """Patch mutable fields.  ``id`` and ``world_id`` are immutable."""
    if not isinstance(patch, dict):
        raise TravelModeError("patch must be a dict")
    record = state.travel_modes.get(mode_id)
    if record is None:
        return None
    if "id" in patch and patch["id"] != mode_id:
        raise TravelModeError("id is immutable; create a new travel mode")
    if "world_id" in patch and patch["world_id"] != record["world_id"]:
        raise TravelModeError(
            "world_id is immutable; create a new travel mode"
        )

    new_name = (
        _validate_name(patch["name"]) if "name" in patch else record["name"]
    )
    new_speed = (
        _validate_speed_factor(patch["speed_factor"])
        if "speed_factor" in patch
        else record["speed_factor"]
    )
    new_stamina = (
        _validate_stamina_cost(patch["stamina_cost"])
        if "stamina_cost" in patch
        else record["stamina_cost"]
    )
    new_chance = (
        _validate_event_chance(patch["event_chance"])
        if "event_chance" in patch
        else record["event_chance"]
    )

    record["name"] = new_name
    record["speed_factor"] = new_speed
    record["stamina_cost"] = new_stamina
    record["event_chance"] = new_chance
    return dict(record)


def delete(mode_id: str) -> bool:
    if mode_id not in state.travel_modes:
        return False
    del state.travel_modes[mode_id]
    return True


# ---------------------------------------------------------------------------
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """No-op seed.

    The world library is operator-curated.  We don't pre-populate any
    default travel modes — operators add the ``walk`` / ``horse`` /
    ``teleport`` modes that fit their world.
    """


def reset_for_testing() -> None:
    state.travel_modes.clear()


__all__ = [
    "TravelModeError",
    "DEFAULT_BASE_TRAVEL_SECONDS",
    "create",
    "get",
    "get_required",
    "list_for_world",
    "list_all",
    "update",
    "delete",
    "estimate_trip",
    "seed_default",
    "reset_for_testing",
]
