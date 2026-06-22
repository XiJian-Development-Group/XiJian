"""Stub memory service — entries CRUD + simple keyword search + async ops.

The memory store starts empty.  Entries are added through the API
(``POST /v1/xijian/memory/entries``) — no preset entries are seeded.
"""

from __future__ import annotations

import random
import threading
import time

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_memory_id
from xijian_api.utils.time import now_ts


def seed_default(character_id: str | None = None) -> None:
    """No-op — the store starts empty by design.

    ``character_id`` is accepted (and ignored) for backwards
    compatibility with prior seed signatures.
    """
    _ = character_id
    return None


def _new_entry(payload: dict) -> dict:
    return {
        "id": gen_memory_id(),
        "object": "memory.entry",
        "character_id": payload.get("character_id"),
        "content": payload.get("content", ""),
        "attributes": payload.get("attributes") or {
            "importance": payload.get("importance", "normal"),
            "decay": payload.get("decay", "normal"),
            "category": payload.get("category"),
        },
        "tags": list(payload.get("tags", [])),
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }


def create(payload: dict) -> dict:
    record = _new_entry(payload)
    state.memory[record["id"]] = record
    return record


def list_all(
    *,
    character_id: str | None = None,
    tags: list[str] | None = None,
    importance: str | None = None,
) -> list[dict]:
    items = list(state.memory.values())
    if character_id:
        items = [it for it in items if it.get("character_id") == character_id]
    if tags:
        items = [it for it in items if any(t in (it.get("tags") or []) for t in tags)]
    if importance:
        items = [
            it
            for it in items
            if (it.get("attributes") or {}).get("importance") == importance
        ]
    return items


def get(entry_id: str) -> dict | None:
    return state.memory.get(entry_id)


def update(entry_id: str, patch: dict) -> dict | None:
    record = state.memory.get(entry_id)
    if record is None:
        return None
    for key in ("content", "tags"):
        if key in patch:
            record[key] = patch[key]
    if "attributes" in patch:
        record["attributes"] = {**(record.get("attributes") or {}), **patch["attributes"]}
    record["updated_at"] = now_ts()
    return record


def delete(entry_id: str) -> bool:
    return state.memory.pop(entry_id, None) is not None


def search(
    *,
    query: str,
    character_id: str | None = None,
    top_k: int = 5,
    min_score: float = 0.0,
) -> list[dict]:
    """Naive keyword search — returns hits with a deterministic score."""
    q = (query or "").lower()
    items = list(state.memory.values())
    if character_id:
        items = [it for it in items if it.get("character_id") == character_id]

    hits: list[dict] = []
    for it in items:
        content = (it.get("content") or "").lower()
        if not content:
            continue
        score = 0.95 if q and q in content else 0.6 + random.random() * 0.25
        if score < min_score:
            continue
        hits.append({"entry": it, "score": round(score, 3)})

    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[: max(1, top_k)]


# ---- async ops ---------------------------------------------------------------


_consolidate_jobs: dict[str, dict] = {}


def schedule_consolidate(job_id: str, character_id: str | None = None) -> None:
    def _run():
        time.sleep(0.05)
        _consolidate_jobs[job_id] = {
            "job_id": job_id,
            "status": "completed",
            "character_id": character_id,
            "finished_at": now_ts(),
        }
    threading.Thread(target=_run, daemon=True).start()


def consolidate_status(job_id: str) -> dict | None:
    return _consolidate_jobs.get(job_id)


def forget(*, entry_ids: list[str] | None = None, decay: str | None = None) -> dict:
    """Forget entries by id or by decay class."""
    removed = 0
    if entry_ids:
        for entry_id in entry_ids:
            if delete(entry_id):
                removed += 1
        return {"forgotten": removed, "by": "ids"}
    if decay:
        for key in list(state.memory.keys()):
            entry = state.memory[key]
            entry_decay = (entry.get("attributes") or {}).get("decay", "normal")
            if entry_decay == decay:
                delete(key)
                removed += 1
        return {"forgotten": removed, "by": "decay", "decay": decay}
    return {"forgotten": 0, "by": "noop"}


__all__ = [
    "seed_default", "create", "list_all", "get", "update", "delete",
    "search", "schedule_consolidate", "consolidate_status", "forget",
]