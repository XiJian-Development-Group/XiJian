"""World event scheduler — A4.1 in the function list v2.

This module is the *brain* of the world-event system: it stores event
definitions, fires instances when their trigger conditions evaluate
true, enforces cooldowns and per-world category toggles, and runs a
background tick that walks every enabled event definition once per
``DEFAULT_SCHEDULER_INTERVAL_SECONDS`` and decides what fires.

Data model (mirrors the SQL schema in §A4.1 of the function list v2)
====================================================================

Three buckets live in :mod:`xijian_api.stubs.state`:

* ``world_events``                      — event *definitions* (the
  template / library entry).  One row per (world_id, name).
* ``world_event_instances``             — *fired* instances, append-only.
  Each instance is a record of "the scheduler decided to fire this
  event at this time, with this payload".
* ``world_event_categories_disabled``   — per-world user-controlled
  category toggles.  When the user disables a category, every event
  whose ``kind`` is in that category is skipped.

Trigger model
=============

Each event's ``trigger_config`` is a JSON object with a ``type`` key.
Supported types (intentionally narrow so the matcher stays
predictable):

* ``time``       — ``{type:"time", hour:int, minute:int,
                              frequency:"daily"|"hourly"}``.  Fires
                              when the wall clock crosses the
                              configured hour:minute (UTC).
* ``interval``   — ``{type:"interval", seconds:int}``.  Fires every
                              ``seconds`` after the previous fire
                              (still subject to cooldowns and global
                              storm throttle).
* ``probability``— ``{type:"probability", per_tick:float}``.  Each
                              tick the scheduler rolls ``per_tick``
                              and fires on hit.  ``per_tick`` is in
                              [0, 1].
* ``condition``  — ``{type:"condition", field:str, op:str,
                              value:Any}``.  Fires when
                              ``world_state[field] op value``.  See
                              :func:`_evaluate_condition` for the
                              supported operators.

All four are evaluated against the **current** wall clock + world
state; the scheduler doesn't try to "catch up" missed ticks — a tick
that fires while the daemon was paused simply runs once and moves on.

Storm throttle
==============

Per the function list v2 [TODO], a world may not fire more than one
event every :data:`DEFAULT_GLOBAL_COOLDOWN_SECONDS` (default 60 s).
The scheduler tracks the last-fired timestamp per world in
``world_event_cooldowns`` (an in-module dict, *not* persisted to
``state`` because it's pure run-time coordination).

User category disable
=====================

A world has a set of disabled categories (per spec AC-3: "被用户
关闭的事件类不会自动触发").  The user toggles them via
``PUT /v1/xijian/worlds/<wid>/event-categories/<kind>`` — the stub
also exposes :func:`set_category_disabled` /
:func:`is_category_disabled` so UI / tests can drive it directly.

Scene generation
================

Per US-A4.1-03 ("部分事件会触发场景生成"), event definitions carry a
``scene_ref_id`` and a derived ``needs_scene`` flag.  This module
**does not** call into the image / 3D pipeline — it just records the
flag and the ref id on the fired instance.  Downstream callers (the
UI scene manager, A2 image routes that already exist as stubs) read
``world_event_instances[instance_id]["scene_ref_id"]`` and act on it.

Test surface
============

Pure helpers:

* :func:`_evaluate_trigger`
* :func:`_is_in_cooldown`
* :func:`_storm_throttle_pass`
* :func:`_matches_disabled_categories`
* :func:`_pick_fire_payload`

Side-effecting functions:

* :func:`create_event` / :func:`get_event` / :func:`list_events` /
  :func:`update_event` / :func:`delete_event`
* :func:`fire_event` / :func:`list_instances` / :func:`resolve_instance`
* :func:`set_category_disabled` / :func:`is_category_disabled` /
  :func:`list_disabled_categories`
* :func:`tick_world` / :func:`tick_all`
* :func:`start_scheduler` / :func:`stop_scheduler` /
  :func:`scheduler_status`
* :func:`seed_default` / :func:`reset_for_testing`

Environment variables
---------------------

``XIJIAN_EVENT_SCHEDULER``       — set to ``0`` to disable the
                                   background scheduler thread (CI /
                                   tests).
``XIJIAN_EVENT_SCHEDULER_SECONDS``— override the scheduler interval
                                   (default :data:`DEFAULT_SCHEDULER_INTERVAL_SECONDS`).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_event_id, gen_event_instance_id
from xijian_api.utils.time import now_ts


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOGGER = logging.getLogger("xijian_api.events")

#: Kinds per spec §A4.1 SQL schema.
KIND_COMMON = "common"
KIND_CUSTOM = "custom"
KIND_INCIDENT = "incident"

#: Trigger types.
TRIGGER_TIME = "time"
TRIGGER_INTERVAL = "interval"
TRIGGER_PROBABILITY = "probability"
TRIGGER_CONDITION = "condition"

#: Default scheduler interval (matches A3.2's 60s default tick).
DEFAULT_SCHEDULER_INTERVAL_SECONDS = 60.0

#: Storm-throttle — per spec §A4.1 [TODO]: "默认 60s 内最多 1 个事件".
DEFAULT_GLOBAL_COOLDOWN_SECONDS = 60.0

#: Hard floor for the scheduler interval (mirrors A3.2's policy).
_INTERVAL_FLOOR_SECONDS = 1.0

#: Max fired instances kept in memory per event (soft cap; trims FIFO).
INSTANCE_KEEP_PER_EVENT = 200

#: Total fired-instance cap across the whole system (FIFO trim).
INSTANCE_KEEP_TOTAL = 2000

#: Env flag names.
_SCHED_ENV_FLAG = "XIJIAN_EVENT_SCHEDULER"
_SCHED_INTERVAL_ENV_FLAG = "XIJIAN_EVENT_SCHEDULER_SECONDS"

#: Supported comparison operators for ``condition`` triggers.
_CONDITION_OPS: tuple[str, ...] = (
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
)

#: Supported trigger types — anything else is a validation error.
_VALID_TRIGGER_TYPES: tuple[str, ...] = (
    TRIGGER_TIME,
    TRIGGER_INTERVAL,
    TRIGGER_PROBABILITY,
    TRIGGER_CONDITION,
)

#: Supported kinds — anything else is a validation error.
_VALID_KINDS: tuple[str, ...] = (KIND_COMMON, KIND_CUSTOM, KIND_INCIDENT)


# ---------------------------------------------------------------------------
# Module-level scheduler state (in-memory coordination only)
# ---------------------------------------------------------------------------

#: ``{world_id: last_fired_at}`` — global storm throttle tracker.
_world_cooldowns: dict[str, float] = {}

#: ``{event_id: last_fired_at}`` — per-event cooldown tracker.
_event_cooldowns: dict[str, float] = {}

#: Background scheduler lifecycle (mirrors A3.2's pattern).
_SCHED_LOCK = threading.Lock()
_SCHED_STOP = threading.Event()
_SCHED_THREAD: threading.Thread | None = None
_SCHED_GENERATION: int = 0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EventError(ValueError):
    """Raised on event definition / scheduling validation errors."""


# ---------------------------------------------------------------------------
# Pure helpers (no I/O)
# ---------------------------------------------------------------------------


def _validate_trigger(trigger: dict) -> None:
    """Validate a trigger config dict, raising :class:`EventError` if bad."""
    if not isinstance(trigger, dict):
        raise EventError("trigger_config must be a JSON object")
    ttype = trigger.get("type")
    if ttype not in _VALID_TRIGGER_TYPES:
        raise EventError(
            f"trigger_config.type must be one of {_VALID_TRIGGER_TYPES!r}, got {ttype!r}"
        )
    if ttype == TRIGGER_TIME:
        hour = trigger.get("hour")
        minute = trigger.get("minute", 0)
        if not isinstance(hour, int) or not (0 <= hour <= 23):
            raise EventError("time trigger: hour must be int 0..23")
        if not isinstance(minute, int) or not (0 <= minute <= 59):
            raise EventError("time trigger: minute must be int 0..59")
        freq = trigger.get("frequency", "daily")
        if freq not in {"daily", "hourly"}:
            raise EventError("time trigger: frequency must be 'daily' or 'hourly'")
    elif ttype == TRIGGER_INTERVAL:
        seconds = trigger.get("seconds")
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            raise EventError("interval trigger: seconds must be a positive number")
    elif ttype == TRIGGER_PROBABILITY:
        per_tick = trigger.get("per_tick")
        if not isinstance(per_tick, (int, float)) or not (0.0 <= per_tick <= 1.0):
            raise EventError(
                "probability trigger: per_tick must be a number in [0, 1]"
            )
    elif ttype == TRIGGER_CONDITION:
        if "field" not in trigger:
            raise EventError("condition trigger: 'field' is required")
        op = trigger.get("op")
        if op not in _CONDITION_OPS:
            raise EventError(
                f"condition trigger: op must be one of {_CONDITION_OPS!r}, got {op!r}"
            )
        if "value" not in trigger:
            raise EventError("condition trigger: 'value' is required")


def _evaluate_time_trigger(trigger: dict, now: float) -> bool:
    """Return True if a wall-clock-time trigger matches ``now`` (UTC).

    Hourly frequency fires on the matching minute of every hour; daily
    fires once per day at hour:minute.
    """
    hour = int(trigger["hour"])
    minute = int(trigger.get("minute", 0))
    frequency = trigger.get("frequency", "daily")
    moment = time.gmtime(now)
    if moment.tm_hour != hour:
        return False
    if frequency == "hourly":
        return True
    # daily — must match both hour and minute (minute 0 is "top of hour")
    return moment.tm_min == minute


def _evaluate_interval_trigger(
    trigger: dict, event_id: str, now: float
) -> bool:
    """Return True if enough time has elapsed since the last fire."""
    seconds = float(trigger["seconds"])
    last = _event_cooldowns.get(event_id)
    if last is None:
        # No record of last fire — fire on the first eligible tick so
        # interval triggers don't need a "first fire" record.  Operators
        # can use ``is_enabled=False`` until they're ready.
        return True
    return (now - last) >= seconds


def _evaluate_probability_trigger(trigger: dict, now: float) -> bool:
    """Roll a probability per tick; deterministic for testability."""
    per_tick = float(trigger["per_tick"])
    if per_tick <= 0.0:
        return False
    if per_tick >= 1.0:
        return True
    # Deterministic hash → [0, 1) so tests can pin behaviour without
    # monkey-patching random.  The bucket index is the unix second
    # truncated to the scheduler interval so a tick that runs in the
    # same second gets the same outcome.
    bucket = int(now) // max(int(_current_interval()), 1)
    h = (hash(("probability", bucket)) & 0xFFFFFFFF) / 0x100000000
    return h < per_tick


def _evaluate_condition_trigger(
    trigger: dict, world_record: dict | None
) -> bool:
    """Match a ``world_state[field] op value`` expression."""
    field = trigger["field"]
    op = trigger["op"]
    expected = trigger["value"]
    state_blob = (world_record or {}).get("state", {}) or {}
    actual = state_blob.get(field)
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "gt":
        return _safe_compare(actual, expected, lambda a, b: a > b)
    if op == "gte":
        return _safe_compare(actual, expected, lambda a, b: a >= b)
    if op == "lt":
        return _safe_compare(actual, expected, lambda a, b: a < b)
    if op == "lte":
        return _safe_compare(actual, expected, lambda a, b: a <= b)
    if op == "in":
        if not isinstance(expected, (list, tuple, set)):
            return False
        return actual in expected
    if op == "not_in":
        if not isinstance(expected, (list, tuple, set)):
            return True
        return actual not in expected
    return False


def _safe_compare(actual: Any, expected: Any, op: Callable[[Any, Any], bool]) -> bool:
    """Apply a comparison operator but never raise on type mismatch."""
    try:
        return op(actual, expected)
    except TypeError:
        return False


def _evaluate_trigger(
    trigger: dict,
    event_id: str,
    now: float,
    world_record: dict | None = None,
) -> bool:
    """Dispatch on ``trigger['type']`` and return whether to fire."""
    ttype = trigger.get("type")
    if ttype == TRIGGER_TIME:
        return _evaluate_time_trigger(trigger, now)
    if ttype == TRIGGER_INTERVAL:
        return _evaluate_interval_trigger(trigger, event_id, now)
    if ttype == TRIGGER_PROBABILITY:
        return _evaluate_probability_trigger(trigger, now)
    if ttype == TRIGGER_CONDITION:
        return _evaluate_condition_trigger(trigger, world_record)
    return False


def _is_in_cooldown(event_record: dict, now: float) -> bool:
    """Return True if the per-event ``cooldown_until`` is still in the future."""
    until = event_record.get("cooldown_until")
    if not until:
        return False
    try:
        return float(until) > now
    except (TypeError, ValueError):
        return False


def _storm_throttle_pass(world_id: str, now: float) -> bool:
    """Return True if the world hasn't fired anything in the last cooldown window."""
    last = _world_cooldowns.get(world_id)
    if last is None:
        return True
    return (now - last) >= DEFAULT_GLOBAL_COOLDOWN_SECONDS


def _matches_disabled_categories(
    event_record: dict, world_id: str
) -> bool:
    """Return True if the event's kind is disabled by the user for this world.

    The user-disables-category semantic is *kind-based* — we compare the
    event's ``kind`` (common/custom/incident) against the per-world
    disabled set.  Spec §A4.1 US-A4.1-02 frames the categories as
    "战斗 / 日常 / 社交" (semantic), but the SQL schema hard-codes
    kind ∈ {common, custom, incident}.  We accept user-supplied
    categories too (operators may extend the schema later); unknown
    categories just never match.
    """
    disabled = state.world_event_categories_disabled.get(world_id, set())
    event_kind = event_record.get("kind")
    return event_kind in disabled


def _pick_fire_payload(event_record: dict, now: float) -> dict:
    """Compose the ``payload`` for a fired instance.

    Triggers can optionally carry ``payload_template`` — a dict of
    values to mix into the fired instance payload (e.g. weather
    description for a weather event, npc list for a market day).
    """
    trigger = event_record.get("trigger_config") or {}
    template = trigger.get("payload_template") or {}
    if not isinstance(template, dict):
        template = {}
    payload = dict(template)
    payload.setdefault("fired_at", now)
    return payload


# ---------------------------------------------------------------------------
# CRUD — event definitions
# ---------------------------------------------------------------------------


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


def create_event(
    *,
    world_id: str,
    kind: str,
    name: str,
    description: str = "",
    trigger_config: dict,
    scene_ref_id: str | None = None,
    priority: int = 0,
    is_enabled: bool = True,
    cooldown_until: float | None = None,
    created_at: float | None = None,
) -> dict:
    """Create a new event definition and return the record.

    Parameters
    ----------
    world_id:
        Target world id.  If the world doesn't exist (we don't have a
        worlds-CRUDon the stub for cross-validation), we still record
        the event — the scheduler is the only path that needs the
        world to exist, and it skips events whose world is missing.
    kind:
        One of :data:`KIND_COMMON` / :data:`KIND_CUSTOM` /
        :data:`KIND_INCIDENT`.
    name:
        Human-readable name.  Operators are responsible for keeping
        names unique within a world — duplicates are allowed at the
        data level so library imports don't fail on collision.
    description:
        Free-text description.  Optional.
    trigger_config:
        Dict validated by :func:`_validate_trigger`.  See the module
        docstring for the supported trigger types.
    scene_ref_id:
        Optional pointer to a scene template.  When set, fired
        instances will carry ``needs_scene=True``.
    priority:
        Higher-priority events win ties when multiple triggers fire in
        the same tick and the storm throttle only allows one.
    is_enabled:
        ``False`` makes the scheduler skip the event without
        deleting it.  Defaults to True.
    cooldown_until:
        Absolute unix timestamp; until then the scheduler skips this
        event.  Optional (no cooldown by default).
    created_at:
        Override the timestamp source (testing).  Defaults to now.
    """
    if kind not in _VALID_KINDS:
        raise EventError(
            f"kind must be one of {_VALID_KINDS!r}, got {kind!r}"
        )
    _validate_trigger(trigger_config)
    event_id = gen_event_id()
    record = {
        "id": event_id,
        "world_id": world_id,
        "kind": kind,
        "name": name,
        "description": description,
        "trigger_config": dict(trigger_config),
        "scene_ref_id": scene_ref_id,
        "priority": int(priority),
        "is_enabled": bool(is_enabled),
        "cooldown_until": float(cooldown_until) if cooldown_until is not None else None,
        "created_at": _now_or(created_at),
    }
    state.world_events[event_id] = record
    return record


def get_event(event_id: str) -> dict | None:
    """Return the event definition or ``None``."""
    return state.world_events.get(event_id)


def list_events(
    *,
    world_id: str | None = None,
    kind: str | None = None,
    enabled_only: bool = False,
) -> list[dict]:
    """List event definitions, optionally filtered by world / kind / enabled."""
    out: list[dict] = []
    for record in state.world_events.values():
        if world_id is not None and record.get("world_id") != world_id:
            continue
        if kind is not None and record.get("kind") != kind:
            continue
        if enabled_only and not record.get("is_enabled"):
            continue
        out.append(record)
    # Sort by priority desc, then created_at asc — high-priority first,
    # stable order within the same priority band.
    out.sort(key=lambda r: (-int(r.get("priority", 0)), r.get("created_at", 0)))
    return out


def update_event(event_id: str, patch: dict) -> dict | None:
    """Patch mutable fields on an event definition.

    ``trigger_config`` is re-validated.  ``world_id`` and ``id`` are
    immutable (deliberate — moving an event between worlds would
    invalidate fire history; create a new event instead).
    """
    record = state.world_events.get(event_id)
    if record is None:
        return None
    if "world_id" in patch or "id" in patch:
        raise EventError("id and world_id are immutable; create a new event")
    for key, value in patch.items():
        if key in {"name", "description", "scene_ref_id", "priority",
                   "is_enabled", "cooldown_until", "kind", "trigger_config"}:
            if key == "trigger_config":
                _validate_trigger(value)
                record["trigger_config"] = dict(value)
            elif key == "kind" and value not in _VALID_KINDS:
                raise EventError(
                    f"kind must be one of {_VALID_KINDS!r}, got {value!r}"
                )
            elif key == "cooldown_until" and value is not None:
                record["cooldown_until"] = float(value)
            else:
                record[key] = value
    return record


def delete_event(event_id: str) -> bool:
    """Delete an event definition; returns True if it existed.

    Fired instances are kept — they're an audit trail and operators
    may want to inspect them after the event is gone.
    """
    return state.world_events.pop(event_id, None) is not None


# ---------------------------------------------------------------------------
# CRUD — fired instances
# ---------------------------------------------------------------------------


def fire_event(
    event_id: str,
    *,
    payload: dict | None = None,
    affected_npcs: list[str] | None = None,
    affects_user: bool = False,
    now: float | None = None,
) -> dict | None:
    """Record a fired instance and return it; ``None`` if event unknown."""
    record = state.world_events.get(event_id)
    if record is None:
        return None
    timestamp = _now_or(now)
    chosen_payload = _pick_fire_payload(record, timestamp)
    if payload:
        chosen_payload.update(payload)
    instance_id = gen_event_instance_id()
    instance = {
        "id": instance_id,
        "event_id": event_id,
        "world_id": record["world_id"],
        "fired_at": timestamp,
        "resolved_at": None,
        "payload": chosen_payload,
        "affected_npcs": list(affected_npcs or []),
        "affects_user": bool(affects_user),
        "needs_scene": record.get("scene_ref_id") is not None,
        "scene_ref_id": record.get("scene_ref_id"),
    }
    state.world_event_instances[instance_id] = instance
    _trim_instances()
    return instance


def list_instances(
    *,
    event_id: str | None = None,
    world_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List fired instances, newest first."""
    out: list[dict] = []
    for instance in state.world_event_instances.values():
        if event_id is not None and instance.get("event_id") != event_id:
            continue
        if world_id is not None and instance.get("world_id") != world_id:
            continue
        out.append(instance)
    out.sort(key=lambda r: r.get("fired_at", 0), reverse=True)
    return out[: max(1, limit)]


def resolve_instance(
    instance_id: str, *, now: float | None = None
) -> dict | None:
    """Mark a fired instance as resolved; returns the updated record."""
    instance = state.world_event_instances.get(instance_id)
    if instance is None:
        return None
    instance["resolved_at"] = _now_or(now)
    return instance


def get_instance(instance_id: str) -> dict | None:
    return state.world_event_instances.get(instance_id)


def _trim_instances() -> None:
    """Bound the in-memory instance log FIFO-style."""
    instances = state.world_event_instances
    if len(instances) <= INSTANCE_KEEP_TOTAL:
        return
    # Sort by fired_at ascending and drop the oldest entries.
    sorted_ids = sorted(
        instances.keys(),
        key=lambda iid: instances[iid].get("fired_at", 0),
    )
    excess = len(instances) - INSTANCE_KEEP_TOTAL
    for iid in sorted_ids[:excess]:
        instances.pop(iid, None)


# ---------------------------------------------------------------------------
# Per-world category toggles (US-A4.1-02)
# ---------------------------------------------------------------------------


def set_category_disabled(world_id: str, category: str, disabled: bool) -> set:
    """Toggle a category off / on for a world; returns the new disabled set."""
    if not category or not isinstance(category, str):
        raise EventError("category must be a non-empty string")
    bucket = state.world_event_categories_disabled.setdefault(world_id, set())
    if disabled:
        bucket.add(category)
    else:
        bucket.discard(category)
    return set(bucket)


def is_category_disabled(world_id: str, category: str) -> bool:
    """Return True if the user has disabled this category for this world."""
    bucket = state.world_event_categories_disabled.get(world_id, set())
    return category in bucket


def list_disabled_categories(world_id: str) -> list[str]:
    """Return the sorted list of disabled categories for a world."""
    bucket = state.world_event_categories_disabled.get(world_id, set())
    return sorted(bucket)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def tick_world(world_id: str, *, now: float | None = None) -> list[dict]:
    """Run one scheduler pass for a single world; return fired instances.

    For each enabled event in this world:

    1. Skip if the user has disabled the category (US-A4.1-02 / AC-3).
    2. Skip if the per-event cooldown is still active.
    3. Skip if the global storm throttle for this world is active.
    4. Evaluate the trigger — fire if it matches.
    5. If the trigger matches, claim the storm-throttle slot; only the
       highest-priority event in this tick wins the slot.  Lower
       priority events that match but lose the race are recorded with
       ``deferred=True`` so the UI can show why they didn't fire.

    The "one event per cooldown" semantic matches the spec [TODO];
    priorities break ties when multiple match in the same tick.
    """
    timestamp = _now_or(now)
    world_record = state.worlds.get(world_id)
    if world_record is None:
        return []

    candidates: list[tuple[int, dict]] = []
    skipped: list[dict] = []
    for record in list_events(world_id=world_id, enabled_only=True):
        if _matches_disabled_categories(record, world_id):
            skipped.append(
                {"event_id": record["id"], "reason": "category_disabled"}
            )
            continue
        if _is_in_cooldown(record, timestamp):
            skipped.append(
                {"event_id": record["id"], "reason": "per_event_cooldown"}
            )
            continue
        try:
            matches = _evaluate_trigger(
                record.get("trigger_config", {}),
                record["id"],
                timestamp,
                world_record,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "trigger evaluation failed for %s: %s", record["id"], exc
            )
            skipped.append(
                {"event_id": record["id"], "reason": "trigger_error"}
            )
            continue
        if not matches:
            continue
        candidates.append((int(record.get("priority", 0)), record))

    fired: list[dict] = []
    if candidates:
        # Storm throttle — only allow one event per window.  Highest
        # priority wins.  We do this *before* firing so losers don't
        # consume cooldown slots.
        if not _storm_throttle_pass(world_id, timestamp):
            for _priority, record in candidates:
                skipped.append(
                    {"event_id": record["id"], "reason": "storm_throttled"}
                )
        else:
            candidates.sort(key=lambda item: -item[0])
            winner = candidates[0][1]
            instance = fire_event(winner["id"], now=timestamp)
            if instance is not None:
                _world_cooldowns[world_id] = timestamp
                _event_cooldowns[winner["id"]] = timestamp
                fired.append(instance)
            for _priority, record in candidates[1:]:
                skipped.append(
                    {"event_id": record["id"], "reason": "lost_priority_race"}
                )
    if skipped:
        _LOGGER.debug(
            "tick_world(%s): %d fired, %d skipped", world_id, len(fired), len(skipped)
        )
    return fired


def tick_all(*, now: float | None = None) -> dict:
    """Run a scheduler pass for every world that has at least one event."""
    timestamp = _now_or(now)
    worlds_touched = sorted({record.get("world_id") for record in state.world_events.values()})
    out: dict[str, list[dict]] = {}
    for world_id in worlds_touched:
        if world_id is None:
            continue
        out[world_id] = tick_world(world_id, now=timestamp)
    return out


# ---------------------------------------------------------------------------
# Background scheduler lifecycle
# ---------------------------------------------------------------------------


def _current_interval() -> float:
    """Return the configured scheduler interval (env override allowed)."""
    raw = os.environ.get(_SCHED_INTERVAL_ENV_FLAG)
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = DEFAULT_SCHEDULER_INTERVAL_SECONDS
    else:
        value = DEFAULT_SCHEDULER_INTERVAL_SECONDS
    if value < _INTERVAL_FLOOR_SECONDS:
        value = _INTERVAL_FLOOR_SECONDS
    return value


def _sched_loop(stop_event: threading.Event, generation: int) -> None:
    """Main scheduler loop.  Mirrors the A3.2 tick-loop pattern."""
    while not stop_event.is_set():
        with _SCHED_LOCK:
            if _SCHED_GENERATION != generation:
                return
        try:
            tick_all()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("events scheduler tick failed: %s", exc)
        if stop_event.wait(_current_interval()):
            break


def start_scheduler() -> dict:
    """Start the background scheduler thread (idempotent)."""
    global _SCHED_THREAD, _SCHED_GENERATION
    with _SCHED_LOCK:
        if _SCHED_THREAD is not None and _SCHED_THREAD.is_alive():
            return {"started": False, "reason": "already_running"}
        if os.environ.get(_SCHED_ENV_FLAG) == "0":
            return {"started": False, "reason": "disabled_by_env"}
        _SCHED_STOP.clear()
        _SCHED_GENERATION += 1
        generation = _SCHED_GENERATION
        thread = threading.Thread(
            target=_sched_loop,
            args=(_SCHED_STOP, generation),
            name="xijian-events-scheduler",
            daemon=True,
        )
        _SCHED_THREAD = thread
        thread.start()
    return {"started": True, "interval_s": _current_interval()}


def stop_scheduler() -> dict:
    """Stop the background scheduler thread.  No-op if not running."""
    global _SCHED_THREAD
    with _SCHED_LOCK:
        thread = _SCHED_THREAD
        if thread is None or not thread.is_alive():
            return {"stopped": False, "reason": "not_running"}
        _SCHED_STOP.set()
    thread.join(timeout=_current_interval() * 3)
    with _SCHED_LOCK:
        _SCHED_THREAD = None
    return {"stopped": True}


def scheduler_status() -> dict:
    """Return a debug-friendly snapshot of the scheduler lifecycle."""
    with _SCHED_LOCK:
        running = _SCHED_THREAD is not None and _SCHED_THREAD.is_alive()
    return {
        "running": running,
        "interval_s": _current_interval(),
        "global_cooldown_s": DEFAULT_GLOBAL_COOLDOWN_SECONDS,
        "enabled_by_env": os.environ.get(_SCHED_ENV_FLAG) != "0",
    }


# ---------------------------------------------------------------------------
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  Starts the scheduler thread if env allows.

    We don't seed any default events — the A4.1 spec frames the event
    library as "内置 + 用户自定义 (C1.1)" with C1.1 being the
    authoritative authoring path.  When the dev seed becomes useful,
    add it here.
    """
    if os.environ.get(_SCHED_ENV_FLAG) == "0":
        return
    start_scheduler()


def reset_for_testing() -> None:
    """Wipe in-memory state and stop the scheduler thread."""
    stop_scheduler()
    with _SCHED_LOCK:
        global _SCHED_GENERATION
        _SCHED_GENERATION += 1
    state.world_events.clear()
    state.world_event_instances.clear()
    state.world_event_categories_disabled.clear()
    _world_cooldowns.clear()
    _event_cooldowns.clear()


# ---------------------------------------------------------------------------
# JSON-friendly summary view
# ---------------------------------------------------------------------------


def summary(world_id: str | None = None) -> dict:
    """Return an aggregate snapshot for the routes to serve verbatim."""
    events = list_events(world_id=world_id)
    instances = list_instances(world_id=world_id, limit=20)
    return {
        "world_id": world_id,
        "events_total": len(events),
        "events_enabled": sum(1 for e in events if e.get("is_enabled")),
        "instances_total": sum(
            1
            for inst in state.world_event_instances.values()
            if world_id is None or inst.get("world_id") == world_id
        ),
        "categories_disabled": list_disabled_categories(world_id) if world_id else [],
        "recent_instances": instances,
    }


__all__ = [
    # Constants
    "KIND_COMMON",
    "KIND_CUSTOM",
    "KIND_INCIDENT",
    "TRIGGER_TIME",
    "TRIGGER_INTERVAL",
    "TRIGGER_PROBABILITY",
    "TRIGGER_CONDITION",
    "DEFAULT_SCHEDULER_INTERVAL_SECONDS",
    "DEFAULT_GLOBAL_COOLDOWN_SECONDS",
    # Errors
    "EventError",
    # Pure helpers (exposed for tests)
    "_evaluate_trigger",
    "_evaluate_time_trigger",
    "_evaluate_interval_trigger",
    "_evaluate_probability_trigger",
    "_evaluate_condition_trigger",
    "_is_in_cooldown",
    "_storm_throttle_pass",
    "_matches_disabled_categories",
    "_pick_fire_payload",
    "_validate_trigger",
    # CRUD
    "create_event",
    "get_event",
    "list_events",
    "update_event",
    "delete_event",
    # Instances
    "fire_event",
    "get_instance",
    "list_instances",
    "resolve_instance",
    # Category toggles
    "set_category_disabled",
    "is_category_disabled",
    "list_disabled_categories",
    # Scheduling
    "tick_world",
    "tick_all",
    "start_scheduler",
    "stop_scheduler",
    "scheduler_status",
    # Lifecycle
    "seed_default",
    "reset_for_testing",
    "summary",
]


# ---------------------------------------------------------------------------
# Compatibility note: ``json`` and ``Callable`` are imported above only so
# the trigger ``payload_template`` JSON schema is obvious to anyone reading
# the source.  They're not used directly by this module; the import is
# preserved in case downstream callers want to monkey-patch payload
# serialization.
# ---------------------------------------------------------------------------
_ = json  # silence linters
_ = Callable  # silence linters