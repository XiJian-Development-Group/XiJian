"""Stub OAI Assistants — minimal but schema-compliant. OAI Assistant 存根 — 最小但符合模式。"""

from __future__ import annotations

from xijian_api.utils.time import now_ts


RUN_STATUSES = {"queued", "in_progress", "completed", "failed", "cancelled", "expired"}


def initial_message(thread_id: str, content: str, role: str = "user", message_id: str = "") -> dict:
    """Create a minimal thread message dict. 创建最小的线程消息字典。"""
    return {
        "id": message_id,
        "object": "thread.message",
        "created_at": now_ts(),
        "thread_id": thread_id,
        "role": role,
        "content": [{"type": "text", "text": {"value": content}}],
        "status": "completed",
    }


def initial_run(thread_id: str, assistant_id: str, run_id: str) -> dict:
    """Create a minimal thread run dict. 创建最小的线程运行字典。"""
    return {
        "id": run_id,
        "object": "thread.run",
        "created_at": now_ts(),
        "thread_id": thread_id,
        "assistant_id": assistant_id,
        "status": "queued",
        "model": "stub-model",
        "instructions": "",
        "tools": [],
        "metadata": {},
    }


__all__ = ["initial_message", "initial_run", "RUN_STATUSES"]