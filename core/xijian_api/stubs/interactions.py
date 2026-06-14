"""Stub interaction service — 3 preset interactions with cooldown + NSFW gating."""

from __future__ import annotations

import threading
import time

from xijian_api.stubs import state


_COOLDOWNS: dict[str, float] = {}
_COOLDOWN_LOCK = threading.Lock()


def seed_default() -> None:
    if state.interactions:
        return
    state.interactions["int_hug"] = {
        "id": "int_hug",
        "object": "interaction",
        "name": "拥抱",
        "nsfw_level": "safe",
        "category": "affection",
        "cooldown_seconds": 60,
        "requires_state": {"intimacy": {"min": 0}},
        "responses": ["轻轻地拥抱了你。", "犹豫了一下，但还是张开了双臂。"],
        "animation": "hug_01",
    }
    state.interactions["int_pet"] = {
        "id": "int_pet",
        "object": "interaction",
        "name": "摸头",
        "nsfw_level": "safe",
        "category": "affection",
        "cooldown_seconds": 30,
        "requires_state": {"intimacy": {"min": 0}},
        "responses": ["微微低下头，让你摸她的头发。", "嘟囔着'别弄乱了'，但没有躲开。"],
        "animation": "pet_head_01",
    }
    state.interactions["int_kiss"] = {
        "id": "int_kiss",
        "object": "interaction",
        "name": "亲吻",
        "nsfw_level": "soft",
        "category": "intimacy",
        "cooldown_seconds": 120,
        "requires_state": {"intimacy": {"min": 20}},
        "responses": ["脸颊泛起红晕，轻轻踮起脚尖。"],
        "animation": "kiss_01",
    }


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