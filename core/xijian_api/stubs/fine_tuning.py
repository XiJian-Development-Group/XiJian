"""Stub OAI fine-tuning jobs. 存根 OAI 微调任务。"""

from __future__ import annotations

from xijian_api.utils.time import now_ts


def initial_event(job_id: str) -> dict:
    """Return the event object written when a job is first created.
    返回任务首次创建时写入的事件对象。
    """
    return {
        "id": "evt_0001",
        "object": "fine_tuning.job.event",
        "created_at": now_ts(),
        "level": "info",
        "message": f"stub fine-tune job {job_id} created",
        "data": {},
        "type": "message",
    }


__all__ = ["initial_event"]