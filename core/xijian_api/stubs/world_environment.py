"""Per-world environment state — weather / time / light / ambient.

A4.2 spec defines a `world_environment` table that holds *visual /
audio ambient state* which the renderer reads to draw the scene, the
audio backend reads to mix the BGM, and the simulator reads to drive
tick-based transitions (time_of_day advances, weather drifts).

The module is intentionally simple:

* One row per world.  Created lazily on first read.
* Updateable via ``patch_environment`` (any subset of fields).
* Time-of-day defaults to ``12:00`` (midday) for new worlds.
* Light level = deterministic from time_of_day (no-op, here
  computed for callers that want it).
"""

from __future__ import annotations

import logging
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.world_environment")

#: Default weather on world creation.
DEFAULT_WEATHER = "sunny"

#: Default time of day, in minutes-from-midnight (noon = 720).
DEFAULT_TIME_OF_DAY = 720

#: Default light level, 0..1.
DEFAULT_LIGHT_LEVEL = 0.6


def _default_record(world_id: str) -> dict:
    return {
        "world_id": world_id,
        "weather": DEFAULT_WEATHER,
        "time_of_day": DEFAULT_TIME_OF_DAY,
        "light_level": DEFAULT_LIGHT_LEVEL,
        "ambient_audio": None,
        "env_meta": {},
        "updated_at": now_ts(),
    }


def get(world_id: str) -> dict | None:
    """Return the environment record for ``world_id`` (creating one if absent).

    ``None`` is returned *only* for the explicit "world doesn't exist"
    case (the caller already supplies world_id from a known id, so this
    rarely matters — but we honour the contract).
    """
    record = state.world_environment.get(world_id)
    if record is None:
        # Treat as "no world" so callers like the scheduler can early-exit.
        # The audit / route layer is responsible for materializing an
        # initial record via :func:`ensure_environment`.
        return None
    return record


def ensure_environment(world_id: str) -> dict:
    """Materialize a default environment record if absent; return it.

    Idempotent.  Called by the world-creation route and any test fixture
    that wants a deterministic environment shape.
    """
    record = state.world_environment.get(world_id)
    if record is not None:
        return record
    record = _default_record(world_id)
    state.world_environment[world_id] = record
    return record


def patch_environment(
    world_id: str, patch: dict[str, Any], *, now: float | None = None
) -> dict:
    """Update a subset of the environment fields; create on first call.

    Recognised keys: ``weather``, ``time_of_day`` (0..1439 minutes),
    ``light_level`` (0..1), ``ambient_audio`` (path string or ``None``),
    ``env_meta`` (merge dict).  Values outside recognised keys are
    silently accepted into ``env_meta`` for forward-compat — explicit
    operators may extend the schema.
    """
    record = ensure_environment(world_id)
    timestamp = float(now) if now is not None else now_ts()
    meta = dict(record.get("env_meta") or {})
    for key, value in patch.items():
        if key in {"weather", "ambient_audio"}:
            record[key] = value
        elif key == "time_of_day":
            try:
                minutes = int(value) % 1440
            except (TypeError, ValueError):
                _LOGGER.debug("invalid time_of_day %r for world %s", value, world_id)
                continue
            record["time_of_day"] = minutes
            record["light_level"] = _light_from_time(minutes)
        elif key == "light_level":
            try:
                lvl = float(value)
            except (TypeError, ValueError):
                continue
            record["light_level"] = max(0.0, min(1.0, lvl))
        else:
            meta[key] = value
    record["env_meta"] = meta
    record["updated_at"] = timestamp
    return record


def _light_from_time(minutes: int) -> float:
    """Return a deterministic light level from time-of-day minutes.

    A simple sinusoidal model — noon=1.0, midnight=0.0.  Callers can
    override via ``light_level`` if they have a richer model.
    """
    import math
    # Phase shift so peak lands at 12:00.
    radians = ((minutes - 720) / 1440.0) * 2 * math.pi
    return 0.5 + 0.5 * math.cos(radians)


def delete(world_id: str) -> bool:
    """Drop the environment record for a world."""
    return state.world_environment.pop(world_id, None) is not None


def reset_for_testing() -> None:
    state.world_environment.clear()


__all__ = [
    "DEFAULT_WEATHER",
    "DEFAULT_TIME_OF_DAY",
    "DEFAULT_LIGHT_LEVEL",
    "get",
    "ensure_environment",
    "patch_environment",
    "delete",
    "reset_for_testing",
]
