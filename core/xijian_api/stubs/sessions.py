"""Stub session service — message list per session."""

from __future__ import annotations

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_message_id, gen_session_id
from xijian_api.utils.time import now_ts


def create(payload: dict | None = None) -> dict:
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
    return state.sessions.get(session_id)


def append_message(session_id: str, payload: dict) -> dict | None:
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
    record = state.sessions.get(session_id)
    if record is None:
        return None
    return list(record.get("messages", []))


def delete(session_id: str) -> bool:
    return state.sessions.pop(session_id, None) is not None


__all__ = ["create", "get", "append_message", "list_messages", "delete"]