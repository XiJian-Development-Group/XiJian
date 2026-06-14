"""Stub character service — in-memory CRUD with seeded Yuki."""

from __future__ import annotations

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_character_id
from xijian_api.utils.time import now_ts


_DEFAULT_PERSONA = (
    "雪 (Yuki) 是一位温柔的二次元少女，"
    "性格略带傲娇，喜欢草莓味的冰淇淋，"
    "在东京的现代都市背景下生活。"
)


def seed_default() -> None:
    """Insert ``char_yuki`` if the store is empty."""
    if state.characters:
        return
    yuki_id = "char_yuki"
    state.characters[yuki_id] = {
        "id": yuki_id,
        "object": "character",
        "name": "雪",
        "display_name": "Yuki",
        "persona_doc": _DEFAULT_PERSONA,
        "voice_profile": "voice_ref_yuki",
        "live2d_model": "models/yuki/runtime.moc3",
        "default_emotion": "neutral",
        "tags": ["tsundere", "student"],
        "loaded": False,
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }


def create(payload: dict) -> dict:
    character_id = gen_character_id()
    record = {
        "id": character_id,
        "object": "character",
        "name": payload.get("name", "Unnamed"),
        "display_name": payload.get("display_name", payload.get("name", "Unnamed")),
        "persona_doc": payload.get("persona_doc", ""),
        "voice_profile": payload.get("voice_profile"),
        "live2d_model": payload.get("live2d_model"),
        "default_emotion": payload.get("default_emotion", "neutral"),
        "tags": list(payload.get("tags", [])),
        "loaded": False,
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    state.characters[character_id] = record
    return record


def list_all() -> list[dict]:
    return list(state.characters.values())


def get(character_id: str) -> dict | None:
    return state.characters.get(character_id)


def update(character_id: str, patch: dict) -> dict | None:
    record = state.characters.get(character_id)
    if record is None:
        return None
    for key in ("name", "display_name", "persona_doc", "voice_profile",
                "live2d_model", "default_emotion", "tags"):
        if key in patch:
            record[key] = patch[key]
    record["updated_at"] = now_ts()
    return record


def delete(character_id: str) -> bool:
    return state.characters.pop(character_id, None) is not None


def set_loaded(character_id: str, loaded: bool) -> dict | None:
    record = state.characters.get(character_id)
    if record is None:
        return None
    record["loaded"] = loaded
    record["updated_at"] = now_ts()
    return record


def get_state(character_id: str) -> dict | None:
    record = state.characters.get(character_id)
    if record is None:
        return None
    return {
        "character_id": character_id,
        "affection": 50,
        "mood": "neutral",
        "recent_memory_summary": f"最近的互动：与 {record.get('display_name', '?')} 的若干对话。",
        "updated_at": now_ts(),
    }


def update_state(character_id: str, patch: dict, *, protection_enabled: bool) -> tuple[dict | None, str | None]:
    """Apply ``patch`` to the character state.

    Returns ``(state_record, error_key)``.  When ``protection_enabled``
    is ``False`` the function refuses with ``error_key="protection_disabled"``.
    """
    record = state.characters.get(character_id)
    if record is None:
        return None, "not_found"
    if not protection_enabled:
        return None, "protection_disabled"
    state_record = get_state(character_id)
    for key in ("affection", "mood", "recent_memory_summary"):
        if key in patch:
            state_record[key] = patch[key]
    state_record["updated_at"] = now_ts()
    return state_record, None


__all__ = [
    "seed_default", "create", "list_all", "get",
    "update", "delete", "set_loaded", "get_state", "update_state",
]