"""MCP tools for the memory domain.

Wraps the in-memory memory stub (:mod:`xijian_api.stubs.memory`) as MCP
tools registered with :mod:`xijian_api.mcp.registry`.  The memory system
holds per-character long/short-term entries with importance, decay, and
recall ranking per the A1.2 spec.

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stub's own
input validation.

Tools registered
----------------

Entry CRUD:

* ``memory_create``        — create a memory entry
* ``memory_list``          — list entries (filterable)
* ``memory_get``           — fetch an entry by id
* ``memory_forget``        — forget entries by id or decay class

Search & recall:

* ``memory_search``        — legacy keyword search
* ``memory_recall``        — A1.2 recall search (importance × decay ranking)
* ``memory_load_context``  — assemble the per-character dialogue context
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import memory as memory_stub


# ---------------------------------------------------------------------------
# Entry CRUD handlers
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
# Search & recall handlers
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
# Registration
# ---------------------------------------------------------------------------


register_tool(
    name="memory_create",
    description="Create a memory entry (long or short term) for a character.",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "Owning character id."},
            "content": {"type": "string", "description": "Memory content text."},
            "type": {"type": "string", "enum": ["long", "short"], "description": "Memory type; inferred from importance when omitted."},
            "importance": {
                "type": ["number", "string"],
                "description": "Importance in [0,1] or a label 'high'/'normal'/'low'.",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "source": {"type": "string", "description": "Provenance: dialogue/manual/world_event/derived."},
            "source_ref_id": {"type": "string"},
            "decay_score": {
                "type": ["number", "string"],
                "description": "Initial decay score in [0,1] or a label 'fast'/'normal'/'slow'.",
            },
            "access_count": {"type": "integer"},
            "last_access_at": {"type": "integer"},
            "attributes": {"type": "object", "description": "Legacy attributes block."},
        },
        "required": ["character_id", "content"],
    },
    handler=_memory_create,
    action_kind=None,
)


register_tool(
    name="memory_list",
    description="List memory entries, optionally filtered by character, tags, importance, or type.",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "importance": {"type": "string", "description": "Legacy importance label: 'high'/'normal'/'low'."},
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
    description="Fetch a single memory entry by id.",
    input_schema={
        "type": "object",
        "properties": {
            "entry_id": {"type": "string", "description": "The memory entry id to fetch."},
        },
        "required": ["entry_id"],
    },
    handler=_memory_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="memory_search",
    description="Keyword search over memory entries; returns entries ranked by match score.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query text."},
            "character_id": {"type": "string", "description": "Restrict to a character's entries."},
            "top_k": {"type": "integer", "minimum": 1, "description": "Maximum results (default 5)."},
            "min_score": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Minimum match score."},
        },
        "required": ["query"],
    },
    handler=_memory_search,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="memory_recall",
    description="A1.2 recall search: rank entries by text match × importance × live decay score, with recency bonus.",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "Character whose entries to recall."},
            "query": {"type": "string", "description": "Recall query text."},
            "top_k": {"type": "integer", "minimum": 1, "description": "Maximum results (default 5)."},
        },
        "required": ["character_id", "query"],
    },
    handler=_memory_recall,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="memory_load_context",
    description="Assemble the per-character memory context (long + short term) for a new dialogue, trimmed to the token budget.",
    input_schema={
        "type": "object",
        "properties": {
            "character_id": {"type": "string", "description": "Character to load context for."},
            "budget_tokens": {"type": "integer", "minimum": 0, "description": "Token budget override; derived from config when omitted."},
        },
        "required": ["character_id"],
    },
    handler=_memory_load_context,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="memory_forget",
    description="Forget memory entries by id list or by decay class ('fast'/'normal'/'slow').",
    input_schema={
        "type": "object",
        "properties": {
            "entry_ids": {"type": "array", "items": {"type": "string"}, "description": "Entry ids to forget."},
            "decay": {"type": "string", "description": "Decay class to forget: 'fast'/'normal'/'slow'."},
        },
        "required": [],
    },
    handler=_memory_forget,
    action_kind=None,
    annotations={"destructiveHint": True},
)
