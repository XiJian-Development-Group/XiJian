"""Economy orchestrator — A4.4 in the function list v2.

Sits on top of :mod:`wallets` / :mod:`transactions` /
:mod:`world_currencies` / :mod:`world_economy_state` and
exposes the *trade* + *crime* verbs the rest of the codebase
calls.

This module is the **only** path that should mutate wallet
balances (outside the wallet stub's own deposit/withdraw/transfer
helpers, which are *still* considered low-level — the orchestrator
is the high-level facade).  Wallet helpers exist for testability
and for the rare cases where an admin tool needs to bypass the
audit log; the orchestrator is what production code uses.

Trade verbs
===========

* :func:`purchase` — user buys from an NPC.  User wallet
  decreases, NPC wallet increases, transaction ``kind=purchase``.
* :func:`sale`     — user sells to an NPC.  User wallet
  increases, NPC wallet decreases, transaction ``kind=sale``.
* :func:`reward`   — system grants money to a wallet.  Sender is
  the ``"system"`` pseudo-owner (kind = ``"system"``).  The wallet
  stub's OWNER_USER / OWNER_NPC validation **does** block "system"
  — the orchestrator special-cases this by writing the transaction
  *without* touching the (non-existent) system wallet.  Balance
  update is a straight deposit on the receiver.

Crime verbs
===========

* :func:`attempt_theft` — NPC tries to steal from user.
  Success probability is a deterministic hash on (npc_id,
  world_id, time-bucket) so tests can pin the outcome.  On
  success, the NPC's wallet gains the amount and the user's
  loses it; the transaction is ``kind=theft`` and the
  ``ref_id`` is set to ``npcsched_<hex>`` (or whatever the
  caller passes).
* :func:`attempt_scam` — same shape, ``kind=scam``.

Both crime verbs honour the world's ``allow_illegal`` toggle
(AC-3) and the per-NPC cooldown (AC-2).

Cooldowns
=========

The per-NPC cooldown is held in module memory — same as the
A4.1 storm-throttle.  Cleared on :func:`reset_for_testing`.

A5.4 cross-link
===============

When the overload guard triggers, the orchestrator **blocks
new crime verbs** (returns ``"blocked:overload"``) but lets
trade verbs through — players shouldn't be punished for
shopping during a recovery window.  See :func:`_is_overload_active`.

Test surface
============

* :func:`purchase` / :func:`sale` / :func:`reward` / :func:`transfer_user_to_user`
* :func:`attempt_theft` / :func:`attempt_scam`
* :func:`summary` — JSON-friendly per-world overview
* :func:`_set_cooldown` / :func:`_cooldown_remaining` — test hooks
* :func:`seed_default` / :func:`reset_for_testing`
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from xijian_api.stubs import state
from xijian_api.stubs import transactions as txn_stub
from xijian_api.stubs import wallets as wallet_stub
from xijian_api.stubs import world_currencies as currency_stub
from xijian_api.stubs import world_economy_state as eco_stub
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.economy")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Per-NPC cooldown for crime verbs.  Spec AC-2 calls for "合理判定
#: 与冷却" — 30 s is the round number.
CRIME_COOLDOWN_SECONDS = 30.0

#: Default success probability for ``attempt_theft``.  Operators can
#: override per-NPC via state_json's ``crime_skill`` field.
DEFAULT_THEFT_PROBABILITY = 0.30

#: Default success probability for ``attempt_scam``.  Scams are
#: typically a bit easier than physical theft (verbal trickery vs
#: physical stealth).
DEFAULT_SCAM_PROBABILITY = 0.40

#: Env flag to disable the orchestrator's tick thread (if any).
#: Currently unused — the orchestrator is purely on-demand (no
#: background thread).  Kept for forward-compat with the macro
#: tick that :mod:`world_economy_state` exposes.
_TICK_ENV_FLAG = "XIJIAN_ECONOMY_TICK"

#: Lock guarding the cooldown dict.
_COOLDOWN_LOCK = threading.Lock()
#: ``{npc_id: last_crime_at}``
_NPC_CRIME_COOLDOWNS: dict[str, float] = {}

#: System pseudo-owner used by :func:`reward` and other system-
#: initiated money movements.  Not a real owner kind; only the
#: transaction log records it — wallet mutation is a direct
#: deposit on the receiver.
SYSTEM_OWNER_KIND = "system"
SYSTEM_OWNER_ID = "system"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EconomyError(ValueError):
    """Raised on validation / lifecycle errors."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _now_or(value: float | None) -> float:
    return float(value) if value is not None else now_ts()


def _is_overload_active() -> bool:
    """True if the A5.4 overload protection is in a recovery
    window.  We read :data:`state.overload` directly to avoid a
    hard import (avoids a circular dep).  Mirrors the A4.1
    helper."""
    recovery = (state.overload or {}).get("recovery")
    if not recovery:
        return False
    return recovery.get("status") in {"waiting", "first_confirmed"}


def _wallet_for_npc(npc_id: str, world_id: str, currency_code: str) -> dict | None:
    return wallet_stub.get(
        wallet_stub.OWNER_NPC, npc_id, world_id, currency_code
    )


def _wallet_for_user(world_id: str, currency_code: str) -> dict | None:
    return wallet_stub.get(
        wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID, world_id, currency_code
    )


def _cooldown_remaining(npc_id: str) -> float:
    last = _NPC_CRIME_COOLDOWNS.get(npc_id)
    if last is None:
        return 0.0
    return max(0.0, CRIME_COOLDOWN_SECONDS - (time.time() - last))


def _set_cooldown(npc_id: str) -> None:
    _NPC_CRIME_COOLDOWNS[npc_id] = time.time()


def _effective_probability(
    npc_state: dict | None,
    default: float,
    state_key: str,
) -> float:
    """Return the NPC's crime skill or the default.  ``state_key``
    is ``"crime_theft_skill"`` or ``"crime_scam_skill"`` — the
    expected fields on the NPC's ``state_json``.  Range [0, 1]."""
    if not isinstance(npc_state, dict):
        return default
    raw = npc_state.get(state_key)
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return default
    value = float(raw)
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _probability_hit(npc_id: str, world_id: str, probability: float) -> bool:
    """Deterministic probability roll.

    Mirrors the A4.1 probability-trigger strategy: hash a tuple
    that includes the actor and a time bucket so a test can pin
    the outcome with a known clock value.
    """
    if probability <= 0.0:
        return False
    if probability >= 1.0:
        return True
    bucket = int(time.time()) // max(int(CRIME_COOLDOWN_SECONDS), 1)
    h = (hash(("economy_crime", npc_id, world_id, bucket)) & 0xFFFFFFFF) / 0x100000000
    return h < probability


def _require_world(world_id: str) -> None:
    from xijian_api.stubs import worlds as worlds_stub
    if worlds_stub.get(world_id) is None:
        raise EconomyError("world %r does not exist" % world_id)


def _require_currency(world_id: str, currency_code: str) -> None:
    if currency_stub.get(world_id, currency_code) is None:
        raise EconomyError(
            "currency %r does not exist in world %r"
            % (currency_code, world_id)
        )


# ---------------------------------------------------------------------------
# Trade verbs
# ---------------------------------------------------------------------------


def purchase(
    *,
    world_id: str,
    npc_id: str,
    currency_code: str,
    amount: float,
    ref_id: str | None = None,
) -> dict:
    """User buys from an NPC for ``amount``.  User wallet decreases,
    NPC wallet increases.  Returns the transaction record.

    Refuses if the NPC's wallet doesn't exist (operators must
    pre-fund the NPC's wallet for it to receive payments).
    Refuses if the user wallet doesn't exist (call
    :func:`ensure_user_wallet` first).
    """
    _require_world(world_id)
    _require_currency(world_id, currency_code)
    if not isinstance(npc_id, str) or not npc_id:
        raise EconomyError("npc_id is required")
    user_wallet = _wallet_for_user(world_id, currency_code)
    if user_wallet is None:
        raise EconomyError(
            "user wallet does not exist for currency %r in world %r; "
            "call ensure_user_wallet() first" % (currency_code, world_id)
        )
    npc_wallet = _wallet_for_npc(npc_id, world_id, currency_code)
    if npc_wallet is None:
        raise EconomyError(
            "npc %r has no wallet for currency %r in world %r"
            % (npc_id, currency_code, world_id)
        )
    wallet_stub.withdraw(
        wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
        world_id, currency_code, amount,
    )
    wallet_stub.deposit(
        wallet_stub.OWNER_NPC, npc_id, world_id, currency_code, amount,
    )
    return txn_stub.record(
        world_id=world_id,
        from_kind=wallet_stub.OWNER_USER,
        from_id=wallet_stub.LOCAL_USER_ID,
        to_kind=wallet_stub.OWNER_NPC,
        to_id=npc_id,
        currency_code=currency_code,
        amount=amount,
        kind=txn_stub.KIND_PURCHASE,
        ref_id=ref_id,
    )


def sale(
    *,
    world_id: str,
    npc_id: str,
    currency_code: str,
    amount: float,
    ref_id: str | None = None,
) -> dict:
    """User sells to an NPC.  User wallet increases, NPC wallet
    decreases.  Refuses if the NPC can't afford the buyback.
    """
    _require_world(world_id)
    _require_currency(world_id, currency_code)
    if not isinstance(npc_id, str) or not npc_id:
        raise EconomyError("npc_id is required")
    npc_wallet = _wallet_for_npc(npc_id, world_id, currency_code)
    if npc_wallet is None:
        raise EconomyError(
            "npc %r has no wallet for currency %r in world %r"
            % (npc_id, currency_code, world_id)
        )
    user_wallet = _wallet_for_user(world_id, currency_code)
    if user_wallet is None:
        raise EconomyError(
            "user wallet does not exist for currency %r in world %r"
            % (currency_code, world_id)
        )
    try:
        wallet_stub.withdraw(
            wallet_stub.OWNER_NPC, npc_id, world_id, currency_code, amount,
        )
    except wallet_stub.WalletError as exc:
        # Re-raise as EconomyError so callers have a single
        # exception type to catch.  The original message is
        # preserved for debugging.
        raise EconomyError(str(exc)) from exc
    wallet_stub.deposit(
        wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
        world_id, currency_code, amount,
    )
    return txn_stub.record(
        world_id=world_id,
        from_kind=wallet_stub.OWNER_NPC,
        from_id=npc_id,
        to_kind=wallet_stub.OWNER_USER,
        to_id=wallet_stub.LOCAL_USER_ID,
        currency_code=currency_code,
        amount=amount,
        kind=txn_stub.KIND_SALE,
        ref_id=ref_id,
    )


def reward(
    *,
    world_id: str,
    to_kind: str,
    to_id: str,
    currency_code: str,
    amount: float,
    ref_id: str | None = None,
) -> dict:
    """System grants ``amount`` to a wallet.  Sender is the
    ``system`` pseudo-owner (no real wallet for it; we just
    record the transaction)."""
    _require_world(world_id)
    _require_currency(world_id, currency_code)
    if to_kind not in (wallet_stub.OWNER_USER, wallet_stub.OWNER_NPC):
        raise EconomyError(
            "to_kind must be %s or %s, got %r"
            % (wallet_stub.OWNER_USER, wallet_stub.OWNER_NPC, to_kind)
        )
    if not isinstance(to_id, str) or not to_id:
        raise EconomyError("to_id is required")
    wallet_stub.ensure_wallet(to_kind, to_id, world_id, currency_code)
    wallet_stub.deposit(to_kind, to_id, world_id, currency_code, amount)
    return txn_stub.record(
        world_id=world_id,
        from_kind=SYSTEM_OWNER_KIND,
        from_id=SYSTEM_OWNER_ID,
        to_kind=to_kind,
        to_id=to_id,
        currency_code=currency_code,
        amount=amount,
        kind=txn_stub.KIND_REWARD,
        ref_id=ref_id,
    )


def transfer_user_to_user(
    *,
    world_id: str,
    currency_code: str,
    amount: float,
    ref_id: str | None = None,
) -> dict:
    """User-to-user transfer.  Currently the local model only has
    one user, so this is a no-op except for the transaction
    record — but the helper exists so multi-user (multi-tenant)
    variants don't have to refactor later."""
    _require_world(world_id)
    _require_currency(world_id, currency_code)
    user_wallet = _wallet_for_user(world_id, currency_code)
    if user_wallet is None:
        raise EconomyError(
            "user wallet does not exist for currency %r in world %r"
            % (currency_code, world_id)
        )
    wallet_stub.withdraw(
        wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
        world_id, currency_code, amount,
    )
    wallet_stub.deposit(
        wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
        world_id, currency_code, amount,
    )
    return txn_stub.record(
        world_id=world_id,
        from_kind=wallet_stub.OWNER_USER,
        from_id=wallet_stub.LOCAL_USER_ID,
        to_kind=wallet_stub.OWNER_USER,
        to_id=wallet_stub.LOCAL_USER_ID,
        currency_code=currency_code,
        amount=amount,
        kind=txn_stub.KIND_TRANSFER,
        ref_id=ref_id,
    )


# ---------------------------------------------------------------------------
# Crime verbs
# ---------------------------------------------------------------------------


def _attempt_crime(
    *,
    world_id: str,
    npc_id: str,
    currency_code: str,
    amount: float,
    state_key: str,
    default_probability: float,
    kind: str,
    ref_id: str | None,
) -> dict:
    """Shared implementation of theft and scam.  Returns a dict
    with ``success`` and the ``transaction`` (or ``None`` on
    failure / blocked)."""
    _require_world(world_id)
    _require_currency(world_id, currency_code)
    if not isinstance(npc_id, str) or not npc_id:
        raise EconomyError("npc_id is required")
    if not eco_stub.allow_illegal(world_id):
        return {
            "success": False,
            "blocked": "allow_illegal_disabled",
            "transaction": None,
            "cooldown_remaining": 0.0,
        }
    if _is_overload_active():
        return {
            "success": False,
            "blocked": "overload_active",
            "transaction": None,
            "cooldown_remaining": 0.0,
        }
    with _COOLDOWN_LOCK:
        remaining = _cooldown_remaining(npc_id)
        if remaining > 0:
            return {
                "success": False,
                "blocked": "cooldown",
                "transaction": None,
                "cooldown_remaining": remaining,
            }
        _set_cooldown(npc_id)
    user_wallet = _wallet_for_user(world_id, currency_code)
    if user_wallet is None:
        return {
            "success": False,
            "blocked": "no_user_wallet",
            "transaction": None,
            "cooldown_remaining": 0.0,
        }
    # Pull the NPC's crime skill from its state_json.
    npc_record = _npc_get(npc_id)
    npc_state = (npc_record or {}).get("state_json") or {}
    prob = _effective_probability(npc_state, default_probability, state_key)
    if not _probability_hit(npc_id, world_id, prob):
        return {
            "success": False,
            "blocked": "failed_roll",
            "transaction": None,
            "cooldown_remaining": 0.0,
        }
    # Cap theft at the user's available balance — otherwise the
    # transaction would overdraw and (since allow_overdraft may
    # be off) crash.
    target_amount = min(float(amount), float(user_wallet["balance"]))
    if target_amount <= 0:
        return {
            "success": False,
            "blocked": "user_empty",
            "transaction": None,
            "cooldown_remaining": 0.0,
        }
    # Atomic move: user → npc.  Use the wallet's transfer so
    # both sides stay consistent.
    wallet_stub.ensure_wallet(
        wallet_stub.OWNER_NPC, npc_id, world_id, currency_code
    )
    try:
        wallet_stub.transfer(
            wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
            wallet_stub.OWNER_NPC, npc_id,
            world_id, currency_code, target_amount,
        )
    except wallet_stub.WalletError as exc:
        return {
            "success": False,
            "blocked": "transfer_failed:%s" % exc,
            "transaction": None,
            "cooldown_remaining": 0.0,
        }
    transaction = txn_stub.record(
        world_id=world_id,
        from_kind=wallet_stub.OWNER_USER,
        from_id=wallet_stub.LOCAL_USER_ID,
        to_kind=wallet_stub.OWNER_NPC,
        to_id=npc_id,
        currency_code=currency_code,
        amount=target_amount,
        kind=kind,
        ref_id=ref_id,
    )
    return {
        "success": True,
        "blocked": None,
        "transaction": transaction,
        "cooldown_remaining": 0.0,
    }


def _npc_get(npc_id: str) -> dict | None:
    from xijian_api.stubs import npcs as npcs_stub
    return npcs_stub.get(npc_id)


def attempt_theft(
    *,
    world_id: str,
    npc_id: str,
    currency_code: str,
    amount: float,
    ref_id: str | None = None,
) -> dict:
    """NPC attempts to steal ``amount`` from the user.

    Returns a dict with ``success``, ``transaction`` (or ``None``),
    and ``blocked`` (a string code describing why it failed —
    ``cooldown`` / ``failed_roll`` / ``allow_illegal_disabled`` /
    ``overload_active`` / ``user_empty`` / etc.).

    Cooldown is **always** consumed (we set it before the roll)
    so a stream of failed attempts doesn't pin a hit-rate that's
    higher than the spec's intent.
    """
    return _attempt_crime(
        world_id=world_id,
        npc_id=npc_id,
        currency_code=currency_code,
        amount=amount,
        state_key="crime_theft_skill",
        default_probability=DEFAULT_THEFT_PROBABILITY,
        kind=txn_stub.KIND_THEFT,
        ref_id=ref_id,
    )


def attempt_scam(
    *,
    world_id: str,
    npc_id: str,
    currency_code: str,
    amount: float,
    ref_id: str | None = None,
) -> dict:
    """NPC attempts to scam ``amount`` from the user.

    Same shape as :func:`attempt_theft`; default probability is
    higher (scams rely on verbal trickery, not physical stealth).
    """
    return _attempt_crime(
        world_id=world_id,
        npc_id=npc_id,
        currency_code=currency_code,
        amount=amount,
        state_key="crime_scam_skill",
        default_probability=DEFAULT_SCAM_PROBABILITY,
        kind=txn_stub.KIND_SCAM,
        ref_id=ref_id,
    )


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def ensure_user_wallet(
    world_id: str,
    currency_code: str,
    *,
    initial_balance: float = 0.0,
) -> dict:
    """Lazy-create the user's wallet.  Idempotent.  Tests use this
    to set up the user side of a transaction."""
    return wallet_stub.ensure_wallet(
        wallet_stub.OWNER_USER, wallet_stub.LOCAL_USER_ID,
        world_id, currency_code, initial_balance=initial_balance,
    )


def ensure_npc_wallet(
    npc_id: str,
    world_id: str,
    currency_code: str,
    *,
    initial_balance: float = 0.0,
) -> dict:
    """Lazy-create an NPC's wallet.  Idempotent."""
    return wallet_stub.ensure_wallet(
        wallet_stub.OWNER_NPC, npc_id, world_id, currency_code,
        initial_balance=initial_balance,
    )


def summary(world_id: str) -> dict:
    """Return a JSON-friendly overview: currency count, wallet
    totals, transaction aggregates, policy toggles."""
    record = eco_stub.get(world_id)
    return {
        "world_id": world_id,
        "economy_state": record,
        "currencies": currency_stub.list_for_world(world_id),
        "wallets": wallet_stub.list_for_world(world_id),
        "transactions": txn_stub.summary(world_id),
    }


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def seed_default() -> None:
    """Idempotent default-seed.  Nothing to seed — currencies,
    wallets and transactions are operator/runtime-driven."""
    return None


def reset_for_testing() -> None:
    """Wipe the cooldown table.  The wallets / transactions /
    currencies / state buckets are cleared by their own modules
    (via :func:`state.reset_for_testing`)."""
    with _COOLDOWN_LOCK:
        _NPC_CRIME_COOLDOWNS.clear()


__all__ = [
    # Constants
    "CRIME_COOLDOWN_SECONDS",
    "DEFAULT_THEFT_PROBABILITY", "DEFAULT_SCAM_PROBABILITY",
    "SYSTEM_OWNER_KIND", "SYSTEM_OWNER_ID",
    # Errors
    "EconomyError",
    # Trade verbs
    "purchase", "sale", "reward", "transfer_user_to_user",
    # Crime verbs
    "attempt_theft", "attempt_scam",
    # Convenience
    "ensure_user_wallet", "ensure_npc_wallet", "summary",
    # Test hooks
    "_set_cooldown", "_cooldown_remaining",
    "_effective_probability", "_probability_hit",
    # Lifecycle
    "seed_default", "reset_for_testing",
]
