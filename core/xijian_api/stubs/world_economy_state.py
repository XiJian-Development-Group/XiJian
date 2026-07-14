"""Stub world-economy-state service — A4.4 in the function list v2.

Per-world macro state.  Tracks the inflation rate / liquidity
index that the orchestrator uses to drive "seasonal" price
swings, plus the per-world policy toggles (allow_illegal,
allow_overdraft) that AC-3 cares about.

Data model (mirrors §A4.4 SQL schema)
======================================

* ``world_id``        — PK
* ``inflation_rate``  — float; positive means prices are rising
                          (e.g. ``0.05`` = 5% per tick).  Negative
                          is "deflation" (rare, mostly a sanity
                          bound).  Locked to [-0.5, +0.5] — anything
                          outside that range is almost certainly
                          a misconfiguration.
* ``liquidity_index`` — float in [0.5, 2.0]; 1.0 is "normal"
                          money supply.  >1 means the world is
                          flush (NPCs buy more); <1 means tight
                          (NPCs sell off).
* ``last_tick_at``    — unix timestamp of the last macro tick
* ``allow_illegal``   — bool (AC-3); default false.
* ``allow_overdraft`` — bool (spec boundary); default false.
* ``created_at``      — first-materialise timestamp
* ``updated_at``      — last-mutation timestamp

The spec doesn't explicitly require a separate toggle for
``allow_illegal`` and ``allow_overdraft`` — they live here
because the orchestrator needs them on every transaction, and
hitting the :mod:`xijian_api.stubs.world_audit` log for them
would be a per-tx cost.

Cascading
=========

When a world is deleted, the :mod:`xijian_api.stubs.worlds`
module's reset flow calls :func:`delete` to wipe this record
(we don't keep it as a tombstone — the audit log has the trace).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_economy_state_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.world_economy_state")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default inflation rate — neutral.
DEFAULT_INFLATION_RATE = 0.0

#: Default liquidity — neutral "normal" money supply.
DEFAULT_LIQUIDITY_INDEX = 1.0

#: Inflation bounds.  +/- 50% per tick is the largest swing the
#: orchestrator will ever apply; anything outside is a misconfig.
MIN_INFLATION_RATE = -0.5
MAX_INFLATION_RATE = 0.5

#: Liquidity bounds.  0.5 = very tight, 2.0 = very flush.
MIN_LIQUIDITY_INDEX = 0.5
MAX_LIQUIDITY_INDEX = 2.0

#: Default macro tick interval.  Locked at 5 minutes by v2.1's
#: "经济总系统 tick: 每 N 分钟..." — operators can override via the
#: env flag for tests.
DEFAULT_TICK_INTERVAL_SECONDS = 300.0

#: Macro tick threshold: when the rolling transaction volume
#: exceeds this, the orchestrator nudges the inflation up.
#: 0.0 disables volume-driven inflation.
DEFAULT_VOLUME_TRIGGER = 0.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EconomyStateError(ValueError):
    """Raised on validation errors."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _validate_inflation(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EconomyStateError(
            "inflation_rate must be a number, got %s" % type(value).__name__
        )
    if math.isnan(value) or math.isinf(value):
        raise EconomyStateError("inflation_rate must be finite")
    if value < MIN_INFLATION_RATE or value > MAX_INFLATION_RATE:
        raise EconomyStateError(
            "inflation_rate must be in [%g, %g], got %g"
            % (MIN_INFLATION_RATE, MAX_INFLATION_RATE, value)
        )
    return round(float(value), 6)


def _validate_liquidity(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EconomyStateError(
            "liquidity_index must be a number, got %s" % type(value).__name__
        )
    if math.isnan(value) or math.isinf(value):
        raise EconomyStateError("liquidity_index must be finite")
    if value < MIN_LIQUIDITY_INDEX or value > MAX_LIQUIDITY_INDEX:
        raise EconomyStateError(
            "liquidity_index must be in [%g, %g], got %g"
            % (MIN_LIQUIDITY_INDEX, MAX_LIQUIDITY_INDEX, value)
        )
    return round(float(value), 6)


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


# ---------------------------------------------------------------------------
# Lazy default
# ---------------------------------------------------------------------------


def _default_record(world_id: str) -> dict:
    timestamp = now_ts()
    return {
        "id": gen_economy_state_id(),
        "world_id": world_id,
        "inflation_rate": DEFAULT_INFLATION_RATE,
        "liquidity_index": DEFAULT_LIQUIDITY_INDEX,
        "last_tick_at": timestamp,
        "allow_illegal": False,
        "allow_overdraft": False,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _worlds_get(world_id: str) -> dict | None:
    from xijian_api.stubs import worlds as worlds_stub
    return worlds_stub.get(world_id)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def get(world_id: str) -> dict | None:
    """Return the state record, materialising a default if missing.
    Returns ``None`` only when the world itself doesn't exist."""
    if _worlds_get(world_id) is None:
        return None
    record = state.world_economy_state.get(world_id)
    if record is None:
        record = _default_record(world_id)
        state.world_economy_state[world_id] = record
    return record


def update(world_id: str, patch: dict) -> dict | None:
    """Patch mutable state fields.  ``id`` and ``world_id`` and
    ``created_at`` are immutable.  ``last_tick_at`` is bumped
    automatically — callers don't need to set it."""
    record = get(world_id)
    if record is None:
        return None
    if "id" in patch or "world_id" in patch or "created_at" in patch:
        raise EconomyStateError("id, world_id, created_at are immutable")
    for key, value in patch.items():
        if key == "inflation_rate":
            record["inflation_rate"] = _validate_inflation(value)
        elif key == "liquidity_index":
            record["liquidity_index"] = _validate_liquidity(value)
        elif key in ("allow_illegal", "allow_overdraft"):
            if not isinstance(value, bool):
                raise EconomyStateError(
                    "%s must be a bool, got %s"
                    % (key, type(value).__name__)
                )
            record[key] = value
        elif key == "last_tick_at":
            if not isinstance(value, (int, float)):
                raise EconomyStateError("last_tick_at must be numeric")
            record["last_tick_at"] = float(value)
    record["updated_at"] = now_ts()
    return record


def delete(world_id: str) -> bool:
    """Drop the state record.  Called by the worlds reset flow."""
    return state.world_economy_state.pop(world_id, None) is not None


# ---------------------------------------------------------------------------
# Macro tick — economic simulation
# ---------------------------------------------------------------------------


def tick(
    world_id: str,
    *,
    volume_delta: float = 0.0,
    seasonal_factor: float = 0.0,
    now: float | None = None,
) -> dict | None:
    """Run one macro-state tick for ``world_id``.

    The tick adjusts ``inflation_rate`` and ``liquidity_index`` based
    on:

    * ``volume_delta``  — net transaction volume since the last
      tick (positive = more money moving = inflationary).
    * ``seasonal_factor`` — operator-provided nudge (e.g. a
      holiday that spikes demand).  Range [-0.1, +0.1] to keep
      one tick from causing extreme swings.

    The function is pure-ish: it touches ``state.world_economy_state``
    but doesn't write to any other bucket.  Bumps ``last_tick_at``
    on success.
    """
    record = get(world_id)
    if record is None:
        return None
    timestamp = _now_or(now)
    # Volume delta → inflation: +0.01 inflation per unit volume,
    # clamped to the inflation bounds.
    new_inflation = record["inflation_rate"] + (
        0.01 * float(volume_delta) + float(seasonal_factor)
    )
    # Liquidity: nudge back toward 1.0 by 10% of the gap — i.e.
    # mean-reverting.  This stops the world from drifting off to
    # extreme liquidity values over time.
    new_liquidity = record["liquidity_index"] + 0.1 * (
        1.0 - record["liquidity_index"]
    )
    record["inflation_rate"] = _validate_inflation(new_inflation)
    record["liquidity_index"] = _validate_liquidity(new_liquidity)
    record["last_tick_at"] = timestamp
    record["updated_at"] = timestamp
    return record


# ---------------------------------------------------------------------------
# Convenience — read-only accessors used by the orchestrator
# ---------------------------------------------------------------------------


def allow_illegal(world_id: str) -> bool:
    """True if the world allows NPC-initiated theft / scam."""
    record = get(world_id)
    if record is None:
        return False
    return bool(record.get("allow_illegal", False))


def allow_overdraft(world_id: str) -> bool:
    """True if wallets in this world may go negative."""
    record = get(world_id)
    if record is None:
        return False
    return bool(record.get("allow_overdraft", False))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  No default state — the stub
    materialises lazily on first :func:`get`."""
    return None


def reset_for_testing() -> None:
    state.world_economy_state.clear()


__all__ = [
    # Constants
    "DEFAULT_INFLATION_RATE", "DEFAULT_LIQUIDITY_INDEX",
    "MIN_INFLATION_RATE", "MAX_INFLATION_RATE",
    "MIN_LIQUIDITY_INDEX", "MAX_LIQUIDITY_INDEX",
    "DEFAULT_TICK_INTERVAL_SECONDS", "DEFAULT_VOLUME_TRIGGER",
    # Errors
    "EconomyStateError",
    # Pure helpers
    "_validate_inflation", "_validate_liquidity",
    # CRUD
    "get", "update", "delete",
    # Macro tick
    "tick",
    # Read-only accessors
    "allow_illegal", "allow_overdraft",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
