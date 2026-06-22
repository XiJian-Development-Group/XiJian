"""Stub world service — empty by design.

Worlds are operator-created resources; the store starts empty.
"""

from __future__ import annotations

from xijian_api.stubs import state
from xijian_api.utils.time import now_ts


def seed_default() -> None:
    """No-op — the store starts empty by design."""
    return None


def list_all() -> list[dict]:
    return list(state.worlds.values())


def get(world_id: str) -> dict | None:
    return state.worlds.get(world_id)


def transition(world_id: str, payload: dict) -> dict | None:
    record = state.worlds.get(world_id)
    if record is None:
        return None
    record["location"] = payload.get("to_location", record.get("location"))
    record["last_transport"] = payload.get("transport")
    record["last_transition_at"] = now_ts()
    record["updated_at"] = now_ts()
    return record


def update_state(world_id: str, patch: dict, *, protection_enabled: bool) -> tuple[dict | None, str | None]:
    record = state.worlds.get(world_id)
    if record is None:
        return None, "not_found"
    if not protection_enabled:
        return None, "protection_disabled"
    state_values = record.setdefault("state", {})
    for key, value in patch.items():
        if key in {"economy", "health", "diet", "stamina", "mentality"}:
            state_values[key] = value
    record["updated_at"] = now_ts()
    return state_values, None


def add_event(world_id: str, payload: dict) -> dict | None:
    record = state.worlds.get(world_id)
    if record is None:
        return None
    events = record.setdefault("events", [])
    event = {
        "id": f"event_{len(events) + 1:04d}",
        "kind": payload.get("kind", "event"),
        "description": payload.get("description", ""),
        "occurred_at": now_ts(),
        "data": payload.get("data", {}),
    }
    events.append(event)
    record["updated_at"] = now_ts()
    return event


def get_state(world_id: str) -> dict | None:
    record = state.worlds.get(world_id)
    if record is None:
        return None
    return {
        "world_id": world_id,
        "location": record.get("location"),
        "state": record.get("state", {}),
        "updated_at": record.get("updated_at"),
    }


__all__ = [
    "seed_default", "list_all", "get", "transition",
    "update_state", "add_event", "get_state",
]