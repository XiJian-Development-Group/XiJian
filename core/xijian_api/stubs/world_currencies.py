"""Stub world-currency service — A4.4 in the function list v2.

A "currency" is an operator-defined unit of value within a single
world.  原神 might define ``mora``, 崩铁 ``credit``, 自创世界
``gold`` — each is a separate row in ``state.world_currencies``.

Data model (mirrors §A4.4 SQL schema)
======================================

Composite key ``(world_id, code)`` — a world may have at most one
currency with a given code (matches the SQL PRIMARY KEY(world_id,
code) constraint).  The natural key is what callers use to look up
a currency; the ``id`` field is the internal handle for audit
references and admin tooling.

* ``id``         — ``curr_<12 hex>`` (internal)
* ``world_id``   — owning world
* ``code``       — short machine-friendly code, e.g. ``mora``
* ``name``       — operator-friendly display name, e.g. ``摩拉``
* ``symbol``     — optional UI glyph, e.g. ``M``
* ``decimals``   — display precision, 0 for whole numbers, 2 for
  cents-style.  Default 0 to keep "knapsack math" simple.

Validation
==========

* ``code`` must be a non-empty alphanumeric+underscore string,
  1..16 chars.  Lowercase recommended but not enforced — operators
  may want to keep ``GOLD`` for visual consistency in some
  worlds.  What we *do* enforce is uniqueness per world.
* ``decimals`` is locked to [0, 6] — anything beyond 6 (e.g. 18 for
  wei-style crypto) is almost certainly a misconfiguration.
* ``name`` is required, 1..64 chars.

Cascading impact
================

Deleting a currency is destructive: it implicitly removes every
wallet holding it and every transaction referencing it.  We refuse
the delete by default unless the caller passes
``cascade=True``.  The route layer follows the same rule.

Test surface
============

* :func:`create` / :func:`get` / :func:`list_for_world` / :func:`update` / :func:`delete`
* :func:`ensure_currency` — lazy default-materialiser (used by
  the economy orchestrator when a transaction lands against an
  unknown code).
* :func:`seed_default` / :func:`reset_for_testing`
"""

from __future__ import annotations

import logging
import re
from typing import Any

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_currency_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.world_currencies")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Validation regex for the ``code`` field.  Alphanumeric + underscore,
#: 1..16 chars.  Unicode is intentionally rejected — codes are
#: machine handles, not user-visible text.
CODE_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")

#: Display precision bounds.  Wei-style (18-decimal) currencies are
#: outside this range; operators with that need should fork the stub.
MIN_DECIMALS = 0
MAX_DECIMALS = 6

#: Default precision (whole-number "knapsack math").
DEFAULT_DECIMALS = 0

#: Hard upper bound on currency name length (UI sanity).
MAX_NAME_LEN = 64


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CurrencyError(ValueError):
    """Raised on validation / lifecycle errors."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _validate_code(code: Any) -> str:
    if not isinstance(code, str) or not CODE_RE.match(code):
        raise CurrencyError(
            "code must match [A-Za-z0-9_]{1,16} (got %r)" % (code,)
        )
    return code


def _validate_name(name: Any) -> str:
    if not isinstance(name, str) or not name:
        raise CurrencyError("name is required")
    if len(name) > MAX_NAME_LEN:
        raise CurrencyError(
            "name too long: %d > %d" % (len(name), MAX_NAME_LEN)
        )
    return name


def _validate_decimals(decimals: Any) -> int:
    if isinstance(decimals, bool) or not isinstance(decimals, int):
        raise CurrencyError(
            "decimals must be an int, got %s" % type(decimals).__name__
        )
    if decimals < MIN_DECIMALS or decimals > MAX_DECIMALS:
        raise CurrencyError(
            "decimals must be in [%d, %d], got %d"
            % (MIN_DECIMALS, MAX_DECIMALS, decimals)
        )
    return decimals


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


def _key(world_id: str, code: str) -> tuple[str, str]:
    return (world_id, code)


# ---------------------------------------------------------------------------
# Internal — pulled into a helper so tests can monkey-patch
# ---------------------------------------------------------------------------


def _worlds_get(world_id: str) -> dict | None:
    from xijian_api.stubs import worlds as worlds_stub
    return worlds_stub.get(world_id)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create(
    *,
    world_id: str,
    code: str,
    name: str,
    symbol: str | None = None,
    decimals: int = DEFAULT_DECIMALS,
    currency_id: str | None = None,
    now: float | None = None,
) -> dict:
    """Create a currency.  Raises on duplicate (world_id, code) or
    world-not-found."""
    if not isinstance(world_id, str) or not world_id:
        raise CurrencyError("world_id is required")
    _validate_code(code)
    _validate_name(name)
    _validate_decimals(decimals)
    if _worlds_get(world_id) is None:
        raise CurrencyError("world %r does not exist" % world_id)
    key = _key(world_id, code)
    if key in state.world_currencies:
        raise CurrencyError(
            "currency %s already exists in world %r" % (code, world_id)
        )
    new_id = currency_id or gen_currency_id()
    if any(r.get("id") == new_id for r in state.world_currencies.values()):
        raise CurrencyError("currency id %r already exists" % new_id)
    timestamp = _now_or(now)
    record = {
        "id": new_id,
        "world_id": world_id,
        "code": code,
        "name": name,
        "symbol": symbol,
        "decimals": int(decimals),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    state.world_currencies[key] = record
    return record


def get(world_id: str, code: str) -> dict | None:
    """Return the currency record or ``None``."""
    return state.world_currencies.get(_key(world_id, code))


def get_by_id(currency_id: str) -> dict | None:
    """Lookup by internal id (audit / admin tool)."""
    for record in state.world_currencies.values():
        if record.get("id") == currency_id:
            return record
    return None


def list_for_world(world_id: str) -> list[dict]:
    """Return every currency in ``world_id`` sorted by code."""
    out = [
        r for r in state.world_currencies.values()
        if r.get("world_id") == world_id
    ]
    out.sort(key=lambda r: str(r.get("code", "")))
    return out


def list_all() -> list[dict]:
    return list(state.world_currencies.values())


def update(
    world_id: str,
    code: str,
    patch: dict,
) -> dict | None:
    """Patch mutable currency fields.  ``id`` / ``world_id`` / ``code``
    are immutable (renaming a currency would invalidate every wallet
    row that references it).  If you really need a rename, create a
    new currency and migrate the wallets."""
    record = state.world_currencies.get(_key(world_id, code))
    if record is None:
        return None
    if "id" in patch or "world_id" in patch or "code" in patch:
        raise CurrencyError("id, world_id, code are immutable")
    for key, value in patch.items():
        if key == "name":
            record["name"] = _validate_name(value)
        elif key == "symbol":
            if value is not None and not isinstance(value, str):
                raise CurrencyError("symbol must be a string or None")
            record["symbol"] = value
        elif key == "decimals":
            record["decimals"] = _validate_decimals(value)
    record["updated_at"] = now_ts()
    return record


def delete(
    world_id: str,
    code: str,
    *,
    cascade: bool = False,
) -> bool:
    """Delete a currency.  Refuses by default if any wallet or
    transaction still references it (AC-1 — keep the audit log
    intact).  Pass ``cascade=True`` to delete the referencing
    wallets + transactions too."""
    key = _key(world_id, code)
    if key not in state.world_currencies:
        return False
    if not cascade:
        wallet_refs = sum(
            1 for w in state.wallets.values()
            if w.get("world_id") == world_id
            and w.get("currency_code") == code
        )
        txn_refs = sum(
            1 for t in state.transactions.values()
            if t.get("world_id") == world_id
            and t.get("currency_code") == code
        )
        if wallet_refs or txn_refs:
            raise CurrencyError(
                "currency %s in world %r has %d wallet(s) and %d "
                "transaction(s) referencing it; pass cascade=True to "
                "delete them too" % (code, world_id, wallet_refs, txn_refs)
            )
    else:
        wallet_keys = [
            k for k, w in state.wallets.items()
            if w.get("world_id") == world_id
            and w.get("currency_code") == code
        ]
        for k in wallet_keys:
            state.wallets.pop(k, None)
        txn_ids = [
            t["id"] for t in state.transactions.values()
            if t.get("world_id") == world_id
            and t.get("currency_code") == code
        ]
        for t_id in txn_ids:
            state.transactions.pop(t_id, None)
    state.world_currencies.pop(key, None)
    return True


# ---------------------------------------------------------------------------
# Lazy default — used by the economy orchestrator
# ---------------------------------------------------------------------------


def ensure_currency(
    world_id: str,
    code: str,
    *,
    name: str | None = None,
) -> dict:
    """Return the currency for ``(world_id, code)``, creating a
    placeholder if missing.  Used by the orchestrator so a
    transaction against an unknown code doesn't crash."""
    record = get(world_id, code)
    if record is not None:
        return record
    return create(
        world_id=world_id,
        code=code,
        name=name or ("%s (auto)" % code),
        decimals=DEFAULT_DECIMALS,
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  We don't seed any default
    currencies — operators define them per world through the route
    layer."""
    return None


def reset_for_testing() -> None:
    """Wipe every bucket."""
    state.world_currencies.clear()


__all__ = [
    # Constants
    "CODE_RE", "MIN_DECIMALS", "MAX_DECIMALS", "DEFAULT_DECIMALS",
    "MAX_NAME_LEN",
    # Errors
    "CurrencyError",
    # Pure helpers
    "_validate_code", "_validate_name", "_validate_decimals",
    # CRUD
    "create", "get", "get_by_id", "list_for_world", "list_all",
    "update", "delete",
    # Lazy default
    "ensure_currency",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
