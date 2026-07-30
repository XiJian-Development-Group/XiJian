"""Stub character service — in-memory CRUD.
角色存根服务 — 内存中的增删改查。

The store starts with one demo character (``char_yuki``) so endpoints
that exercise the canonical scenario (load/unload, state, interactions,
…) have a known id to work with.  Operators add their own characters
via ``POST /v1/xijian/characters``; the demo record is intentionally
*not* removed automatically so dev workflows can rely on it.

存储以一个人物 (``char_yuki``) 开始，以便执行规范场景
(加载/卸载、状态、互动等) 的端点有已知 ID 可用。
运营者通过 ``POST /v1/xijian/characters`` 添加自己的人物；
演示记录有意*不*自动删除，以便开发工作流可依赖它。
"""

from __future__ import annotations

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_character_id
from xijian_api.utils.time import now_ts


#: Canonical demo character id used across the spec / docs / tests.
#: 贯穿规范 / 文档 / 测试的规范演示人物 ID。
DEFAULT_CHARACTER_ID = "char_yuki"


def seed_default() -> None:
    """Populate the canonical demo character ``char_yuki``.

    Idempotent: if a record already exists under ``char_yuki`` we leave
    it untouched.  Any user-created characters are likewise preserved.
    填充规范演示人物 ``char_yuki``。
    幂等：如果 ``char_yuki`` 下已存在记录，我们不触碰它。
    任何用户创建的人物同样保留。
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
        "default_emotion": "neutral",
        "tags": ["demo", "default", "ai-companion"],
        "loaded": False,
        "created_at": now,
        "updated_at": now,
    }
    state.characters[DEFAULT_CHARACTER_ID] = record


def create(payload: dict) -> dict:
    """Create a new character record. 创建新的人物记录。"""
    character_id = gen_character_id()
    record = {
        "id": character_id,
        "object": "character",
        "name": payload.get("name", "Unnamed"),
        "display_name": payload.get("display_name", payload.get("name", "Unnamed")),
        "persona_doc": payload.get("persona_doc", ""),
        "voice_profile": payload.get("voice_profile"),
        "default_emotion": payload.get("default_emotion", "neutral"),
        "tags": list(payload.get("tags", [])),
        "loaded": False,
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    state.characters[character_id] = record
    return record


def list_all() -> list[dict]:
    """List all character records. 列出所有人物记录。"""
    return list(state.characters.values())


def get(character_id: str) -> dict | None:
    """Get a character record by id. 通过 ID 获取人物记录。"""
    return state.characters.get(character_id)


def update(character_id: str, patch: dict) -> dict | None:
    """Update a character record with patch fields. 用补丁字段更新人物记录。"""
    record = state.characters.get(character_id)
    if record is None:
        return None
    for key in ("name", "display_name", "persona_doc", "voice_profile",
                "default_emotion", "tags"):
        if key in patch:
            record[key] = patch[key]
    record["updated_at"] = now_ts()
    return record


def delete(character_id: str) -> bool:
    """Delete a character record by id. 通过 ID 删除人物记录。"""
    return state.characters.pop(character_id, None) is not None


def set_loaded(character_id: str, loaded: bool) -> dict | None:
    """Set the loaded flag on a character. 设置人物的 loaded 标志。"""
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

    返回人物状态，委托给 A3.2 状态存根。
    为与 v1 人物状态端点向后兼容而保留。
    A3.2 数值字段在存在时被合并，以便旧端点免费获得它们。
    """
    record = state.characters.get(character_id)
    if record is None:
        return None
    # Lazy import to avoid a circular dependency at module-load time.
    # 惰性导入以避免模块加载时的循环依赖。
    from xijian_api.stubs import character_state as cs_stub

    summary = cs_stub.summary(character_id) or {}
    return {
        "character_id": character_id,
        # Legacy fields — preserved verbatim so the v1 test suite
        # (``test_character_state_round_trip``) keeps passing.
        # 旧版字段 — 原样保留，以便 v1 测试套件继续通过。
        "affection": 50,
        "mood": "neutral",
        "recent_memory_summary": f"最近的互动：与 {record.get('display_name', '?')} 的若干对话。",
        "updated_at": now_ts(),
        # A3.2 fields — present whenever the character has a state
        # record; absent otherwise so a never-touched character
        # returns the v1 shape exactly.
        # A3.2 字段 — 人物有状态记录时存在；否则不存在，
        # 以便从未被触碰的人物精确返回 v1 形状。
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

    将 ``patch`` 应用到人物状态。
    返回 ``(state_record, error_key)``。当 ``protection_enabled``
    为 ``False`` 时，函数以 ``error_key="protection_disabled"`` 拒绝。
    旧版字段仍受支持以保持向后兼容；数值型 A3.2 字段
    被转发到状态存根，由它执行钳制、日志写入和状态机更新。
    """
    record = state.characters.get(character_id)
    if record is None:
        return None, "not_found"
    if not protection_enabled:
        return None, "protection_disabled"
    # Lazy import — same circular-dependency concern as in get_state.
    # 惰性导入 — 与 get_state 中相同的循环依赖问题。
    from xijian_api.stubs import character_state as cs_stub

    # A3.2 numeric fields.  ``mood_value`` is the v1-friendly name
    # callers can use to set the numeric mood without clashing with
    # the legacy ``mood`` text field.
    # A3.2 数值字段。``mood_value`` 是 v1 友好的名称，
    # 调用者可以用它设置数值情绪而不会与旧版文本 ``mood`` 字段冲突。
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


# -------------------------------------------------------------------------
# A3-01 Resource table CRUD helpers  A3-01 资源表增删改查辅助函数
#
# Six resource tables store per-character asset metadata:
# 六个资源表存储每角色资产元数据：
#   character_models        — {character_id: {model_id, model_url, format, ...}}
#   character_motions       — {character_id: {motion_id, animation_ref, ...}}
#   character_voices        — {character_id: {voice_id, profile_ref, ...}}
#   character_handwritings  — {character_id: {handwriting_id, style_ref, ...}}
#   character_styles        — {character_id: {style_id, art_style_ref, ...}}
#   character_asset_cache   — {character_id: {asset_key, cached_data, ...}}
#
# Each bucket is keyed by character_id.  The value is a dict whose keys
# are resource ids (or asset keys for the cache).  CRUD follows the same
# pattern used by the other stubs: create via allocate-id + store, list by
# character, get by id, update via patch, delete by id.
# 每个桶以 character_id 为键。值是字典，其键为资源 id (或缓存的资产键)。
# CRUD 遵循其他存根使用的相同模式：通过分配 ID + 存储创建，按角色列出，
# 按 ID 获取，通过补丁更新，按 ID 删除。
# -------------------------------------------------------------------------


# ---- character_models ------------------------------------------------------

def create_model(character_id: str, payload: dict) -> dict:
    """Create a new character model record. 创建新的人物模型记录。"""
    mid = gen_character_id()  # reuse id generator 重用 ID 生成器
    bucket = state.character_models.setdefault(character_id, {})
    record = {
        "id": mid,
        "character_id": character_id,
        "object": "character.model",
        "name": payload.get("name", "Unnamed Model"),
        "model_url": payload.get("model_url", ""),
        "format": payload.get("format", "glb"),
        "tags": list(payload.get("tags", [])),
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    bucket[mid] = record
    return record


def list_models(character_id: str) -> list[dict]:
    """List all models for a character. 列出一个角色的所有模型。"""
    bucket = state.character_models.get(character_id, {})
    return list(bucket.values())


def get_model(character_id: str, model_id: str) -> dict | None:
    """Get a specific model record. 获取特定的模型记录。"""
    bucket = state.character_models.get(character_id, {})
    return bucket.get(model_id)


def update_model(character_id: str, model_id: str, patch: dict) -> dict | None:
    """Update a model record with patch fields. 用补丁字段更新模型记录。"""
    bucket = state.character_models.get(character_id, {})
    record = bucket.get(model_id)
    if record is None:
        return None
    for key in ("name", "model_url", "format", "tags"):
        if key in patch:
            record[key] = patch[key]
    record["updated_at"] = now_ts()
    return record


def delete_model(character_id: str, model_id: str) -> bool:
    """Delete a model record by id. 通过 ID 删除模型记录。"""
    bucket = state.character_models.get(character_id, {})
    return bucket.pop(model_id, None) is not None


# ---- character_motions -----------------------------------------------------

def create_motion(character_id: str, payload: dict) -> dict:
    """Create a new character motion record. 创建新的人物动作记录。"""
    mid = gen_character_id()
    bucket = state.character_motions.setdefault(character_id, {})
    record = {
        "id": mid,
        "character_id": character_id,
        "object": "character.motion",
        "name": payload.get("name", "Unnamed Motion"),
        "animation_ref": payload.get("animation_ref", ""),
        "tags": list(payload.get("tags", [])),
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    bucket[mid] = record
    return record


def list_motions(character_id: str) -> list[dict]:
    """List all motions for a character. 列出一个角色的所有动作。"""
    bucket = state.character_motions.get(character_id, {})
    return list(bucket.values())


def get_motion(character_id: str, motion_id: str) -> dict | None:
    """Get a specific motion record. 获取特定的动作记录。"""
    bucket = state.character_motions.get(character_id, {})
    return bucket.get(motion_id)


def update_motion(character_id: str, motion_id: str, patch: dict) -> dict | None:
    """Update a motion record with patch fields. 用补丁字段更新动作记录。"""
    bucket = state.character_motions.get(character_id, {})
    record = bucket.get(motion_id)
    if record is None:
        return None
    for key in ("name", "animation_ref", "tags"):
        if key in patch:
            record[key] = patch[key]
    record["updated_at"] = now_ts()
    return record


def delete_motion(character_id: str, motion_id: str) -> bool:
    """Delete a motion record by id. 通过 ID 删除动作记录。"""
    bucket = state.character_motions.get(character_id, {})
    return bucket.pop(motion_id, None) is not None


# ---- character_voices ------------------------------------------------------

def create_voice(character_id: str, payload: dict) -> dict:
    """Create a new character voice record. 创建新的人物语音记录。"""
    vid = gen_character_id()
    bucket = state.character_voices.setdefault(character_id, {})
    record = {
        "id": vid,
        "character_id": character_id,
        "object": "character.voice",
        "name": payload.get("name", "Unnamed Voice"),
        "profile_ref": payload.get("profile_ref", ""),
        "tags": list(payload.get("tags", [])),
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    bucket[vid] = record
    return record


def list_voices(character_id: str) -> list[dict]:
    """List all voices for a character. 列出一个角色的所有语音。"""
    bucket = state.character_voices.get(character_id, {})
    return list(bucket.values())


def get_voice(character_id: str, voice_id: str) -> dict | None:
    """Get a specific voice record. 获取特定的语音记录。"""
    bucket = state.character_voices.get(character_id, {})
    return bucket.get(voice_id)


def update_voice(character_id: str, voice_id: str, patch: dict) -> dict | None:
    """Update a voice record with patch fields. 用补丁字段更新语音记录。"""
    bucket = state.character_voices.get(character_id, {})
    record = bucket.get(voice_id)
    if record is None:
        return None
    for key in ("name", "profile_ref", "tags"):
        if key in patch:
            record[key] = patch[key]
    record["updated_at"] = now_ts()
    return record


def delete_voice(character_id: str, voice_id: str) -> bool:
    """Delete a voice record by id. 通过 ID 删除语音记录。"""
    bucket = state.character_voices.get(character_id, {})
    return bucket.pop(voice_id, None) is not None


# ---- character_handwritings ------------------------------------------------

def create_handwriting(character_id: str, payload: dict) -> dict:
    """Create a new character handwriting record. 创建新的人物笔迹记录。"""
    hid = gen_character_id()
    bucket = state.character_handwritings.setdefault(character_id, {})
    record = {
        "id": hid,
        "character_id": character_id,
        "object": "character.handwriting",
        "name": payload.get("name", "Unnamed Handwriting"),
        "style_ref": payload.get("style_ref", ""),
        "tags": list(payload.get("tags", [])),
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    bucket[hid] = record
    return record


def list_handwritings(character_id: str) -> list[dict]:
    """List all handwritings for a character. 列出一个角色的所有笔迹。"""
    bucket = state.character_handwritings.get(character_id, {})
    return list(bucket.values())


def get_handwriting(character_id: str, handwriting_id: str) -> dict | None:
    """Get a specific handwriting record. 获取特定的笔迹记录。"""
    bucket = state.character_handwritings.get(character_id, {})
    return bucket.get(handwriting_id)


def update_handwriting(character_id: str, handwriting_id: str, patch: dict) -> dict | None:
    """Update a handwriting record with patch fields. 用补丁字段更新笔迹记录。"""
    bucket = state.character_handwritings.get(character_id, {})
    record = bucket.get(handwriting_id)
    if record is None:
        return None
    for key in ("name", "style_ref", "tags"):
        if key in patch:
            record[key] = patch[key]
    record["updated_at"] = now_ts()
    return record


def delete_handwriting(character_id: str, handwriting_id: str) -> bool:
    """Delete a handwriting record by id. 通过 ID 删除笔迹记录。"""
    bucket = state.character_handwritings.get(character_id, {})
    return bucket.pop(handwriting_id, None) is not None


# ---- character_styles ------------------------------------------------------

def create_style(character_id: str, payload: dict) -> dict:
    """Create a new character style record. 创建新的人物风格记录。"""
    sid = gen_character_id()
    bucket = state.character_styles.setdefault(character_id, {})
    record = {
        "id": sid,
        "character_id": character_id,
        "object": "character.style",
        "name": payload.get("name", "Unnamed Style"),
        "art_style_ref": payload.get("art_style_ref", ""),
        "tags": list(payload.get("tags", [])),
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    bucket[sid] = record
    return record


def list_styles(character_id: str) -> list[dict]:
    """List all styles for a character. 列出一个角色的所有风格。"""
    bucket = state.character_styles.get(character_id, {})
    return list(bucket.values())


def get_style(character_id: str, style_id: str) -> dict | None:
    """Get a specific style record. 获取特定的风格记录。"""
    bucket = state.character_styles.get(character_id, {})
    return bucket.get(style_id)


def update_style(character_id: str, style_id: str, patch: dict) -> dict | None:
    """Update a style record with patch fields. 用补丁字段更新风格记录。"""
    bucket = state.character_styles.get(character_id, {})
    record = bucket.get(style_id)
    if record is None:
        return None
    for key in ("name", "art_style_ref", "tags"):
        if key in patch:
            record[key] = patch[key]
    record["updated_at"] = now_ts()
    return record


def delete_style(character_id: str, style_id: str) -> bool:
    """Delete a style record by id. 通过 ID 删除风格记录。"""
    bucket = state.character_styles.get(character_id, {})
    return bucket.pop(style_id, None) is not None


# ---- character_asset_cache -------------------------------------------------

def set_asset_cache(character_id: str, asset_key: str, payload: dict) -> dict:
    """Set a cached asset value. 设置缓存的资产值。"""
    bucket = state.character_asset_cache.setdefault(character_id, {})
    record = {
        "character_id": character_id,
        "asset_key": asset_key,
        "object": "character.asset_cache",
        "data": payload.get("data"),
        "mime_type": payload.get("mime_type", ""),
        "cached_at": now_ts(),
    }
    bucket[asset_key] = record
    return record


def get_asset_cache(character_id: str, asset_key: str) -> dict | None:
    """Get a cached asset by key. 通过键获取缓存的资产。"""
    bucket = state.character_asset_cache.get(character_id, {})
    return bucket.get(asset_key)


def list_asset_cache(character_id: str) -> list[dict]:
    """List all cached assets for a character. 列出一个角色的所有缓存资产。"""
    bucket = state.character_asset_cache.get(character_id, {})
    return list(bucket.values())


def delete_asset_cache(character_id: str, asset_key: str) -> bool:
    """Delete a cached asset by key. 通过键删除缓存的资产。"""
    bucket = state.character_asset_cache.get(character_id, {})
    return bucket.pop(asset_key, None) is not None


def clear_asset_cache(character_id: str) -> int:
    """Clear all cached assets for a character; returns count removed.
    清除角色的所有缓存资产；返回移除数量。
    """
    bucket = state.character_asset_cache.pop(character_id, {})
    return len(bucket)


__all__ = [
    "DEFAULT_CHARACTER_ID",
    "seed_default", "create", "list_all", "get",
    "update", "delete", "set_loaded", "get_state", "update_state",
    # A3-01 resource table CRUD
    "create_model", "list_models", "get_model", "update_model", "delete_model",
    "create_motion", "list_motions", "get_motion", "update_motion", "delete_motion",
    "create_voice", "list_voices", "get_voice", "update_voice", "delete_voice",
    "create_handwriting", "list_handwritings", "get_handwriting", "update_handwriting", "delete_handwriting",
    "create_style", "list_styles", "get_style", "update_style", "delete_style",
    "set_asset_cache", "get_asset_cache", "list_asset_cache", "delete_asset_cache", "clear_asset_cache",
]