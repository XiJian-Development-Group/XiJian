"""MCP tools for the economy domain.
MCP 经济域工具。

Wraps the in-memory economy orchestrator
(:mod:`xijian_api.stubs.economy`) and the underlying wallet /
transaction stubs (:mod:`xijian_api.stubs.wallets`,
:mod:`xijian_api.stubs.transactions`) as MCP tools registered with
:mod:`xijian_api.mcp.registry`.
将内存经济编排器 (:mod:`xijian_api.stubs.economy`) 及底层钱包/交易桩层
封装为 MCP 工具，注册到 :mod:`xijian_api.mcp.registry`。

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stubs' own
input validation.
这些是内部领域工具 (``action_kind=None``)：仅操作内存状态，因此绕过 A5.2 门禁。

Tools registered / 已注册工具
----------------

Trade & reward (A4.4 orchestrator) / 交易与奖励 (A4.4 编排器):

* ``economy_purchase``  — user buys from an NPC / 用户从 NPC 购买
* ``economy_reward``    — system grants money to a wallet / 系统向钱包授予资金
* ``economy_summary``   — JSON-friendly per-world economy overview / JSON 友好的按世界经济概览

Wallets (A4.4 wallet store) / 钱包 (A4.4 钱包存储):

* ``wallet_get``         — fetch a wallet by id or by owner+world+currency
  按 ID 或按所有者+世界+货币获取钱包
* ``wallet_list``        — list wallets (optionally scoped to a world)
  列出钱包（可选范围限定到世界）

Transactions (A4.4 audit log) / 交易 (A4.4 审计日志):

* ``transaction_list``  — list transactions (by world, wallet, or all)
  列出交易（按世界、钱包或全部）
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import economy as economy_stub
from xijian_api.stubs import transactions as transactions_stub
from xijian_api.stubs import wallets as wallets_stub


# ---------------------------------------------------------------------------
# Trade & reward handlers / 交易与奖励处理器
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
# Wallet handlers / 钱包处理器
# ---------------------------------------------------------------------------


def _wallet_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    wallet_id = args.get("wallet_id")
    if wallet_id:
        record = wallets_stub.get_by_id(wallet_id)
        if record is None:
            raise ToolError(f"wallet {wallet_id!r} not found")
        return record
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
# Transaction handlers / 交易处理器
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
# Registration / 注册
# ---------------------------------------------------------------------------


register_tool(
    name="economy_purchase",
    description="User purchases from an NPC: user wallet decreases, NPC wallet increases, audit-logged as a purchase transaction. / 用户从 NPC 购买：用户钱包减少，NPC 钱包增加，审计记录为购买交易。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Owning world id. / 所属世界 ID。"},
            "npc_id": {"type": "string", "description": "NPC receiving the payment. / 接收付款的 NPC。"},
            "currency_code": {"type": "string", "description": "Currency code to spend. / 要花费的货币代码。"},
            "amount": {"type": "number", "description": "Non-negative amount to pay. / 非负付款金额。"},
            "ref_id": {"type": "string", "description": "Optional traceability ref id. / 可选的可追溯性引用 ID。"},
        },
        "required": ["world_id", "npc_id", "currency_code", "amount"],
    },
    handler=_economy_purchase,
    action_kind=None,
)


register_tool(
    name="economy_reward",
    description="System grants money to a user or NPC wallet (audit-logged as a reward transaction). / 系统向用户或 NPC 钱包授予资金（审计记录为奖励交易）。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Owning world id. / 所属世界 ID。"},
            "to_kind": {"type": "string", "description": "Receiver owner kind: 'user' or 'npc'. / 接收者所有者类型：'user' 或 'npc'。"},
            "to_id": {"type": "string", "description": "Receiver owner id (user id or NPC id). / 接收者所有者 ID。"},
            "currency_code": {"type": "string", "description": "Currency code to grant. / 要授予的货币代码。"},
            "amount": {"type": "number", "description": "Non-negative amount to grant. / 非负授予金额。"},
            "ref_id": {"type": "string", "description": "Optional traceability ref id. / 可选的可追溯性引用 ID。"},
        },
        "required": ["world_id", "to_kind", "to_id", "currency_code", "amount"],
    },
    handler=_economy_reward,
    action_kind=None,
)


register_tool(
    name="economy_summary",
    description="Return a JSON-friendly per-world economy overview (currencies, wallets, transaction aggregates). / 返回 JSON 友好的按世界经济概览（货币、钱包、交易汇总）。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "World id to summarize. / 要摘要的世界 ID。"},
        },
        "required": ["world_id"],
    },
    handler=_economy_summary,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="wallet_get",
    description="Fetch a wallet by its internal id, or by the (owner_kind, owner_id, world_id, currency_code) composite key. / 按内部 ID 或 (owner_kind, owner_id, world_id, currency_code) 组合键获取钱包。",
    input_schema={
        "type": "object",
        "properties": {
            "wallet_id": {"type": "string", "description": "Internal wallet id (e.g. wlt_...). / 内部钱包 ID。"},
            "owner_kind": {"type": "string", "description": "Owner kind: 'user' or 'npc'. / 所有者类型。"},
            "owner_id": {"type": "string", "description": "Owner id (user id or NPC id). / 所有者 ID。"},
            "world_id": {"type": "string", "description": "Owning world id. / 所属世界 ID。"},
            "currency_code": {"type": "string", "description": "Currency code of the wallet. / 钱包的货币代码。"},
        },
        "required": [],
    },
    handler=_wallet_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="wallet_list",
    description="List wallets. Scoped to a world when world_id is provided, otherwise every wallet. / 列出钱包。提供 world_id 时限定到世界，否则列出所有钱包。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Optional world id to scope the listing. / 可选的限定范围的世界 ID。"},
        },
        "required": [],
    },
    handler=_wallet_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="transaction_list",
    description="List transactions newest-first. Scoped by world_id, by wallet_id (resolves to its owner), or all when neither is given. / 列出交易（最新优先）。按 world_id、wallet_id 限定范围，或两者均不提供时列出全部。",
    input_schema={
        "type": "object",
        "properties": {
            "world_id": {"type": "string", "description": "Optional world id filter. / 可选的世界 ID 筛选。"},
            "wallet_id": {"type": "string", "description": "Optional wallet id; resolves to its owner's transactions. / 可选的钱包 ID；解析为其所有者的交易。"},
            "limit": {"type": "integer", "description": "Max items to return (default 50). / 最大返回条目数。"},
        },
        "required": [],
    },
    handler=_transaction_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)
