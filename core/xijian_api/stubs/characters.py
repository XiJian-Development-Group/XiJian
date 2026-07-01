"""Stub character service — in-memory CRUD.

The store starts with one demo character (``char_yuki``) so endpoints
that exercise the canonical scenario (load/unload, state, interactions,
…) have a known id to work with.  Operators add their own characters
via ``POST /v1/xijian/characters``; the demo record is intentionally
*not* removed automatically so dev workflows can rely on it.
"""

from __future__ import annotations

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_character_id
from xijian_api.utils.time import now_ts


#: Canonical demo character id used across the spec / docs / tests.
DEFAULT_CHARACTER_ID = "char_yuki"


def seed_default() -> None:
    """Populate the canonical demo character ``char_yuki``.

    Idempotent: if a record already exists under ``char_yuki`` we leave
    it untouched.  Any user-created characters are likewise preserved.
    """
    if DEFAULT_CHARACTER_ID in state.characters:
        return
    now = now_ts()
    record = {
        "id": DEFAULT_CHARACTER_ID,
        "object": "character",
        "name": "Yuki",
        "display_name": "Yuki",
        "persona_doc": (
            "Yuki 是主人的 AI 助手，性格温和、细心，喜欢猫和安静的氛围。"
            "她会用轻柔的语气回应主人的日常点滴，偶尔主动问候。"
        ),
        "voice_profile": "melo_zh_female_warm_v1",
        "live2d_model": None,
        "default_emotion": "neutral",
        "tags": ["demo", "default", "ai-companion"],
        "loaded": False,
        "created_at": now,
        "updated_at": now,
    }
    state.characters[DEFAULT_CHARACTER_ID] = record


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
    """Return the character state, delegating to the A3.2 state stub.

    Kept for backward compatibility with the v1 character state
    endpoints (``affection`` / ``mood`` / ``recent_memory_summary``).
    The A3.2 numeric fields are merged in when present so the old
    endpoint gains them for free.
    """
    record = state.characters.get(character_id)
    if record is None:
        return None
    # Lazy import to avoid a circular dependency at module-load time.
    from xijian_api.stubs import character_state as cs_stub

    summary = cs_stub.summary(character_id) or {}
    return {
        "character_id": character_id,
        # Legacy fields — preserved verbatim so the v1 test suite
        # (``test_character_state_round_trip``) keeps passing.
        "affection": 50,
        "mood": "neutral",
        "recent_memory_summary": f"最近的互动：与 {record.get('display_name', '?')} 的若干对话。",
        "updated_at": now_ts(),
        # A3.2 fields — present whenever the character has a state
        # record; absent otherwise so a never-touched character
        # returns the v1 shape exactly.
        **(
            {
                "values": summary.get("values"),
                "max": summary.get("max"),
                "status": summary.get("status"),
                "can_dialogue": summary.get("can_dialogue"),
                "active_behavior": summary.get("active_behavior"),
            }
            if summary
            else {}
        ),
    }


def update_state(character_id: str, patch: dict, *, protection_enabled: bool) -> tuple[dict | None, str | None]:
    """Apply ``patch`` to the character state.

    Returns ``(state_record, error_key)``.  When ``protection_enabled``
    is ``False`` the function refuses with ``error_key="protection_disabled"``.

    Legacy fields (``affection`` / ``mood`` / ``recent_memory_summary``)
    are still supported for backward compatibility; numeric A3.2
    fields (``hunger`` / ``thirst`` / ``health`` / ``mood_value``) are
    forwarded to the state stub which performs clamping, log writes,
    and status-machine updates.
    """
    record = state.characters.get(character_id)
    if record is None:
        return None, "not_found"
    if not protection_enabled:
        return None, "protection_disabled"
    # Lazy import — same circular-dependency concern as in get_state.
    from xijian_api.stubs import character_state as cs_stub

    # A3.2 numeric fields.  ``mood_value`` is the v1-friendly name
    # callers can use to set the numeric mood without clashing with
    # the legacy ``mood`` text field.
    numeric_patch: dict = {}
    for key in ("hunger", "thirst", "health"):
        if key in patch:
            numeric_patch[key] = patch[key]
    if "mood_value" in patch:
        numeric_patch["mood"] = patch["mood_value"]
    if numeric_patch:
        cs_stub.apply_patch(
            character_id,
            numeric_patch,
            reason=patch.get("reason", "dialogue"),
            ref_id=patch.get("ref_id"),
        )

    state_record = get_state(character_id)
    for key in ("affection", "mood", "recent_memory_summary"):
        if key in patch:
            state_record[key] = patch[key]
    state_record["updated_at"] = now_ts()
    return state_record, None


__all__ = [
    "DEFAULT_CHARACTER_ID",
    "seed_default", "create", "list_all", "get",
    "update", "delete", "set_loaded", "get_state", "update_state",
]