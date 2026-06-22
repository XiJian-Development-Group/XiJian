"""Stub interaction service — empty by design.

Interactions are operator-defined and registered explicitly via
``POST /v1/xijian/interactions``.  Cooldown + NSFW gating still apply
to whatever records end up in the store.
"""

from __future__ import annotations

import threading
import time

from xijian_api.stubs import state


_COOLDOWNS: dict[str, float] = {}
_COOLDOWN_LOCK = threading.Lock()


def seed_default() -> None:
    """No-op — the store starts empty by design."""
    return None


def list_all() -> list[dict]:
    return list(state.interactions.values())


def get(interaction_id: str) -> dict | None:
    return state.interactions.get(interaction_id)


def trigger(
    interaction_id: str,
    *,
    character_id: str | None = None,
    context: dict | None = None,
    nsfw_allowed: bool = False,
) -> dict:
    """Return a trigger result envelope."""
    record = state.interactions.get(interaction_id)
    if record is None:
        return {
            "accepted": False,
            "reason": "interaction_not_found",
            "interaction_id": interaction_id,
        }
    nsfw_level = record.get("nsfw_level", "safe")
    if nsfw_level in {"soft", "explicit"} and not nsfw_allowed:
        return {
            "accepted": False,
            "reason": "nsfw_blocked",
            "interaction_id": interaction_id,
            "nsfw_level": nsfw_level,
        }
    cooldown = int(record.get("cooldown_seconds", 0))
    if cooldown > 0:
        with _COOLDOWN_LOCK:
            last = _COOLDOWNS.get(interaction_id, 0.0)
            now = time.time()
            if now - last < cooldown:
                return {
                    "accepted": False,
                    "reason": "cooldown",
                    "interaction_id": interaction_id,
                    "retry_after_seconds": int(cooldown - (now - last)),
                }
            _COOLDOWNS[interaction_id] = now

    import random
    responses = record.get("responses", [])
    response = random.choice(responses) if responses else ""
    return {
        "accepted": True,
        "interaction_id": interaction_id,
        "character_id": character_id,
        "response": response,
        "animation": record.get("animation"),
        "context": context or {},
    }


__all__ = ["seed_default", "list_all", "get", "trigger"]