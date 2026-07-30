"""MCP tools for the memory domain.
MCP 记忆域工具。

Wraps the in-memory memory stub (:mod:`xijian_api.stubs.memory`) as MCP
tools registered with :mod:`xijian_api.mcp.registry`.  The memory system
holds per-character long/short-term entries with importance, decay, and
recall ranking per the A1.2 spec.
将内存记忆桩层封装为 MCP 工具。记忆系统按 A1.2 规范存储每个角色的长/短期记忆条目，
带重要度、衰减和回忆排序。

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stub's own
input validation.
这些是内部领域工具 (``action_kind=None``)：仅操作内存状态，绕过 A5.2 门禁。

Tools registered / 已注册工具
----------------

Entry CRUD / 条目增删改查:

* ``memory_create``        — create a memory entry / 创建记忆条目
* ``memory_list``          — list entries (filterable) / 列出条目（可筛选）
* ``memory_get``           — fetch an entry by id / 按 ID 获取条目
* ``memory_forget``        — forget entries by id or decay class / 按 ID 或衰减类型遗忘条目

Search & recall / 搜索与回忆:

* ``memory_search``        — legacy keyword search / 传统关键词搜索
* ``memory_recall``        — A1.2 recall search (importance × decay ranking) / A1.2 回忆搜索
* ``memory_load_context``  — assemble the per-character dialogue context / 组装每个角色的对话上下文
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import memory as memory_stub


# ---------------------------------------------------------------------------
# Entry CRUD handlers / 条目增删改查处理器
# ---------------------------------------------------------------------------


_MEMORY_CREATE_FIELDS = (
    "character_id", "type", "importance", "tags", "source",
    "source_ref_id", "decay_score", "access_count", "last_access_at",
    "attributes",
)


def _memory_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    character_id = args.get("character_id")
    if not character_id:
        raise ToolError("character_id is required")
    content = args.get("content")
    if not content:
        raise ToolError("content is required")
    payload: dict[str, Any] = {"character_id": character_id, "content": content}
    for key in _MEMORY_CREATE_FIELDS:
        if key in args:
            payload[key] = args[key]
    return memory_stub.create(payload)


def _memory_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    for key in ("character_id", "tags", "importance", "type"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    return memory_stub.list_all(**kwargs)


def _memory_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    entry_id = args.get("entry_id")
    if not entry_id:
        raise ToolError("entry_id is required")
    record = memory_stub.get(entry_id)
    if record is None:
        raise ToolError(f"memory entry {entry_id!r} not found")
    return record


def _memory_forget(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    kwargs: dict[str, Any] = {}
    if "entry_ids" in args and args["entry_ids"] is not None:
        kwargs["entry_ids"] = args["entry_ids"]
    if "decay" in args and args["decay"] is not None:
        kwargs["decay"] = args["decay"]
    if not kwargs:
        raise ToolError("either entry_ids or decay must be provided")
    return memory_stub.forget(**kwargs)


# ---------------------------------------------------------------------------
# Search & recall handlers / 搜索与回忆处理器
# ---------------------------------------------------------------------------


def _memory_search(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    query = args.get("query")
    if not query:
        raise ToolError("query is required")
    kwargs: dict[str, Any] = {"query": query}
    for key in ("character_id", "top_k", "min_score"):
        if key in args and args[key] is not None:
            kwargs[key] = args[key]
    return memory_stub.search(**kwargs)


def _memory_recall(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    character_id = args.get("character_id")
    if not character_id:
        raise ToolError("character_id is required")
    query = args.get("query")
    if not query:
        raise ToolError("query is required")
    kwargs: dict[str, Any] = {"character_id": character_id, "query": query}
    if "top_k" in args and args["top_k"] is not None:
        kwargs["top_k"] = args["top_k"]
    return memory_stub.recall_search(**kwargs)


def _memory_load_context(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    character_id = args.get("character_id")
    if not character_id:
        raise ToolError("character_id is required")
    kwargs: dict[str, Any] = {}
    if "budget_tokens" in args and args["budget_tokens"] is not None:
        kwargs["budget_tokens"] = args["budget_tokens"]
    return memory_stub.load_context(character_id, **kwargs)


# ---------------------------------------------------------------------------
# Registration / 注册
# ---------------------------------------------------------------------------


register_tool(
    name="memory_create",
    description="Create a memory entry (long or short term) for a character. / 为角色创建记忆条目（长期或短期）。",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "Owning character id. / 所属角色 ID。"},
            "content": {"type": "string", "description": "Memory content text. / 记忆内容文本。"},
            "type": {"type": "string", "enum": ["long", "short"], "description": "Memory type / 记忆类型"},
            "importance": {"type": ["number", "string"], "description": "Importance in [0,1] or a label 'high'/'normal'/'low'. / 重要度"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "source": {"type": "string", "description": "Provenance: dialogue/manual/world_event/derived. / 来源"},
            "source_ref_id": {"type": "string"},
            "decay_score": {"type": ["number", "string"], "description": "Initial decay score / 初始衰减分"},
            "access_count": {"type": "integer"},
            "last_access_at": {"type": "integer"},
            "attributes": {"type": "object", "description": "Legacy attributes block. / 遗留属性块。"},
        },
        "required": ["character_id", "content"],
    },
    handler=_memory_create,
    action_kind=None,
)


register_tool(
    name="memory_list",
    description="List memory entries, optionally filtered by character, tags, importance, or type. / 列出记忆条目，可选按角色、标签、重要度或类型筛选。",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "importance": {"type": "string", "description": "Legacy importance label / 遗留重要度标签"},
            "type": {"type": "string", "enum": ["long", "short"]},
        },
        "required": [],
    },
    handler=_memory_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="memory_get",
    description="Fetch a single memory entry by id. / 按 ID 获取单个记忆条目。",
    input_schema={
        "type": "object",
        "properties": {
            "entry_id": {"type": "string", "description": "The memory entry id to fetch. / 要获取的记忆条目 ID。"},
        },
        "required": ["entry_id"],
    },
    handler=_memory_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="memory_search",
    description="Keyword search over memory entries; returns entries ranked by match score. / 记忆条目的关键词搜索；返回按匹配分排序的条目。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query text. / 搜索查询文本。"},
            "character_id": {"type": "string", "description": "Restrict to a character's entries. / 限制到角色的条目。"},
            "top_k": {"type": "integer", "minimum": 1, "description": "Maximum results (default 5). / 最大结果数。"},
            "min_score": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Minimum match score. / 最低匹配分。"},
        },
        "required": ["query"],
    },
    handler=_memory_search,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="memory_recall",
    description="A1.2 recall search: rank entries by text match × importance × live decay score, with recency bonus. / A1.2 回忆搜索：按文本匹配×重要度×实时衰减分排序，带近期奖励。",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "Character whose entries to recall. / 要回忆其条目的角色。"},
            "query": {"type": "string", "description": "Recall query text. / 回忆查询文本。"},
            "top_k": {"type": "integer", "minimum": 1, "description": "Maximum results (default 5). / 最大结果数。"},
        },
        "required": ["character_id", "query"],
    },
    handler=_memory_recall,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="memory_load_context",
    description="Assemble the per-character memory context (long + short term) for a new dialogue, trimmed to the token budget. / 为新对话组装每个角色的记忆上下文（长期+短期），裁剪到 Token 预算。",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "Character to load context for. / 要加载上下文的角色。"},
            "budget_tokens": {"type": "integer", "minimum": 0, "description": "Token budget override / Token 预算覆盖"},
        },
        "required": ["character_id"],
    },
    handler=_memory_load_context,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="memory_forget",
    description="Forget memory entries by id list or by decay class ('fast'/'normal'/'slow'). / 按 ID 列表或衰减类型（'fast'/'normal'/'slow'）遗忘记忆条目。",
    input_schema={
        "type": "object",
        "properties": {
            "entry_ids": {"type": "array", "items": {"type": "string"}, "description": "Entry ids to forget. / 要遗忘的条目 ID。"},
            "decay": {"type": "string", "description": "Decay class to forget / 要遗忘的衰减类型"},
        },
        "required": [],
    },
    handler=_memory_forget,
    action_kind=None,
    annotations={"destructiveHint": True},
)
