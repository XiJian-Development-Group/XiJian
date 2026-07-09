"""World view editor for the Developer Kit.

Lets developers create and edit world-view documents and world
configurations.  Data is stored as JSON + Markdown under the
working directory.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from typing import Any

from devkit import DevKitError
from devkit._vendor import iso_now


_WORLDS_SUBDIR = "worlds"


def _gen_id() -> str:
    return f"world_{secrets.token_hex(8)}"


def _world_dir(work_dir: str, world_id: str) -> str:
    return os.path.join(work_dir, _WORLDS_SUBDIR, world_id)


def _world_path(work_dir: str, world_id: str) -> str:
    return os.path.join(_world_dir(work_dir, world_id), "world.json")


def _world_doc_path(work_dir: str, world_id: str) -> str:
    return os.path.join(_world_dir(work_dir, world_id), "world_doc.md")


def list_worlds(work_dir: str) -> list[dict[str, Any]]:
    base = os.path.join(work_dir, _WORLDS_SUBDIR)
    if not os.path.isdir(base):
        return []
    results: list[dict[str, Any]] = []
    for entry in sorted(os.listdir(base)):
        dirpath = os.path.join(base, entry)
        if not os.path.isdir(dirpath):
            continue
        fpath = os.path.join(dirpath, "world.json")
        if os.path.isfile(fpath):
            try:
                with open(fpath, encoding="utf-8") as f:
                    results.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                continue
    return results


def get_world(work_dir: str, world_id: str) -> dict[str, Any] | None:
    fpath = _world_path(work_dir, world_id)
    if not os.path.isfile(fpath):
        return None
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_world(work_dir: str, data: dict[str, Any]) -> dict[str, Any]:
    if not data.get("name"):
        raise DevKitError(400, "世界观名称不能为空", code="missing_name")
    from devkit._vendor import iso_now
    now = iso_now()
    existing_id = data.get("id", "")
    if existing_id:
        world_id = existing_id
    else:
        world_id = _gen_id()
    world_dir = _world_dir(work_dir, world_id)
    os.makedirs(world_dir, exist_ok=True)
    world_doc = data.get("world_doc", "")
    if world_doc:
        doc_path = _world_doc_path(work_dir, world_id)
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(world_doc)
    record = {
        "id": world_id,
        "name": data.get("name", ""),
        "world_doc": world_doc,
        "config": data.get("config", {}),
        "created_at": data.get("created_at", now),
        "updated_at": now,
    }
    fpath = _world_path(work_dir, world_id)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return record


def delete_world(work_dir: str, world_id: str) -> bool:
    wdir = _world_dir(work_dir, world_id)
    if not os.path.isdir(wdir):
        return False
    import shutil
    shutil.rmtree(wdir)
    return True


def export_world_for_submit(work_dir: str, world_id: str) -> dict[str, Any]:
    record = get_world(work_dir, world_id)
    if not record:
        raise DevKitError(404, f"世界观 {world_id} 不存在", code="not_found")
    doc_path = _world_doc_path(work_dir, world_id)
    files = []
    if os.path.isfile(doc_path):
        files.append({"path": doc_path, "arcname": "world_doc.md"})
    cfg_path = _world_config_path(work_dir, world_id)
    if os.path.isfile(cfg_path):
        files.append({"path": cfg_path, "arcname": "world.json"})
    return {
        "target_kind": "world",
        "target_id": world_id,
        "payload": {
            "notes": f"世界观: {record['name']}",
            "files": [doc_path],
        },
        "files": files,
    }


# ---------------------------------------------------------------------------
# C1.3 — structured world configuration (time / scene / weather)
# ---------------------------------------------------------------------------

#: Default structured configuration a new world starts with.  Every
#: field is range-checked by :func:`validate_world_config` before save.
WORLD_CONFIG_DEFAULT: dict[str, Any] = {
    "time_flow_multiplier": 30.0,   # 1 real minute = N virtual minutes
    "day_length_minutes": 1440,     # virtual minutes per full day
    "night_ratio": 0.4,             # fraction of the day that is "night"
    "weather_probabilities": {      # per-slot weather distribution (0..1)
        "morning": {"sunny": 0.6, "rain": 0.2, "snow": 0.05, "cloudy": 0.15},
        "noon": {"sunny": 0.7, "rain": 0.15, "snow": 0.03, "cloudy": 0.12},
        "evening": {"sunny": 0.4, "rain": 0.3, "snow": 0.05, "cloudy": 0.25},
        "night": {"sunny": 0.1, "rain": 0.4, "snow": 0.1, "cloudy": 0.4},
    },
    "lighting_presets": ["default", "warm", "cold", "dramatic"],
    "ambient_audio_library": [],
}


def _world_config_path(work_dir: str, world_id: str) -> str:
    return os.path.join(_world_dir(work_dir, world_id), "world_config.json")


def get_world_config(work_dir: str, world_id: str) -> dict[str, Any]:
    fpath = _world_config_path(work_dir, world_id)
    if not os.path.isfile(fpath):
        return dict(WORLD_CONFIG_DEFAULT)
    try:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(WORLD_CONFIG_DEFAULT)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(WORLD_CONFIG_DEFAULT)


#: Key sections a world-view document should contain (C1.2 AC-2).
_WORLD_DOC_REQUIRED_SECTIONS = ("时间线", "地理", "主要势力")

#: Built-in world-doc templates (C1.2 AC-1).
_WORLD_DOC_TEMPLATES: dict[str, str] = {
    "异世界": (
        "## 时间线\n\n"
        "### 创世 / 纪元开端\n"
        "（描述世界的诞生传说、神话起源）\n\n"
        "### 关键转折\n"
        "（改变世界格局的重大事件）\n\n"
        "### 现代\n"
        "（当前时间点，角色所处的时代）\n\n"
        "## 地理\n\n"
        "### 主要大陆 / 区域\n"
        "- （区域名）：（特征、文化、冲突）\n"
        "- （区域名）：（特征、文化、冲突）\n\n"
        "### 重要地标\n"
        "- （地标名）：（用途、背景故事）\n\n"
        "## 主要势力\n\n"
        "- （势力名）：（目标、手段、与主角的关系）\n"
        "- （势力名）：（目标、手段、与主角的关系）\n\n"
        "## 魔法 / 科技体系\n\n"
        "（世界独特的规则，如魔法体系、科技水平）\n\n"
        "## 文化习俗\n\n"
        "（节日、禁忌、社会结构等）"
    ),
    "现代都市": (
        "## 时间线\n\n"
        "### 历史背景\n"
        "（城市建立、重要发展阶段）\n\n"
        "### 现代\n"
        "（当前时间点）\n\n"
        "## 地理\n\n"
        "### 城区分布\n"
        "- （区名）：（氛围、主要人群）\n"
        "- （区名）：（氛围、主要人群）\n\n"
        "### 重要场所\n"
        "- （场所名）：（描述）\n\n"
        "## 主要势力\n\n"
        "- （势力名）：（背景、影响力、隐藏面）\n"
        "- （势力名）：（背景、影响力、隐藏面）\n\n"
        "## 社会规则\n\n"
        "（都市里的潜规则、阶级分化、特殊设定）"
    ),
    "校园": (
        "## 时间线\n\n"
        "### 学年历\n"
        "（开学、文化祭、考试、毕业等关键时间点）\n\n"
        "## 地理\n\n"
        "### 校园布局\n"
        "- （校舍/教学楼名）：（用途、传闻）\n"
        "- （社团楼）：（活跃社团）\n"
        "- （后庭/天台等）：（学生聚集地）\n\n"
        "### 校外区域\n"
        "（学生常去的场所）\n\n"
        "## 主要势力\n\n"
        "- （学生会/风纪委员会）：（宗旨、权力范围）\n"
        "- （社团/圈子）：（特点、成员）\n"
        "- （问题学生群体）：（威胁程度）\n\n"
        "## 校园传说\n\n"
        "（七大不可思议、流传的都市传说等）"
    ),
    "星际": (
        "## 时间线\n\n"
        "### 大航海纪元\n"
        "（人类踏入星际的关键节点）\n\n"
        "### 主要冲突\n"
        "（星系战争、外交危机）\n\n"
        "### 当前纪元\n"
        "（政治格局、科技水平）\n\n"
        "## 地理\n\n"
        "### 星系 / 星区\n"
        "- （星区）：（政权、资源、威胁等级）\n"
        "- （星区）：（政权、资源、威胁等级）\n\n"
        "### 重要空间站 / 行星\n"
        "- （名称）：（功能、人口、特色）\n\n"
        "## 主要势力\n\n"
        "- （星际联邦/帝国）：（体制、领土、军队）\n"
        "- （企业/商会）：（经济影响力）\n"
        "- （海盗/反抗组织）：（威胁、隐藏阵营）\n\n"
        "## 科技设定\n\n"
        "（超光速航行、AI 伦理、基因改造等）"
    ),
}


def lint_world_doc(doc: str) -> dict[str, Any]:
    """Lightweight world-doc linter (C1.2 AC-2).

    Flags missing key sections (时间线 / 地理 / 主要势力) by scanning
    Markdown headings.  Returns ``ok`` only when every required section
    is present.
    """
    if not isinstance(doc, str) or not doc.strip():
        return {"ok": False, "missing": list(_WORLD_DOC_REQUIRED_SECTIONS), "warnings": ["文档为空"]}
    text = doc
    # Normalise heading markers that may carry leading '#' whitespace.
    headings = [ln.lstrip("#").strip() for ln in text.splitlines() if ln.lstrip().startswith("#")]
    missing = [s for s in _WORLD_DOC_REQUIRED_SECTIONS if not any(s in h for h in headings)]
    warnings = []
    if len(text) < 200:
        warnings.append("文档过短，建议补充更多设定")
    return {"ok": len(missing) == 0, "missing": missing, "warnings": warnings}


def get_world_doc_templates() -> dict[str, str]:
    """Return built-in world-doc markdown templates (C1.2 AC-1)."""
    return dict(_WORLD_DOC_TEMPLATES)


def save_world_config(work_dir: str, world_id: str, config: dict[str, Any]) -> dict[str, Any]:
    ok, errors = validate_world_config(config)
    if not ok:
        raise DevKitError(400, "；".join(errors), code="bad_world_config")
    world_dir = _world_dir(work_dir, world_id)
    os.makedirs(world_dir, exist_ok=True)
    merged = dict(WORLD_CONFIG_DEFAULT)
    merged.update(config)
    with open(_world_config_path(work_dir, world_id), "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return merged


def validate_world_config(config: dict[str, Any]) -> tuple[bool, list[str]]:
    """Range-check a structured world config (C1.3 AC-1)."""
    errors: list[str] = []
    if not isinstance(config, dict):
        return False, ["配置必须是对象"]
    tfm = config.get("time_flow_multiplier", WORLD_CONFIG_DEFAULT["time_flow_multiplier"])
    try:
        tfm = float(tfm)
    except (TypeError, ValueError):
        return False, ["time_flow_multiplier 必须是数字"]
    if tfm <= 0:
        errors.append("time_flow_multiplier 必须大于 0")
    if tfm > 1440:
        errors.append("time_flow_multiplier 不能超过 1440（1 现实分钟 = 1 虚拟天）")
    dl = config.get("day_length_minutes", WORLD_CONFIG_DEFAULT["day_length_minutes"])
    try:
        dl = float(dl)
    except (TypeError, ValueError):
        return False, ["day_length_minutes 必须是数字"]
    if dl <= 0:
        errors.append("day_length_minutes 必须大于 0")
    nr = config.get("night_ratio", WORLD_CONFIG_DEFAULT["night_ratio"])
    try:
        nr = float(nr)
    except (TypeError, ValueError):
        return False, ["night_ratio 必须是数字"]
    if not (0.0 <= nr <= 1.0):
        errors.append("night_ratio 必须在 0~1 之间")
    wps = config.get("weather_probabilities", WORLD_CONFIG_DEFAULT["weather_probabilities"])
    if isinstance(wps, dict):
        for slot, dist in wps.items():
            if not isinstance(dist, dict):
                errors.append(f"weather_probabilities.{slot} 必须是分布对象")
                continue
            total = sum(float(v) for v in dist.values() if isinstance(v, (int, float)))
            if abs(total - 1.0) > 0.01:
                errors.append(f"weather_probabilities.{slot} 概率之和须为 1（当前 {total:.2f}）")
    return (len(errors) == 0), errors


# ---------------------------------------------------------------------------
# C1.1 — custom event DSL store
# ---------------------------------------------------------------------------

#: Recognised trigger operators for the event DSL (see C1.1).
_EVENT_TRIGGER_KINDS = ("time", "state", "probability", "composite")

#: Single-world event cap (function list C1.1 AC-2, ``[TODO: 默认 200]``).
MAX_EVENTS_PER_WORLD: int = 200


def _events_path(work_dir: str, world_id: str) -> str:
    return os.path.join(_world_dir(work_dir, world_id), "events.json")


def list_world_events(work_dir: str, world_id: str) -> list[dict[str, Any]]:
    fpath = _events_path(work_dir, world_id)
    if not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_world_event(work_dir: str, world_id: str, data: dict[str, Any]) -> dict[str, Any]:
    if not get_world(work_dir, world_id):
        raise DevKitError(404, f"世界观 {world_id} 不存在", code="not_found")
    name = (data.get("name") or "").strip()
    if not name:
        raise DevKitError(400, "事件名称不能为空", code="missing_name")
    trigger = data.get("trigger", {})
    ok, errors = validate_event_trigger(trigger)
    if not ok:
        raise DevKitError(400, "；".join(errors), code="bad_event_trigger")

    events = list_world_events(work_dir, world_id)
    event_id = data.get("id") or f"evt_{secrets.token_hex(8)}"
    is_new = event_id not in {e.get("id") for e in events}

    # C1.1 AC-2 — single-world event cap.
    if is_new and len(events) >= MAX_EVENTS_PER_WORLD:
        raise DevKitError(
            400,
            f"单世界事件数量已达上限 {MAX_EVENTS_PER_WORLD} 条，无法继续添加",
            code="event_cap_exceeded",
        )

    # C1.1 boundary — reject a new event whose name + trigger duplicate an
    # existing one (conflicting definition).
    if is_new:
        trigger_json = json.dumps(trigger, sort_keys=True, ensure_ascii=False)
        for existing in events:
            if existing.get("name") == name and json.dumps(
                existing.get("trigger", {}), sort_keys=True, ensure_ascii=False
            ) == trigger_json:
                raise DevKitError(
                    400,
                    f"已存在名称与触发条件完全相同的事件「{name}」，触发条件冲突，拒绝保存",
                    code="event_conflict",
                )

    record = {
        "id": event_id,
        "world_id": world_id,
        "name": name,
        "description": data.get("description", ""),
        "kind": data.get("kind", "custom"),
        "trigger": trigger,
        "priority": int(data.get("priority", 50)),
        "scene": data.get("scene", ""),
        "effects": data.get("effects", {}),
        "is_enabled": bool(data.get("is_enabled", True)),
        "updated_at": iso_now(),
    }
    existing_idx = next((i for i, e in enumerate(events) if e.get("id") == event_id), -1)
    if existing_idx >= 0:
        events[existing_idx] = record
    else:
        events.append(record)
    _save_events(work_dir, world_id, events)
    return record


def _save_events(work_dir: str, world_id: str, events: list[dict[str, Any]]) -> None:
    world_dir = _world_dir(work_dir, world_id)
    os.makedirs(world_dir, exist_ok=True)
    with open(_events_path(work_dir, world_id), "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)


def delete_world_event(work_dir: str, world_id: str, event_id: str) -> bool:
    events = list_world_events(work_dir, world_id)
    before = len(events)
    events = [e for e in events if e.get("id") != event_id]
    if len(events) < before:
        _save_events(work_dir, world_id, events)
        return True
    return False


def validate_event_trigger(trigger: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate an event trigger definition (C1.1 AC-1).

    A trigger is one of::

        {"kind": "time", "at": "MON 10:00"}            # scheduled
        {"kind": "state", "field": "mood", "op": "<", "value": 20}
        {"kind": "probability", "chance": 0.1}         # random daily
        {"kind": "composite", "op": "AND", "rules": [...]}
    """
    errors: list[str] = []
    if not isinstance(trigger, dict):
        return False, ["trigger 必须是对象"]
    kind = trigger.get("kind")
    if kind not in _EVENT_TRIGGER_KINDS:
        return False, [f"trigger.kind 必须是 {_EVENT_TRIGGER_KINDS} 之一"]
    if kind == "time":
        if not trigger.get("at"):
            errors.append("time 触发器需要 at 字段（如 'MON 10:00'）")
    elif kind == "state":
        if not trigger.get("field"):
            errors.append("state 触发器需要 field 字段")
        if trigger.get("op") not in (">", ">=", "<", "<=", "==", "!="):
            errors.append("state 触发器的 op 必须是 > >= < <= == !=")
    elif kind == "probability":
        try:
            c = float(trigger.get("chance", 0))
        except (TypeError, ValueError):
            return False, ["probability.chance 必须是 0~1 的数字"]
        if not (0.0 <= c <= 1.0):
            errors.append("probability.chance 必须在 0~1 之间")
    elif kind == "composite":
        op = trigger.get("op")
        if op not in ("AND", "OR"):
            errors.append("composite.op 必须是 AND 或 OR")
        rules = trigger.get("rules")
        if not isinstance(rules, list) or not rules:
            errors.append("composite.rules 必须是非空列表")
        else:
            for sub in rules:
                ok, sub_errors = validate_event_trigger(sub)
                errors.extend(sub_errors)
    return (len(errors) == 0), errors
