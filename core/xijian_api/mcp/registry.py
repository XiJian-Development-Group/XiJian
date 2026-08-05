"""Tool registry and dispatcher for the MCP server.
MCP 服务器的工具注册表与分发器。

Tools register themselves at import time via :func:`register_tool`.
The dispatcher :func:`call_tool` looks up the tool by name and, if
the tool declared an ``action_kind``, routes the call through the
A5.2 gate (:func:`xijian_api.stubs.mcp.check`) before executing the
handler.
工具在导入时通过 :func:`register_tool` 自行注册。
分发器 :func:`call_tool` 按名称查找工具，若工具声明了 ``action_kind``，
则先经 A5.2 门禁 (:func:`xijian_api.stubs.mcp.check`) 路由再执行处理器。

Gate routing / 门禁路由
==============

The 8 A5.2 action_kinds
(``file_delete`` / ``file_write`` / ``file_read`` / ``shell`` /
``network`` / ``app_launch`` / ``settings_modify`` / ``system_cmd``)
are the ones the spec says must pass the blacklist/whitelist gate.
Internal domain tools (character CRUD, world management, memory
search, …) only touch in-memory state and therefore skip the gate —
they're protected by the API's own input validation.
8 个 A5.2 action_kind (``file_delete`` / ``file_write`` / ``file_read`` / ``shell`` /
``network`` / ``app_launch`` / ``settings_modify`` / ``system_cmd``)
是规范规定必须经黑名单/白名单门禁的。内部领域工具 (角色 CRUD、世界管理、记忆搜索、…)
仅操作内存状态，因此绕过门禁 —— 由 API 自身的输入验证保护。

A tool declares its gate relationship via ``action_kind``:
工具通过 ``action_kind`` 声明其门禁关系：

* ``action_kind=None``  → no gate (internal domain tool)
  无门禁 (内部领域工具)
* ``action_kind="file_read"`` → gate with that kind; denied calls
  raise :class:`ToolGateError` instead of executing.
  该种类的门禁；被拒绝的调用抛出 :class:`ToolGateError` 而非执行。

Tool spec shape / 工具规格结构
===================

Each tool is stored as::
每个工具存储为::

    {
        "name": str,
        "description": str,
        "inputSchema": dict,       # JSON Schema for the tool's args / 工具参数的 JSON Schema
        "annotations": dict | None,  # readOnlyHint / destructiveHint / …
        "handler": callable,       # (args: dict, ctx: dict) -> dict
        "action_kind": str | None, # gate kind, or None to skip gate / 门禁类型，None 则绕过门禁
    }

The ``handler`` receives the parsed arguments dict and a context
dict carrying ``world_id`` (optional) and ``caller`` info.  It
returns a result dict that the protocol layer wraps into the MCP
``tools/call`` response envelope::
``handler`` 接收已解析的参数字典和携带 ``world_id`` (可选) 与 ``caller`` 信息的上下文字典。
它返回结果字典，协议层将其包装为 MCP ``tools/call`` 响应信封::

    {"content": [{"type": "text", "text": "..."}], "isError": False}

Handlers may also raise :class:`ToolError` to signal a structured
error (the protocol layer turns it into an ``isError: true`` result
rather than a JSON-RPC error, per the MCP spec).
处理器也可抛出 :class:`ToolError` 表示结构化错误
(协议层将其转换为 ``isError: true`` 结果而非 JSON-RPC 错误，依 MCP 规范)。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from xijian_api.stubs import mcp as mcp_stub
from xijian_api.stubs import mcp_rules as rules_stub


_LOGGER = logging.getLogger("xijian_api.mcp.registry")


# ---------------------------------------------------------------------------
# Exceptions / 异常
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """Raised by a tool handler to signal a structured error.
    由工具处理器抛出，表示结构化错误。

    The protocol layer wraps this into an MCP ``tools/call`` result
    with ``isError: true`` rather than a JSON-RPC error response,
    per the MCP spec ("tools/call errors are returned as results,
    not JSON-RPC errors, unless the error is a protocol-level
    mistake like unknown tool name").
    协议层将此包装为 MCP ``tools/call`` 结果，设置 ``isError: true``，
    而非 JSON-RPC 错误响应，依 MCP 规范 (tools/call 错误作为结果返回，
    而非 JSON-RPC 错误，除非是协议级错误如未知工具名)。
    """

    def __init__(self, message: str, *, data: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.data = data


class ToolGateError(ToolError):
    """Raised when the A5.2 gate denies a tool call.
    当 A5.2 门禁拒绝工具调用时抛出。

    Carries the gate verdict so the caller / model can understand
    *why* the call was blocked (blacklist hit, lockout, freeze, …).
    携带门禁裁决，使调用者/模型了解调用被阻止的原因 (黑名单命中、锁定、冻结、…)。
    """


class ToolNotFoundError(KeyError):
    """Raised when ``call_tool`` is asked for an unknown tool name.
    当 ``call_tool`` 接收未知工具名称时抛出。"""


# ---------------------------------------------------------------------------
# Registry / 注册表
# ---------------------------------------------------------------------------


#: Type alias for tool handler functions.
#: 工具处理函数的类型别名。
ToolHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


#: The registry itself.  Keyed by tool name.
#: 注册表本身。按工具名称索引。
_REGISTRY: dict[str, dict[str, Any]] = {}

#: Lock for registry mutations.  Registration happens at import time
#: (single-threaded) but ``call_tool`` may fire from request threads,
#: so we guard reads of the registry dict shape with the GIL + this
#: lock for compound operations.
#: 注册表变动的锁。注册在导入时发生 (单线程)，但 ``call_tool`` 可能从请求线程触发，
#: 因此用 GIL + 此锁保护复合操作期间的注册表字典读取。
_LOCK = threading.RLock()


def register_tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    handler: ToolHandler,
    *,
    action_kind: str | None = None,
    annotations: dict[str, Any] | None = None,
) -> None:
    """Register a tool in the MCP registry.
    在 MCP 注册表中注册一个工具。

    Parameters / 参数
    ----------
    name:
        Dotted snake_case tool name (e.g. ``character_create``).
        点分隔蛇形工具名 (如 ``character_create``)。
    description:
        Human-readable description shown to the model in
        ``tools/list``.  Keep it concise but actionable.
        在 ``tools/list`` 中向模型展示的人类可读描述。保持简洁且可操作。
    input_schema:
        JSON Schema describing the tool's arguments.  Must be a
        ``{"type": "object", "properties": {...}, "required": [...]}``
        shape.
        描述工具参数的 JSON Schema。必须为
        ``{"type": "object", "properties": {...}, "required": [...]}`` 格式。
    handler:
        Callable ``(args: dict, ctx: dict) -> dict``.  The result
        dict should have the shape
        ``{"content": [{"type": "text", "text": "..."}], "isError": False}``.
        可调用对象 ``(args: dict, ctx: dict) -> dict``。结果字典应为
        ``{"content": [{"type": "text", "text": "..."}], "isError": False}`` 格式。
    action_kind:
        If set, the dispatcher runs the A5.2 gate before calling
        ``handler``.  Must be one of
        :data:`xijian_api.stubs.mcp_rules.VALID_KINDS`.
        若设置，分发器在调用 ``handler`` 前执行 A5.2 门禁检查。
        必须为 :data:`xijian_api.stubs.mcp_rules.VALID_KINDS` 之一。
    annotations:
        Optional MCP tool annotations
        (``readOnlyHint`` / ``destructiveHint`` / ``idempotentHint``
        / ``openWorldHint``).
        可选的 MCP 工具注解
        (``readOnlyHint`` / ``destructiveHint`` / ``idempotentHint`` / ``openWorldHint``)。
    """
    if not isinstance(name, str) or not name:
        raise ValueError("tool name is required")
    if action_kind is not None and action_kind not in rules_stub.VALID_KINDS:
        raise ValueError(
            "action_kind must be one of %s, got %r"
            % (sorted(rules_stub.VALID_KINDS), action_kind)
        )
    with _LOCK:
        if name in _REGISTRY:
            _LOGGER.warning("overwriting already-registered tool %r", name)
        _REGISTRY[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
            "annotations": annotations,
            "handler": handler,
            "action_kind": action_kind,
        }
        _LOGGER.debug("registered MCP tool %r (action_kind=%s)", name, action_kind)


def unregister_tool(name: str) -> bool:
    """Remove a tool from the registry.  Returns True if it existed.
    从注册表中移除一个工具。存在则返回 True。"""
    with _LOCK:
        return _REGISTRY.pop(name, None) is not None


def get_tool(name: str) -> dict[str, Any] | None:
    """Return the internal tool record (including handler) or None.
    返回内部工具记录 (含 handler) 或 None。"""
    with _LOCK:
        return _REGISTRY.get(name)


def list_tools() -> list[dict[str, Any]]:
    """Return the public tool specs (no handler) for ``tools/list``.
    返回公开工具规格 (不含 handler)，用于 ``tools/list``。"""
    with _LOCK:
        out: list[dict[str, Any]] = []
        for record in _REGISTRY.values():
            spec: dict[str, Any] = {
                "name": record["name"],
                "description": record["description"],
                "inputSchema": record["inputSchema"],
            }
            if record["annotations"] is not None:
                spec["annotations"] = record["annotations"]
            out.append(spec)
        # Sort by name for stable output.
        # 按名称排序以确保输出稳定。
        out.sort(key=lambda t: t["name"])
        return out


def list_tool_names() -> list[str]:
    """Return just the tool names, sorted.
    仅返回工具名称，已排序。"""
    with _LOCK:
        return sorted(_REGISTRY.keys())


def call_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    world_id: str | None = None,
    caller: str | None = None,
    skip_gate: bool = False,
) -> dict[str, Any]:
    """Dispatch a tool call, routing through the A5.2 gate if needed.
    分发工具调用，必要时经 A5.2 门禁路由。

    Returns the MCP ``tools/call`` result envelope::
    返回 MCP ``tools/call`` 结果信封::

        {"content": [...], "isError": False}

    ``skip_gate=True`` tells the dispatcher the caller **already** ran
    the A5.2 gate (e.g. the chat pipeline's T0-1 ``_mcp_gate_check``)
    so the inner ``mcp.check()`` is not repeated — this avoids a
    duplicate audit entry for the same allowed call.  Denied /
    frozen / lockout / crash verdicts are still surfaced by the
    caller's own gate; a ``skip_gate`` caller is responsible for them.
    ``skip_gate`` 为真时表示调用方**已**执行过 A5.2 门禁（例如聊天管线
    的 T0-1 ``_mcp_gate_check``），内层不再重复 ``mcp.check()``，避免
    同一次 allowed 调用写入两条审计。denied/frozen/lockout/crash 仍由
    调用方自己的闸门处理；使用 ``skip_gate`` 的调用方对其负责。

    Raises :class:`ToolNotFoundError` if the tool isn't registered,
    :class:`ToolGateError` if the A5.2 gate denies the call, and
    :class:`ToolError` if the handler raises a structured error.
    抛出 :class:`ToolNotFoundError` (工具未注册)、:class:`ToolGateError` (门禁拒绝)、
    :class:`ToolError` (处理器抛出结构化错误)。
    """
    arguments = arguments or {}
    with _LOCK:
        record = _REGISTRY.get(name)
    if record is None:
        raise ToolNotFoundError(name)

    ctx: dict[str, Any] = {
        "world_id": world_id,
        "caller": caller,
        "tool_name": name,
    }

    # A5.2 gate — only for tools that declare an action_kind, and only
    # when the caller hasn't already run the gate (skip_gate).
    # A5.2 门禁 — 仅对声明了 action_kind 的工具执行，且仅在调用方尚未
    # 执行过门禁时执行（skip_gate）。
    action_kind = record.get("action_kind")
    if action_kind is not None and not skip_gate:
        gate_result = mcp_stub.check(
            action_kind=action_kind,
            args=arguments,
            world_id=world_id,
        )
        verdict = gate_result.get("verdict")
        if verdict != mcp_stub.VERDICT_ALLOWED:
            raise ToolGateError(
                "MCP gate denied the call (verdict=%s, blocked=%s)"
                % (verdict, gate_result.get("blocked")),
                data={
                    "verdict": verdict,
                    "blocked": gate_result.get("blocked"),
                    "matched_rule": gate_result.get("matched_rule"),
                    "audit_id": gate_result.get("audit_id"),
                },
            )

    handler: ToolHandler = record["handler"]
    try:
        result = handler(arguments, ctx)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 — wrap as ToolError 包装为 ToolError
        _LOGGER.exception("tool %r handler raised", name)
        raise ToolError(
            "tool %r failed: %s" % (name, exc),
            data={"exception": type(exc).__name__},
        ) from exc

    # Normalise the result envelope.  Handlers may return a bare
    # dict / list / str — we wrap it into the MCP content shape.
    # 规范化结果信封。处理器可能返回裸 dict / list / str — 将其包装为 MCP 内容格式。
    if not isinstance(result, dict) or "content" not in result:
        if isinstance(result, str):
            text = result
        else:
            import json
            text = json.dumps(result, ensure_ascii=False, default=str)
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    elif "isError" not in result:
        result["isError"] = False
    return result


def reset_registry() -> None:
    """Wipe every registered tool.  Used by tests.
    清除所有已注册工具。供测试使用。"""
    with _LOCK:
        _REGISTRY.clear()


__all__ = [
    "ToolError",
    "ToolGateError",
    "ToolNotFoundError",
    "ToolHandler",
    "register_tool",
    "unregister_tool",
    "get_tool",
    "list_tools",
    "list_tool_names",
    "call_tool",
    "reset_registry",
]