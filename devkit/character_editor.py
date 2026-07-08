"""Character persona editor for the Developer Kit.

Lets developers create, edit, and manage character persona documents
locally.  Output can be fed into the submission pipeline (C5) for
packing and email delivery.

Data is stored as JSON files under the user's working directory.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from typing import Any

from devkit import DevKitError


_CHARACTERS_SUBDIR = "characters"

# Built-in character config schema definition (C2.3).
CHARACTER_CONFIG_SCHEMA: dict[str, dict[str, Any]] = {
    "max_long_term": {"type": "integer", "min": 1, "max": 1000, "default": 200, "label": "长期记忆上限"},
    "long_term_importance_min": {"type": "number", "min": 0.0, "max": 1.0, "default": 0.6, "label": "长期记忆重要性阈值", "step": 0.05},
    "max_short_term": {"type": "integer", "min": 0, "max": 500, "default": 50, "label": "短期记忆上限"},
    "short_term_decay_rate": {"type": "number", "min": 0.0, "max": 1.0, "default": 0.05, "label": "短期记忆衰减率", "step": 0.01},
    "short_term_importance_min": {"type": "number", "min": 0.0, "max": 1.0, "default": 0.3, "label": "短期记忆重要性阈值", "step": 0.05},
    "max_context_tokens": {"type": "integer", "min": 100, "max": 32000, "default": 8000, "label": "上下文 Token 上限"},
    "reserve_tokens_for_reply": {"type": "integer", "min": 0, "max": 16000, "default": 2000, "label": "回复保留 Token"},
    "force_recall_on_history": {"type": "boolean", "default": True, "label": "强制召回历史"},
    "speaking_speed": {"type": "number", "min": 0.5, "max": 2.0, "default": 1.0, "label": "语速倍率", "step": 0.1},
    "emotion_stability": {"type": "number", "min": 0.0, "max": 1.0, "default": 0.6, "label": "情绪稳定性", "step": 0.05},
}


def get_character_config_schema() -> dict[str, dict[str, Any]]:
    """Return the character config schema definition (C2.3)."""
    return dict(CHARACTER_CONFIG_SCHEMA)


def validate_character_config(config: dict[str, Any]) -> tuple[bool, list[str]]:
    """Schema-validate a character config dict (C2.3 AC-1).

    Checks each field against its type/range constraints.
    Returns ``(ok, [errors])``.
    """
    errors: list[str] = []
    if not isinstance(config, dict):
        return False, ["配置必须是对象"]
    for key, rule in CHARACTER_CONFIG_SCHEMA.items():
        if key not in config:
            continue
        value = config[key]
        kind = rule["type"]
        if kind == "integer":
            try:
                v = int(value)
            except (TypeError, ValueError):
                errors.append(f"{rule['label']}（{key}）必须是整数")
                continue
            if v < rule["min"]:
                errors.append(f"{rule['label']}（{key}）不能小于 {rule['min']}")
            if v > rule["max"]:
                errors.append(f"{rule['label']}（{key}）不能大于 {rule['max']}")
        elif kind == "number":
            try:
                v = float(value)
            except (TypeError, ValueError):
                errors.append(f"{rule['label']}（{key}）必须是数字")
                continue
            if v < rule["min"]:
                errors.append(f"{rule['label']}（{key}）不能小于 {rule['min']}")
            if v > rule["max"]:
                errors.append(f"{rule['label']}（{key}）不能大于 {rule['max']}")
        elif kind == "boolean" and not isinstance(value, bool):
            errors.append(f"{rule['label']}（{key}）必须是布尔值")
    return (len(errors) == 0), errors


# Built-in persona-doc templates (C2.4).
_PERSONA_TEMPLATES: dict[str, str] = {
    "通用角色": (
        "## 基本信息\n\n"
        "- 姓名：\n- 年龄：\n- 性别：\n- 职业：\n\n"
        "## 性格描述\n\n"
        "（核心性格特征、矛盾点）\n\n"
        "## 背景故事\n\n"
        "（出生、成长经历、关键转折事件）\n\n"
        "## 语言风格\n\n"
        "（说话方式、口头禅、语气特点）\n\n"
        "## 人际关系\n\n"
        "- （人物名）：（关系、看法）\n- （人物名）：（关系、看法）\n\n"
        "## 癖好与习惯\n\n"
        "（小动作、偏好、忌讳）"
    ),
    "主角型": (
        "## 基本信息\n\n"
        "- 姓名：\n- 年龄：\n- 身份／定位：\n- 标签：\n\n"
        "## 核心动机\n\n"
        "（驱动角色行动的根本原因）\n\n"
        "## 性格光谱\n\n"
        "- 外向 ← → 内向：\n- 理性 ← → 感性：\n- 善良 ← → 冷酷：\n\n"
        "## 成长弧线\n\n"
        "（初始状态 → 关键事件 → 转变后状态）\n\n"
        "## 标志性台词\n\n"
        "（2-3 句最能代表角色的台词）\n\n"
        "## 禁忌 / 弱点\n\n"
        "（角色最不想面对的事物）"
    ),
    "配角型": (
        "## 基本信息\n\n"
        "- 姓名：\n- 年龄：\n- 与主角的关系：\n\n"
        "## 性格快照\n\n"
        "（2-3 句话概括性格）\n\n"
        "## 功能定位\n\n"
        "（在剧情中扮演的角色：助攻、阻碍、情报源等）\n\n"
        "## 秘密\n\n"
        "（角色隐藏的事）\n\n"
        "## 可变性\n\n"
        "（角色能否被说服、收买、改变立场）"
    ),
}


def _gen_id() -> str:
    return f"char_{secrets.token_hex(8)}"


def _char_dir(work_dir: str, char_id: str) -> str:
    return os.path.join(work_dir, _CHARACTERS_SUBDIR, char_id)


def _char_path(work_dir: str, char_id: str) -> str:
    return os.path.join(_char_dir(work_dir, char_id), "character.json")


def _persona_path(work_dir: str, char_id: str) -> str:
    return os.path.join(_char_dir(work_dir, char_id), "persona.md")


def list_characters(work_dir: str) -> list[dict[str, Any]]:
    base = os.path.join(work_dir, _CHARACTERS_SUBDIR)
    if not os.path.isdir(base):
        return []
    results: list[dict[str, Any]] = []
    for entry in sorted(os.listdir(base)):
        dirpath = os.path.join(base, entry)
        if not os.path.isdir(dirpath):
            continue
        fpath = os.path.join(dirpath, "character.json")
        if os.path.isfile(fpath):
            try:
                with open(fpath, encoding="utf-8") as f:
                    data = json.load(f)
                results.append(data)
            except (json.JSONDecodeError, OSError):
                continue
    return results


def get_character(work_dir: str, char_id: str) -> dict[str, Any] | None:
    fpath = _char_path(work_dir, char_id)
    if not os.path.isfile(fpath):
        return None
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def import_persona(work_dir: str, char_id: str, file_path: str) -> str:
    if not os.path.isfile(file_path):
        raise DevKitError(400, f"文件不存在: {file_path}", code="file_not_found")
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".md", ".markdown", ".txt"):
        raise DevKitError(400, f"不支持的文件格式: {ext}（仅支持 .md / .markdown / .txt）", code="bad_format")
    with open(file_path, encoding="utf-8") as f:
        content = f.read()
    if not content.strip():
        raise DevKitError(400, "文件内容为空", code="empty_file")
    record = get_character(work_dir, char_id)
    if not record:
        raise DevKitError(404, f"角色 {char_id} 不存在", code="not_found")
    persona_path = _persona_path(work_dir, char_id)
    os.makedirs(os.path.dirname(persona_path), exist_ok=True)
    with open(persona_path, "w", encoding="utf-8") as f:
        f.write(content)
    record["persona_doc"] = content
    record["updated_at"] = __import__("devkit._vendor", fromlist=["iso_now"]).iso_now()
    with open(_char_path(work_dir, char_id), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return content


def save_character(work_dir: str, data: dict[str, Any]) -> dict[str, Any]:
    if not data.get("name"):
        raise DevKitError(400, "角色名称不能为空", code="missing_name")
    from devkit._vendor import iso_now
    now = iso_now()
    existing_id = data.get("id", "")
    if existing_id:
        char_id = existing_id
    else:
        char_id = _gen_id()
    char_dir = _char_dir(work_dir, char_id)
    os.makedirs(char_dir, exist_ok=True)

    persona_doc = data.get("persona_doc", "")
    if persona_doc:
        persona_path = _persona_path(work_dir, char_id)
        with open(persona_path, "w", encoding="utf-8") as f:
            f.write(persona_doc)

    record = {
        "id": char_id,
        "name": data.get("name", ""),
        "display_name": data.get("display_name", data.get("name", "")),
        "persona_doc": persona_doc,
        "voice_profile": data.get("voice_profile", ""),
        "default_emotion": data.get("default_emotion", "neutral"),
        "language_style": data.get("language_style", ""),
        "tags": data.get("tags", []),
        "models": data.get("models", []),
        "memory_config": {
            "max_long_term": int(data.get("memory_config", {}).get("max_long_term", 200)),
            "long_term_importance_min": float(data.get("memory_config", {}).get("long_term_importance_min", 0.6)),
            "max_short_term": int(data.get("memory_config", {}).get("max_short_term", 50)),
            "short_term_decay_rate": float(data.get("memory_config", {}).get("short_term_decay_rate", 0.05)),
            "short_term_importance_min": float(data.get("memory_config", {}).get("short_term_importance_min", 0.3)),
            "max_context_tokens": int(data.get("memory_config", {}).get("max_context_tokens", 8000)),
            "reserve_tokens_for_reply": int(data.get("memory_config", {}).get("reserve_tokens_for_reply", 2000)),
            "force_recall_on_history": bool(data.get("memory_config", {}).get("force_recall_on_history", True)),
        },
        "character_config": data.get("character_config", {}),
        "assigned_memory_pack": data.get("assigned_memory_pack", ""),
        "assigned_voice_pack": data.get("assigned_voice_pack", ""),
        "assigned_model": data.get("assigned_model", ""),
        "assigned_world": data.get("assigned_world", ""),
        "created_at": data.get("created_at", now),
        "updated_at": now,
    }
    enforce_initial_memory_minimum(
        work_dir, char_id, record["assigned_memory_pack"]
    )
    fpath = _char_path(work_dir, char_id)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return record


def delete_character(work_dir: str, char_id: str) -> bool:
    char_dir = _char_dir(work_dir, char_id)
    if not os.path.isdir(char_dir):
        return False
    shutil.rmtree(char_dir)
    return True


def get_persona_templates() -> dict[str, str]:
    """Return built-in persona-doc markdown templates (C2.4)."""
    return dict(_PERSONA_TEMPLATES)


#: Minimum number of long-term initial memory entries a character must
#: carry before it can be saved with an assigned memory pack (function
#: list C2.5).  Mirrors the spec's ``[TODO: 默认 10]``.
_MIN_INITIAL_MEMORY = 10


def check_initial_memory_minimum(
    work_dir: str, char_id: str, min_count: int = _MIN_INITIAL_MEMORY
) -> dict[str, Any]:
    """Verify a character has enough initial memories (C2.5).

    The function list requires a new character to carry at least
    ``min_count`` long/short memory entries (manual) before it is
    considered saveable.  We count the entries of the character's own
    memory pack (the pack is keyed by ``character_id``).
    """
    from devkit.memory_editor import list_entries

    entries = list_entries(work_dir, char_id)
    count = len(entries)
    return {
        "character_id": char_id,
        "current_count": count,
        "minimum_required": min_count,
        "meets_requirement": count >= min_count,
        "ok": count >= min_count,
        "message": (
            f"当前 {count} 条记忆条目，已满足最少 {min_count} 条要求"
            if count >= min_count
            else f"当前仅 {count} 条记忆条目，至少需要 {min_count} 条（还差 {min_count - count} 条）"
        ),
    }


def enforce_initial_memory_minimum(
    work_dir: str, char_id: str, assigned_pack: str, min_count: int = _MIN_INITIAL_MEMORY
) -> None:
    """Block saving a character whose assigned memory pack is too thin (C2.5).

    Only enforced when a memory pack is actually assigned, so first-time
    creation (no pack yet) is still allowed; the developer must populate
    the pack before linking it to the character.
    """
    if not assigned_pack:
        return
    result = check_initial_memory_minimum(work_dir, char_id, min_count=min_count)
    if not result["meets_requirement"]:
        raise DevKitError(
            400,
            result["message"],
            code="insufficient_initial_memory",
        )


def export_character_for_submit(work_dir: str, char_id: str) -> dict[str, Any]:
    record = get_character(work_dir, char_id)
    if not record:
        raise DevKitError(404, f"角色 {char_id} 不存在", code="not_found")
    files = []
    persona_path = _persona_path(work_dir, char_id)
    if os.path.isfile(persona_path):
        files.append({"path": persona_path, "arcname": "persona.md"})
    export = {
        "target_kind": "character",
        "target_id": char_id,
        "payload": {
            "notes": f"角色: {record.get('display_name', record['name'])} ({record['name']})",
            "files": [persona_path] if files else [],
        },
        "files": files,
    }
    return export
