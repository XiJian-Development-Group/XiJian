"""MCP tools for the economy domain.

Wraps the in-memory economy orchestrator
(:mod:`xijian_api.stubs.economy`) and the underlying wallet /
transaction stubs (:mod:`xijian_api.stubs.wallets`,
:mod:`xijian_api.stubs.transactions`) as MCP tools registered with
:mod:`xijian_api.mcp.registry`.

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stubs' own
input validation.

Tools registered
----------------

Trade & reward (A4.4 orchestrator):

* ``economy_purchase``  — user buys from an NPC
* ``economy_reward``    — system grants money to a wallet
* ``economy_summary``   — JSON-friendly per-world economy overview

Wallets (A4.4 wallet store):

* ``wallet_get``         — fetch a wallet by id or by owner+world+currency
* ``wallet_list``        — list wallets (optionally scoped to a world)

Transactions (A4.4 audit log):

* ``transaction_list``  — list transactions (by world, wallet, or all)
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import economy as economy_stub
from xijian_api.stubs import transactions as transactions_stub
from xijian_api.stubs import wallets as wallets_stub


# ---------------------------------------------------------------------------
# Trade & reward handlers
# ---------------------------------------------------------------------------


def _economy_purchase(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    for key in ("world_id", "npc_id", "currency_code", "amount"):
        if args.get(key) in (None, ""):
            raise ToolError(f"{key} is required")
    kwargs: dict[str, Any] = {
        "world_id": args["world_id"],
        "npc_id": args["npc_id"],
        "currency_code": args["currency_code"],
        "amount": args["amount"],
    }
    if "ref_id" in args and args["ref_id"] is not None:
        kwargs["ref_id"] = args["ref_id"]
    try:
        return economy_stub.purchase(**kwargs)
    except economy_stub.EconomyError as exc:
        raise ToolError(str(exc)) from exc


def _economy_reward(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    for key in ("world_id", "to_kind", "to_id", "currency_code", "amount"):
        if args.get(key) in (None, ""):
            raise ToolError(f"{key} is required")
    kwargs: dict[str, Any] = {
        "world_id": args["world_id"],
        "to_kind": args["to_kind"],
        "to_id": args["to_id"],
        "currency_code": args["currency_code"],
        "amount": args["amount"],
    }
    if "ref_id" in args and args["ref_id"] is not None:
        kwargs["ref_id"] = args["ref_id"]
    try:
        return economy_stub.reward(**kwargs)
    except economy_stub.EconomyError as exc:
        raise ToolError(str(exc)) from exc


def _economy_summary(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if not world_id:
        raise ToolError("world_id is required")
    return economy_stub.summary(world_id)


# ---------------------------------------------------------------------------
# Wallet handlers
# ---------------------------------------------------------------------------


def _wallet_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    wallet_id = args.get("wallet_id")
    if wallet_id:
        record = wallets_stub.get_by_id(wallet_id)
        if record is None:
            raise ToolError(f"wallet {wallet_id!r} not found")
        return record
    # Owner-based lookup requires the full composite key, including
    # currency_code — the wallet store keys on (owner_kind, owner_id,
    # world_id, currency_code).
    for key in ("owner_kind", "owner_id", "world_id", "currency_code"):
        if args.get(key) in (None, ""):
            raise ToolError(f"{key} is required when wallet_id is not provided")
    try:
        record = wallets_stub.get(
            args["owner_kind"], args["owner_id"],
            args["world_id"], args["currency_code"],
        )
    except wallets_stub.WalletError as exc:
        raise ToolError(str(exc)) from exc
    if record is None:
        raise ToolError(
            "wallet for %s/%s in world %r currency %s not found"
            % (args["owner_kind"], args["owner_id"],
               args["world_id"], args["currency_code"])
        )
    return record


def _wallet_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    world_id = args.get("world_id")
    if world_id:
        return wallets_stub.list_for_world(world_id)
    return wallets_stub.list_all()


# ---------------------------------------------------------------------------
# Transaction handlers
# ---------------------------------------------------------------------------


def _transaction_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    limit = args.get("limit")
    limit_value = int(limit) if isinstance(limit, (int, float)) and not isinstance(limit, bool) else 50
    world_id = args.get("world_id")
    if world_id:
        return transactions_stub.list_for_world(world_id, limit=limit_value)
    wallet_id = args.get("wallet_id")
    if wallet_id:
        wallet = wallets_stub.get_by_id(wallet_id)
        if wallet is None:
            raise ToolError(f"wallet {wallet_id!r} not found")
        return transactions_stub.list_for_owner(
            wallet["owner_kind"], wallet["owner_id"], limit=limit_value,
        )
    return transactions_stub.list_all(limit=limit_value)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


register_tool(
    name="economy_purchase",
    description="User purchases from an NPC: user wallet decreases, NPC wallet increases, audit-logged as a purchase transaction.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Owning world id."},
            "npc_id": {"type": "string", "description": "NPC receiving the payment."},
            "currency_code": {"type": "string", "description": "Currency code to spend."},
            "amount": {"type": "number", "description": "Non-negative amount to pay."},
            "ref_id": {"type": "string", "description": "Optional traceability ref id."},
        },
        "required": ["world_id", "npc_id", "currency_code", "amount"],
    },
    handler=_economy_purchase,
    action_kind=None,
)


register_tool(
    name="economy_reward",
    description="System grants money to a user or NPC wallet (audit-logged as a reward transaction).",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Owning world id."},
            "to_kind": {"type": "string", "description": "Receiver owner kind: 'user' or 'npc'."},
            "to_id": {"type": "string", "description": "Receiver owner id (user id or NPC id)."},
            "currency_code": {"type": "string", "description": "Currency code to grant."},
            "amount": {"type": "number", "description": "Non-negative amount to grant."},
            "ref_id": {"type": "string", "description": "Optional traceability ref id."},
        },
        "required": ["world_id", "to_kind", "to_id", "currency_code", "amount"],
    },
    handler=_economy_reward,
    action_kind=None,
)


register_tool(
    name="economy_summary",
    description="Return a JSON-friendly per-world economy overview (currencies, wallets, transaction aggregates).",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "World id to summarize."},
        },
        "required": ["world_id"],
    },
    handler=_economy_summary,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="wallet_get",
    description="Fetch a wallet by its internal id, or by the (owner_kind, owner_id, world_id, currency_code) composite key.",
    input_schema={
        "type": "object",
        "properties": {
            "wallet_id": {"type": "string", "description": "Internal wallet id (e.g. wlt_...)."},
            "owner_kind": {"type": "string", "description": "Owner kind: 'user' or 'npc'."},
            "owner_id": {"type": "string", "description": "Owner id (user id or NPC id)."},
            "world_id": {"type": "string", "description": "Owning world id."},
            "currency_code": {"type": "string", "description": "Currency code of the wallet."},
        },
        "required": [],
    },
    handler=_wallet_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="wallet_list",
    description="List wallets. Scoped to a world when world_id is provided, otherwise every wallet.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Optional world id to scope the listing."},
        },
        "required": [],
    },
    handler=_wallet_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="transaction_list",
    description="List transactions newest-first. Scoped by world_id, by wallet_id (resolves to its owner), or all when neither is given.",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Optional world id filter."},
            "wallet_id": {"type": "string", "description": "Optional wallet id; resolves to its owner's transactions."},
            "limit": {"type": "integer", "description": "Max items to return (default 50)."},
        },
        "required": [],
    },
    handler=_transaction_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)
