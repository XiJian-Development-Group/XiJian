"""Stub transaction log — A4.4 in the function list v2.

Every money movement writes one transaction record.  This is the
audit log spec AC-1 requires ("所有资金变动必须写入 transactions
表") — and the orchestrator :mod:`xijian_api.stubs.economy` is the
*only* path that creates a transaction; wallet helpers don't write
to this table on their own.

Data model (mirrors §A4.4 SQL schema)
======================================

* ``id``             — ``txn_<12 hex>`` (PK)
* ``world_id``       — owning world
* ``from_kind``      — ``"user"`` or ``"npc"``
* ``from_id``        — sender's id
* ``to_kind``        — ``"user"`` or ``"npc"``
* ``to_id``          — receiver's id
* ``currency_code``  — currency code
* ``amount``         — positive float (the sign is conveyed by
                        from/to, not by the amount)
* ``kind``           — one of ``"purchase"`` / ``"sale"`` /
                        ``"theft"`` / ``"scam"`` / ``"reward"`` /
                        ``"transfer"``.  Forward-compat — unknown
                        kinds are accepted but the route layer
                        validates on known ones.
* ``ref_id``         — optional handle pointing to the originating
                        event / scene interaction / NPC decision
                        (e.g. ``npcsched_<hex>`` from the
                        A4.2 scheduler, ``sint_<hex>`` from A4.3,
                        ``evinst_<hex>`` from A4.1).
* ``created_at``     — unix timestamp (seconds, float)

CRUD surface
============

* :func:`record` — append a transaction.  Returns the record.
* :func:`get` / :func:`list_for_world` / :func:`list_for_owner` /
  :func:`list_for_kind` — read paths.
* :func:`summary` — JSON-friendly aggregate for the dashboard.
* :func:`seed_default` / :func:`reset_for_testing`.

FIFO cap
========

The in-memory log is bounded per-world by
:data:`TXN_KEEP_PER_WORLD` to keep the stub from growing
without limit.  A real implementation would archive to disk
(see cross-link notes).
"""

from __future__ import annotations

import logging
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_transaction_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.transactions")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Transaction kinds.  Forward-compat: unknown kinds are accepted
#: at the stub level (audit must not block the action) but the
#: route layer validates against this set.
KIND_PURCHASE = "purchase"
KIND_SALE = "sale"
KIND_THEFT = "theft"
KIND_SCAM = "scam"
KIND_REWARD = "reward"
KIND_TRANSFER = "transfer"
KIND_FINE = "fine"
KIND_REPAIR = "repair"

VALID_KINDS: frozenset[str] = frozenset({
    KIND_PURCHASE, KIND_SALE, KIND_THEFT, KIND_SCAM, KIND_REWARD,
    KIND_TRANSFER, KIND_FINE, KIND_REPAIR,
})

#: FIFO cap per world.
TXN_KEEP_PER_WORLD = 5_000

#: Maximum ``amount`` per single transaction.  Sanity bound —
#: anything larger is almost certainly a typo.
MAX_TXN_AMOUNT = 1_000_000_000.0

#: Sender / receiver kind values.  Forward-compat: the stub
#: accepts other strings (the future may introduce new
#: participants), but the route layer validates on this set.
KIND_USER = "user"
KIND_NPC = "npc"
VALID_PARTY_KINDS: frozenset[str] = frozenset({KIND_USER, KIND_NPC})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TransactionError(ValueError):
    """Raised on validation errors."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _validate_party_kind(kind: Any) -> str:
    if not isinstance(kind, str) or not kind:
        raise TransactionError("party kind is required")
    return kind


def _validate_party_id(party_id: Any) -> str:
    if not isinstance(party_id, str) or not party_id:
        raise TransactionError("party id is required")
    return party_id


def _validate_world_id(world_id: Any) -> str:
    if not isinstance(world_id, str) or not world_id:
        raise TransactionError("world_id is required")
    return world_id


def _validate_currency_code(code: Any) -> str:
    if not isinstance(code, str) or not code:
        raise TransactionError("currency_code is required")
    return code


def _validate_amount(amount: Any) -> float:
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise TransactionError(
            "amount must be a number, got %s" % type(amount).__name__
        )
    if amount <= 0:
        raise TransactionError("amount must be > 0")
    if amount > MAX_TXN_AMOUNT:
        raise TransactionError(
            "amount %g exceeds the per-transaction cap %g"
            % (amount, MAX_TXN_AMOUNT)
        )
    return round(float(amount), 6)


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


# ---------------------------------------------------------------------------
# FIFO trim
# ---------------------------------------------------------------------------


def _trim_for_world(world_id: str) -> None:
    """Bound the per-world transaction log FIFO-style."""
    bucket = [
        t for t in state.transactions.values()
        if t.get("world_id") == world_id
    ]
    if len(bucket) <= TXN_KEEP_PER_WORLD:
        return
    bucket.sort(key=lambda t: t.get("created_at", 0.0))
    for entry in bucket[: len(bucket) - TXN_KEEP_PER_WORLD]:
        state.transactions.pop(entry["id"], None)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def record(
    *,
    world_id: str,
    from_kind: str,
    from_id: str,
    to_kind: str,
    to_id: str,
    currency_code: str,
    amount: float,
    kind: str,
    ref_id: str | None = None,
    transaction_id: str | None = None,
    now: float | None = None,
) -> dict:
    """Append a transaction.  Returns the stored record.

    Pure write — does *not* touch wallet balances.  The
    :mod:`xijian_api.stubs.economy` orchestrator is the single
    point that calls :func:`record` *and* mutates wallets, so the
    audit trail is consistent.
    """
    _validate_world_id(world_id)
    _validate_party_kind(from_kind)
    _validate_party_id(from_id)
    _validate_party_kind(to_kind)
    _validate_party_id(to_id)
    _validate_currency_code(currency_code)
    amt = _validate_amount(amount)
    if not isinstance(kind, str) or not kind:
        raise TransactionError("kind is required")
    new_id = transaction_id or gen_transaction_id()
    if new_id in state.transactions:
        raise TransactionError("transaction id %r already exists" % new_id)
    timestamp = _now_or(now)
    record_obj = {
        "id": new_id,
        "world_id": world_id,
        "from_kind": from_kind,
        "from_id": from_id,
        "to_kind": to_kind,
        "to_id": to_id,
        "currency_code": currency_code,
        "amount": amt,
        "kind": kind,
        "ref_id": ref_id,
        "created_at": timestamp,
    }
    state.transactions[new_id] = record_obj
    _trim_for_world(world_id)
    return record_obj


def get(transaction_id: str) -> dict | None:
    """Return the transaction record or ``None``."""
    return state.transactions.get(transaction_id)


def list_for_world(
    world_id: str,
    *,
    kind: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return transactions for a world, newest first."""
    out = [
        t for t in state.transactions.values()
        if t.get("world_id") == world_id
        and (kind is None or t.get("kind") == kind)
    ]
    out.sort(key=lambda t: t.get("created_at", 0.0), reverse=True)
    if limit < 1:
        limit = 1
    return out[:limit]


def list_for_owner(
    owner_kind: str,
    owner_id: str,
    *,
    limit: int = 50,
) -> list[dict]:
    """Return transactions where the owner is either sender or receiver."""
    out = [
        t for t in state.transactions.values()
        if (
            (t.get("from_kind") == owner_kind and t.get("from_id") == owner_id)
            or (t.get("to_kind") == owner_kind and t.get("to_id") == owner_id)
        )
    ]
    out.sort(key=lambda t: t.get("created_at", 0.0), reverse=True)
    if limit < 1:
        limit = 1
    return out[:limit]


def list_for_kind(kind: str, *, limit: int = 50) -> list[dict]:
    """Return transactions of a specific kind (newest first).  Used
    by the dashboard's "all thefts" / "all rewards" tabs."""
    out = [
        t for t in state.transactions.values()
        if t.get("kind") == kind
    ]
    out.sort(key=lambda t: t.get("created_at", 0.0), reverse=True)
    if limit < 1:
        limit = 1
    return out[:limit]


def list_all(*, limit: int = 50) -> list[dict]:
    out = list(state.transactions.values())
    out.sort(key=lambda t: t.get("created_at", 0.0), reverse=True)
    if limit < 1:
        limit = 1
    return out[:limit]


def count_for_world(world_id: str) -> int:
    return sum(
        1 for t in state.transactions.values()
        if t.get("world_id") == world_id
    )


def summary(world_id: str | None = None) -> dict:
    """Return a JSON-friendly aggregate view."""
    items = list(state.transactions.values())
    if world_id is not None:
        items = [t for t in items if t.get("world_id") == world_id]
    by_kind: dict[str, int] = {}
    total_volume = 0.0
    for t in items:
        by_kind[t.get("kind", "?")] = by_kind.get(t.get("kind", "?"), 0) + 1
        total_volume += float(t.get("amount", 0.0))
    out: dict[str, Any] = {
        "total": len(items),
        "total_volume": round(total_volume, 6),
        "by_kind": by_kind,
    }
    if world_id is not None:
        out["world_id"] = world_id
    return out


# ---------------------------------------------------------------------------
# Cascading deletes
# ---------------------------------------------------------------------------


def delete_for_world(world_id: str) -> int:
    """Drop every transaction in a world.  Called by the worlds
    reset flow."""
    txn_ids = [
        t["id"] for t in state.transactions.values()
        if t.get("world_id") == world_id
    ]
    for tid in txn_ids:
        state.transactions.pop(tid, None)
    return len(txn_ids)


def delete_for_owner(owner_kind: str, owner_id: str) -> int:
    """Drop every transaction involving a specific owner."""
    txn_ids = [
        t["id"] for t in state.transactions.values()
        if (t.get("from_kind") == owner_kind and t.get("from_id") == owner_id)
        or (t.get("to_kind") == owner_kind and t.get("to_id") == owner_id)
    ]
    for tid in txn_ids:
        state.transactions.pop(tid, None)
    return len(txn_ids)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  No default transactions — they
    come from the orchestrator at runtime."""
    return None


def reset_for_testing() -> None:
    state.transactions.clear()


__all__ = [
    # Constants
    "KIND_PURCHASE", "KIND_SALE", "KIND_THEFT", "KIND_SCAM",
    "KIND_REWARD", "KIND_TRANSFER", "KIND_FINE", "KIND_REPAIR",
    "VALID_KINDS",
    "KIND_USER", "KIND_NPC", "VALID_PARTY_KINDS",
    "TXN_KEEP_PER_WORLD", "MAX_TXN_AMOUNT",
    # Errors
    "TransactionError",
    # Pure helpers
    "_validate_party_kind", "_validate_party_id", "_validate_world_id",
    "_validate_currency_code", "_validate_amount",
    # CRUD
    "record", "get",
    "list_for_world", "list_for_owner", "list_for_kind", "list_all",
    "count_for_world", "summary",
    # Cascading
    "delete_for_world", "delete_for_owner",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
