"""Stub wallet service — A4.4 in the function list v2.

A "wallet" is the per-(owner, world, currency) balance sheet.
The owner is either the user (``owner_kind='user'``) or an NPC
(``owner_kind='npc'``); ``owner_id`` is the corresponding id.

Data model (mirrors §A4.4 SQL schema)
======================================

Composite key ``(owner_kind, owner_id, world_id, currency_code)`` —
matches the SQL PRIMARY KEY constraint.

* ``id``            — ``wlt_<12 hex>`` (internal handle)
* ``owner_kind``    — ``"user"`` or ``"npc"``
* ``owner_id``      — for ``user`` this is the user id
                        (e.g. ``"user_local"``); for ``npc`` this is
                        the NPC id (``npc_<12 hex>``).
* ``world_id``      — owning world
* ``currency_code`` — currency code (FK to ``world_currencies``)
* ``balance``       — float; may go negative only when
                        ``world_economy_state.allow_overdraft`` is
                        true for the world (per spec boundary
                        scenario).

Validation
==========

* ``amount`` (deposit / withdraw) must be a non-negative number.
  The sign comes from the operation, not the argument — keeps the
  public API from accepting ``amount=-50`` as a deposit.
* Negative balances are blocked by default; pass
  ``allow_overdraft=True`` (or set the world's
  ``allow_overdraft`` to true) to permit.
* ``owner_kind`` is locked to ``{"user", "npc"}`` — anything else
  is rejected with a 400 in the route layer.

Cascading impact
================

* When a currency is deleted (with ``cascade=True``), the
  :mod:`xijian_api.stubs.world_currencies` module wipes matching
  wallet rows directly via the ``state.wallets`` dict.
* When a world is deleted, the route layer asks us to clean up
  via :func:`delete_for_world`.
* When an NPC is deleted, the route layer asks us to clean up
  via :func:`delete_for_owner`.

Test surface
============

* :func:`get` / :func:`list_for_owner` / :func:`list_for_world` / :func:`list_all`
* :func:`ensure_wallet` / :func:`deposit` / :func:`withdraw` / :func:`transfer`
* :func:`delete` / :func:`delete_for_world` / :func:`delete_for_owner`
* :func:`seed_default` / :func:`reset_for_testing`
"""

from __future__ import annotations

import logging
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_wallet_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.wallets")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OWNER_USER = "user"
OWNER_NPC = "npc"
VALID_OWNER_KINDS: frozenset[str] = frozenset({OWNER_USER, OWNER_NPC})

#: Default per-currency starting balance for newly-created wallets.
#: 0 — operators seed via ``deposit`` (e.g. an opening grant).
DEFAULT_BALANCE = 0.0

#: Cap on a single deposit/withdraw.  Sanity bound to catch
#: operator typos; the real ceiling is the per-world inflation
#: guard in :mod:`world_economy_state`.
MAX_SINGLE_AMOUNT = 1_000_000_000.0

#: Sentinel id for the local user when no real auth is in play.
#: Tests / orchestrator code use this when they need to stand in
#: for the user wallet.
LOCAL_USER_ID = "user_local"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WalletError(ValueError):
    """Raised on validation / balance / lifecycle errors."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _validate_owner_kind(kind: Any) -> str:
    if kind not in VALID_OWNER_KINDS:
        raise WalletError(
            "owner_kind must be one of %s, got %r"
            % (sorted(VALID_OWNER_KINDS), kind)
        )
    return kind


def _validate_owner_id(owner_id: Any) -> str:
    if not isinstance(owner_id, str) or not owner_id:
        raise WalletError("owner_id is required")
    return owner_id


def _validate_world_id(world_id: Any) -> str:
    if not isinstance(world_id, str) or not world_id:
        raise WalletError("world_id is required")
    return world_id


def _validate_currency_code(code: Any) -> str:
    if not isinstance(code, str) or not code:
        raise WalletError("currency_code is required")
    return code


def _validate_amount(amount: Any) -> float:
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise WalletError(
            "amount must be a number, got %s" % type(amount).__name__
        )
    if amount < 0:
        raise WalletError("amount must be >= 0")
    if amount > MAX_SINGLE_AMOUNT:
        raise WalletError(
            "amount %g exceeds the per-call cap %g"
            % (amount, MAX_SINGLE_AMOUNT)
        )
    # Round to 6 decimals (matches currency max precision).
    return round(float(amount), 6)


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


def _key(
    owner_kind: str,
    owner_id: str,
    world_id: str,
    currency_code: str,
) -> tuple[str, str, str, str]:
    return (owner_kind, owner_id, world_id, currency_code)


# ---------------------------------------------------------------------------
# Internal — check world exists, currency exists, overdraft allowed
# ---------------------------------------------------------------------------


def _worlds_get(world_id: str) -> dict | None:
    from xijian_api.stubs import worlds as worlds_stub
    return worlds_stub.get(world_id)


def _currency_get(world_id: str, currency_code: str) -> dict | None:
    from xijian_api.stubs import world_currencies as wc_stub
    return wc_stub.get(world_id, currency_code)


def _overdraft_allowed(world_id: str) -> bool:
    """True if the world's economy state allows negative balances."""
    rec = state.world_economy_state.get(world_id)
    if rec is None:
        return False
    return bool(rec.get("allow_overdraft", False))


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def get(
    owner_kind: str,
    owner_id: str,
    world_id: str,
    currency_code: str,
) -> dict | None:
    """Return the wallet record or ``None``."""
    return state.wallets.get(
        _key(owner_kind, owner_id, world_id, currency_code)
    )


def get_by_id(wallet_id: str) -> dict | None:
    """Lookup by internal id (audit / admin tool)."""
    for record in state.wallets.values():
        if record.get("id") == wallet_id:
            return record
    return None


def list_for_owner(owner_kind: str, owner_id: str) -> list[dict]:
    """Return every wallet the owner has, across worlds and currencies."""
    out = [
        r for r in state.wallets.values()
        if r.get("owner_kind") == owner_kind
        and r.get("owner_id") == owner_id
    ]
    out.sort(key=lambda r: (str(r.get("world_id")), str(r.get("currency_code"))))
    return out


def list_for_world(world_id: str) -> list[dict]:
    """Return every wallet in a world (user + NPCs)."""
    out = [
        r for r in state.wallets.values()
        if r.get("world_id") == world_id
    ]
    out.sort(key=lambda r: (
        str(r.get("owner_kind")),
        str(r.get("owner_id")),
        str(r.get("currency_code")),
    ))
    return out


def list_all() -> list[dict]:
    return list(state.wallets.values())


def ensure_wallet(
    owner_kind: str,
    owner_id: str,
    world_id: str,
    currency_code: str,
    *,
    initial_balance: float = DEFAULT_BALANCE,
) -> dict:
    """Return the wallet, creating it with ``initial_balance`` if absent.

    Unlike ``create``, this never raises on duplicate — the use-case
    is "I want a wallet, hand me one" (e.g. when a transaction
    references a wallet that doesn't exist yet).  ``initial_balance``
    only applies on creation; subsequent calls return the existing
    record untouched.
    """
    _validate_owner_kind(owner_kind)
    _validate_owner_id(owner_id)
    _validate_world_id(world_id)
    _validate_currency_code(currency_code)
    if _worlds_get(world_id) is None:
        raise WalletError("world %r does not exist" % world_id)
    if _currency_get(world_id, currency_code) is None:
        raise WalletError(
            "currency %r does not exist in world %r"
            % (currency_code, world_id)
        )
    key = _key(owner_kind, owner_id, world_id, currency_code)
    record = state.wallets.get(key)
    if record is not None:
        return record
    if initial_balance < 0 and not _overdraft_allowed(world_id):
        raise WalletError(
            "initial_balance cannot be negative when overdraft "
            "is disabled for world %r" % world_id
        )
    record = {
        "id": gen_wallet_id(),
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "world_id": world_id,
        "currency_code": currency_code,
        "balance": round(float(initial_balance), 6),
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    state.wallets[key] = record
    return record


def create(
    owner_kind: str,
    owner_id: str,
    world_id: str,
    currency_code: str,
    *,
    initial_balance: float = DEFAULT_BALANCE,
) -> dict:
    """Create a wallet explicitly.  Raises on duplicate (use
    :func:`ensure_wallet` for an idempotent variant)."""
    _validate_owner_kind(owner_kind)
    _validate_owner_id(owner_id)
    _validate_world_id(world_id)
    _validate_currency_code(currency_code)
    if _worlds_get(world_id) is None:
        raise WalletError("world %r does not exist" % world_id)
    if _currency_get(world_id, currency_code) is None:
        raise WalletError(
            "currency %r does not exist in world %r"
            % (currency_code, world_id)
        )
    key = _key(owner_kind, owner_id, world_id, currency_code)
    if key in state.wallets:
        raise WalletError(
            "wallet for %s/%s already exists in world %r currency %s"
            % (owner_kind, owner_id, world_id, currency_code)
        )
    if initial_balance < 0 and not _overdraft_allowed(world_id):
        raise WalletError(
            "initial_balance cannot be negative when overdraft "
            "is disabled for world %r" % world_id
        )
    initial = _validate_amount(initial_balance)
    record = {
        "id": gen_wallet_id(),
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "world_id": world_id,
        "currency_code": currency_code,
        "balance": initial,
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    state.wallets[key] = record
    return record


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def deposit(
    owner_kind: str,
    owner_id: str,
    world_id: str,
    currency_code: str,
    amount: float,
    *,
    allow_create: bool = True,
) -> dict:
    """Add ``amount`` to the wallet balance.  Creates the wallet if
    missing (use ``allow_create=False`` to refuse the implicit
    create).  Returns the updated record."""
    amt = _validate_amount(amount)
    if amt == 0:
        record = state.wallets.get(_key(owner_kind, owner_id, world_id, currency_code))
        if record is not None:
            return record
        if not allow_create:
            raise WalletError("wallet does not exist and allow_create=False")
        return ensure_wallet(owner_kind, owner_id, world_id, currency_code)
    record = state.wallets.get(_key(owner_kind, owner_id, world_id, currency_code))
    if record is None:
        if not allow_create:
            raise WalletError("wallet does not exist and allow_create=False")
        record = ensure_wallet(owner_kind, owner_id, world_id, currency_code)
    record["balance"] = round(record["balance"] + amt, 6)
    record["updated_at"] = now_ts()
    return record


def withdraw(
    owner_kind: str,
    owner_id: str,
    world_id: str,
    currency_code: str,
    amount: float,
    *,
    allow_overdraft: bool | None = None,
) -> dict:
    """Subtract ``amount`` from the wallet.  Raises on insufficient
    funds unless the world's ``allow_overdraft`` is true (or the
    caller passes ``allow_overdraft=True``)."""
    amt = _validate_amount(amount)
    record = state.wallets.get(_key(owner_kind, owner_id, world_id, currency_code))
    if record is None:
        raise WalletError("wallet does not exist")
    if amt == 0:
        return record
    if allow_overdraft is None:
        allow_overdraft = _overdraft_allowed(world_id)
    new_balance = round(record["balance"] - amt, 6)
    if new_balance < 0 and not allow_overdraft:
        raise WalletError(
            "insufficient funds: balance=%g, withdraw=%g, overdraft=disabled"
            % (record["balance"], amt)
        )
    record["balance"] = new_balance
    record["updated_at"] = now_ts()
    return record


def transfer(
    from_kind: str,
    from_id: str,
    to_kind: str,
    to_id: str,
    world_id: str,
    currency_code: str,
    amount: float,
    *,
    allow_overdraft: bool | None = None,
) -> tuple[dict, dict]:
    """Atomic move: subtract from one wallet, add to the other.

    Both wallets must exist (call :func:`ensure_wallet` first if
    you need a lazy create).  Overdraft policy is the same as
    :func:`withdraw`.  Returns ``(from_wallet, to_wallet)``.
    """
    amt = _validate_amount(amount)
    if from_kind == to_kind and from_id == to_id:
        raise WalletError("cannot transfer to the same wallet")
    from_wallet = state.wallets.get(_key(from_kind, from_id, world_id, currency_code))
    to_wallet = state.wallets.get(_key(to_kind, to_id, world_id, currency_code))
    if from_wallet is None or to_wallet is None:
        raise WalletError(
            "both wallets must exist; from=%s to_wallet=%s"
            % (
                "present" if from_wallet is not None else "missing",
                "present" if to_wallet is not None else "missing",
            )
        )
    if amt == 0:
        return from_wallet, to_wallet
    if allow_overdraft is None:
        allow_overdraft = _overdraft_allowed(world_id)
    new_from_balance = round(from_wallet["balance"] - amt, 6)
    if new_from_balance < 0 and not allow_overdraft:
        raise WalletError(
            "insufficient funds for transfer: balance=%g, amount=%g, overdraft=disabled"
            % (from_wallet["balance"], amt)
        )
    from_wallet["balance"] = new_from_balance
    from_wallet["updated_at"] = now_ts()
    to_wallet["balance"] = round(to_wallet["balance"] + amt, 6)
    to_wallet["updated_at"] = now_ts()
    return from_wallet, to_wallet


# ---------------------------------------------------------------------------
# Cascading deletes
# ---------------------------------------------------------------------------


def delete(
    owner_kind: str,
    owner_id: str,
    world_id: str,
    currency_code: str,
) -> bool:
    """Delete a single wallet.  Transactions referencing it are
    kept (audit log)."""
    key = _key(owner_kind, owner_id, world_id, currency_code)
    return state.wallets.pop(key, None) is not None


def delete_for_world(world_id: str) -> int:
    """Drop every wallet in a world.  Returns the count removed.
    Called by the worlds reset flow."""
    keys = [
        k for k, w in state.wallets.items()
        if w.get("world_id") == world_id
    ]
    for k in keys:
        state.wallets.pop(k, None)
    return len(keys)


def delete_for_owner(owner_kind: str, owner_id: str) -> int:
    """Drop every wallet belonging to a specific owner.  Called by
    the NPC delete flow."""
    keys = [
        k for k, w in state.wallets.items()
        if w.get("owner_kind") == owner_kind
        and w.get("owner_id") == owner_id
    ]
    for k in keys:
        state.wallets.pop(k, None)
    return len(keys)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  Wallets are created lazily via
    :func:`ensure_wallet` — operators do the initial grant through
    the route layer.  This stub exists so :func:`xijian_api.stubs.seed_all`
    can call us uniformly."""
    return None


def reset_for_testing() -> None:
    """Wipe every wallet."""
    state.wallets.clear()


__all__ = [
    # Constants
    "OWNER_USER", "OWNER_NPC", "VALID_OWNER_KINDS",
    "DEFAULT_BALANCE", "MAX_SINGLE_AMOUNT", "LOCAL_USER_ID",
    # Errors
    "WalletError",
    # Pure helpers
    "_validate_owner_kind", "_validate_owner_id",
    "_validate_world_id", "_validate_currency_code", "_validate_amount",
    # CRUD
    "get", "get_by_id", "list_for_owner", "list_for_world", "list_all",
    "create", "ensure_wallet",
    # Mutations
    "deposit", "withdraw", "transfer",
    # Cascading
    "delete", "delete_for_world", "delete_for_owner",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
