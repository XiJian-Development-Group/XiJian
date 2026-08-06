"""开发者工具的角色人设编辑器。

让开发者能够在本地创建、编辑和管理角色人设文档。
输出可以进入提交管线（C5）进行打包和邮件投递。

数据以 JSON 文件形式存储在工作目录下。
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from typing import Any

from devkit import DevKitError


from devkit.persona_parser import extract_persona_features, get_persona_templates
_CHARACTERS_SUBDIR = "characters"

# 内置角色配置模式定义（C2.3）。
# 与记忆相关的旋钮位于 ``memory_config`` 中（单一事实来源，
# 由 core 通过 ``state.memory_configs`` 消费）；此模式只保存
# 非记忆的角色调优字段。
CHARACTER_CONFIG_SCHEMA: dict[str, dict[str, Any]] = {
    "speaking_speed": {"type": "number", "min": 0.5, "max": 2.0, "default": 1.0, "label": "语速倍率", "step": 0.1},
    "emotion_stability": {"type": "number", "min": 0.0, "max": 1.0, "default": 0.6, "label": "情绪稳定性", "step": 0.05},
}


def get_character_config_schema() -> dict[str, dict[str, Any]]:
    """返回角色配置模式定义（C2.3）。"""
    return dict(CHARACTER_CONFIG_SCHEMA)


# 内置人设文档模板（C2.4）。
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


def _parse_character_config_from_fields(data: dict[str, Any]) -> dict[str, Any]:
    """从各个表单字段解析角色配置。

    UI 会发送诸如 `speaking_speed`、`emotion_stability` 等单独字段。
    此函数根据模式提取并验证这些字段。
    """
    config: dict[str, Any] = {}
    for key, rule in CHARACTER_CONFIG_SCHEMA.items():
        if key not in data:
            continue
        value = data[key]
        kind = rule["type"]
        try:
            if kind == "integer":
                v = int(value)
            elif kind == "number":
                v = float(value)
            elif kind == "boolean":
                if isinstance(value, str):
                    v = value.lower() in ("true", "1", "yes", "on")
                else:
                    v = bool(value)
            else:
                continue
            # 范围检查
            if rule.get("min") is not None and v < rule["min"]:
                v = rule["min"]
            if rule.get("max") is not None and v > rule["max"]:
                v = rule["max"]
            config[key] = v
        except (TypeError, ValueError):
            # 跳过无效值
            continue
    return config


def _extract_random_emotion_from_persona(persona_doc: str) -> str:
    """从人设文档中提取一个随机的默认情绪。

    解析人设文档中的情绪特征，返回随机选取的基础情绪。
    如果没有找到情绪特征，则返回 'neutral'。

    情绪按其在人设中的显著程度加权。
    """
    import random
    import re

    if not persona_doc or not persona_doc.strip():
        return "neutral"

    # 带权重的情绪关键词（中文和英文）
    emotion_keywords = {
        "happy": ["开心", "快乐", "愉快", "高兴", "欢喜", "喜悦", "欢快", "开朗", "乐观", "阳光", "happy", "joyful", "cheerful", "optimistic"],
        "sad": ["悲伤", "伤心", "难过", "忧郁", "悲观", "消沉", "痛苦", "sad", "melancholic", "depressed", "sorrowful"],
        "angry": ["愤怒", "生气", "暴躁", "易怒", "暴怒", "愤慨", "angry", "irritable", "furious", "temperamental"],
        "fear": ["恐惧", "害怕", "担忧", "焦虑", "不安", "恐慌", "fearful", "anxious", "worried", "nervous"],
        "surprise": ["惊讶", "惊奇", "震惊", "意外", "吃惊", "surprised", "amazed", "astonished"],
        "calm": ["冷静", "沉稳", "平静", "淡定", "从容", "镇定", "calm", "composed", "serene", "peaceful"],
        "excited": ["兴奋", "激动", "亢奋", "热血", "激昂", "excited", "enthusiastic", "passionate"],
        "shy": ["害羞", "腼腆", "内向", "羞涩", "shy", "timid"],
    }

    # 统计每种情绪的关键词出现次数
    emotion_scores = {}
    for emotion, keywords in emotion_keywords.items():
        score = 0
        for kw in keywords:
            score += len(re.findall(rf"{re.escape(kw)}", persona_doc, re.IGNORECASE))
        if score > 0:
            emotion_scores[emotion] = score

    if not emotion_scores:
        return "neutral"

    # 加权随机选择
    emotions = list(emotion_scores.keys())
    weights = [emotion_scores[e] for e in emotions]
    return random.choices(emotions, weights=weights, k=1)[0]


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

    # 从各个表单字段解析 character_config（C2.3）
    character_config = _parse_character_config_from_fields(data)

    # 确定 default_emotion：若提供则使用，否则从人设中提取
    default_emotion = data.get("default_emotion")
    if not default_emotion or default_emotion == "neutral":
        default_emotion = _extract_random_emotion_from_persona(persona_doc)

    record = {
        "id": char_id,
        "name": data.get("name", ""),
        "display_name": data.get("display_name", data.get("name", "")),
        "description": data.get("description", ""),
        "persona_doc": persona_doc,
        "voice_profile": data.get("voice_profile", ""),
        "default_emotion": default_emotion,
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
        "character_config": character_config,
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



#: 角色在分配了记忆包后、可被保存前必须携带的最少长期初始记忆条目数
#: （功能清单 C2.5）。镜像规范中的 ``[TODO: 默认 10]``。
_MIN_INITIAL_MEMORY = 10


def check_initial_memory_minimum(
    work_dir: str, char_id: str, min_count: int = _MIN_INITIAL_MEMORY
) -> dict[str, Any]:
    """验证角色是否有足够的初始记忆（C2.5）。

    功能清单要求新角色在被视为可保存之前，至少携带 ``min_count``
    条（手动的）长期/短期记忆条目。我们统计该角色自己的记忆包
    （记忆包以 ``character_id`` 为键）中的条目数。
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
    """阻止保存记忆包过薄的角色（C2.5）。

    仅在确实分配了记忆包时强制检查，因此首次创建（尚无记忆包）
    仍然允许；开发者必须先填充记忆包，再将其关联到角色。
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


def auto_fill_character_config(
    work_dir: str, char_id: str, *, apply: bool = True
) -> dict[str, Any]:
    """根据模式默认值自动填充角色配置（C2.3 + C4）。

    使用 :data:`CHARACTER_CONFIG_SCHEMA` 中定义的值作为合理的
    起始默认值，并将它们标记为 ``source='ai_suggested'``，以便 30%
    AI 占比审计（C4 AC-1）能够将其计入。开发者在启用角色之前
    应审核并调整每个字段。

    返回（提议或已保存的）配置 dict 以及 ``source`` 标记。
    """
    from devkit.ai_assistant import log_assist_event

    record = get_character(work_dir, char_id)
    if not record:
        raise DevKitError(404, f"角色 {char_id} 不存在", code="not_found")

    proposed: dict[str, Any] = {}
    for key, rule in CHARACTER_CONFIG_SCHEMA.items():
        if key in record.get("character_config", {}):
            proposed[key] = record["character_config"][key]
        else:
            proposed[key] = rule.get("default")

    result = {"config": proposed, "source": "ai_suggested"}

    if apply:
        record["character_config"] = proposed
        record["character_config_source"] = "ai_suggested"
        record["updated_at"] = __import__("devkit._vendor", fromlist=["iso_now"]).iso_now()
        fpath = _char_path(work_dir, char_id)
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        log_assist_event(
            work_dir,
            event_type="auto_fill_config",
            target_module="character",
            description=f"auto-filled config for {char_id}",
            accepted=True,
            source="ai_suggested",
        )
    return result


def export_character_for_submit(work_dir: str, char_id: str) -> dict[str, Any]:
    """以包兼容的归档负载导出一个角色。

    以包兼容的归档负载导出一个角色。

    返回的 ``files`` 列表使用**资源包布局**
    （``characters/<id>/character.json``、``characters/<id>/persona.md``、
    ``memories/<id>/entries.json``），这样产出的 7Z/zip 归档可被核心
    资源包引擎（§B）直接安装 —— DevKit 导出即包，一套格式两用。

    返回的 ``files`` 列表使用**资源包布局**（``characters/<id>/character.json``、
    ``characters/<id>/persona.md``、``memories/<id>/entries.json``），
    这样产出的 7Z/zip 归档可被核心资源包引擎（§B）直接安装 ——
    DevKit 导出即包，一套格式两用。
    """
    record = get_character(work_dir, char_id)
    if not record:
        raise DevKitError(404, f"角色 {char_id} 不存在", code="not_found")
    files = []
    prefix = f"characters/{char_id}"
    char_path = _char_path(work_dir, char_id)
    if os.path.isfile(char_path):
        files.append({"path": char_path, "arcname": f"{prefix}/character.json"})
    persona_path = _persona_path(work_dir, char_id)
    if os.path.isfile(persona_path):
        files.append({"path": persona_path, "arcname": f"{prefix}/persona.md"})
    mem_path = os.path.join(work_dir, "memories", char_id, "entries.json")
    if os.path.isfile(mem_path):
        files.append({"path": mem_path, "arcname": f"memories/{char_id}/entries.json"})
    display = record.get("display_name") or record.get("name") or char_id
    export = {
        "target_kind": "character",
        "target_id": char_id,
        "payload": {
            "name": display,
            "notes": f"角色: {display} ({record.get('name', char_id)})",
            "files": [entry["path"] for entry in files],
        },
        "files": files,
    }
    return export
