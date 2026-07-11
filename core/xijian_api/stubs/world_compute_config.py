"""Stub world-compute-config service — A4.2 in the function list v2.

The per-world compute config captures two things:

* the active tier (``high_active`` or ``low_active``) — the *intended*
  set the world manager tries to maintain.  AC-6 frames it as a
  binary choice the user flips at runtime.
* the budget caps that follow from that tier: 3 active NPCs at
  ~16.6k tokens/min each (high) or 10 at ~5k tokens/min (low).  The
  world total is locked at 50 000 tokens/min per v2.1.

The bucket is ``state.world_compute_config`` (``{world_id: dict}``)
and a default config is materialized lazily on first read so the
route layer never has to special-case "first request after world
creation".

CRUD surface
============

* :func:`get` — return the config (auto-materialize a default if
  the world exists but has no config yet).
* :func:`set_tier` — flip between ``high_active`` and ``low_active``.
  Side effect: writes a ``world_audit_log`` entry (action ``tier_change``).
* :func:`update` — patch mutable fields (caps, total budget).
* :func:`set_for_world` — full replace of the config (used by
  world-reset to restore defaults).
* :func:`summary` — JSON-friendly view that the route layer
  serves verbatim.
* :func:`seed_default` / :func:`reset_for_testing`.

The "active tier" the spec calls out is the *intended* tier.  The
*actual* number of high_active / low_active NPCs is what the NPC
scheduler maintains, and the world manager cross-checks them at
each :func:`xijian_api.stubs.npcs.tick_world` call.
"""

from __future__ import annotations

import logging
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.world_compute_config")


# ---------------------------------------------------------------------------
# Constants — locked by v2.1 of the function list
# ---------------------------------------------------------------------------

#: Default active tier.  ``low_active`` matches the A4.2 spec's "日常
#: 推进 / 长周期事件" default — high_active is opt-in for hot scenes.
DEFAULT_ACTIVE_TIER = "low_active"

#: Hard cap on total NPCs in a world.  AC-5.
DEFAULT_MAX_NPCS = 50

#: Hard cap on high_active NPCs in a world.  AC-6.
DEFAULT_MAX_ACTIVE_NPCS = 3

#: Hard cap on low_active NPCs in a world.  AC-6 (low_active variant).
DEFAULT_MAX_LOW_ACTIVE_NPCS = 10

#: Default world total budget (tokens/min).  v2.1 lock.
DEFAULT_TOTAL_TOKEN_BUDGET = 50_000

#: Valid active tiers.  ``idle`` is *not* a "config" tier — it's a
#: per-NPC demotion state that the scheduler controls.  Spec AC-6
#: explicitly says "high_active=3 or low_active=10, 二选一".
VALID_ACTIVE_TIERS: frozenset[str] = frozenset({"high_active", "low_active"})


class ComputeConfigError(ValueError):
    """Raised on validation errors (unknown tier, negative budget, etc)."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _validate_tier(tier: str) -> None:
    if tier not in VALID_ACTIVE_TIERS:
        raise ComputeConfigError(
            f"active_tier must be one of {sorted(VALID_ACTIVE_TIERS)!r}, got {tier!r}"
        )


def _validate_budget(total: int) -> None:
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        raise ComputeConfigError(
            f"total_token_budget must be a number, got {type(total).__name__}"
        )
    if int(total) <= 0:
        raise ComputeConfigError("total_token_budget must be > 0")
    if int(total) > 1_000_000:
        # Sanity cap — anything above 1M tokens/min is almost
        # certainly a misconfiguration.  Operators can override
        # later by editing state directly if they really need to.
        raise ComputeConfigError(
            f"total_token_budget {int(total)} is implausibly high (>1M tokens/min)"
        )


def _validate_cap(cap: int) -> None:
    if not isinstance(cap, (int, float)) or isinstance(cap, bool):
        raise ComputeConfigError(
            f"cap must be a number, got {type(cap).__name__}"
        )
    if int(cap) < 0:
        raise ComputeConfigError("cap must be >= 0")


def _default_config(world_id: str) -> dict:
    """Return the spec's default config (does not store it)."""
    return {
        "world_id": world_id,
        "active_tier": DEFAULT_ACTIVE_TIER,
        "max_npcs": DEFAULT_MAX_NPCS,
        "max_active_npcs": DEFAULT_MAX_ACTIVE_NPCS,
        "max_low_active_npcs": DEFAULT_MAX_LOW_ACTIVE_NPCS,
        "total_token_budget": DEFAULT_TOTAL_TOKEN_BUDGET,
        "updated_at": now_ts(),
    }


# ---------------------------------------------------------------------------
# Side-effecting helpers
# ---------------------------------------------------------------------------


def _record_audit(world_id: str, action: str, payload: dict[str, Any]) -> None:
    """Best-effort write to world_audit_log.  Failures stay DEBUG-only."""
    try:
        from xijian_api.stubs import world_audit as audit_stub
        audit_stub.record(
            world_id=world_id,
            action=action,
            actor="user",
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("world_compute_config audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def get(world_id: str) -> dict | None:
    """Return the config for ``world_id`` or ``None`` if the world
    itself doesn't exist.

    If the world exists but has no config, a default is materialized
    and returned.  This matches the "lazy default" pattern used by
    :mod:`xijian_api.stubs.world_environment`.
    """
    from xijian_api.stubs import worlds as worlds_stub
    if worlds_stub.get(world_id) is None:
        return None
    cfg = state.world_compute_config.get(world_id)
    if cfg is None:
        cfg = _default_config(world_id)
        state.world_compute_config[world_id] = cfg
    return cfg


def set_tier(world_id: str, tier: str) -> dict | None:
    """Flip a world's active tier.

    Side effects:

    * updates the config (active_tier, updated_at).
    * writes a ``world_audit_log`` entry (action ``tier_change``).
    * bumps the per-tier cap to match: high_active=3, low_active=10.

    Returns the updated config or ``None`` if the world doesn't
    exist.
    """
    _validate_tier(tier)
    from xijian_api.stubs import worlds as worlds_stub
    if worlds_stub.get(world_id) is None:
        return None
    cfg = get(world_id)  # materializes default if needed
    if cfg is None:
        return None
    old_tier = cfg.get("active_tier")
    cfg["active_tier"] = tier
    if tier == "high_active":
        cfg["max_active_npcs"] = DEFAULT_MAX_ACTIVE_NPCS
    else:
        cfg["max_low_active_npcs"] = DEFAULT_MAX_LOW_ACTIVE_NPCS
    cfg["updated_at"] = now_ts()
    _record_audit(
        world_id,
        "tier_change",
        {"from_tier": old_tier, "to_tier": tier},
    )
    return cfg


def update(world_id: str, patch: dict) -> dict | None:
    """Patch mutable fields on a world's compute config.

    Mutable: ``max_npcs`` (1..50), ``max_active_npcs`` (>=0),
    ``max_low_active_npcs`` (>=0), ``total_token_budget`` (>0).
    Setting ``active_tier`` here is allowed but goes through
    :func:`set_tier` semantics (audit + cap bump) so callers should
    prefer that helper.

    Returns the updated config or ``None`` if the world doesn't
    exist.
    """
    from xijian_api.stubs import worlds as worlds_stub
    if worlds_stub.get(world_id) is None:
        return None
    cfg = get(world_id)  # materializes default if needed
    if cfg is None:
        return None
    if "active_tier" in patch:
        # Funnel through set_tier so the audit + cap bump happens.
        set_tier(world_id, patch["active_tier"])
    if "max_npcs" in patch:
        _validate_cap(patch["max_npcs"])
        cap = int(patch["max_npcs"])
        if not (1 <= cap <= 50):
            raise ComputeConfigError(
                f"max_npcs must be in [1, 50], got {cap}"
            )
        cfg["max_npcs"] = cap
    if "max_active_npcs" in patch:
        _validate_cap(patch["max_active_npcs"])
        cfg["max_active_npcs"] = int(patch["max_active_npcs"])
    if "max_low_active_npcs" in patch:
        _validate_cap(patch["max_low_active_npcs"])
        cfg["max_low_active_npcs"] = int(patch["max_low_active_npcs"])
    if "total_token_budget" in patch:
        _validate_budget(patch["total_token_budget"])
        cfg["total_token_budget"] = int(patch["total_token_budget"])
    cfg["updated_at"] = now_ts()
    return cfg


def set_for_world(
    world_id: str,
    *,
    active_tier: str | None = None,
    max_npcs: int | None = None,
    max_active_npcs: int | None = None,
    max_low_active_npcs: int | None = None,
    total_token_budget: int | None = None,
) -> dict | None:
    """Replace the config for ``world_id`` with the supplied overrides.

    Unspecified fields keep their current values (or the spec
    defaults if no config exists yet).  Used by world-reset to
    restore defaults.
    """
    from xijian_api.stubs import worlds as worlds_stub
    if worlds_stub.get(world_id) is None:
        return None
    cfg = get(world_id) or _default_config(world_id)
    if active_tier is not None:
        _validate_tier(active_tier)
        cfg["active_tier"] = active_tier
        if active_tier == "high_active":
            cfg["max_active_npcs"] = DEFAULT_MAX_ACTIVE_NPCS
        else:
            cfg["max_low_active_npcs"] = DEFAULT_MAX_LOW_ACTIVE_NPCS
    if max_npcs is not None:
        _validate_cap(max_npcs)
        if not (1 <= int(max_npcs) <= 50):
            raise ComputeConfigError(
                f"max_npcs must be in [1, 50], got {int(max_npcs)}"
            )
        cfg["max_npcs"] = int(max_npcs)
    if max_active_npcs is not None:
        _validate_cap(max_active_npcs)
        cfg["max_active_npcs"] = int(max_active_npcs)
    if max_low_active_npcs is not None:
        _validate_cap(max_low_active_npcs)
        cfg["max_low_active_npcs"] = int(max_low_active_npcs)
    if total_token_budget is not None:
        _validate_budget(total_token_budget)
        cfg["total_token_budget"] = int(total_token_budget)
    cfg["updated_at"] = now_ts()
    state.world_compute_config[world_id] = cfg
    return cfg


def summary(world_id: str) -> dict | None:
    """Return a JSON-friendly view of the config, or ``None``."""
    cfg = get(world_id)
    if cfg is None:
        return None
    return {
        "world_id": world_id,
        "active_tier": cfg.get("active_tier"),
        "max_npcs": cfg.get("max_npcs"),
        "max_active_npcs": cfg.get("max_active_npcs"),
        "max_low_active_npcs": cfg.get("max_low_active_npcs"),
        "total_token_budget": cfg.get("total_token_budget"),
        "updated_at": cfg.get("updated_at"),
    }


def reset_for_testing() -> None:
    """Wipe the bucket.  Used by conftest."""
    state.world_compute_config.clear()


__all__ = [
    # Constants
    "DEFAULT_ACTIVE_TIER",
    "DEFAULT_MAX_NPCS",
    "DEFAULT_MAX_ACTIVE_NPCS",
    "DEFAULT_MAX_LOW_ACTIVE_NPCS",
    "DEFAULT_TOTAL_TOKEN_BUDGET",
    "VALID_ACTIVE_TIERS",
    "ComputeConfigError",
    # CRUD
    "get", "set_tier", "update", "set_for_world", "summary",
    # Lifecycle
    "reset_for_testing",
]
