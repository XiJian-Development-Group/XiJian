"""Stub session service — message list per session.
存根会话服务 — 每个会话的消息列表。
"""

from __future__ import annotations

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_message_id, gen_session_id
from xijian_api.utils.time import now_ts


def create(payload: dict | None = None) -> dict:
    """Create a new session record.
    创建新的会话记录。
    """
    session_id = gen_session_id()
    record = {
        "id": session_id,
        "object": "session",
        "title": (payload or {}).get("title", "新会话"),
        "messages": [],
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    state.sessions[session_id] = record
    return record


def get(session_id: str) -> dict | None:
    """Return a session record or ``None``.
    返回会话记录或 ``None``。
    """
    return state.sessions.get(session_id)


def append_message(session_id: str, payload: dict) -> dict | None:
    """Append a message to a session. Returns the message record.
    将会话追加一条消息。返回消息记录。
    """
    record = state.sessions.get(session_id)
    if record is None:
        return None
    msg_id = gen_message_id()
    message = {
        "id": msg_id,
        "object": "session.message",
        "session_id": session_id,
        "role": payload.get("role", "user"),
        "content": payload.get("content", ""),
        "created_at": now_ts(),
    }
    record["messages"].append(message)
    record["updated_at"] = now_ts()
    return message


def list_messages(session_id: str) -> list[dict] | None:
    """List all messages in a session.
    列出会话中的所有消息。
    """
    record = state.sessions.get(session_id)
    if record is None:
        return None
    return list(record.get("messages", []))


def delete(session_id: str) -> bool:
    """Delete a session. Returns True if it existed.
    删除会话。存在则返回 True。
    """
    return state.sessions.pop(session_id, None) is not None


__all__ = ["create", "get", "append_message", "list_messages", "delete"]
