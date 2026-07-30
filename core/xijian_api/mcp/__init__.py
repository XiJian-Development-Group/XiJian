"""MCP (Model Context Protocol) server package — JSON-RPC 2.0 over HTTP.
MCP (模型上下文协议) 服务端包 — 基于 HTTP 的 JSON-RPC 2.0 实现。

Implements the MCP 1.0 spec as a lightweight JSON-RPC 2.0 handler
mounted on the existing Flask app at ``POST /v1/mcp``.  The official
MCP Python SDK is ASGI-oriented and can't mount on Flask (WSGI) without
an adapter; hand-rolling the protocol is lighter and more controllable.
实现了 MCP 1.0 规范，作为轻量级 JSON-RPC 2.0 处理器挂载在现有 Flask 应用的 ``POST /v1/mcp`` 端点上。
官方 MCP Python SDK 面向 ASGI，无适配器无法挂载到 Flask (WSGI)；手写协议更轻量且可控性更强。

Layout
======

* :mod:`xijian_api.mcp.protocol`  — JSON-RPC 2.0 envelope + MCP 1.0
  method dispatcher (initialize / ping / tools/list / tools/call /
  resources/list / resources/read / prompts/list / prompts/get).
  JSON-RPC 2.0 信封 + MCP 1.0 方法分发器 (initialize / ping / tools/list / tools/call /
  resources/list / resources/read / prompts/list / prompts/get)。
* :mod:`xijian_api.mcp.registry`  — tool registry.  Tools register
  via :func:`~xijian_api.mcp.registry.register_tool`; the dispatcher
  routes desktop-control tools through the A5.2 gate
  (:func:`xijian_api.stubs.mcp.check`) before execution.
  工具注册表。工具通过 :func:`~xijian_api.mcp.registry.register_tool` 注册；分发器将桌面控制工具
  通过 A5.2 门禁 (:func:`xijian_api.stubs.mcp.check`) 路由后再执行。
* :mod:`xijian_api.mcp.resources` — read-only resource views.
  只读资源视图。
* :mod:`xijian_api.mcp.prompts`   — prompt templates.
  提示词模板。
* :mod:`xijian_api.mcp.tools`     — tool modules organised by domain
  (characters, worlds, memory, npcs, economy, events, sessions,
  settings, files, desktop, protection).
  按领域组织的工具模块 (角色、世界、记忆、NPC、经济、事件、会话、设置、文件、桌面、防护)。

Design decisions
================

* **Single endpoint** — ``POST /v1/mcp`` accepts a JSON-RPC 2.0
  request (or a batch) and returns the matching response.  Stateless
  so it scales trivially.
  **单一端点** — ``POST /v1/mcp`` 接受 JSON-RPC 2.0 请求 (或批量请求) 并返回匹配响应。无状态设计，易于水平扩展。
* **A5.2 gate routing** — tools that touch the user's machine
  (file_read / file_write / file_delete / shell / app_launch / …)
  declare an ``action_kind``; the dispatcher runs
  :func:`mcp_stub.check` first and refuses the call on denial.
  Internal domain tools (character CRUD, world management, …) skip
  the gate — they're protected by the API's own validation and only
  mutate in-memory state.
  **A5.2 门禁路由** — 接触用户机器的工具 (file_read / file_write / file_delete / shell / app_launch / …)
  需声明 ``action_kind``；分发器先运行 :func:`mcp_stub.check`，拒绝则拒绝调用。
  内部领域工具 (角色 CRUD、世界管理、…) 绕过门禁 — 由 API 自身的验证保护，仅修改内存状态。
* **Tool naming** — domain-prefixed snake_case
  (``character_create``, ``world_list``, ``memory_search``,
  ``file_read``, ``file_write``, ``file_list`` …) for discoverability.
  **工具命名** — 领域前缀蛇形命名 (``character_create``, ``world_list``, ``memory_search``,
  ``file_read``, ``file_write``, ``file_list`` …) 便于发现。
"""

from __future__ import annotations

from xijian_api.mcp.protocol import handle_request, handle_batch
from xijian_api.mcp.registry import (
    call_tool,
    list_tools,
    register_tool,
    reset_registry,
)

# Import the tools package last so that every tool module registers
# itself via register_tool() at import time.  This side-effect import
# must happen after the registry is importable (it is — the line
# above already imported it).  Placed at the bottom to avoid
# circular-import issues: tools → registry → (no back-ref to __init__).
# 最后导入 tools 包，使每个工具模块在导入时通过 register_tool() 自动注册。
# 这种副作用导入必须在注册表可导入后进行 (已满足 —— 上方已导入)。
# 放在底部避免循环导入：tools → registry → (无反向引用 __init__)。
from xijian_api.mcp import tools as _tools  # noqa: F401

__all__ = [
    "handle_request",
    "handle_batch",
    "call_tool",
    "list_tools",
    "register_tool",
    "reset_registry",
]