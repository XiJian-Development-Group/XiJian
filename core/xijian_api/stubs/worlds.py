"""Stub world service — A4.2 in the function list v2.

A "world" is an operator-curated sandbox: a self-contained
universe (原神 / 崩铁 / 自创) with its own NPCs, events, environment
state, and compute config.  Worlds are **operator-created** resources
— the stub starts empty so production operators control what shows
up in the user's picker.

Data model (mirrors the SQL schema in §A4.2 of the function list v2)
====================================================================

The bucket is ``state.worlds`` (``{world_id: dict}``).  Per the SQL
schema the fields are:

* ``id``              — primary key, ``world_<12 hex>``
* ``name``            — operator-given display name
* ``world_doc_path``  — path to the world's lore Markdown
* ``config_path``     — path to the world's config (e.g. JSON)
* ``state_doc_path``  — path to the world's persistent state file
* ``is_active``       — boolean, whether the world is "in rotation"
* ``last_active_at``  — most recent switch-in timestamp
* ``created_at``      — when the world was created
* ``updated_at``      — last mutation timestamp

There is **no** ``location`` / ``state`` / ``events`` field on the
world record — those live in :mod:`xijian_api.stubs.world_environment`
(environment state) and :mod:`xijian_api.stubs.events` (event
log).  The legacy fields from the pre-A4.2 stub are gone.

Cross-module integration
========================

* :mod:`xijian_api.stubs.world_environment` — per-world weather /
  time / light / ambient.  Materialized on first read.
* :mod:`xijian_api.stubs.world_compute_config` — per-world budget
  and active tier.  Materialized on first read.
* :mod:`xijian_api.stubs.world_audit` — every world-reset /
  world-patch / world-delete / switch-active writes a record.
* :mod:`xijian_api.stubs.npcs` — per-world NPC list.  The
  ``summary`` view rolls up the count by tier.

Two-step reset (AC-4)
=====================

Per spec AC-4 the user must double-confirm a world reset.  The
reset flow is:

1. ``preview_reset(world_id)`` returns a ``reset_token`` that the
   caller must echo back within ``RESET_TOKEN_TTL_SECONDS``.
2. ``confirm_reset(world_id, reset_token)`` actually wipes the
   world — including every NPC, environment record, and compute
   config.  A ``world_audit_log`` entry is written before the wipe
   so the operator has a recovery breadcrumb.

Test surface
============

CRUD:

* :func:`create` / :func:`get` / :func:`list_all` / :func:`update` /
  :func:`delete`

State & views:

* :func:`get_state` — combined view (world + env + compute + npc count)
* :func:`update_state` — patch the white-listed state fields
* :func:`patch_state_doc` — operator-only path that updates the
  ``state_doc_path`` (no state mutation)

Lifecycle:

* :func:`switch_active` — mark a world as the user's current world
* :func:`preview_reset` / :func:`confirm_reset` — two-step reset
* :func:`summary` — JSON-friendly overview

Legacy aliases (kept for backward compat — see A4.1 / pre-A4.2 tests):

* :func:`transition` — old location-transition API; now delegates
  to ``world_environment`` (records a transit log entry but doesn't
  update a stale ``location`` field, since the world no longer has
  one).
* :func:`add_event` — old per-world event-log API; now records into
  the global :mod:`xijian_api.stubs.events` library as a custom
  kind and returns the event instance.

Environment variables
---------------------

None.  Worlds are pure data; the only async is the A4.2 NPC tick
that lives in :mod:`xijian_api.stubs.npcs`.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_world_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.worlds")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Legacy demo world id, kept so dev workflows have a known handle.
DEFAULT_WORLD_ID = "world_modern_tokyo"

#: Whitelist of state-patch fields.  Pre-A4.2 callers passed
#: ``{economy, health, diet, stamina, mentality}`` — these are the
#: fields spec Dev.md §4.3.3 ("系统维度") recognises.  We accept
#: extras as a forward-compat escape hatch but log a DEBUG line so
#: the audit team can spot them.
WHITELISTED_STATE_FIELDS: frozenset[str] = frozenset(
    {"economy", "health", "diet", "stamina", "mentality"}
)

#: TTL for reset tokens.  AC-4 requires double-confirmation; 60 s
#: matches the spec's "20-30s wait" wording rounded up to give
#: operators a comfortable buffer.
RESET_TOKEN_TTL_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WorldError(ValueError):
    """Raised on world validation / lifecycle errors."""


# ---------------------------------------------------------------------------
# Module-level reset-token store
# ---------------------------------------------------------------------------

#: ``{world_id: {token, issued_at, expires_at}}`` — in-memory only;
#: resets are an operator-driven, single-process operation.
_reset_tokens: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


def _default_record(
    *,
    name: str,
    world_doc_path: str,
    config_path: str,
    state_doc_path: str,
) -> dict:
    ts = now_ts()
    return {
        "name": name,
        "world_doc_path": world_doc_path,
        "config_path": config_path,
        "state_doc_path": state_doc_path,
        "is_active": True,
        "last_active_at": None,
        "created_at": ts,
        "updated_at": ts,
    }


def _audit(
    world_id: str,
    action: str,
    *,
    actor: str = "user",
    payload: dict[str, Any] | None = None,
) -> None:
    """Best-effort write to world_audit_log.  Audit must not break
    the audited operation."""
    try:
        from xijian_api.stubs import world_audit as audit_stub
        audit_stub.record(
            world_id=world_id,
            action=action,
            actor=actor,
            payload=payload or {},
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("worlds audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create(
    *,
    name: str,
    world_doc_path: str = "",
    config_path: str = "",
    state_doc_path: str = "",
    world_id: str | None = None,
    is_active: bool = True,
    now: float | None = None,
) -> dict:
    """Create a new world record and return it.

    Side effects: materializes default entries in
    ``world_environment`` and ``world_compute_config`` so the
    downstream views always have a record to read.
    """
    if not isinstance(name, str) or not name:
        raise WorldError("name is required")
    new_id = world_id or gen_world_id()
    if new_id in state.worlds:
        raise WorldError(f"world id {new_id!r} already exists")
    timestamp = _now_or(now)
    record = _default_record(
        name=name,
        world_doc_path=world_doc_path,
        config_path=config_path,
        state_doc_path=state_doc_path,
    )
    record["id"] = new_id
    record["created_at"] = timestamp
    record["updated_at"] = timestamp
    record["is_active"] = bool(is_active)
    state.worlds[new_id] = record
    # Materialize defaults in the related buckets so route views
    # have something to read.
    from xijian_api.stubs import world_environment as env_stub
    from xijian_api.stubs import world_compute_config as wcc_stub
    env_stub.ensure_environment(new_id)
    wcc_stub.get(new_id)  # lazy default
    _audit(new_id, "create", payload={"name": name})
    return record


def get(world_id: str) -> dict | None:
    """Return the world record or ``None``."""
    return state.worlds.get(world_id)


def list_all() -> list[dict]:
    """Return every world.  Sort: active first, then by name."""
    out = list(state.worlds.values())
    out.sort(
        key=lambda r: (
            0 if r.get("is_active") else 1,
            str(r.get("name", "")),
        )
    )
    return out


def update(world_id: str, patch: dict) -> dict | None:
    """Patch mutable world fields.  ``id`` and ``created_at`` are immutable."""
    record = state.worlds.get(world_id)
    if record is None:
        return None
    if "id" in patch or "created_at" in patch:
        raise WorldError("id and created_at are immutable")
    for key in ("name", "world_doc_path", "config_path",
                "state_doc_path", "is_active"):
        if key in patch:
            if key == "name" and (not isinstance(patch[key], str) or not patch[key]):
                raise WorldError("name must be a non-empty string")
            record[key] = patch[key]
    record["updated_at"] = now_ts()
    _audit(world_id, "update", payload=patch)
    return record


def delete(world_id: str) -> bool:
    """Delete a world.  NPC / env / compute-config records are
    kept — the audit trail needs them.  A ``delete`` audit entry
    is written before removal so the breadcrumb is on disk.
    """
    record = state.worlds.get(world_id)
    if record is None:
        return False
    _audit(world_id, "delete", payload={"name": record.get("name")})
    # Drop reset tokens for this world — they're meaningless now.
    _reset_tokens.pop(world_id, None)
    state.worlds.pop(world_id, None)
    return True


# ---------------------------------------------------------------------------
# Active-world switch
# ---------------------------------------------------------------------------


def switch_active(world_id: str) -> dict | None:
    """Mark a world as the user's current world and bump
    ``last_active_at``.  Returns the updated record or ``None`` if
    the world doesn't exist."""
    record = state.worlds.get(world_id)
    if record is None:
        return None
    if not record.get("is_active"):
        raise WorldError(f"world {world_id!r} is not active")
    record["last_active_at"] = now_ts()
    record["updated_at"] = record["last_active_at"]
    _audit(world_id, "switch_active")
    return record


# ---------------------------------------------------------------------------
# State & views
# ---------------------------------------------------------------------------


def get_state(world_id: str) -> dict | None:
    """Return a combined view: world record + environment + compute
    config + NPC count.  The route layer serves this verbatim.

    Returns ``None`` if the world doesn't exist.
    """
    record = state.worlds.get(world_id)
    if record is None:
        return None
    from xijian_api.stubs import world_environment as env_stub
    from xijian_api.stubs import world_compute_config as wcc_stub
    from xijian_api.stubs import npcs as npcs_stub
    env = env_stub.get(world_id) or env_stub.ensure_environment(world_id)
    cfg = wcc_stub.get(world_id) or {}
    npc_count = len(npcs_stub.list_for_world(world_id))
    return {
        "world_id": world_id,
        "name": record.get("name"),
        "is_active": record.get("is_active"),
        "last_active_at": record.get("last_active_at"),
        "world_doc_path": record.get("world_doc_path"),
        "config_path": record.get("config_path"),
        "state_doc_path": record.get("state_doc_path"),
        "environment": {
            "weather": env.get("weather"),
            "time_of_day": env.get("time_of_day"),
            "light_level": env.get("light_level"),
            "ambient_audio": env.get("ambient_audio"),
            "env_meta": env.get("env_meta") or {},
        },
        "compute_config": {
            "active_tier": cfg.get("active_tier"),
            "max_npcs": cfg.get("max_npcs"),
            "max_active_npcs": cfg.get("max_active_npcs"),
            "max_low_active_npcs": cfg.get("max_low_active_npcs"),
            "total_token_budget": cfg.get("total_token_budget"),
        },
        "npc_count": npc_count,
        "updated_at": record.get("updated_at"),
    }


def update_state(
    world_id: str,
    patch: dict,
    *,
    protection_enabled: bool = True,
) -> tuple[dict | None, str | None]:
    """Patch white-listed state fields.  Returns ``(state_record, error_key)``.

    Pre-A4.2 callers passed ``{economy, health, diet, stamina, mentality}``
    — these remain the canonical fields.  We also accept the
    "world_event ledger" key (``event_ledger``) so A4.1 events can
    drop a breadcrumb without an explicit endpoint.
    """
    record = state.worlds.get(world_id)
    if record is None:
        return None, "not_found"
    if not protection_enabled:
        return None, "protection_disabled"
    state_blob = record.setdefault("state", {})
    accepted: dict[str, Any] = {}
    for key, value in patch.items():
        if key in WHITELISTED_STATE_FIELDS:
            state_blob[key] = value
            accepted[key] = value
        elif key == "event_ledger":
            # Forward-compat slot for A4.1 events to drop a breadcrumb.
            state_blob.setdefault("event_ledger", []).append(value)
            accepted["event_ledger"] = value
        else:
            _LOGGER.debug(
                "worlds.update_state: unknown key %r on %s (forward-compat)",
                key,
                world_id,
            )
            state_blob[key] = value
            accepted[key] = value
    record["updated_at"] = now_ts()
    _audit(world_id, "patch", payload=accepted)
    return state_blob, None


def patch_state_doc(
    world_id: str, *, world_doc_path: str | None = None,
    config_path: str | None = None, state_doc_path: str | None = None,
) -> dict | None:
    """Operator-only path: update the file paths on a world record.
    Used by the import / DevKit flow when the operator moves the
    world's backing files."""
    record = state.worlds.get(world_id)
    if record is None:
        return None
    for key, value in (
        ("world_doc_path", world_doc_path),
        ("config_path", config_path),
        ("state_doc_path", state_doc_path),
    ):
        if value is not None:
            record[key] = value
    record["updated_at"] = now_ts()
    _audit(world_id, "patch_doc", payload={
        k: v for k, v in (
            ("world_doc_path", world_doc_path),
            ("config_path", config_path),
            ("state_doc_path", state_doc_path),
        ) if v is not None
    })
    return record


# ---------------------------------------------------------------------------
# Reset (two-step) — AC-4
# ---------------------------------------------------------------------------


def preview_reset(
    world_id: str, *, now: float | None = None
) -> dict | None:
    """Begin the reset handshake.  Returns a token the caller must
    echo to :func:`confirm_reset` within ``RESET_TOKEN_TTL_SECONDS``.

    Returns ``None`` if the world doesn't exist.
    """
    record = state.worlds.get(world_id)
    if record is None:
        return None
    timestamp = _now_or(now)
    token = secrets.token_urlsafe(16)
    _reset_tokens[world_id] = {
        "token": token,
        "issued_at": timestamp,
        "expires_at": timestamp + RESET_TOKEN_TTL_SECONDS,
    }
    _audit(world_id, "reset_preview", payload={"expires_at": timestamp + RESET_TOKEN_TTL_SECONDS})
    return {
        "world_id": world_id,
        "reset_token": token,
        "expires_at": timestamp + RESET_TOKEN_TTL_SECONDS,
        "ttl_seconds": RESET_TOKEN_TTL_SECONDS,
    }


def confirm_reset(
    world_id: str,
    reset_token: str,
    *,
    now: float | None = None,
) -> dict | None:
    """Second confirmation.  Resets the world to defaults and
    returns the new record.

    Returns a tuple-shaped dict ``{"ok": bool, "error": str | None,
    "world": dict | None}`` for callers that want a structured
    answer.  Raises :class:`WorldError` only on programming errors
    (missing token, etc.); routine misses (expired, wrong token)
    are reported via ``error``.
    """
    record = state.worlds.get(world_id)
    if record is None:
        return None
    handle = _reset_tokens.get(world_id)
    timestamp = _now_or(now)
    if handle is None:
        return {
            "ok": False,
            "error": "no_pending_reset",
            "world": record,
        }
    if timestamp > float(handle["expires_at"]):
        _reset_tokens.pop(world_id, None)
        return {
            "ok": False,
            "error": "token_expired",
            "world": record,
        }
    if not secrets.compare_digest(str(handle["token"]), str(reset_token)):
        return {
            "ok": False,
            "error": "token_mismatch",
            "world": record,
        }
    # Wipe the world + related buckets.
    _audit(world_id, "reset_confirmed")
    new_record = _default_record(
        name=record.get("name", "Untitled"),
        world_doc_path=record.get("world_doc_path", ""),
        config_path=record.get("config_path", ""),
        state_doc_path=record.get("state_doc_path", ""),
    )
    new_record["id"] = world_id
    new_record["created_at"] = record.get("created_at", timestamp)
    new_record["updated_at"] = timestamp
    new_record["last_active_at"] = record.get("last_active_at")
    new_record["is_active"] = record.get("is_active", True)
    state.worlds[world_id] = new_record
    # Wipe NPCs + log + env + compute + audit (audit is append-only,
    # we keep it).  Order matters: clear NPCs first so the
    # scheduling-log trim doesn't keep the world_id alive.
    from xijian_api.stubs import npcs as npcs_stub
    from xijian_api.stubs import world_environment as env_stub
    from xijian_api.stubs import world_compute_config as wcc_stub
    npcs_in_world = [nid for nid, n in state.npcs.items() if n.get("world_id") == world_id]
    for npc_id in npcs_in_world:
        npcs_stub.delete(npc_id)
    # Drop the npc_scheduling_log entries for this world.
    for log_id in list(state.npc_scheduling_log.keys()):
        if state.npc_scheduling_log[log_id].get("world_id") == world_id:
            state.npc_scheduling_log.pop(log_id, None)
    env_stub.delete(world_id)
    wcc_stub.reset_for_testing()  # we re-materialize below
    state.world_compute_config.pop(world_id, None)
    # Re-seed defaults.
    env_stub.ensure_environment(world_id)
    wcc_stub.get(world_id)
    _reset_tokens.pop(world_id, None)
    _audit(world_id, "reset_finalized", actor="system", payload={
        "wiped_npc_count": len(npcs_in_world),
    })
    return {"ok": True, "error": None, "world": new_record}


def cancel_reset(world_id: str) -> dict:
    """Drop any pending reset token for ``world_id`` (idempotent)."""
    had = _reset_tokens.pop(world_id, None) is not None
    return {"cancelled": had}


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def summary() -> dict:
    """Return a JSON-friendly overview of every world."""
    out: list[dict] = []
    for record in state.worlds.values():
        out.append({
            "id": record.get("id"),
            "name": record.get("name"),
            "is_active": record.get("is_active"),
            "last_active_at": record.get("last_active_at"),
            "updated_at": record.get("updated_at"),
        })
    return {
        "worlds_total": len(out),
        "worlds_active": sum(1 for w in out if w.get("is_active")),
        "worlds": out,
    }


# ---------------------------------------------------------------------------
# Legacy aliases (pre-A4.2 compat — see module docstring)
# ---------------------------------------------------------------------------


def transition(world_id: str, payload: dict) -> dict | None:
    """Legacy API — was a location transition.  Now delegates to the
    A4.3 ambient logger (records a transit log entry into the
    world audit log) and returns the current world record.

    Tests from the pre-A4.2 era use this; we keep the signature
    so they don't have to update.
    """
    record = state.worlds.get(world_id)
    if record is None:
        return None
    to_location = payload.get("to_location", record.get("last_transport"))
    transport = payload.get("transport", "unknown")
    _audit(world_id, "transition", payload={
        "to_location": to_location,
        "transport": transport,
    })
    record["last_transport"] = transport
    record["last_transition_at"] = now_ts()
    record["updated_at"] = record["last_transition_at"]
    return record


def add_event(world_id: str, payload: dict) -> dict | None:
    """Legacy API — was a per-world event log.  Now records into
    :mod:`xijian_api.stubs.events` as a custom event (the canonical
    A4.1 path) and returns the event instance.

    Tests from the pre-A4.2 era use this; we keep the signature
    so they don't have to update.
    """
    record = state.worlds.get(world_id)
    if record is None:
        return None
    # Defer import to avoid a circular dep at module load.
    from xijian_api.stubs import events as events_stub
    event = events_stub.create_event(
        world_id=world_id,
        kind=events_stub.KIND_CUSTOM,
        name=payload.get("name", payload.get("kind", "custom_event")),
        description=payload.get("description", ""),
        trigger_config={"type": "interval", "seconds": 3600},
        scene_ref_id=payload.get("scene_ref_id"),
        priority=int(payload.get("priority", 0)),
        is_enabled=bool(payload.get("is_enabled", True)),
    )
    instance = events_stub.fire_event(event["id"])
    return instance


# ---------------------------------------------------------------------------
# Seed / reset
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  Seeds the ``world_modern_tokyo`` demo
    world so dev / tests have a known handle.

    Production may want to disable this — operators create their
    own worlds through the route layer.
    """
    if DEFAULT_WORLD_ID in state.worlds:
        return
    create(
        world_id=DEFAULT_WORLD_ID,
        name="Modern Tokyo",
        world_doc_path="worlds/modern_tokyo/lore.md",
        config_path="worlds/modern_tokyo/config.json",
        state_doc_path="worlds/modern_tokyo/state.json",
    )


def reset_for_testing() -> None:
    """Wipe every world and clear the reset-token store."""
    state.worlds.clear()
    _reset_tokens.clear()


__all__ = [
    # Constants
    "DEFAULT_WORLD_ID", "WHITELISTED_STATE_FIELDS", "RESET_TOKEN_TTL_SECONDS",
    # Errors
    "WorldError",
    # CRUD
    "create", "get", "list_all", "update", "delete",
    # Active
    "switch_active",
    # State & views
    "get_state", "update_state", "patch_state_doc", "summary",
    # Reset
    "preview_reset", "confirm_reset", "cancel_reset",
    # Legacy aliases (pre-A4.2)
    "transition", "add_event",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
