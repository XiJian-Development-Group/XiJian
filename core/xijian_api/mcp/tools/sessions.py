"""MCP tools for the session domain.
MCP 会话域工具。

Wraps the in-memory session stub (:mod:`xijian_api.stubs.sessions`) as
MCP tools registered with :mod:`xijian_api.mcp.registry`.  A "session"
is a per-conversation message log keyed by session id.
将内存会话桩层封装为 MCP 工具。"会话"是按会话 ID 索引的每个对话的消息日志。

These are internal domain tools (``action_kind=None``): they only touch
in-memory state, so they skip the A5.2 gate and rely on the stub's own
input validation.  The session stub exposes no ``list_all`` helper, so
``session_list`` reads the ``state.sessions`` container directly.
内部领域工具，仅操作内存状态，绕过 A5.2 门禁。会话桩层未暴露 ``list_all`` 辅助函数，
因此 ``session_list`` 直接读取 ``state.sessions`` 容器。

Tools registered / 已注册工具
----------------

* ``session_create``         — create a session / 创建会话
* ``session_get``            — fetch a session by id / 按 ID 获取会话
* ``session_list``           — list every session / 列出所有会话
* ``session_append_message`` — append a message to a session / 向会话追加消息
* ``session_list_messages``  — list messages in a session / 列出会话中的消息
* ``session_delete``         — delete a session / 删除会话
"""

from __future__ import annotations

from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import sessions as sessions_stub
from xijian_api.stubs import state


# ---------------------------------------------------------------------------
# Handlers / 处理器
# ---------------------------------------------------------------------------


def _session_create(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    payload: dict[str, Any] = {}
    for key in ("title", "character_id", "world_id"):
        if key in args and args[key] is not None:
            payload[key] = args[key]
    return sessions_stub.create(payload)


def _session_get(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    session_id = args.get("session_id")
    if not session_id:
        raise ToolError("session_id is required")
    record = sessions_stub.get(session_id)
    if record is None:
        raise ToolError(f"session {session_id!r} not found")
    return record


def _session_list(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    return list(state.sessions.values())


def _session_append_message(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    session_id = args.get("session_id")
    if not session_id:
        raise ToolError("session_id is required")
    role = args.get("role")
    if not role:
        raise ToolError("role is required")
    content = args.get("content")
    if content is None:
        raise ToolError("content is required")
    payload: dict[str, Any] = {"role": role, "content": content}
    if "name" in args and args["name"] is not None:
        payload["name"] = args["name"]
    message = sessions_stub.append_message(session_id, payload)
    if message is None:
        raise ToolError(f"session {session_id!r} not found")
    return message


def _session_list_messages(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    session_id = args.get("session_id")
    if not session_id:
        raise ToolError("session_id is required")
    messages = sessions_stub.list_messages(session_id)
    if messages is None:
        raise ToolError(f"session {session_id!r} not found")
    return messages


def _session_delete(args: dict[str, Any], ctx: dict[str, Any]) -> dict:
    session_id = args.get("session_id")
    if not session_id:
        raise ToolError("session_id is required")
    if not sessions_stub.delete(session_id):
        raise ToolError(f"session {session_id!r} not found")
    return {"deleted": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# Registration / 注册
# ---------------------------------------------------------------------------


register_tool(
    name="session_create",
    description="Create a new session (per-conversation message log). / 创建新会话（每对话消息日志）。",
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Session title (defaults to '新会话'). / 会话标题。"},
            "character_id": {"type": "string", "description": "Optional character id / 可选角色 ID"},
            "world_id": {"type": "string", "description": "Optional world id / 可选世界 ID"},
        },
        "required": [],
    },
    handler=_session_create,
    action_kind=None,
)


register_tool(
    name="session_get",
    description="Fetch a single session by id. / 按 ID 获取单个会话。",
    input_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "The session id to fetch. / 要获取的会话 ID。"},
        },
        "required": ["session_id"],
    },
    handler=_session_get,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="session_list",
    description="List every session record. / 列出每个会话记录。",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_session_list,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="session_append_message",
    description="Append a message (role/content) to a session's message log. / 向会话的消息日志追加消息（角色/内容）。",
    input_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "The session id to append to. / 要追加到的会话 ID。"},
            "role": {"type": "string", "description": "Message role (e.g. 'user', 'assistant'). / 消息角色。"},
            "content": {"type": "string", "description": "Message content text. / 消息内容文本。"},
            "name": {"type": "string", "description": "Optional sender name. / 可选的发送者名称。"},
        },
        "required": ["session_id", "role", "content"],
    },
    handler=_session_append_message,
    action_kind=None,
)


register_tool(
    name="session_list_messages",
    description="List every message in a session, oldest-first. / 列出会话中的每条消息，最早优先。",
    input_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "The session id to list messages for. / 要列出消息的会话 ID。"},
        },
        "required": ["session_id"],
    },
    handler=_session_list_messages,
    action_kind=None,
    annotations={"readOnlyHint": True},
)


register_tool(
    name="session_delete",
    description="Delete a session by id. / 按 ID 删除会话。",
    input_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "The session id to delete. / 要删除的会话 ID。"},
        },
        "required": ["session_id"],
    },
    handler=_session_delete,
    action_kind=None,
    annotations={"destructiveHint": True},
)
