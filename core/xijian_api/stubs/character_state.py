"""Character state system — A3.2 in the function list v2.

This module is the *brain* of the per-character state machine.  It
owns three in-memory buckets (mirrored in :mod:`xijian_api.stubs.state`):

* ``character_states``      — current values for hunger / thirst /
  health / mood plus the machine status (Healthy / Hungry / Thirsty /
  Sick / Recovering / Critical) and the timestamp the status entered
  its current state.
* ``character_state_configs`` — per-character decay rates, thresholds,
  transition dwell times, behaviour bindings and the runtime
  modifiers (time / activity / world).
* ``character_state_log``    — append-only audit trail.  Every value
  change is recorded with a reason (``tick`` / ``dialogue`` /
  ``world_event`` / ``manual``) and a ``ref_id`` for traceability.

The state machine follows the diagram in §A3.2 of the function list:

    Healthy <-> Hungry      (hunger <= 30 → Hungry, hunger > 60
                             sustained 5 min → Healthy)
    Healthy <-> Thirsty     (thirst <= 30 / > 60 sustained 5 min)
    Healthy -> Sick         (health <= 30)
    Sick    -> Recovering   (manual recovery event — A1.2 / world)
    Recovering -> Healthy   (health > 70 sustained 10 min)
    Sick    -> Critical     (health <= 0)
    Critical -> [end]       (dialogue disabled; only force_recover
                             from the admin path can lift it)

Decay algorithm (per the function list spec):

    actual_decay = config.rate_per_hour * (dt / 3600)
                   * time_modifier * activity_modifier * world_modifier

The tick thread runs at 1 Hz and applies the decay; the per-character
loop iterates every character with a state record, so the cost is
``O(N)`` per tick.  For a 50-character world this is trivial.

The system is **intentionally headless** when it comes to *side
effects* — it just records state and writes the log.  Cross-system
side effects (chat-pipeline truncation, TTS degradation, memory
compression) flow through the same handler-registry pattern the
overload module uses.  A behaviour binding is just a declarative
JSON object on the config; the UI / animation layer reads
:func:`get_active_behavior` to decide what motion / line to play.

Test surface
------------
Pure helpers that the test suite drives directly:

* :func:`clamp`
* :func:`decay_amount`
* :func:`compute_target_status`
* :func:`resolve_behavior_bindings`

Side-effecting functions:

* :func:`get_or_init_state` / :func:`get_or_init_config`
* :func:`apply_field_change`
* :func:`tick_character` / :func:`tick_all`
* :func:`set_modifier` / :func:`clear_modifier`
* :func:`can_dialogue` / :func:`force_recover`
* :func:`get_active_behavior` / :func:`list_log`
* :func:`start_tick` / :func:`stop_tick`

Environment variables
---------------------
``XIJIAN_STATE_TICK``       — set to ``0`` to disable the background
                              tick thread (CI / tests).
``XIJIAN_STATE_TICK_SECONDS``— override the tick interval (default 60).
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_state_log_id
from xijian_api.utils.time import now_ts


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOGGER = logging.getLogger("xijian_api.character_state")

#: Status machine labels — kept as a tuple for type narrowing in tests.
STATUS_HEALTHY = "healthy"
STATUS_HUNGRY = "hungry"
STATUS_THIRSTY = "thirsty"
STATUS_SICK = "sick"
STATUS_RECOVERING = "recovering"
STATUS_CRITICAL = "critical"

ALL_STATUSES: tuple[str, ...] = (
    STATUS_HEALTHY,
    STATUS_HUNGRY,
    STATUS_THIRSTY,
    STATUS_SICK,
    STATUS_RECOVERING,
    STATUS_CRITICAL,
)

#: Default starting values for a brand-new character state record.
DEFAULT_HUNGER = 80.0
DEFAULT_THIRST = 80.0
DEFAULT_HEALTH = 100.0
DEFAULT_MOOD = 70.0

DEFAULT_MAX_HUNGER = 100.0
DEFAULT_MAX_THIRST = 100.0
DEFAULT_MAX_HEALTH = 100.0
DEFAULT_MAX_MOOD = 100.0

#: Default per-hour decay rates — locked by v2 of the function list.
DEFAULT_DECAY_RATES: dict[str, float] = {
    "hunger": 2.0,
    "thirst": 3.0,
    "health": 0.1,
    "mood": 1.0,
}

#: Default threshold values that trip the named state transitions.
DEFAULT_LOW_THRESHOLDS: dict[str, float] = {
    "hunger": 30.0,   # hunger <= 30 → Hungry
    "thirst": 30.0,   # thirst <= 30 → Thirsty
    "health": 30.0,   # health <= 30 → Sick
    "mood": 20.0,     # mood <= 20 → Sick (via mood)
}
#: Upper recovery thresholds — value must clear this to recover.
DEFAULT_RECOVERY_THRESHOLDS: dict[str, float] = {
    "hunger": 60.0,
    "thirst": 60.0,
    "health": 70.0,
    "mood": 60.0,
}

#: Default dwell times (seconds) before a recovery transition is
#: allowed.  Matches the spec's "hunger > 60 持续 5 min → Healthy".
DEFAULT_TRANSITION_DWELL_SECONDS: dict[str, float] = {
    "hungry_recover": 5.0 * 60.0,
    "thirsty_recover": 5.0 * 60.0,
    "recovering_recover": 10.0 * 60.0,
    "high_mood_low_hunger": 0.0,  # edge case from spec
}

#: Default behaviour bindings — JSON shape that the UI can read.
#: Each key is the binding name; the value is ``{trigger, motion?}``.
DEFAULT_BEHAVIOR_BINDINGS: dict[str, dict[str, str]] = {
    "hungry": {"trigger": "low_energy", "motion": "yawn"},
    "thirsty": {"trigger": "ask_for_water", "motion": "fidget"},
    "sick": {"trigger": "concerned", "motion": "sigh"},
    "recovering": {"trigger": "gentle", "motion": "soft_smile"},
    "critical": {"trigger": "silence"},
    "high_mood_low_hunger": {"trigger": "playful_line", "motion": "smile"},
}

#: Special edge case from the function list spec: "mood ≥ 95 且
#: hunger < 20 → 角色可能触发自定义台词/动作".  Encoded as a
#: pure-function check on the live values.
HIGH_MOOD_LOW_HUNGER_MOOD = 95.0
HIGH_MOOD_LOW_HUNGER_HUNGER = 20.0

#: Background tick interval.  60 s per the spec ("每 N 秒 tick 一次
#: [TODO: N 默认 60s]").  Tests override via env.
DEFAULT_TICK_INTERVAL_SECONDS = 60.0
_TICK_ENV_FLAG = "XIJIAN_STATE_TICK"
_TICK_INTERVAL_ENV_FLAG = "XIJIAN_STATE_TICK_SECONDS"

#: Append-only log cap.  A long-running session can rack up thousands
#: of tick-driven log entries; we keep the most recent N to bound
#: memory without losing the diagnostic window.
LOG_MAX_ENTRIES = 2000

#: Field metadata — the canonical list of value fields and the
#: corresponding max-field for clamping.  Order matters for the
#: tick loop (we decay in this order).
VALUE_FIELDS: tuple[str, ...] = ("hunger", "thirst", "health", "mood")
MAX_FIELDS: dict[str, str] = {
    "hunger": "max_hunger",
    "thirst": "max_thirst",
    "health": "max_health",
    "mood": "max_mood",
}


# ---------------------------------------------------------------------------
# Module-level state — monitor lifecycle
# ---------------------------------------------------------------------------

#: Lock guarding ``_TICK_THREAD`` + ``_TICK_STOP`` so concurrent
#: :func:`start_tick` / :func:`stop_tick` calls from the test suite
#: and the app factory don't race.
_TICK_LOCK = threading.Lock()
_TICK_THREAD: threading.Thread | None = None
_TICK_STOP = threading.Event()

#: Generation counter so the test suite can detect that the running
#: tick thread belongs to the previous reset.
_TICK_GENERATION: int = 0


# ---------------------------------------------------------------------------
# Pure helpers — easy to unit test
# ---------------------------------------------------------------------------


def clamp(value: float, max_value: float) -> float:
    """Clamp ``value`` into ``[0, max_value]`` (AC-1)."""
    if value < 0:
        return 0.0
    if value > max_value:
        return max_value
    return float(value)


def decay_amount(
    rate_per_hour: float,
    dt_seconds: float,
    *,
    time_modifier: float = 1.0,
    activity_modifier: float = 1.0,
    world_modifier: float = 1.0,
) -> float:
    """Compute the absolute decay amount for a single field.

    Formula (per spec):

        amount = rate_per_hour * (dt / 3600)
                 * time_modifier * activity_modifier * world_modifier

    Returns ``0.0`` (not a negative number) when ``dt_seconds`` is
    non-positive.  This keeps the tick loop safe against clock
    jitter — a backwards jump doesn't accidentally refill a stat.
    """
    if dt_seconds <= 0:
        return 0.0
    return (
        float(rate_per_hour)
        * (float(dt_seconds) / 3600.0)
        * float(time_modifier)
        * float(activity_modifier)
        * float(world_modifier)
    )


def _field_status(field: str, value: float, config: dict) -> str | None:
    """Return the status name triggered by ``field`` going low, or ``None``.

    Used by :func:`compute_target_status` to decide whether a field
    crossing the low threshold warrants entering Hungry / Thirsty /
    Sick.  Threshold values come from ``config['thresholds']``.
    """
    low = (config.get("thresholds") or {}).get(field)
    if low is None:
        return None
    if value <= float(low):
        if field == "hunger":
            return STATUS_HUNGRY
        if field == "thirst":
            return STATUS_THIRSTY
        if field == "health":
            return STATUS_SICK
        if field == "mood":
            return STATUS_SICK
    return None


def _recovery_field(status: str) -> str | None:
    """Map a status to the field whose *high* value recovers it."""
    if status == STATUS_HUNGRY:
        return "hunger"
    if status == STATUS_THIRSTY:
        return "thirst"
    if status == STATUS_RECOVERING:
        return "health"
    return None


def _recovery_dwell_key(status: str) -> str | None:
    if status == STATUS_HUNGRY:
        return "hungry_recover"
    if status == STATUS_THIRSTY:
        return "thirsty_recover"
    if status == STATUS_RECOVERING:
        return "recovering_recover"
    return None


def compute_target_status(state_record: dict, config: dict, now: float) -> str:
    """Decide the desired status for ``state_record`` at ``now``.

    Pure function — does not mutate state.  Returns the name of the
    status the state record *should* be in given its current values,
    the status it was in, and the elapsed dwell time.

    Rules
    -----
    * ``health <= 0`` → ``STATUS_CRITICAL`` (highest priority — always
      wins, even if other rules would also fire).
    * ``health <= health_low`` → ``STATUS_SICK``.  If the record is
      already in ``STATUS_RECOVERING`` and the value is climbing, we
      keep it there; only an explicit health drop below 30 re-enters
      Sick.
    * ``hunger <= hunger_low`` → ``STATUS_HUNGRY`` (only when
      currently Healthy / Hungry — never from Sick/Recovering/Critical).
    * ``thirst <= thirst_low`` → ``STATUS_THIRSTY`` (same).
    * Recovery dwell checks:
      - ``Hungry → Healthy`` when ``hunger > 60`` sustained 5 min.
      - ``Thirsty → Healthy`` when ``thirst > 60`` sustained 5 min.
      - ``Recovering → Healthy`` when ``health > 70`` sustained 10 min.
      - ``Sick → Recovering`` only via the manual
        :func:`enter_recovering` hook (e.g. world event / A1.2
        recovery event).  Auto-recovery requires admin/force_recover
        to bypass, because per the spec a Sick character has to
        "触发恢复事件" before the state machine will move them on.
    """
    current = state_record.get("status", STATUS_HEALTHY)
    health = float(state_record.get("health", 0.0))
    hunger = float(state_record.get("hunger", 0.0))
    thirst = float(state_record.get("thirst", 0.0))
    status_changed_at = float(state_record.get("status_changed_at") or now)
    dwell = max(0.0, now - status_changed_at)
    thresholds = config.get("thresholds") or DEFAULT_LOW_THRESHOLDS
    rec_thresholds = config.get("recovery_thresholds") or DEFAULT_RECOVERY_THRESHOLDS
    dwell_seconds = config.get("transition_dwell_seconds") or DEFAULT_TRANSITION_DWELL_SECONDS

    # Critical has absolute priority.
    if health <= 0.0:
        return STATUS_CRITICAL

    # Recovery: from Hungry / Thirsty / Recovering → Healthy when
    # the value has been above the recovery threshold for the dwell.
    if current in (STATUS_HUNGRY, STATUS_THIRSTY, STATUS_RECOVERING):
        field = _recovery_field(current)
        if field is not None:
            rec_threshold = float(rec_thresholds.get(field, 0.0))
            dwell_key = _recovery_dwell_key(current)
            dwell_threshold = float(dwell_seconds.get(dwell_key, 0.0))
            value = float(state_record.get(field, 0.0))
            if value > rec_threshold and dwell >= dwell_threshold:
                return STATUS_HEALTHY

    # Hungry / Thirsty — independent; both can be active but we only
    # surface the more severe one in ``status`` (the other is
    # reflected in the field values themselves).
    hungry_target = _field_status("hunger", hunger, {"thresholds": thresholds})
    thirsty_target = _field_status("thirst", thirst, {"thresholds": thresholds})
    sick_target = _field_status("health", health, {"thresholds": thresholds})

    # If currently Healthy, fall into whichever threshold trips first.
    if current == STATUS_HEALTHY:
        # health <= 30 → Sick takes priority over Hungry / Thirsty.
        if sick_target == STATUS_SICK:
            return STATUS_SICK
        if hungry_target == STATUS_HUNGRY:
            return STATUS_HUNGRY
        if thirsty_target == STATUS_THIRSTY:
            return STATUS_THIRSTY
        return STATUS_HEALTHY

    # If currently Hungry and hunger has recovered, leave Hungry.
    if current == STATUS_HUNGRY and hungry_target is None:
        # hunger > 60: rely on the recovery-dwell branch above; if it
        # hasn't elapsed yet we keep the current status.
        if hunger > float(rec_thresholds.get("hunger", 0.0)):
            return STATUS_HUNGRY  # still waiting for dwell
        return STATUS_HEALTHY

    # If currently Thirsty and thirst has recovered, leave Thirsty.
    if current == STATUS_THIRSTY and thirsty_target is None:
        if thirst > float(rec_thresholds.get("thirst", 0.0)):
            return STATUS_THIRSTY
        return STATUS_HEALTHY

    # Recovering → only Healthy via the dwell branch.
    if current == STATUS_RECOVERING:
        return STATUS_RECOVERING

    # Sick: stay Sick until manually moved to Recovering.  Health
    # climbing back above 30 alone does not auto-recover (spec says
    # "触发恢复事件" — explicit).
    if current == STATUS_SICK:
        return STATUS_SICK

    # Critical: can only be lifted by :func:`force_recover`.  We keep
    # the status unchanged here.
    if current == STATUS_CRITICAL:
        return STATUS_CRITICAL

    # Default: keep the current status.
    return current


def resolve_behavior_bindings(
    state_record: dict, config: dict
) -> list[dict[str, str]]:
    """Return the behaviour bindings currently active for the character.

    Combines:

    * The state-machine status (Hungry / Thirsty / Sick / Recovering /
      Critical).
    * The spec's edge case: ``mood >= 95 and hunger < 20`` →
      ``high_mood_low_hunger``.

    Returns a list of binding objects, each ``{name, trigger, motion}``
    for the UI to consume.  The list is intentionally ordered with
    the spec's edge case first so the most expressive animation
    wins ties.
    """
    bindings_cfg = config.get("behavior_bindings") or DEFAULT_BEHAVIOR_BINDINGS
    active: list[dict[str, str]] = []

    mood = float(state_record.get("mood", 0.0))
    hunger = float(state_record.get("hunger", 0.0))
    if mood >= HIGH_MOOD_LOW_HUNGER_MOOD and hunger < HIGH_MOOD_LOW_HUNGER_HUNGER:
        binding = bindings_cfg.get("high_mood_low_hunger") or {}
        if binding:
            active.append(
                {
                    "name": "high_mood_low_hunger",
                    "trigger": binding.get("trigger", "playful_line"),
                    "motion": binding.get("motion", "smile"),
                }
            )

    status = state_record.get("status", STATUS_HEALTHY)
    status_binding = bindings_cfg.get(status)
    if status_binding:
        active.append(
            {
                "name": status,
                "trigger": status_binding.get("trigger", status),
                "motion": status_binding.get("motion", ""),
            }
        )
    return active


# ---------------------------------------------------------------------------
# State record helpers
# ---------------------------------------------------------------------------


def _default_state_record(character_id: str, *, now: float | None = None) -> dict:
    """Build a brand-new state record with all defaults applied."""
    moment = float(now) if now is not None else float(time.time())
    return {
        "character_id": character_id,
        "hunger": DEFAULT_HUNGER,
        "thirst": DEFAULT_THIRST,
        "health": DEFAULT_HEALTH,
        "mood": DEFAULT_MOOD,
        "max_hunger": DEFAULT_MAX_HUNGER,
        "max_thirst": DEFAULT_MAX_THIRST,
        "max_health": DEFAULT_MAX_HEALTH,
        "max_mood": DEFAULT_MAX_MOOD,
        "status": STATUS_HEALTHY,
        "status_changed_at": moment,
        "last_updated": moment,
        # Note: time / activity / world modifiers live on the config
        # record (``cfg['modifiers']``), not here.  ``tick_character``
        # reads them from the config so a single modifier change
        # affects every subsequent tick without touching the state.
    }


def _default_config(character_id: str) -> dict:
    return {
        "character_id": character_id,
        "decay_per_hour": dict(DEFAULT_DECAY_RATES),
        "thresholds": dict(DEFAULT_LOW_THRESHOLDS),
        "recovery_thresholds": dict(DEFAULT_RECOVERY_THRESHOLDS),
        "transition_dwell_seconds": dict(DEFAULT_TRANSITION_DWELL_SECONDS),
        "behavior_bindings": {
            name: dict(binding) for name, binding in DEFAULT_BEHAVIOR_BINDINGS.items()
        },
        # Modifier slot for world / activity / time — defaulted to
        # neutral (1.0) so the spec's "world / activity 修饰因子" is
        # present even when nothing has registered one.
        "modifiers": {
            "time_modifier": 1.0,
            "activity_modifier": 1.0,
            "world_modifier": 1.0,
        },
    }


def get_or_init_state(character_id: str) -> dict:
    """Return the character's state record, creating one with defaults."""
    record = state.character_states.get(character_id)
    if record is None:
        record = _default_state_record(character_id)
        state.character_states[character_id] = record
    return record


def get_state(character_id: str) -> dict | None:
    """Read-only access to the state record, or ``None`` if absent."""
    return state.character_states.get(character_id)


def get_or_init_config(character_id: str) -> dict:
    """Return the character's config record, creating one with defaults."""
    cfg = state.character_state_configs.get(character_id)
    if cfg is None:
        cfg = _default_config(character_id)
        state.character_state_configs[character_id] = cfg
    return cfg


def get_config(character_id: str) -> dict | None:
    return state.character_state_configs.get(character_id)


def set_modifier(character_id: str, modifier: dict) -> dict:
    """Set runtime modifiers on a character's config.

    ``modifier`` is a dict with any of:
    ``time_modifier``, ``activity_modifier``, ``world_modifier``.
    Unknown keys are ignored.  Values are clamped to ``(0, 8]`` so
    a bug in caller code can't accidentally make decay go backwards
    or explode.
    """
    cfg = get_or_init_config(character_id)
    mods = cfg.setdefault("modifiers", {})
    for key, value in modifier.items():
        if key not in {"time_modifier", "activity_modifier", "world_modifier"}:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if v <= 0:
            v = 0.01
        elif v > 8.0:
            v = 8.0
        mods[key] = v
    return dict(mods)


def clear_modifier(character_id: str, *keys: str) -> dict:
    """Reset one or more modifiers back to 1.0 (neutral)."""
    cfg = get_or_init_config(character_id)
    mods = cfg.setdefault("modifiers", {})
    for key in keys:
        if key in {"time_modifier", "activity_modifier", "world_modifier"}:
            mods[key] = 1.0
    return dict(mods)


def list_log(character_id: str, *, limit: int = 50) -> list[dict]:
    """Return the most recent log entries for ``character_id``."""
    entries = [
        entry
        for entry in state.character_state_log.values()
        if entry.get("character_id") == character_id
    ]
    entries.sort(key=lambda e: int(e.get("created_at") or 0), reverse=True)
    return entries[: max(1, int(limit))]


def _append_log(
    character_id: str,
    field: str,
    old_value: float,
    new_value: float,
    reason: str,
    ref_id: str | None = None,
    *,
    now: float | None = None,
) -> dict:
    """Append a single log entry and bound the log size.

    ``now`` is the float-second timestamp to use for ``created_at``.
    Tests that freeze the clock pass a controlled value here so log
    ordering is deterministic; production callers omit it and we
    fall back to :func:`now_ts` (wall clock).
    """
    log_id = gen_state_log_id()
    ts = int(now) if now is not None else now_ts()
    entry = {
        "id": log_id,
        "character_id": character_id,
        "field": field,
        "old_value": float(old_value),
        "new_value": float(new_value),
        "reason": reason,
        "ref_id": ref_id,
        "created_at": ts,
    }
    state.character_state_log[log_id] = entry
    # Bound the log: keep the most recent LOG_MAX_ENTRIES.
    if len(state.character_state_log) > LOG_MAX_ENTRIES:
        ordered = sorted(
            state.character_state_log.values(),
            key=lambda e: int(e.get("created_at") or 0),
        )
        to_drop = len(state.character_state_log) - LOG_MAX_ENTRIES
        for entry in ordered[:to_drop]:
            state.character_state_log.pop(entry["id"], None)
    return entry


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


def can_dialogue(character_id: str) -> bool:
    """Return ``False`` only when the character is in ``critical`` status.

    Per the spec edge case "健康 ≤ 0 → 角色不可对话".  All other
    statuses permit dialogue; we leave the gating to the route layer
    so a soft-disabled character can still receive admin commands.
    """
    record = get_state(character_id)
    if record is None:
        return True
    return record.get("status") != STATUS_CRITICAL


def force_recover(character_id: str, *, reason: str = "admin_recover", ref_id: str | None = None) -> dict:
    """Admin path: lift a Critical / Sick character back to Healthy.

    Sets health to ``max_health`` and forces the status to Healthy,
    writing one log entry per affected field so the audit trail
    captures the manual intervention.
    """
    record = get_or_init_state(character_id)
    max_health = float(record.get("max_health", DEFAULT_MAX_HEALTH))
    now = time.time()
    old_health = float(record.get("health", 0.0))
    record["health"] = clamp(max_health, max_health)
    if old_health != record["health"]:
        _append_log(character_id, "health", old_health, record["health"], reason, ref_id)
    if record.get("status") != STATUS_HEALTHY:
        record["status"] = STATUS_HEALTHY
        record["status_changed_at"] = now
    record["last_updated"] = now
    _publish_state_change(character_id, "status", record.get("status"), STATUS_HEALTHY, reason, ref_id)
    return dict(record)


def enter_recovering(character_id: str, *, reason: str = "world_recover", ref_id: str | None = None) -> dict:
    """Manually move a Sick character into Recovering (event-driven)."""
    record = get_or_init_state(character_id)
    if record.get("status") != STATUS_SICK:
        # No-op if not Sick; we don't want to inject a Recovering
        # state on a Healthy character (which would make the spec
        # "Sick → Recovering → Healthy" state machine ambiguous).
        return dict(record)
    now = time.time()
    record["status"] = STATUS_RECOVERING
    record["status_changed_at"] = now
    record["last_updated"] = now
    _publish_state_change(character_id, "status", STATUS_SICK, STATUS_RECOVERING, reason, ref_id)
    return dict(record)


# ---------------------------------------------------------------------------
# Side-effect: WS broadcast + handler registry
# ---------------------------------------------------------------------------


#: Per-status handler registry.  Subsystems (chat-pipeline,
#: animation layer, world event emitter) can register a callable
#: to fire whenever a character enters a particular status.  Mirrors
#: the overload module's handler pattern.
_STATUS_HANDLERS: dict[str, list[Callable[[dict], None]]] = {
    STATUS_HEALTHY: [],
    STATUS_HUNGRY: [],
    STATUS_THIRSTY: [],
    STATUS_SICK: [],
    STATUS_RECOVERING: [],
    STATUS_CRITICAL: [],
}
_STATUS_HANDLER_LOCK = threading.Lock()


def register_status_handler(status: str, handler: Callable[[dict], None]) -> dict:
    """Register ``handler`` to fire when a character enters ``status``."""
    if status not in _STATUS_HANDLERS:
        raise ValueError(f"unknown status: {status!r}")
    with _STATUS_HANDLER_LOCK:
        _STATUS_HANDLERS[status].append(handler)
    return {"status": status, "handlers": len(_STATUS_HANDLERS[status])}


def unregister_status_handler(status: str, handler: Callable[[dict], None]) -> dict:
    if status not in _STATUS_HANDLERS:
        raise ValueError(f"unknown status: {status!r}")
    with _STATUS_HANDLER_LOCK:
        try:
            _STATUS_HANDLERS[status].remove(handler)
            return {"status": status, "removed": True}
        except ValueError:
            return {"status": status, "removed": False}


def _publish_state_change(
    character_id: str,
    field: str,
    old_value: Any,
    new_value: Any,
    reason: str,
    ref_id: str | None = None,
) -> None:
    """Fire WS event + status handlers.  Best-effort, never raises."""
    payload = {
        "character_id": character_id,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "reason": reason,
        "ref_id": ref_id,
    }
    # WS broadcast — same pattern the overload module uses.
    try:
        from xijian_api.routes.ws_routes import publish_event
        publish_event("character.state.changed", payload)
    except Exception:  # noqa: BLE001 — best effort, never raise
        pass
    # Status-specific handlers (only when the field is the status).
    if field == "status" and new_value in _STATUS_HANDLERS:
        with _STATUS_HANDLER_LOCK:
            handlers = list(_STATUS_HANDLERS.get(new_value, []))
        for handler in handlers:
            try:
                handler(payload)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "character_state status handler for %s failed: %s",
                    new_value, exc,
                )


# ---------------------------------------------------------------------------
# Mutations: apply_field_change + tick_character
# ---------------------------------------------------------------------------


def apply_field_change(
    character_id: str,
    field: str,
    value: float,
    *,
    reason: str = "manual",
    ref_id: str | None = None,
    now: float | None = None,
) -> dict:
    """Apply ``value`` to a single state field, clamp, log, broadcast.

    Returns the updated state record.  Raises ``ValueError`` for
    unknown field names.  Negative values are clamped to 0; values
    above the field's ``max_*`` are clamped to the max (AC-1).
    """
    if field not in VALUE_FIELDS:
        raise ValueError(f"unknown value field: {field!r}")
    record = get_or_init_state(character_id)
    cfg = get_or_init_config(character_id)
    moment = float(now) if now is not None else float(time.time())

    old_value = float(record.get(field, 0.0))
    max_field = MAX_FIELDS[field]
    max_value = float(record.get(max_field, 100.0))
    new_value = clamp(float(value), max_value)
    record[field] = new_value
    record["last_updated"] = moment

    # Status transition.  Only field-driven statuses (Hungry /
    # Thirsty / Sick) are considered here; Critical is health-driven
    # and gets its own check below.
    new_status = compute_target_status(record, cfg, moment)
    if record.get("status") != new_status:
        old_status = record.get("status")
        record["status"] = new_status
        record["status_changed_at"] = moment
        # Surface the status change through the broadcast channel.
        _publish_state_change(
            character_id, "status", old_status, new_status, reason, ref_id
        )

    # Always log the field value change — AC-2.
    if old_value != new_value:
        _append_log(character_id, field, old_value, new_value, reason, ref_id, now=moment)
        _publish_state_change(character_id, field, old_value, new_value, reason, ref_id)
    return dict(record)


def apply_patch(
    character_id: str, patch: dict, *, reason: str = "manual", ref_id: str | None = None
) -> dict:
    """Apply multiple field changes in one go.  Unknown keys are ignored."""
    record = get_or_init_state(character_id)
    for field in VALUE_FIELDS:
        if field in patch:
            try:
                value = float(patch[field])
            except (TypeError, ValueError):
                continue
            apply_field_change(
                character_id, field, value, reason=reason, ref_id=ref_id
            )
    # Allow setting the max values too.
    for field, max_field in MAX_FIELDS.items():
        if max_field in patch:
            try:
                new_max = float(patch[max_field])
            except (TypeError, ValueError):
                continue
            if new_max <= 0:
                continue
            record[max_field] = new_max
            # Re-clamp the corresponding value.
            old = float(record.get(field, 0.0))
            new = clamp(old, new_max)
            if new != old:
                record[field] = new
                _append_log(character_id, field, old, new, reason, ref_id)
    return dict(record)


def tick_character(character_id: str, *, now: float | None = None) -> dict:
    """Apply one tick of decay + status-machine update to one character.

    Returns a small dict describing the per-character result so the
    tick loop and the routes can surface what changed.  Reads the
    record's ``last_updated`` to compute the elapsed time and uses
    the config's decay rates + modifiers.
    """
    record = get_or_init_state(character_id)
    cfg = get_or_init_config(character_id)
    moment = float(now) if now is not None else float(time.time())
    last_updated = float(record.get("last_updated") or moment)
    dt = max(0.0, moment - last_updated)

    mods = cfg.get("modifiers") or {}
    time_mod = float(mods.get("time_modifier", 1.0))
    activity_mod = float(mods.get("activity_modifier", 1.0))
    world_mod = float(mods.get("world_modifier", 1.0))

    decay_rates = cfg.get("decay_per_hour") or DEFAULT_DECAY_RATES
    changes: list[dict] = []
    for field in VALUE_FIELDS:
        rate = float(decay_rates.get(field, 0.0))
        amount = decay_amount(
            rate,
            dt,
            time_modifier=time_mod,
            activity_modifier=activity_mod,
            world_modifier=world_mod,
        )
        if amount <= 0:
            continue
        old_value = float(record.get(field, 0.0))
        new_value = clamp(old_value - amount, float(record.get(MAX_FIELDS[field], 100.0)))
        if new_value == old_value:
            continue
        record[field] = new_value
        _append_log(character_id, field, old_value, new_value, "tick", now=moment)
        _publish_state_change(character_id, field, old_value, new_value, "tick", None)
        changes.append({"field": field, "old_value": old_value, "new_value": new_value})

    # Status machine.
    new_status = compute_target_status(record, cfg, moment)
    if record.get("status") != new_status:
        old_status = record.get("status")
        record["status"] = new_status
        record["status_changed_at"] = moment
        _publish_state_change(
            character_id, "status", old_status, new_status, "tick", None
        )
        changes.append({"field": "status", "old_value": old_status, "new_value": new_status})

    record["last_updated"] = moment
    return {
        "character_id": character_id,
        "dt_seconds": dt,
        "changes": changes,
        "status": record.get("status"),
    }


def tick_all(*, now: float | None = None) -> list[dict]:
    """Tick every character with a state record."""
    results: list[dict] = []
    for character_id in list(state.character_states.keys()):
        results.append(tick_character(character_id, now=now))
    return results


def get_active_behavior(character_id: str) -> list[dict[str, str]]:
    """Return the behaviour bindings currently active for the character."""
    record = get_state(character_id)
    if record is None:
        return []
    cfg = get_config(character_id) or _default_config(character_id)
    return resolve_behavior_bindings(record, cfg)


# ---------------------------------------------------------------------------
# Monitor lifecycle
# ---------------------------------------------------------------------------


def _current_interval() -> float:
    """Return the configured tick interval (env override allowed)."""
    raw = os.environ.get(_TICK_INTERVAL_ENV_FLAG)
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = DEFAULT_TICK_INTERVAL_SECONDS
    else:
        value = DEFAULT_TICK_INTERVAL_SECONDS
    # Hard floor of 1 s — fast loop is the test path; 0.5 s tick
    # would be irresponsible for the default 60s schema.
    if value < 1.0:
        value = 1.0
    return value


def _tick_loop(stop_event: threading.Event, generation: int) -> None:
    """Main loop: tick every ``interval`` seconds until stopped.

    ``generation`` is a monotonic counter bumped on every
    :func:`start_tick` / :func:`reset_for_testing`.  When the
    generation drifts the loop exits immediately rather than
    racing against the fresh instance — this is what lets a test
    suite (or a re-entrant ``seed_all``) install a new tick thread
    without leaving the old one spinning until the next interval.
    """
    while not stop_event.is_set():
        with _TICK_LOCK:
            if _TICK_GENERATION != generation:
                return
        try:
            tick_all()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("character_state tick failed: %s", exc)
        if stop_event.wait(_current_interval()):
            break


def start_tick() -> dict:
    """Start the background tick thread (idempotent)."""
    global _TICK_THREAD, _TICK_GENERATION
    with _TICK_LOCK:
        if _TICK_THREAD is not None and _TICK_THREAD.is_alive():
            return {"started": False, "reason": "already_running"}
        if os.environ.get(_TICK_ENV_FLAG) == "0":
            return {"started": False, "reason": "disabled_by_env"}
        _TICK_STOP.clear()
        _TICK_GENERATION += 1
        generation = _TICK_GENERATION
        thread = threading.Thread(
            target=_tick_loop,
            args=(_TICK_STOP, generation),
            name="xijian-character-state-tick",
            daemon=True,
        )
        _TICK_THREAD = thread
        thread.start()
    return {"started": True, "interval_s": _current_interval()}


def stop_tick() -> dict:
    """Stop the background tick thread.  No-op if not running."""
    global _TICK_THREAD
    with _TICK_LOCK:
        thread = _TICK_THREAD
        if thread is None or not thread.is_alive():
            return {"stopped": False, "reason": "not_running"}
        _TICK_STOP.set()
    thread.join(timeout=_current_interval() * 3)
    with _TICK_LOCK:
        _TICK_THREAD = None
    return {"stopped": True}


def tick_status() -> dict:
    """Return a debug-friendly snapshot of the tick lifecycle."""
    with _TICK_LOCK:
        running = _TICK_THREAD is not None and _TICK_THREAD.is_alive()
    return {
        "running": running,
        "interval_s": _current_interval(),
        "enabled_by_env": os.environ.get(_TICK_ENV_FLAG) != "0",
    }


# ---------------------------------------------------------------------------
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  Starts the tick thread if env allows.

    Called by ``stubs.seed_all()`` at app start-up.  We don't seed
    per-character records here — the A3.2 state record is created
    lazily on first access (in :func:`get_or_init_state`) so we don't
    pollute the in-memory store with records for characters that
    never get queried.
    """
    if os.environ.get(_TICK_ENV_FLAG) == "0":
        return
    start_tick()


def reset_for_testing() -> None:
    """Wipe in-memory state and stop the tick thread."""
    stop_tick()
    with _TICK_LOCK:
        global _TICK_GENERATION
        _TICK_GENERATION += 1
    state.character_states.clear()
    state.character_state_configs.clear()
    state.character_state_log.clear()
    with _STATUS_HANDLER_LOCK:
        for handlers in _STATUS_HANDLERS.values():
            handlers.clear()


# ---------------------------------------------------------------------------
# HTTP-friendly summary view
# ---------------------------------------------------------------------------


def summary(character_id: str) -> dict | None:
    """Return a JSON-friendly snapshot the routes can serve verbatim.

    Pulls together current values, status, machine-readable config
    summary, and the active behaviour bindings.  ``None`` if the
    character has never been touched by the state system.
    """
    record = get_state(character_id)
    if record is None:
        return None
    cfg = get_config(character_id) or _default_config(character_id)
    return {
        "character_id": character_id,
        "values": {
            "hunger": float(record.get("hunger", 0.0)),
            "thirst": float(record.get("thirst", 0.0)),
            "health": float(record.get("health", 0.0)),
            "mood": float(record.get("mood", 0.0)),
        },
        "max": {
            "hunger": float(record.get("max_hunger", DEFAULT_MAX_HUNGER)),
            "thirst": float(record.get("max_thirst", DEFAULT_MAX_THIRST)),
            "health": float(record.get("max_health", DEFAULT_MAX_HEALTH)),
            "mood": float(record.get("max_mood", DEFAULT_MAX_MOOD)),
        },
        "status": record.get("status", STATUS_HEALTHY),
        "status_changed_at": record.get("status_changed_at"),
        "last_updated": record.get("last_updated"),
        "can_dialogue": can_dialogue(character_id),
        "active_behavior": get_active_behavior(character_id),
        "modifiers": dict(cfg.get("modifiers") or {}),
    }


__all__ = [
    # constants
    "STATUS_HEALTHY", "STATUS_HUNGRY", "STATUS_THIRSTY",
    "STATUS_SICK", "STATUS_RECOVERING", "STATUS_CRITICAL",
    "ALL_STATUSES",
    "VALUE_FIELDS", "MAX_FIELDS",
    "DEFAULT_TICK_INTERVAL_SECONDS", "LOG_MAX_ENTRIES",
    # pure helpers
    "clamp", "decay_amount", "compute_target_status",
    "resolve_behavior_bindings",
    # state accessors
    "get_or_init_state", "get_state",
    "get_or_init_config", "get_config",
    "set_modifier", "clear_modifier",
    "list_log",
    # mutations
    "apply_field_change", "apply_patch",
    "tick_character", "tick_all",
    "can_dialogue", "force_recover", "enter_recovering",
    "get_active_behavior",
    # monitor
    "start_tick", "stop_tick", "tick_status",
    # seed/reset
    "seed_default", "reset_for_testing",
    # handler registry
    "register_status_handler", "unregister_status_handler",
    # summary
    "summary",
]
