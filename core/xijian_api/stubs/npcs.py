"""Stub NPC service — A4.2 in the function list v2.

The NPC store is the runtime home of "this world has many other
people that act on their own".  Each NPC carries a small compute
budget; the world manager (this module) decides which NPCs get the
"active" tier (high / low) per tick and demotes the rest to ``idle``
when the budget runs out.

Data model (mirrors the SQL schema in §A4.2 of the function list v2)
====================================================================

Three buckets live in :mod:`xijian_api.stubs.state`:

* ``npcs``                  — one record per NPC.  Per the SQL schema
  fields are: id, world_id, name, persona_doc, state_json,
  compute_budget, is_alive, activity_tier, importance,
  last_think_at, created_at.
* ``npc_scheduling_log``    — append-only log of tier transitions.
  Every move ``spawn`` / ``tick`` / ``degrade`` / ``sleep`` / ``wake``
  writes a record so operators can reconstruct "why did this NPC
  stop being active at 14:32".
* ``world_compute_config``  — owned by :mod:`stubs.world_compute_config`
  but consulted here for the per-world budget (50 000 tokens/min) and
  the active-tier cap (3 high / 10 low).

Activity tier semantics
=======================

The active tier is a *budget-driven* label — it's not a permanent
personality attribute, and the scheduler is allowed to change it on
the fly.  The allowed values:

* ``high_active`` — the "currently-thinking" tier.  Up to
  :data:`HIGH_ACTIVE_LIMIT` (3) NPCs per world at once.  The scheduler
  calls them every :data:`HIGH_ACTIVE_INTERVAL_S` (5 s).
* ``low_active``  — the "background" tier.  Up to
  :data:`LOW_ACTIVE_LIMIT` (10) NPCs per world at once.  The
  scheduler calls them every :data:`LOW_ACTIVE_INTERVAL_S` (15 s).
* ``idle``        — heartbeats only.  Scheduler visits every
  :data:`IDLE_INTERVAL_S` (60 s) to check whether the NPC should be
  promoted back (e.g. the world switched into a hot scene).

Compute budget
==============

* Default :data:`DEFAULT_TOTAL_TOKEN_BUDGET` = 50 000 tokens/min per
  world (locked by v2.1, the engine-selection decision).
* Per-NPC ``compute_budget`` is the *ceiling*; the world manager
  enforces the world total by demoting NPCs to ``idle`` once the
  total exceeds the cap.  The demotion is reversible: a manual
  ``set_tier`` always wins, but the next ``tick_world`` may demote
  the NPC again if the budget is still over.

The 50-NPC hard cap
===================

Per AC-5, a single world may not contain more than 50 NPCs + main
character.  ``create`` enforces this at the moment of insertion; the
caller must either archive an existing NPC or pick a different
``world_id``.  Tests should use this limit only as a boundary
condition.

A4.1 cross-link — ``select_affected_npcs``
==========================================

A4.1 events stub fires world events with an ``affected_npcs`` list.
This module exposes :func:`select_affected_npcs` that the events
stub calls to translate an event into the list of NPCs that should
land in ``affected_npcs``.  Default policy: every ``high_active``
NPC in the world is affected, plus any ``low_active`` NPC whose
``state_json`` shares the event's ``npc_kind`` tag.  Operators can
override the selector via :func:`set_affected_npc_selector`.

A5.4 cross-link — suspend_idle_npcs
===================================

When :mod:`stubs.overload` triggers
:data:`xijian_api.stubs.overload.ACTION_SUSPEND_IDLE_NPCS`, the
chat-pipeline / scheduler are expected to drop the floor.  This
module registers a handler on import that *immediately* demotes
every ``high_active`` / ``low_active`` NPC to ``idle`` and stops the
background tick.  A subsequent ``tick_world`` call (or a
``resume_from_overload`` manual call) wakes them back up.

Test surface
============

Pure helpers (no I/O):

* :func:`_validate_tier`
* :func:`_validate_compute_budget`
* :func:`_cap_for_tier`
* :func:`_interval_for_tier`
* :func:`_pick_demote_candidates`
* :func:`_should_degrade`
* :func:`select_affected_npcs`

Side-effecting functions (CRUD + scheduling):

* :func:`create` / :func:`get` / :func:`list_for_world` /
  :func:`list_all` / :func:`update` / :func:`delete`
* :func:`set_tier` (single NPC) / :func:`set_world_tier` (bulk)
* :func:`compute_world_budget` / :func:`compute_world_summary`
* :func:`tick_world` / :func:`tick_all`
* :func:`start_tick` / :func:`stop_tick` / :func:`tick_status`
* :func:`seed_default` / :func:`reset_for_testing`

Environment variables
---------------------

``XIJIAN_NPC_TICK``            — set to ``0`` to disable the
                                  background tick thread (CI /
                                  tests).
``XIJIAN_NPC_TICK_SECONDS``    — override the tick interval
                                  (default
                                  :data:`DEFAULT_TICK_INTERVAL_SECONDS`).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_npc_id, gen_npc_scheduling_log_id
from xijian_api.utils.time import now_ts


# ---------------------------------------------------------------------------
# Constants — locked by v2.1 of the function list
# ---------------------------------------------------------------------------

_LOGGER = logging.getLogger("xijian_api.npcs")

#: Per-NPC tier labels.  ``idle`` is a *budget-driven* demotion, not a
#: permanent attribute.
TIER_HIGH_ACTIVE = "high_active"
TIER_LOW_ACTIVE = "low_active"
TIER_IDLE = "idle"

VALID_TIERS: frozenset[str] = frozenset(
    {TIER_HIGH_ACTIVE, TIER_LOW_ACTIVE, TIER_IDLE}
)

#: Hard cap on total NPCs in a single world (AC-5).  Includes NPCs
#: *and* the main character; main-character count is tracked elsewhere,
#: but for the stub we count only NPCs.
MAX_NPCS_PER_WORLD = 50

#: Default per-NPC compute budget (tokens/min ceiling).
DEFAULT_NPC_COMPUTE_BUDGET = 100

#: Default world total budget.  Locked by v2.1.
DEFAULT_TOTAL_TOKEN_BUDGET = 50_000

#: Cap on simultaneously high_active NPCs per world.
HIGH_ACTIVE_LIMIT = 3

#: Cap on simultaneously low_active NPCs per world.
LOW_ACTIVE_LIMIT = 10

#: Thinking interval for high_active NPCs.
HIGH_ACTIVE_INTERVAL_S = 5.0

#: Thinking interval for low_active NPCs.
LOW_ACTIVE_INTERVAL_S = 15.0

#: Idle heartbeat interval — scheduler pings idle NPCs to check for
#: promotion back into an active tier.
IDLE_INTERVAL_S = 60.0

#: Default tick interval for the background thread.
DEFAULT_TICK_INTERVAL_SECONDS = 60.0

#: Lower bound for the tick interval — same floor policy as
#: A3.2 / A4.1.
_TICK_FLOOR_SECONDS = 1.0

#: Log cap per NPC (FIFO trim).
SCHEDULING_LOG_KEEP_PER_NPC = 200

#: Log cap per world (FIFO trim).
SCHEDULING_LOG_KEEP_PER_WORLD = 2_000

#: Env flag names.
_TICK_ENV_FLAG = "XIJIAN_NPC_TICK"
_TICK_INTERVAL_ENV_FLAG = "XIJIAN_NPC_TICK_SECONDS"

#: Per-NPC tier rank, used by the demotion order (high is more
#: valuable, demote the *least* important first).
_TIER_RANK: dict[str, int] = {
    TIER_IDLE: 0,
    TIER_LOW_ACTIVE: 1,
    TIER_HIGH_ACTIVE: 2,
}

#: Default LLM-queue P99 latency that triggers degradation (spec §A4.2
#: 配角算力调度 — "当 LLM 队列 P99 延迟 > 5s 时").
DEFAULT_DEGRADE_P99_LATENCY_S = 5.0

#: Scheduling-log ``action`` values.  Forward-compatible — unknown
#: values are accepted but validated in tests.
ACTIONS: frozenset[str] = frozenset(
    {"spawn", "tick", "degrade", "sleep", "wake", "manual", "overload"}
)

#: Scheduling-log ``reason`` values.
REASONS: frozenset[str] = frozenset(
    {"overload", "idle_timeout", "manual", "world_reset", "budget_exceeded", "promote"}
)


# ---------------------------------------------------------------------------
# Module-level scheduling state (in-memory coordination only)
# ---------------------------------------------------------------------------

_TICK_LOCK = threading.Lock()
_TICK_STOP = threading.Event()
_TICK_THREAD: threading.Thread | None = None
_TICK_GENERATION: int = 0
#: Set to ``True`` when the A5.4 overload handler suspends every NPC.
#: The background tick skips the world entirely while suspended.
_SUSPENDED: bool = False


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class NPCError(ValueError):
    """Raised on NPC validation errors (tier, budget, world cap)."""


# ---------------------------------------------------------------------------
# Pure helpers (no I/O)
# ---------------------------------------------------------------------------


def _validate_tier(tier: str) -> None:
    """Validate an activity tier label."""
    if tier not in VALID_TIERS:
        raise NPCError(
            f"activity_tier must be one of {sorted(VALID_TIERS)!r}, got {tier!r}"
        )


def _validate_compute_budget(budget: int) -> None:
    """Validate a per-NPC compute budget.  Must be a non-negative int."""
    if not isinstance(budget, (int, float)) or isinstance(budget, bool):
        raise NPCError(f"compute_budget must be a number, got {type(budget).__name__}")
    if int(budget) < 0:
        raise NPCError("compute_budget must be >= 0")
    if int(budget) > DEFAULT_TOTAL_TOKEN_BUDGET:
        # A single NPC may not exceed the entire world budget.
        raise NPCError(
            f"compute_budget {int(budget)} exceeds world total "
            f"{DEFAULT_TOTAL_TOKEN_BUDGET}"
        )


def _cap_for_tier(tier: str) -> int:
    """Return the world-wide cap for ``tier`` (0 for ``idle``)."""
    if tier == TIER_HIGH_ACTIVE:
        return HIGH_ACTIVE_LIMIT
    if tier == TIER_LOW_ACTIVE:
        return LOW_ACTIVE_LIMIT
    return 0


def _interval_for_tier(tier: str) -> float:
    """Return the think interval in seconds for ``tier``."""
    if tier == TIER_HIGH_ACTIVE:
        return HIGH_ACTIVE_INTERVAL_S
    if tier == TIER_LOW_ACTIVE:
        return LOW_ACTIVE_INTERVAL_S
    if tier == TIER_IDLE:
        return IDLE_INTERVAL_S
    return IDLE_INTERVAL_S


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


def _world_total_budget(world_id: str) -> int:
    """Read the world-level compute config, falling back to defaults.

    Lazy import to avoid a circular dependency at module-load time.
    """
    from xijian_api.stubs import world_compute_config as wcc_stub
    cfg = wcc_stub.get(world_id)
    if cfg is None:
        return DEFAULT_TOTAL_TOKEN_BUDGET
    return int(cfg.get("total_token_budget", DEFAULT_TOTAL_TOKEN_BUDGET))


def _should_degrade(
    *,
    npc_idle_seconds: float,
    queue_p99_latency_s: float | None = None,
) -> bool:
    """Decide whether a high_active NPC should be demoted.

    Returns ``True`` if any of the degradation rules fires.  A
    ``queue_p99_latency_s`` value of ``None`` means "no LLM queue data
    available — do not degrade on this basis".
    """
    # Idle timeout rule — same as character_state's high_active rest.
    if npc_idle_seconds >= 30.0:
        return True
    # LLM-queue pressure rule — spec §A4.2.
    if queue_p99_latency_s is not None and queue_p99_latency_s > DEFAULT_DEGRADE_P99_LATENCY_S:
        return True
    return False


def _pick_demote_candidates(
    npcs: list[dict],
    *,
    world_total: int,
    overage: int | None = None,
) -> list[str]:
    """Pick NPC ids to demote, lowest-importance first.

    The caller passes the *current* set of NPCs (typically all
    high_active in a world) and the world total.  The function
    computes how many must be demoted (the overage of the active
    tiers above their per-tier cap, OR the overage of the total above
    the world budget if explicit) and returns the chosen ids.
    """
    if not npcs:
        return []
    if overage is None:
        # Compute the overage from the active tier caps.
        high = [n for n in npcs if n.get("activity_tier") == TIER_HIGH_ACTIVE]
        low = [n for n in npcs if n.get("activity_tier") == TIER_LOW_ACTIVE]
        overage = max(0, len(high) - HIGH_ACTIVE_LIMIT) + max(0, len(low) - LOW_ACTIVE_LIMIT)
        # Plus the budget overage.
        total = sum(int(n.get("compute_budget", 0)) for n in npcs)
        if total > world_total:
            overage = max(overage, total - world_total)
    if overage <= 0:
        return []
    # Demote least-important first; break ties by oldest think_at
    # (longer-no-think gets demoted first).
    ranked = sorted(
        npcs,
        key=lambda n: (
            float(n.get("importance", 1.0)),
            float(n.get("last_think_at") or 0.0),
        ),
    )
    return [n["id"] for n in ranked[:overage]]


# ---------------------------------------------------------------------------
# Affected-NPC selector — used by A4.1 events
# ---------------------------------------------------------------------------

#: Callable that takes a world record + an event record and returns
#: a list of NPC ids.  Operators / tests can override via
#: :func:`set_affected_npc_selector`.
AffectedNPCSelector = Callable[[dict, dict], list[str]]

_DEFAULT_SELECTOR: AffectedNPCSelector | None = None


def _default_selector(world_record: dict, event_record: dict) -> list[str]:
    """Pick affected NPCs for an event.

    Policy: every high_active NPC in the world is "always affected"
    (they're the ones whose storyline the event should advance).
    Plus any low_active NPC whose ``state_json.npc_kind`` matches the
    event's ``npc_kind`` tag (forward-compat — events may carry a
    payload field that scopes who reacts).
    """
    wid = world_record.get("id") or world_record.get("world_id")
    if wid is None:
        return []
    npcs_in_world = [n for n in state.npcs.values() if n.get("world_id") == wid]
    affected: list[str] = []
    payload = event_record.get("payload") or {}
    npc_kind = payload.get("npc_kind")
    for npc in npcs_in_world:
        if npc.get("activity_tier") == TIER_HIGH_ACTIVE:
            affected.append(npc["id"])
        elif npc_kind and npc.get("state_json", {}).get("npc_kind") == npc_kind:
            affected.append(npc["id"])
    return affected


def set_affected_npc_selector(selector: AffectedNPCSelector | None) -> dict:
    """Override the affected-NPC selector.  ``None`` restores default."""
    global _DEFAULT_SELECTOR
    _DEFAULT_SELECTOR = selector
    return {"overridden": selector is not None}


def select_affected_npcs(world_record: dict, event_record: dict) -> list[str]:
    """Return NPC ids that should appear in the fired event's
    ``affected_npcs`` field.

    The events stub calls this from inside :func:`tick_world` to fill
    the field — see ``docs/notes.md`` A4.1 cross-link.  When no
    selector is registered the default policy is used.
    """
    selector = _DEFAULT_SELECTOR or _default_selector
    try:
        out = list(selector(world_record, event_record) or [])
    except Exception:  # noqa: BLE001 — a buggy selector must not crash event firing
        _LOGGER.warning(
            "affected-NPC selector failed for event %s", event_record.get("id")
        )
        return []
    return out


# ---------------------------------------------------------------------------
# Scheduling log helpers
# ---------------------------------------------------------------------------


def _append_log(
    *,
    npc_id: str,
    world_id: str,
    action: str,
    from_tier: str | None = None,
    to_tier: str | None = None,
    reason: str | None = None,
    now: float | None = None,
) -> dict:
    """Append a scheduling-log entry.  Returns the stored record."""
    log_id = gen_npc_scheduling_log_id()
    entry = {
        "id": log_id,
        "npc_id": npc_id,
        "world_id": world_id,
        "action": action,
        "from_tier": from_tier,
        "to_tier": to_tier,
        "reason": reason,
        "created_at": _now_or(now),
    }
    state.npc_scheduling_log[log_id] = entry
    _trim_log(npc_id, world_id)
    return entry


def _trim_log(npc_id: str, world_id: str) -> None:
    """Bound the in-memory log FIFO-style, both per-NPC and per-world."""
    bucket = list(state.npc_scheduling_log.values())
    # Per-NPC trim.
    per_npc = [e for e in bucket if e.get("npc_id") == npc_id]
    if len(per_npc) > SCHEDULING_LOG_KEEP_PER_NPC:
        per_npc.sort(key=lambda e: e.get("created_at", 0.0))
        for entry in per_npc[: len(per_npc) - SCHEDULING_LOG_KEEP_PER_NPC]:
            state.npc_scheduling_log.pop(entry["id"], None)
    # Per-world trim.
    per_world = [
        e for e in state.npc_scheduling_log.values() if e.get("world_id") == world_id
    ]
    if len(per_world) > SCHEDULING_LOG_KEEP_PER_WORLD:
        per_world.sort(key=lambda e: e.get("created_at", 0.0))
        for entry in per_world[: len(per_world) - SCHEDULING_LOG_KEEP_PER_WORLD]:
            state.npc_scheduling_log.pop(entry["id"], None)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create(
    *,
    world_id: str,
    name: str,
    persona_doc: str = "",
    state_json: dict | None = None,
    compute_budget: int = DEFAULT_NPC_COMPUTE_BUDGET,
    activity_tier: str = TIER_LOW_ACTIVE,
    importance: float = 1.0,
    npc_id: str | None = None,
    is_alive: bool = True,
    now: float | None = None,
) -> dict:
    """Create a new NPC and return the record.

    Raises :class:`NPCError` on:

    * unknown ``world_id`` (we require the world record to exist)
    * 50-NPC hard-cap exceeded (AC-5)
    * invalid ``activity_tier`` or ``compute_budget``

    The scheduling log gets a ``spawn`` entry so the audit trail
    knows when this NPC came online.
    """
    from xijian_api.stubs import worlds as worlds_stub
    if worlds_stub.get(world_id) is None:
        raise NPCError(f"world {world_id!r} does not exist")
    _validate_tier(activity_tier)
    _validate_compute_budget(compute_budget)
    # 50-NPC hard cap.
    current = [n for n in state.npcs.values() if n.get("world_id") == world_id]
    if len(current) >= MAX_NPCS_PER_WORLD:
        raise NPCError(
            f"world {world_id!r} has reached the {MAX_NPCS_PER_WORLD}-NPC hard cap"
        )
    new_id = npc_id or gen_npc_id()
    if new_id in state.npcs:
        raise NPCError(f"npc id {new_id!r} already exists")
    timestamp = _now_or(now)
    record = {
        "id": new_id,
        "world_id": world_id,
        "name": name,
        "persona_doc": persona_doc,
        "state_json": dict(state_json or {}),
        "compute_budget": int(compute_budget),
        "is_alive": bool(is_alive),
        "activity_tier": activity_tier,
        "importance": float(importance),
        "last_think_at": None,
        "created_at": timestamp,
    }
    state.npcs[new_id] = record
    _append_log(
        npc_id=new_id,
        world_id=world_id,
        action="spawn",
        from_tier=None,
        to_tier=activity_tier,
        reason="manual",
        now=timestamp,
    )
    return record


def get(npc_id: str) -> dict | None:
    """Return the NPC record or ``None``."""
    return state.npcs.get(npc_id)


def list_for_world(
    world_id: str,
    *,
    tier: str | None = None,
    alive_only: bool = False,
) -> list[dict]:
    """Return NPCs in ``world_id``, optionally filtered by tier / alive."""
    out: list[dict] = []
    for record in state.npcs.values():
        if record.get("world_id") != world_id:
            continue
        if tier is not None and record.get("activity_tier") != tier:
            continue
        if alive_only and not record.get("is_alive"):
            continue
        out.append(record)
    # Stable order: importance desc, then created_at asc.
    out.sort(
        key=lambda r: (
            -float(r.get("importance", 1.0)),
            r.get("created_at", 0.0),
        )
    )
    return out


def list_all() -> list[dict]:
    """Return every NPC across every world.  Used by the routes summary."""
    return list(state.npcs.values())


def update(npc_id: str, patch: dict) -> dict | None:
    """Patch mutable NPC fields.  ``id`` and ``world_id`` are immutable."""
    record = state.npcs.get(npc_id)
    if record is None:
        return None
    if "id" in patch or "world_id" in patch:
        raise NPCError("id and world_id are immutable; create a new NPC")
    for key, value in patch.items():
        if key in {"name", "persona_doc", "is_alive", "last_think_at", "state_json"}:
            record[key] = value
        elif key == "compute_budget":
            _validate_compute_budget(value)
            record["compute_budget"] = int(value)
        elif key == "activity_tier":
            _validate_tier(value)
            # Tier changes go through :func:`set_tier` so the log is
            # written; refuse here to keep a single audit-trail path.
            raise NPCError(
                "use npcs.set_tier() to change activity_tier; it writes a log entry"
            )
        elif key == "importance":
            record["importance"] = float(value)
    return record


def delete(npc_id: str) -> bool:
    """Delete an NPC.  Returns True if it existed.  The log is kept."""
    return state.npcs.pop(npc_id, None) is not None


# ---------------------------------------------------------------------------
# Tier transitions (always log)
# ---------------------------------------------------------------------------


def set_tier(
    npc_id: str,
    tier: str,
    *,
    reason: str = "manual",
    now: float | None = None,
) -> dict | None:
    """Change an NPC's activity tier.  Returns the updated record or ``None``.

    Every tier transition writes a ``npc_scheduling_log`` entry — the
    log is the audit trail for "why did this NPC change tier" and is
    forwarded to operators by the audit route layer.
    """
    _validate_tier(tier)
    record = state.npcs.get(npc_id)
    if record is None:
        return None
    from_tier = record.get("activity_tier")
    if from_tier == tier:
        return record
    record["activity_tier"] = tier
    _append_log(
        npc_id=npc_id,
        world_id=record["world_id"],
        action="sleep" if tier == TIER_IDLE else "wake",
        from_tier=from_tier,
        to_tier=tier,
        reason=reason,
        now=now,
    )
    return record


def set_world_tier(
    world_id: str,
    tier: str,
    *,
    reason: str = "manual",
) -> dict:
    """Bulk-set every NPC in a world to ``tier``.  Returns a count dict.

    Used by world-reset and by the overload handler.
    """
    _validate_tier(tier)
    updated = 0
    for record in list(state.npcs.values()):
        if record.get("world_id") != world_id:
            continue
        if record.get("activity_tier") == tier:
            continue
        record["activity_tier"] = tier
        _append_log(
            npc_id=record["id"],
            world_id=world_id,
            action="sleep" if tier == TIER_IDLE else "wake",
            from_tier=None,  # bulk — unknown
            to_tier=tier,
            reason=reason,
        )
        updated += 1
    return {"world_id": world_id, "tier": tier, "updated": updated}


# ---------------------------------------------------------------------------
# Budget + summary views
# ---------------------------------------------------------------------------


def compute_world_budget(world_id: str) -> dict:
    """Return the live budget view for ``world_id`` (no side effects)."""
    world_total = _world_total_budget(world_id)
    npcs_in_world = list_for_world(world_id)
    high = [n for n in npcs_in_world if n.get("activity_tier") == TIER_HIGH_ACTIVE]
    low = [n for n in npcs_in_world if n.get("activity_tier") == TIER_LOW_ACTIVE]
    idle = [n for n in npcs_in_world if n.get("activity_tier") == TIER_IDLE]
    total_used = sum(int(n.get("compute_budget", 0)) for n in high + low)
    return {
        "world_id": world_id,
        "world_total_budget": world_total,
        "total_used": total_used,
        "over_budget": total_used > world_total,
        "npc_count": len(npcs_in_world),
        "high_active_count": len(high),
        "low_active_count": len(low),
        "idle_count": len(idle),
        "high_active_cap": HIGH_ACTIVE_LIMIT,
        "low_active_cap": LOW_ACTIVE_LIMIT,
        "high_active_over": max(0, len(high) - HIGH_ACTIVE_LIMIT),
        "low_active_over": max(0, len(low) - LOW_ACTIVE_LIMIT),
    }


def compute_world_summary() -> list[dict]:
    """Return per-world budget views for every world with at least one NPC."""
    worlds = sorted({n.get("world_id") for n in state.npcs.values()})
    return [compute_world_budget(wid) for wid in worlds if wid is not None]


# ---------------------------------------------------------------------------
# Scheduling — tick a world, demote over-budget NPCs
# ---------------------------------------------------------------------------


def tick_world(
    world_id: str,
    *,
    now: float | None = None,
    queue_p99_latency_s: float | None = None,
) -> dict:
    """Run one scheduler pass for a single world.

    Steps:

    1. If the world is currently suspended by the A5.4 overload
       handler, return immediately with ``{"suspended": True}``.
    2. Re-evaluate per-NPC tier — if any high_active NPC has been
       idle past the degradation threshold, demote it to low_active
       (or to ``idle`` if it's the LLM-queue-pressure case).
    3. Enforce the world budget: if the total exceeds the cap,
       demote the least-important NPCs to ``idle`` until the budget
       is satisfied.
    4. Mark each visited NPC's ``last_think_at`` and write a
       scheduling-log entry with action ``tick``.

    The function is *pure-ish* — it does not spawn threads or talk
    to the LLM.  Operators that want to do "real" NPC thinking
    (LLM round-trip) should call this, then dispatch a separate
    coroutine per high_active NPC.
    """
    if _SUSPENDED:
        return {"world_id": world_id, "suspended": True, "fired": 0, "demoted": 0}
    timestamp = _now_or(now)
    npcs_in_world = list_for_world(world_id, alive_only=True)
    if not npcs_in_world:
        return {
            "world_id": world_id,
            "fired": 0,
            "demoted": 0,
            "over_budget": False,
        }
    world_total = _world_total_budget(world_id)
    # Per-NPC idle demotion.
    demoted = 0
    for npc in npcs_in_world:
        last = npc.get("last_think_at")
        idle_s = (timestamp - float(last)) if last is not None else 1e9
        if npc.get("activity_tier") == TIER_HIGH_ACTIVE and _should_degrade(
            npc_idle_seconds=idle_s, queue_p99_latency_s=queue_p99_latency_s
        ):
            old_tier = npc["activity_tier"]
            npc["activity_tier"] = TIER_LOW_ACTIVE
            _append_log(
                npc_id=npc["id"],
                world_id=world_id,
                action="degrade",
                from_tier=old_tier,
                to_tier=TIER_LOW_ACTIVE,
                reason="overload" if queue_p99_latency_s and queue_p99_latency_s > DEFAULT_DEGRADE_P99_LATENCY_S else "idle_timeout",
                now=timestamp,
            )
            demoted += 1
    # World-budget demotion.
    budget_view = compute_world_budget(world_id)
    if budget_view["over_budget"] or budget_view["high_active_over"] or budget_view["low_active_over"]:
        active_npcs = [
            n for n in list_for_world(world_id, alive_only=True)
            if n.get("activity_tier") in (TIER_HIGH_ACTIVE, TIER_LOW_ACTIVE)
        ]
        candidates = _pick_demote_candidates(active_npcs, world_total=world_total)
        for npc_id in candidates:
            npc = state.npcs.get(npc_id)
            if npc is None:
                continue
            old_tier = npc["activity_tier"]
            npc["activity_tier"] = TIER_IDLE
            _append_log(
                npc_id=npc_id,
                world_id=world_id,
                action="degrade",
                from_tier=old_tier,
                to_tier=TIER_IDLE,
                reason="budget_exceeded",
                now=timestamp,
            )
            demoted += 1
    # Mark every active NPC's last_think_at — operators can read this
    # to see "who got ticked this pass".
    fired = 0
    for npc in list_for_world(world_id, alive_only=True):
        if npc.get("activity_tier") in (TIER_HIGH_ACTIVE, TIER_LOW_ACTIVE):
            npc["last_think_at"] = timestamp
            _append_log(
                npc_id=npc["id"],
                world_id=world_id,
                action="tick",
                from_tier=npc["activity_tier"],
                to_tier=npc["activity_tier"],
                reason="scheduled",
                now=timestamp,
            )
            fired += 1
    return {
        "world_id": world_id,
        "suspended": False,
        "fired": fired,
        "demoted": demoted,
        "over_budget": budget_view["over_budget"],
        "budget_view": compute_world_budget(world_id),
    }


def tick_all(
    *,
    now: float | None = None,
    queue_p99_latency_s: float | None = None,
) -> dict:
    """Run a scheduler pass for every world that has at least one NPC."""
    if _SUSPENDED:
        return {"suspended": True, "worlds": {}}
    timestamp = _now_or(now)
    worlds = sorted({n.get("world_id") for n in state.npcs.values()})
    out: dict[str, dict] = {}
    for wid in worlds:
        if wid is None:
            continue
        out[wid] = tick_world(wid, now=timestamp, queue_p99_latency_s=queue_p99_latency_s)
    return {"suspended": False, "worlds": out}


# ---------------------------------------------------------------------------
# Background tick thread lifecycle
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
    if value < _TICK_FLOOR_SECONDS:
        value = _TICK_FLOOR_SECONDS
    return value


def _tick_loop(stop_event: threading.Event, generation: int) -> None:
    """Main tick loop.  Mirrors A3.2 / A4.1 / A5.4 pattern."""
    while not stop_event.is_set():
        with _TICK_LOCK:
            if _TICK_GENERATION != generation:
                return
        try:
            tick_all()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("npc tick failed: %s", exc)
        if stop_event.wait(_current_interval()):
            break


def start_tick() -> dict:
    """Start the background NPC tick thread.  Idempotent."""
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
            name="xijian-npc-tick",
            daemon=True,
        )
        _TICK_THREAD = thread
        thread.start()
    return {"started": True, "interval_s": _current_interval()}


def stop_tick() -> dict:
    """Stop the background NPC tick thread.  No-op if not running."""
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
        "suspended": _SUSPENDED,
        "enabled_by_env": os.environ.get(_TICK_ENV_FLAG) != "0",
    }


# ---------------------------------------------------------------------------
# A5.4 overload cross-link — suspend every active NPC
# ---------------------------------------------------------------------------


def _suspend_for_overload(event: dict) -> None:
    """Drop every active NPC to ``idle`` and pause the background tick.

    Registered as a handler on the A5.4 overload ``suspend_idle_npcs``
    action.  Idempotent — running it twice is a no-op.
    """
    global _SUSPENDED
    if _SUSPENDED:
        return
    _SUSPENDED = True
    worlds = sorted({n.get("world_id") for n in state.npcs.values()})
    for wid in worlds:
        if wid is None:
            continue
        set_world_tier(wid, TIER_IDLE, reason="overload")
    _LOGGER.info(
        "npc tick suspended by overload event %s (event_id=%s)",
        event.get("id"),
        event.get("id"),
    )


def resume_from_overload() -> dict:
    """Re-arm the background tick.  NPCs stay at ``idle`` — they need
    a manual ``set_tier`` to come back to active.  This matches the
    A4.2 spec: the scheduler is the only one allowed to promote
    back, and only after the next ``tick_world`` finds the budget
    has room.
    """
    global _SUSPENDED
    _SUSPENDED = False
    return {"resumed": True}


def install_overload_handler() -> dict:
    """Register the A5.4 overload handler for ``suspend_idle_npcs``.

    Safe to call multiple times — the handler is idempotent.  Returns
    a small status dict so callers (the route module's ``seed_default``
    hook) can log the registration.
    """
    from xijian_api.stubs.overload import ACTION_SUSPEND_IDLE_NPCS, register_action_handler
    register_action_handler(ACTION_SUSPEND_IDLE_NPCS, _suspend_for_overload)
    return {"action": ACTION_SUSPEND_IDLE_NPCS, "installed": True}


# ---------------------------------------------------------------------------
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.

    We do **not** seed any default NPCs — operators create them via
    the route layer, and (per the v2 spec) the world library is
    operator-curated.  We *do* install the A5.4 overload handler
    on first boot so the cross-link is wired without operators
    having to remember.

    Also starts the background tick thread if the env allows it.
    """
    install_overload_handler()
    if os.environ.get(_TICK_ENV_FLAG) == "0":
        return
    start_tick()


def reset_for_testing() -> None:
    """Wipe in-memory state and stop the tick thread."""
    global _SUSPENDED
    stop_tick()
    with _TICK_LOCK:
        global _TICK_GENERATION
        _TICK_GENERATION += 1
    _SUSPENDED = False
    state.npcs.clear()
    state.npc_scheduling_log.clear()


__all__ = [
    # Constants
    "TIER_HIGH_ACTIVE", "TIER_LOW_ACTIVE", "TIER_IDLE",
    "VALID_TIERS", "MAX_NPCS_PER_WORLD",
    "DEFAULT_NPC_COMPUTE_BUDGET", "DEFAULT_TOTAL_TOKEN_BUDGET",
    "HIGH_ACTIVE_LIMIT", "LOW_ACTIVE_LIMIT",
    "HIGH_ACTIVE_INTERVAL_S", "LOW_ACTIVE_INTERVAL_S", "IDLE_INTERVAL_S",
    "DEFAULT_TICK_INTERVAL_SECONDS", "DEFAULT_DEGRADE_P99_LATENCY_S",
    "ACTIONS", "REASONS",
    # Errors
    "NPCError",
    # Pure helpers
    "_validate_tier", "_validate_compute_budget",
    "_cap_for_tier", "_interval_for_tier",
    "_pick_demote_candidates", "_should_degrade",
    "select_affected_npcs", "set_affected_npc_selector",
    # CRUD
    "create", "get", "list_for_world", "list_all", "update", "delete",
    # Tier transitions
    "set_tier", "set_world_tier",
    # Budget / summary
    "compute_world_budget", "compute_world_summary",
    # Scheduling
    "tick_world", "tick_all",
    "start_tick", "stop_tick", "tick_status",
    # Overload cross-link
    "install_overload_handler", "resume_from_overload",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
