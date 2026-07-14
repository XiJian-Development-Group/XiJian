"""``/v1/xijian/economy/*`` routes — A4.4.

Currencies
==========

* ``GET    /v1/xijian/currencies``                       — list (optional ?world_id)
* ``POST   /v1/xijian/currencies``                       — create
* ``GET    /v1/xijian/currencies/<wid>/<code>``          — get
* ``PATCH  /v1/xijian/currencies/<wid>/<code>``          — patch
* ``DELETE /v1/xijian/currencies/<wid>/<code>``          — delete (?cascade=true)

Wallets
=======

* ``GET    /v1/xijian/wallets``                          — list (?world_id, ?owner_kind, ?owner_id)
* ``POST   /v1/xijian/wallets/ensure``                   — idempotent ensure
* ``GET    /v1/xijian/wallets/<kind>/<id>``              — list-for-owner
* ``GET    /v1/xijian/wallets/<kind>/<id>/<wid>/<code>`` — get
* ``DELETE /v1/xijian/wallets/<kind>/<id>/<wid>/<code>`` — delete
* ``POST   /v1/xijian/wallets/<kind>/<id>/<wid>/<code>/deposit``  — deposit
* ``POST   /v1/xijian/wallets/<kind>/<id>/<wid>/<code>/withdraw`` — withdraw

Transactions
============

* ``GET    /v1/xijian/economy/transactions``             — list (?world_id, ?kind, ?owner)
* ``GET    /v1/xijian/economy/transactions/<txn_id>``    — get
* ``GET    /v1/xijian/economy/transactions/summary``     — aggregate (?world_id)

Economy state
=============

* ``GET    /v1/xijian/economy/state/<wid>``              — get
* ``PATCH  /v1/xijian/economy/state/<wid>``              — patch
* ``POST   /v1/xijian/economy/state/<wid>/tick``         — dev-only (XIJIAN_DEV=1)
* ``GET    /v1/xijian/economy/state/<wid>/summary``      — per-world overview

Trade + crime
=============

* ``POST   /v1/xijian/economy/purchase``                 — user buys from NPC
* ``POST   /v1/xijian/economy/sale``                     — user sells to NPC
* ``POST   /v1/xijian/economy/reward``                   — system grant
* ``POST   /v1/xijian/economy/transfer``                 — user-to-user
* ``POST   /v1/xijian/economy/crime/theft``              — NPC attempts theft
* ``POST   /v1/xijian/economy/crime/scam``               — NPC attempts scam
"""

from __future__ import annotations

import logging
import os
from typing import Any

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.pagination import paginate
from xijian_api.stubs import economy as economy_stub
from xijian_api.stubs import npcs as npcs_stub
from xijian_api.stubs import transactions as txn_stub
from xijian_api.stubs import wallets as wallet_stub
from xijian_api.stubs import world_currencies as currency_stub
from xijian_api.stubs import world_economy_state as eco_stub
from xijian_api.stubs import worlds as worlds_stub


bp = Blueprint("xijian_economy", __name__)
_LOGGER = logging.getLogger("xijian_api.routes.xijian_economy")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_json() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(
            400, "request body must be a JSON object",
            "invalid_request_error", code="invalid_body",
        )
    return body


def _dev_only() -> None:
    if os.environ.get("XIJIAN_DEV") != "1":
        raise ApiError(
            403, "dev-only endpoint", "forbidden_error", code="dev_only",
        )


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _parse_positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(
            400, "%s must be a number" % name,
            "invalid_request_error", code="invalid_%s" % name,
        )
    fv = float(value)
    if fv < 0:
        raise ApiError(
            400, "%s must be >= 0" % name,
            "invalid_request_error", code="invalid_%s" % name,
        )
    return fv


# ---------------------------------------------------------------------------
# Currencies
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/currencies")
def list_currencies():
    world_id = request.args.get("world_id")
    if world_id is not None:
        if worlds_stub.get(world_id) is None:
            raise ApiError(
                404, "world not found", "not_found_error", code="world_not_found",
            )
        return jsonify({
            "world_id": world_id,
            "currencies": currency_stub.list_for_world(world_id),
        })
    return jsonify(paginate(currency_stub.list_all()).to_dict())


@bp.post("/v1/xijian/currencies")
def create_currency():
    body = _require_json()
    world_id = body.get("world_id")
    if not isinstance(world_id, str) or not world_id:
        raise ApiError(
            400, "`world_id` is required", "invalid_request_error",
            code="missing_world_id", param="world_id",
        )
    code = body.get("code")
    if not isinstance(code, str) or not code:
        raise ApiError(
            400, "`code` is required", "invalid_request_error",
            code="missing_code", param="code",
        )
    name = body.get("name")
    if not isinstance(name, str) or not name:
        raise ApiError(
            400, "`name` is required", "invalid_request_error",
            code="missing_name", param="name",
        )
    try:
        record = currency_stub.create(
            world_id=world_id,
            code=code,
            name=name,
            symbol=body.get("symbol"),
            decimals=body.get("decimals", currency_stub.DEFAULT_DECIMALS),
        )
    except currency_stub.CurrencyError as exc:
        msg = str(exc)
        if "does not exist" in msg:
            raise ApiError(404, msg, "not_found_error", code="world_not_found")
        if "already exists" in msg:
            raise ApiError(409, msg, "world_error", code="currency_conflict")
        raise ApiError(400, msg, "invalid_request_error", code="currency_error")
    return jsonify(record), 201


@bp.get("/v1/xijian/currencies/<world_id>/<code>")
def get_currency(world_id: str, code: str):
    record = currency_stub.get(world_id, code)
    if record is None:
        raise ApiError(
            404, "currency not found", "not_found_error",
            code="currency_not_found",
        )
    return jsonify(record)


@bp.patch("/v1/xijian/currencies/<world_id>/<code>")
def patch_currency(world_id: str, code: str):
    body = _require_json()
    try:
        record = currency_stub.update(world_id, code, body)
    except currency_stub.CurrencyError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="currency_error",
        )
    if record is None:
        raise ApiError(
            404, "currency not found", "not_found_error",
            code="currency_not_found",
        )
    return jsonify(record)


@bp.delete("/v1/xijian/currencies/<world_id>/<code>")
def delete_currency(world_id: str, code: str):
    cascade = _parse_bool(request.args.get("cascade"), default=False)
    try:
        ok = currency_stub.delete(world_id, code, cascade=cascade)
    except currency_stub.CurrencyError as exc:
        raise ApiError(
            409, str(exc), "world_error", code="currency_in_use",
        )
    if not ok:
        raise ApiError(
            404, "currency not found", "not_found_error",
            code="currency_not_found",
        )
    return jsonify({"deleted": True, "world_id": world_id, "code": code, "cascade": cascade})


# ---------------------------------------------------------------------------
# Wallets — list / ensure / get
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/wallets")
def list_wallets():
    world_id = request.args.get("world_id")
    owner_kind = request.args.get("owner_kind")
    owner_id = request.args.get("owner_id")
    if world_id is not None:
        if worlds_stub.get(world_id) is None:
            raise ApiError(
                404, "world not found", "not_found_error", code="world_not_found",
            )
        return jsonify({
            "world_id": world_id,
            "wallets": wallet_stub.list_for_world(world_id),
        })
    if owner_kind is not None and owner_id is not None:
        return jsonify({
            "owner_kind": owner_kind,
            "owner_id": owner_id,
            "wallets": wallet_stub.list_for_owner(owner_kind, owner_id),
        })
    return jsonify(paginate(wallet_stub.list_all()).to_dict())


@bp.post("/v1/xijian/wallets/ensure")
def ensure_wallet():
    body = _require_json()
    try:
        record = wallet_stub.ensure_wallet(
            owner_kind=body.get("owner_kind"),
            owner_id=body.get("owner_id"),
            world_id=body.get("world_id"),
            currency_code=body.get("currency_code"),
            initial_balance=body.get("initial_balance", wallet_stub.DEFAULT_BALANCE),
        )
    except wallet_stub.WalletError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="wallet_error",
        )
    return jsonify(record)


@bp.get("/v1/xijian/wallets/<owner_kind>/<owner_id>")
def list_wallets_for_owner(owner_kind: str, owner_id: str):
    return jsonify({
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "wallets": wallet_stub.list_for_owner(owner_kind, owner_id),
    })


@bp.get("/v1/xijian/wallets/<owner_kind>/<owner_id>/<world_id>/<currency_code>")
def get_wallet(owner_kind: str, owner_id: str, world_id: str, currency_code: str):
    record = wallet_stub.get(owner_kind, owner_id, world_id, currency_code)
    if record is None:
        raise ApiError(
            404, "wallet not found", "not_found_error", code="wallet_not_found",
        )
    return jsonify(record)


@bp.delete("/v1/xijian/wallets/<owner_kind>/<owner_id>/<world_id>/<currency_code>")
def delete_wallet(owner_kind: str, owner_id: str, world_id: str, currency_code: str):
    if not wallet_stub.delete(owner_kind, owner_id, world_id, currency_code):
        raise ApiError(
            404, "wallet not found", "not_found_error", code="wallet_not_found",
        )
    return jsonify({
        "deleted": True,
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "world_id": world_id,
        "currency_code": currency_code,
    })


@bp.post("/v1/xijian/wallets/<owner_kind>/<owner_id>/<world_id>/<currency_code>/deposit")
def deposit_wallet(owner_kind: str, owner_id: str, world_id: str, currency_code: str):
    body = _require_json()
    amount = _parse_positive_number(body.get("amount"), "amount")
    allow_create = _parse_bool(body.get("allow_create"), default=True)
    try:
        record = wallet_stub.deposit(
            owner_kind, owner_id, world_id, currency_code, amount,
            allow_create=allow_create,
        )
    except wallet_stub.WalletError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="wallet_error",
        )
    return jsonify(record)


@bp.post("/v1/xijian/wallets/<owner_kind>/<owner_id>/<world_id>/<currency_code>/withdraw")
def withdraw_wallet(owner_kind: str, owner_id: str, world_id: str, currency_code: str):
    body = _require_json()
    amount = _parse_positive_number(body.get("amount"), "amount")
    allow_overdraft_raw = body.get("allow_overdraft")
    allow_overdraft = (
        None if allow_overdraft_raw is None
        else _parse_bool(allow_overdraft_raw, default=False)
    )
    try:
        record = wallet_stub.withdraw(
            owner_kind, owner_id, world_id, currency_code, amount,
            allow_overdraft=allow_overdraft,
        )
    except wallet_stub.WalletError as exc:
        msg = str(exc)
        if "insufficient funds" in msg:
            raise ApiError(409, msg, "wallet_error", code="insufficient_funds")
        if "does not exist" in msg:
            raise ApiError(404, msg, "not_found_error", code="wallet_not_found")
        raise ApiError(400, msg, "invalid_request_error", code="wallet_error")
    return jsonify(record)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/economy/transactions")
def list_transactions():
    world_id = request.args.get("world_id")
    kind = request.args.get("kind")
    owner_kind = request.args.get("owner_kind")
    owner_id = request.args.get("owner_id")
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    if owner_kind is not None and owner_id is not None:
        items = txn_stub.list_for_owner(owner_kind, owner_id, limit=limit)
    elif world_id is not None:
        if worlds_stub.get(world_id) is None:
            raise ApiError(
                404, "world not found", "not_found_error", code="world_not_found",
            )
        items = txn_stub.list_for_world(world_id, kind=kind, limit=limit)
    else:
        items = txn_stub.list_all(limit=limit)
    return jsonify({"transactions": items})


@bp.get("/v1/xijian/economy/transactions/summary")
def transactions_summary():
    world_id = request.args.get("world_id")
    if world_id is not None and worlds_stub.get(world_id) is None:
        raise ApiError(
            404, "world not found", "not_found_error", code="world_not_found",
        )
    return jsonify(txn_stub.summary(world_id))


@bp.get("/v1/xijian/economy/transactions/<txn_id>")
def get_transaction(txn_id: str):
    record = txn_stub.get(txn_id)
    if record is None:
        raise ApiError(
            404, "transaction not found", "not_found_error",
            code="transaction_not_found",
        )
    return jsonify(record)


# ---------------------------------------------------------------------------
# Economy state
# ---------------------------------------------------------------------------


@bp.get("/v1/xijian/economy/state/<world_id>")
def get_economy_state(world_id: str):
    record = eco_stub.get(world_id)
    if record is None:
        raise ApiError(
            404, "world not found", "not_found_error", code="world_not_found",
        )
    return jsonify(record)


@bp.patch("/v1/xijian/economy/state/<world_id>")
def patch_economy_state(world_id: str):
    body = _require_json()
    try:
        record = eco_stub.update(world_id, body)
    except eco_stub.EconomyStateError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="economy_state_error",
        )
    if record is None:
        raise ApiError(
            404, "world not found", "not_found_error", code="world_not_found",
        )
    return jsonify(record)


@bp.post("/v1/xijian/economy/state/<world_id>/tick")
def tick_economy_state(world_id: str):
    _dev_only()
    body = _require_json()
    if worlds_stub.get(world_id) is None:
        raise ApiError(
            404, "world not found", "not_found_error", code="world_not_found",
        )
    record = eco_stub.tick(
        world_id,
        volume_delta=body.get("volume_delta", 0.0),
        seasonal_factor=body.get("seasonal_factor", 0.0),
    )
    return jsonify(record)


@bp.get("/v1/xijian/economy/state/<world_id>/summary")
def economy_state_summary(world_id: str):
    if worlds_stub.get(world_id) is None:
        raise ApiError(
            404, "world not found", "not_found_error", code="world_not_found",
        )
    return jsonify(economy_stub.summary(world_id))


# ---------------------------------------------------------------------------
# Trade + crime orchestrator
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/economy/purchase")
def purchase_route():
    body = _require_json()
    try:
        record = economy_stub.purchase(
            world_id=body.get("world_id"),
            npc_id=body.get("npc_id"),
            currency_code=body.get("currency_code"),
            amount=_parse_positive_number(body.get("amount"), "amount"),
            ref_id=body.get("ref_id"),
        )
    except economy_stub.EconomyError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="economy_error",
        )
    return jsonify(record), 201


@bp.post("/v1/xijian/economy/sale")
def sale_route():
    body = _require_json()
    try:
        record = economy_stub.sale(
            world_id=body.get("world_id"),
            npc_id=body.get("npc_id"),
            currency_code=body.get("currency_code"),
            amount=_parse_positive_number(body.get("amount"), "amount"),
            ref_id=body.get("ref_id"),
        )
    except economy_stub.EconomyError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="economy_error",
        )
    return jsonify(record), 201


@bp.post("/v1/xijian/economy/reward")
def reward_route():
    body = _require_json()
    try:
        record = economy_stub.reward(
            world_id=body.get("world_id"),
            to_kind=body.get("to_kind"),
            to_id=body.get("to_id"),
            currency_code=body.get("currency_code"),
            amount=_parse_positive_number(body.get("amount"), "amount"),
            ref_id=body.get("ref_id"),
        )
    except economy_stub.EconomyError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="economy_error",
        )
    return jsonify(record), 201


@bp.post("/v1/xijian/economy/transfer")
def transfer_route():
    body = _require_json()
    try:
        record = economy_stub.transfer_user_to_user(
            world_id=body.get("world_id"),
            currency_code=body.get("currency_code"),
            amount=_parse_positive_number(body.get("amount"), "amount"),
            ref_id=body.get("ref_id"),
        )
    except economy_stub.EconomyError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="economy_error",
        )
    return jsonify(record), 201


@bp.post("/v1/xijian/economy/crime/theft")
def theft_route():
    body = _require_json()
    try:
        result = economy_stub.attempt_theft(
            world_id=body.get("world_id"),
            npc_id=body.get("npc_id"),
            currency_code=body.get("currency_code"),
            amount=_parse_positive_number(body.get("amount"), "amount"),
            ref_id=body.get("ref_id"),
        )
    except economy_stub.EconomyError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="economy_error",
        )
    return jsonify(result)


@bp.post("/v1/xijian/economy/crime/scam")
def scam_route():
    body = _require_json()
    try:
        result = economy_stub.attempt_scam(
            world_id=body.get("world_id"),
            npc_id=body.get("npc_id"),
            currency_code=body.get("currency_code"),
            amount=_parse_positive_number(body.get("amount"), "amount"),
            ref_id=body.get("ref_id"),
        )
    except economy_stub.EconomyError as exc:
        raise ApiError(
            400, str(exc), "invalid_request_error", code="economy_error",
        )
    return jsonify(result)


__all__ = ["bp"]
