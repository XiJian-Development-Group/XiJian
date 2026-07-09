"""World audit log — append-only ledger of operator / system actions.

A4.2 spec defines a `world_audit_log` table that captures *every*
non-trivial world-level event: reset / patch / npc_create /
transition / environment_change / etc.  Operators can inspect the
log to reconstruct "what happened to my world" without grepping
through deeper observability tooling.

Scope is intentionally narrow:

* Append-only — there is no ``delete`` or ``update``.  An audit
  trail that can be edited is not a trail.
* Per-world — the route layer filters by ``world_id``.
* Best-effort failures stay in DEBUG — a write failure must NOT
  block the operation being audited; if the ledger is unhealthy
  we still want the user action to land.

The log is bounded by ``AUDIT_KEEP_PER_WORLD`` per world, with a
FIFO trim that matches the A4.1 / A3.2 pattern.
"""

from __future__ import annotations

import logging
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_world_audit_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.world_audit")

#: FIFO cap per world (matches the A3.2 character-state log policy).
AUDIT_KEEP_PER_WORLD = 1000

#: Valid ``action`` values.  Anything outside this set still gets
#: recorded (forward-compat) but the routes validate on known ones.
ACTIONS: frozenset[str] = frozenset(
    {
        "create",
        "update",
        "patch",
        "delete",
        "reset",
        "reset_preview",
        "reset_confirmed",
        "reset_finalized",
        "transition",
        "npc_create",
        "npc_update",
        "npc_delete",
        "npc_suspend",
        "npc_resume",
        "tier_change",
        "environment_update",
    }
)

#: Valid ``actor`` values.  We accept other strings for forward-compat
#: (the future B/C chapters might introduce new agent types) but
#: anything outside this set is logged as a warning at write time.
ACTORS: frozenset[str] = frozenset({"user", "system", "scheduler", "overload"})


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


def record(
    *,
    world_id: str,
    action: str,
    actor: str = "user",
    payload: dict[str, Any] | None = None,
    log_id: str | None = None,
    now: float | None = None,
) -> dict | None:
    """Append a record; return the stored dict or ``None`` on validation failure.

    Silent ``None`` rather than raising because audit must not block the
    audited action.  Failures are noisy-DEBUG-logged so production can
    grep without filling INFO/WARNING channels.
    """
    if not isinstance(world_id, str) or not world_id:
        _LOGGER.debug("audit skipped: missing world_id")
        return None
    if not isinstance(action, str) or not action:
        _LOGGER.debug("audit skipped: missing action")
        return None
    if actor not in ACTORS:
        _LOGGER.warning(
            "world_audit: unknown actor %r for action %r", actor, action
        )
    record_id = log_id or gen_world_audit_id()
    entry = {
        "id": record_id,
        "world_id": world_id,
        "action": action,
        "actor": actor,
        "payload": dict(payload or {}),
        "created_at": _now_or(now),
    }
    state.world_audit_log[record_id] = entry
    _trim(world_id)
    return entry


def _trim(world_id: str) -> None:
    """Bound the per-world audit log FIFO-style."""
    bucket = [
        e
        for e in state.world_audit_log.values()
        if e.get("world_id") == world_id
    ]
    excess = len(bucket) - AUDIT_KEEP_PER_WORLD
    if excess <= 0:
        return
    bucket.sort(key=lambda e: e.get("created_at", 0.0))
    for entry in bucket[:excess]:
        state.world_audit_log.pop(entry["id"], None)


def list_log(
    *,
    world_id: str | None = None,
    action: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return audit entries newest-first, optionally filtered."""
    out: list[dict] = []
    for entry in state.world_audit_log.values():
        if world_id is not None and entry.get("world_id") != world_id:
            continue
        if action is not None and entry.get("action") != action:
            continue
        out.append(entry)
    out.sort(key=lambda e: e.get("created_at", 0.0), reverse=True)
    if limit < 1:
        limit = 1
    return out[:limit]


def count_for(world_id: str) -> int:
    """Count entries for a world — handy for tests and dashboards."""
    return sum(
        1
        for e in state.world_audit_log.values()
        if e.get("world_id") == world_id
    )


def reset_for_world(world_id: str) -> int:
    """Remove every audit entry for a world.  Returns count removed.

    Operators may want this when retiring a world.  System action.
    """
    removed = 0
    for entry in list(state.world_audit_log.values()):
        if entry.get("world_id") == world_id:
            state.world_audit_log.pop(entry["id"], None)
            removed += 1
    return removed


def reset_for_testing() -> None:
    """Clear the audit log (test-only)."""
    state.world_audit_log.clear()


__all__ = [
    "AUDIT_KEEP_PER_WORLD",
    "ACTIONS",
    "ACTORS",
    "record",
    "list_log",
    "count_for",
    "reset_for_world",
    "reset_for_testing",
]
