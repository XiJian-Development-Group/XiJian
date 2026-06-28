"""Stub interaction service — pre-seeded with two canonical demos.

The store starts with a small library of canonical interactions
(``int_hug`` safe, ``int_kiss`` soft-NSFW) so endpoint tests and the
demo character ``char_yuki`` always have a known id to trigger.
Operators add their own interactions via ``POST /v1/xijian/interactions``
or the generation endpoint; the demo records stay put for parity.
"""

from __future__ import annotations

import threading
import time

from xijian_api.stubs import state
from xijian_api.utils.time import now_ts


_COOLDOWNS: dict[str, float] = {}
_COOLDOWN_LOCK = threading.Lock()


# Canonical interaction ids used by docs / tests.
INTERACTION_HUG = "int_hug"
INTERACTION_KISS = "int_kiss"


def seed_default() -> None:
    """Populate the canonical interaction library.

    Idempotent: re-seeding is a no-op once ``int_hug`` exists.  The
    set mirrors the spec's "default library" — operators can extend
    or replace it freely without touching this function.
    """
    if INTERACTION_HUG in state.interactions:
        return
    now = now_ts()
    defaults = [
        {
            "id": INTERACTION_HUG,
            "label": "Hug",
            "nsfw_level": "safe",
            "cooldown_seconds": 0,
            "animation": "hug_idle",
            "responses": [
                "轻轻地抱住了主人，感受到对方的心跳。",
                "双手环绕，把头靠在主人的肩膀上。",
            ],
        },
        {
            "id": INTERACTION_KISS,
            "label": "Kiss",
            "nsfw_level": "soft",
            "cooldown_seconds": 0,
            "animation": "kiss_soft",
            "responses": [
                "温柔地凑近，在主人的脸颊上轻轻一吻。",
            ],
        },
    ]
    for record in defaults:
        record.setdefault("object", "interaction")
        record["created_at"] = now
        record["updated_at"] = now
        state.interactions[record["id"]] = record


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


__all__ = [
    "INTERACTION_HUG",
    "INTERACTION_KISS",
    "seed_default", "list_all", "get", "trigger",
]