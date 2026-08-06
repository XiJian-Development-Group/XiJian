"""开发者工具的世界观编辑器。

让开发者能够创建和编辑世界观文档与世界配置。
数据以 JSON + Markdown 形式存储在工作目录下。
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


# DSL 解析器异常
class DSLParseError(ValueError):
    """DSL 解析失败时抛出。"""
    pass


def _gen_id() -> str:
    return f"world_{secrets.token_hex(8)}"


def _world_dir(work_dir: str, world_id: str) -> str:
    return os.path.join(work_dir, _WORLDS_SUBDIR, world_id)


def _world_path(work_dir: str, world_id: str) -> str:
    return os.path.join(_world_dir(work_dir, world_id), "world.json")


def _world_doc_path(work_dir: str, world_id: str) -> str:
    return os.path.join(_world_dir(work_dir, world_id), "world_doc.md")


def _world_doc_versions_dir(work_dir: str, world_id: str) -> str:
    return os.path.join(_world_dir(work_dir, world_id), "world_doc_versions")


def _world_doc_version_path(work_dir: str, world_id: str, version: int) -> str:
    return os.path.join(_world_doc_versions_dir(work_dir, world_id), f"world_doc_v{version}.md")


def _list_world_doc_versions(work_dir: str, world_id: str) -> list[dict[str, Any]]:
    """列出世界文档的所有版本。"""
    versions_dir = _world_doc_versions_dir(work_dir, world_id)
    if not os.path.isdir(versions_dir):
        return []
    versions = []
    for fname in sorted(os.listdir(versions_dir)):
        if fname.startswith("world_doc_v") and fname.endswith(".md"):
            try:
                version = int(fname[11:-3])  # 从 "world_doc_v{N}.md" 提取版本号
                fpath = os.path.join(versions_dir, fname)
                stat = os.stat(fpath)
                with open(fpath, encoding="utf-8") as f:
                    content = f.read()
                # 提取第一个标题作为标题
                title = ""
                for line in content.splitlines():
                    if line.startswith("#"):
                        title = line.lstrip("#").strip()
                        break
                versions.append({
                    "version": version,
                    "title": title or f"版本 {version}",
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "preview": content[:200] + "..." if len(content) > 200 else content,
                })
            except (ValueError, OSError):
                continue
    return sorted(versions, key=lambda v: v["version"], reverse=True)


def _save_world_doc_version(work_dir: str, world_id: str, content: str, version: int | None = None) -> int:
    """保存世界文档的一个版本。返回版本号。"""
    versions_dir = _world_doc_versions_dir(work_dir, world_id)
    os.makedirs(versions_dir, exist_ok=True)

    if version is None:
        # 自动递增版本号
        existing = _list_world_doc_versions(work_dir, world_id)
        version = (existing[0]["version"] + 1) if existing else 1

    fpath = _world_doc_version_path(work_dir, world_id, version)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    return version


def _get_world_doc_version(work_dir: str, world_id: str, version: int) -> str | None:
    """获取世界文档的特定版本。"""
    fpath = _world_doc_version_path(work_dir, world_id, version)
    if not os.path.isfile(fpath):
        return None
    with open(fpath, encoding="utf-8") as f:
        return f.read()


def _extract_world_doc_keywords(doc: str) -> list[str]:
    """从世界文档中提取关键词，用于 A4 NPC 生成。

    提取：
    - 标题（markdown # 标题）
    - 专有名词（中英文的大写词）
    - 必需章节中的关键术语（时间线、地理、主要势力）
    """
    if not doc or not doc.strip():
        return []

    keywords = set()

    # 提取标题
    for line in doc.splitlines():
        line = line.strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            if heading:
                keywords.add(heading)

    # 提取中文专有名词（2 字以上、大写或已知模式）
    # 简单启发式：出现在必需章节中的词
    import re
    chinese_words = re.findall(r'[\u4e00-\u9fff]{2,}', doc)
    for w in chinese_words:
        if len(w) >= 2 and len(w) <= 10:
            keywords.add(w)

    # 提取英文大写词
    english_words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', doc)
    for w in english_words:
        if len(w) >= 2:
            keywords.add(w)

    # 限制为最相关的 50 个
    return list(keywords)[:50]


def _event_factories_path(work_dir: str, world_id: str) -> str:
    return os.path.join(_world_dir(work_dir, world_id), "event_factories.json")


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
        # 如果提供了 world_doc，则保存新版本
        world_doc = data.get("world_doc", "")
        if world_doc:
            _save_world_doc_version(work_dir, world_id, world_doc)
    else:
        world_id = _gen_id()
        world_doc = data.get("world_doc", "")
    world_dir = _world_dir(work_dir, world_id)
    os.makedirs(world_dir, exist_ok=True)
    if world_doc:
        doc_path = _world_doc_path(work_dir, world_id)
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(world_doc)
    # 获取记录的所有版本
    versions = _list_world_doc_versions(work_dir, world_id)
    record = {
        "id": world_id,
        "name": data.get("name", ""),
        "world_doc": world_doc,
        "config": data.get("config", {}),
        "created_at": data.get("created_at", now),
        "updated_at": now,
        "doc_versions": versions,
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
    """以包兼容的归档负载导出一个世界。

    以包兼容的归档负载导出一个世界。

    使用**资源包布局**（``worlds/<id>/world.json``、
    ``worlds/<id>/world_doc.md``、``worlds/<id>/world_config.json``），
    产出的归档可被核心资源包引擎（§B）直接安装。

    使用**资源包布局**（``worlds/<id>/world.json``、``worlds/<id>/world_doc.md``、
    ``worlds/<id>/world_config.json``），产出的归档可被核心资源包引擎（§B）直接安装。
    """
    record = get_world(work_dir, world_id)
    if not record:
        raise DevKitError(404, f"世界观 {world_id} 不存在", code="not_found")
    prefix = f"worlds/{world_id}"
    files = []
    wjson_path = _world_path(work_dir, world_id)
    if os.path.isfile(wjson_path):
        files.append({"path": wjson_path, "arcname": f"{prefix}/world.json"})
    doc_path = _world_doc_path(work_dir, world_id)
    if os.path.isfile(doc_path):
        files.append({"path": doc_path, "arcname": f"{prefix}/world_doc.md"})
    cfg_path = _world_config_path(work_dir, world_id)
    if os.path.isfile(cfg_path):
        files.append({"path": cfg_path, "arcname": f"{prefix}/world_config.json"})
    return {
        "target_kind": "world",
        "target_id": world_id,
        "payload": {
            "name": record.get("name") or world_id,
            "notes": f"世界观: {record.get('name', world_id)}",
            "files": [entry["path"] for entry in files],
        },
        "files": files,
    }


# ---------------------------------------------------------------------------
# C1.3 —— 结构化世界配置（时间 / 场景 / 天气）
# ---------------------------------------------------------------------------

#: 新世界启动时的默认结构化配置。每个字段在保存前都会由
#: :func:`validate_world_config` 做范围检查。
WORLD_CONFIG_DEFAULT: dict[str, Any] = {
    "time_flow_multiplier": 30.0,   # 1 现实分钟 = N 虚拟分钟
    "day_length_minutes": 1440,     # 每个完整天的虚拟分钟数
    "night_ratio": 0.4,             # 一天中属于“夜晚”的比例
    "weather_probabilities": {      # 按时段划分的天气分布（0..1）
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


#: 世界观文档应包含的关键章节（C1.2 AC-2）。
_WORLD_DOC_REQUIRED_SECTIONS = ("时间线", "地理", "主要势力")

#: 内置世界观文档模板（C1.2 AC-1）。
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
    """轻量级世界观文档检查器（C1.2 AC-2）。

    通过扫描 Markdown 标题标记缺失的关键章节（时间线 / 地理 / 主要势力）。
    仅当每个必需章节都存在时返回 ``ok``。
    """
    if not isinstance(doc, str) or not doc.strip():
        return {"ok": False, "missing": list(_WORLD_DOC_REQUIRED_SECTIONS), "warnings": ["文档为空"]}
    text = doc
    # 规范化可能带前导 '#' 空白的标题标记。
    headings = [ln.lstrip("#").strip() for ln in text.splitlines() if ln.lstrip().startswith("#")]
    missing = [s for s in _WORLD_DOC_REQUIRED_SECTIONS if not any(s in h for h in headings)]
    warnings = []
    if len(text) < 200:
        warnings.append("文档过短，建议补充更多设定")
    return {"ok": len(missing) == 0, "missing": missing, "warnings": warnings}


def get_world_doc_templates() -> dict[str, str]:
    """返回内置的世界观文档 markdown 模板（C1.2 AC-1）。"""
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
    """对结构化世界配置做范围检查（C1.3 AC-1）。"""
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
# C1.1 —— 自定义事件 DSL 存储
# ---------------------------------------------------------------------------

#: 事件 DSL 可识别的触发器操作符（参见 C1.1）。
_EVENT_TRIGGER_KINDS = ("time", "state", "probability", "composite")

#: 单世界事件上限（功能清单 C1.1 AC-2，``[TODO: 默认 200]``）。
MAX_EVENTS_PER_WORLD: int = 200

#: 事件工厂存储路径
def _event_factories_path(work_dir: str, world_id: str) -> str:
    return os.path.join(_world_dir(work_dir, world_id), "event_factories.json")


# ---------------------------------------------------------------------------
# DSL 解析器（C1.1 AC-1）
# ---------------------------------------------------------------------------


class DSLParseError(DevKitError):
    """DSL 解析失败时抛出。"""

    def __init__(self, message: str):
        super().__init__(400, message, code="dsl_parse_error")


def parse_event_dsl(dsl_text: str) -> dict[str, Any]:
    """将事件 DSL 文本解析为结构化的事件定义。

    DSL 语法（简化）：
        event "name" {
            trigger: <trigger_expr>
            priority: <int>
            scene: <string>
            effects: { <json_object> }
            kind: <string>
            description: <string>
            is_enabled: <bool>
        }

    触发器表达式支持：
        - time: "weekday in [1,4] AND hour == 10"
        - state: "field op value"（例如 "mood < 20"）
        - probability: "chance 0.1"
        - composite: 带子规则的 "AND/OR"

    返回包含以下键的 dict：name、trigger、priority、scene、effects、kind、description、is_enabled
    """
    if not dsl_text or not dsl_text.strip():
        raise DSLParseError("DSL 文本为空")

    text = dsl_text.strip()

    # 提取事件名称
    name_match = re.match(r'event\s+"([^"]+)"\s*\{', text)
    if not name_match:
        raise DSLParseError('DSL 必须以 event "名称" { 开头')
    name = name_match.group(1)

    # 找到匹配的右花括号
    brace_start = name_match.end() - 1
    brace_count = 0
    brace_end = -1
    for i, ch in enumerate(text[brace_start:], start=brace_start):
        if ch == "{":
            brace_count += 1
        elif ch == "}":
            brace_count -= 1
            if brace_count == 0:
                brace_end = i
                break
    if brace_end == -1:
        raise DSLParseError("DSL 大括号不匹配")

    body = text[brace_start + 1 : brace_end].strip()

    # 解析主体中的键值对
    result = {
        "name": name,
        "kind": "custom",
        "priority": 50,
        "scene": "",
        "effects": {},
        "description": "",
        "is_enabled": True,
    }

    # 按顶层逗号分割（不在花括号/方括号内）
    pairs = _split_top_level(body, ",")
    for pair in pairs:
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            continue
        key, value = pair.split(":", 1)
        key = key.strip()
        value = value.strip()

        if key == "trigger":
            result["trigger"] = _parse_trigger_dsl(value)
        elif key == "priority":
            try:
                result["priority"] = int(value)
            except ValueError:
                raise DSLParseError(f"priority 必须是整数: {value}")
        elif key == "scene":
            result["scene"] = value.strip('"\'')
        elif key == "effects":
            try:
                result["effects"] = json.loads(value)
            except json.JSONDecodeError as e:
                raise DSLParseError(f"effects 必须是有效 JSON: {e}")
        elif key == "kind":
            result["kind"] = value.strip('"\'')
        elif key == "description":
            result["description"] = value.strip('"\'')
        elif key == "is_enabled":
            result["is_enabled"] = value.lower() in ("true", "1", "yes")

    # 验证解析后的触发器
    ok, errors = validate_event_trigger(result["trigger"])
    if not ok:
        raise DSLParseError("；".join(errors))

    return result


def _split_top_level(text: str, delimiter: str) -> list[str]:
    """在顶层（不在 {}、[]、() 内）按分隔符分割文本。

    当分隔符为 ',' 时，也将换行视为分隔符。
    """
    if delimiter == ",":
        # 对于 DSL 主体，在顶层同时按逗号和换行分割
        parts = []
        current = []
        depth_brace = 0
        depth_bracket = 0
        depth_paren = 0
        in_string = False
        string_char = ""

        for ch in text:
            if in_string:
                current.append(ch)
                if ch == string_char:
                    in_string = False
            elif ch in ('"', "'"):
                in_string = True
                string_char = ch
                current.append(ch)
            elif ch == "{":
                depth_brace += 1
                current.append(ch)
            elif ch == "}":
                depth_brace -= 1
                current.append(ch)
            elif ch == "[":
                depth_bracket += 1
                current.append(ch)
            elif ch == "]":
                depth_bracket -= 1
                current.append(ch)
            elif ch == "(":
                depth_paren += 1
                current.append(ch)
            elif ch == ")":
                depth_paren -= 1
                current.append(ch)
            elif (ch == "," or ch == "\n") and depth_brace == 0 and depth_bracket == 0 and depth_paren == 0:
                parts.append("".join(current))
                current = []
            else:
                current.append(ch)

        if current:
            parts.append("".join(current))
        return parts
    else:
        # 其他分隔符的原始逻辑
        parts = []
        current = []
        depth_brace = 0
        depth_bracket = 0
        depth_paren = 0
        in_string = False
        string_char = ""

        for ch in text:
            if in_string:
                current.append(ch)
                if ch == string_char:
                    in_string = False
            elif ch in ('"', "'"):
                in_string = True
                string_char = ch
                current.append(ch)
            elif ch == "{":
                depth_brace += 1
                current.append(ch)
            elif ch == "}":
                depth_brace -= 1
                current.append(ch)
            elif ch == "[":
                depth_bracket += 1
                current.append(ch)
            elif ch == "]":
                depth_bracket -= 1
                current.append(ch)
            elif ch == "(":
                depth_paren += 1
                current.append(ch)
            elif ch == ")":
                depth_paren -= 1
                current.append(ch)
            elif ch == delimiter and depth_brace == 0 and depth_bracket == 0 and depth_paren == 0:
                parts.append("".join(current))
                current = []
            else:
                current.append(ch)

        if current:
            parts.append("".join(current))
        return parts


def _parse_trigger_dsl(trigger_text: str) -> dict[str, Any]:
    """将触发器 DSL 表达式解析为结构化的触发器 dict。

    支持：
      - time: "weekday in [1,4] AND hour == 10"
      - state: "mood < 20"
      - probability: "chance 0.1"
      - composite: "AND: rule1, rule2" 或 "OR: rule1, rule2"
    """
    trigger_text = trigger_text.strip()

    # 检查是否为概率触发器
    if trigger_text.lower().startswith("chance"):
        try:
            chance = float(trigger_text.split()[1])
            return {"kind": "probability", "chance": chance}
        except (IndexError, ValueError):
            raise DSLParseError(f"概率触发器格式错误: {trigger_text}")

    # 检查是否为复合触发器（AND/OR 带规则）
    if trigger_text.upper().startswith("AND:") or trigger_text.upper().startswith("OR:"):
        op = "AND" if trigger_text.upper().startswith("AND:") else "OR"
        rules_text = trigger_text[4:].strip()
        rules = _split_top_level(rules_text, ",")
        parsed_rules = []
        for rule in rules:
            rule = rule.strip()
            if rule:
                parsed_rules.append(_parse_trigger_dsl(rule))
        return {"kind": "composite", "op": op, "rules": parsed_rules}

    # 检查是否为时间触发器（weekday/hour 表达式）
    time_keywords = ("weekday", "hour", "minute")
    if any(kw in trigger_text.lower() for kw in time_keywords):
        # 简单解析："weekday in [1,4] AND hour == 10"
        # 我们存储原始表达式，同时尝试提取结构化部分
        trigger = {"kind": "time", "expression": trigger_text}
        # 尝试提取 weekday
        wd_match = re.search(r"weekday\s+in\s+\[([^\]]+)\]", trigger_text, re.IGNORECASE)
        if wd_match:
            try:
                days = [int(d.strip()) for d in wd_match.group(1).split(",")]
                trigger["weekday"] = days
            except ValueError:
                pass
        # 尝试提取 hour
        hr_match = re.search(r"hour\s*==\s*(\d+)", trigger_text, re.IGNORECASE)
        if hr_match:
            trigger["hour"] = int(hr_match.group(1))
        # 尝试提取 minute
        mn_match = re.search(r"minute\s*==\s*(\d+)", trigger_text, re.IGNORECASE)
        if mn_match:
            trigger["minute"] = int(mn_match.group(1))
        # 默认频率
        trigger["frequency"] = "daily"
        return trigger

    # 默认：状态触发器（field op value）
    # 格式："field op value"，例如 "mood < 20"
    state_match = re.match(r"(\w+)\s*(>|>=|<|<=|==|!=)\s*(.+)", trigger_text)
    if state_match:
        field, op, value_str = state_match.groups()
        value_str = value_str.strip()
        # 尝试解析值
        try:
            if "." in value_str:
                value: Any = float(value_str)
            else:
                value = int(value_str)
        except ValueError:
            value = value_str.strip('"\'')
        return {"kind": "state", "field": field, "op": op, "value": value}

    raise DSLParseError(f"无法解析触发器表达式: {trigger_text}")


# ---------------------------------------------------------------------------
# 事件工厂（C1.1 US-C1.1-02）—— 定义一类事件以便批量实例化
# ---------------------------------------------------------------------------


def list_event_factories(work_dir: str, world_id: str) -> list[dict[str, Any]]:
    """列出某个世界的所有事件工厂。"""
    fpath = _event_factories_path(work_dir, world_id)
    if not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_event_factory(work_dir: str, world_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """创建或更新一个事件工厂（用于批量实例化的模板）。

    工厂模式：
    {
        "id": "factory_xxx",
        "name": "随机暴雨事件",
        "base_trigger": {"kind": "probability", "chance": 0.1},
        "base_effects": {"weather": "storm", "npc_mood": -10},
        "instance_pattern": "rainy_day_{n}",
        "max_instances": 50,
        "cooldown_seconds": 3600
    }
    """
    if not get_world(work_dir, world_id):
        raise DevKitError(404, f"世界观 {world_id} 不存在", code="not_found")

    name = (data.get("name") or "").strip()
    if not name:
        raise DevKitError(400, "工厂名称不能为空", code="missing_name")

    factories = list_event_factories(work_dir, world_id)
    factory_id = data.get("id") or f"factory_{secrets.token_hex(8)}"
    is_new = factory_id not in {f.get("id") for f in factories}

    # 如果提供了 base_trigger 则验证
    base_trigger = data.get("base_trigger", data.get("trigger", {}))
    if base_trigger:
        ok, errors = validate_event_trigger(base_trigger)
        if not ok:
            raise DevKitError(400, "；".join(errors), code="bad_factory_trigger")

    record = {
        "id": factory_id,
        "world_id": world_id,
        "name": name,
        "description": data.get("description", ""),
        "base_trigger": base_trigger,
        "base_effects": data.get("base_effects", {}),
        "instance_pattern": data.get("instance_pattern", "{name}_{n}"),
        "max_instances": int(data.get("max_instances", 50)),
        "cooldown_seconds": int(data.get("cooldown_seconds", 3600)),
        "is_enabled": bool(data.get("is_enabled", True)),
        "created_at": data.get("created_at", iso_now()) if not is_new else iso_now(),
        "updated_at": iso_now(),
    }

    existing_idx = next((i for i, f in enumerate(factories) if f.get("id") == factory_id), -1)
    if existing_idx >= 0:
        factories[existing_idx] = record
    else:
        factories.append(record)

    _save_event_factories(work_dir, world_id, factories)
    return record


def _save_event_factories(work_dir: str, world_id: str, factories: list[dict[str, Any]]) -> None:
    world_dir = _world_dir(work_dir, world_id)
    os.makedirs(world_dir, exist_ok=True)
    with open(_event_factories_path(work_dir, world_id), "w", encoding="utf-8") as f:
        json.dump(factories, f, ensure_ascii=False, indent=2)


def delete_event_factory(work_dir: str, world_id: str, factory_id: str) -> bool:
    factories = list_event_factories(work_dir, world_id)
    before = len(factories)
    factories = [f for f in factories if f.get("id") != factory_id]
    if len(factories) < before:
        _save_event_factories(work_dir, world_id, factories)
        return True
    return False


def instantiate_event_factory(
    work_dir: str, world_id: str, factory_id: str, count: int = 1
) -> list[dict[str, Any]]:
    """从工厂实例化事件（批量创建）。

    返回创建的事件记录列表。
    """
    factories = list_event_factories(work_dir, world_id)
    factory = next((f for f in factories if f.get("id") == factory_id), None)
    if not factory:
        raise DevKitError(404, f"工厂 {factory_id} 不存在", code="not_found")

    events = list_world_events(work_dir, world_id)
    base_count = len(events)

    # 检查容量
    if base_count + count > MAX_EVENTS_PER_WORLD:
        raise DevKitError(
            400,
            f"实例化 {count} 个事件将超过单世界上限 {MAX_EVENTS_PER_WORLD}",
            code="event_cap_exceeded",
        )

    created = []
    base_name = factory["name"]
    pattern = factory["instance_pattern"]
    base_trigger = factory.get("base_trigger", {})
    base_effects = factory.get("base_effects", {})

    for i in range(count):
        n = base_count + i + 1
        instance_name = pattern.format(name=base_name, n=n, i=i)
        event_data = {
            "name": instance_name,
            "description": f"由工厂「{factory['name']}」批量生成",
            "kind": "custom",
            "trigger": base_trigger,
            "effects": base_effects,
            "priority": 50,
            "scene": "",
            "is_enabled": True,
        }
        event = save_world_event(work_dir, world_id, event_data)
        created.append(event)
        events.append(event)  # 更新本地列表以供下一次迭代

    return created


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

    # C1.1 AC-2 —— 单世界事件上限。
    if is_new and len(events) >= MAX_EVENTS_PER_WORLD:
        raise DevKitError(
            400,
            f"单世界事件数量已达上限 {MAX_EVENTS_PER_WORLD} 条，无法继续添加",
            code="event_cap_exceeded",
        )

    # C1.1 边界 —— 拒绝保存名称与触发条件都与现有事件重复的新事件
    #（定义冲突）。
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
    """验证事件触发器定义（C1.1 AC-1）。

    触发器是以下之一::

        {"kind": "time", "at": "MON 10:00"}            # 定时（旧式）
        {"kind": "time", "weekday": [1,4], "hour": 10}  # 结构化（来自 DSL）
        {"kind": "state", "field": "mood", "op": "<", "value": 20}
        {"kind": "probability", "chance": 0.1}         # 每日随机
        {"kind": "composite", "op": "AND", "rules": [...]}
    """
    errors: list[str] = []
    if not isinstance(trigger, dict):
        return False, ["trigger 必须是对象"]
    kind = trigger.get("kind")
    if kind not in _EVENT_TRIGGER_KINDS:
        return False, [f"trigger.kind 必须是 {_EVENT_TRIGGER_KINDS} 之一"]
    if kind == "time":
        # 同时接受旧式 'at' 格式和新式结构化格式
        has_at = trigger.get("at")
        has_structured = trigger.get("weekday") is not None or trigger.get("hour") is not None
        if not has_at and not has_structured:
            errors.append("time 触发器需要 at 字段或 weekday/hour 字段")
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


# ---------------------------------------------------------------------------
# 世界文档版本管理（C1.2 AC-1）
# ---------------------------------------------------------------------------


def list_world_doc_versions(work_dir: str, world_id: str) -> list[dict[str, Any]]:
    """列出世界文档的所有版本。"""
    return _list_world_doc_versions(work_dir, world_id)


def get_world_doc_version(work_dir: str, world_id: str, version: int) -> str | None:
    """获取世界文档的特定版本。"""
    return _get_world_doc_version(work_dir, world_id, version)


def restore_world_doc_version(work_dir: str, world_id: str, version: int) -> dict[str, Any]:
    """将世界文档恢复到特定版本。

    将恢复的内容保存为新版本（不覆盖旧版本）。
    """
    content = _get_world_doc_version(work_dir, world_id, version)
    if content is None:
        raise DevKitError(404, f"版本 {version} 不存在", code="version_not_found")

    # 保存为新版本
    new_version = _save_world_doc_version(work_dir, world_id, content)

    # 更新主 world_doc
    doc_path = _world_doc_path(work_dir, world_id)
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(content)

    # 更新 world.json 记录
    world = get_world(work_dir, world_id)
    if world:
        world["world_doc"] = content
        world["updated_at"] = iso_now()
        versions = _list_world_doc_versions(work_dir, world_id)
        world["doc_versions"] = versions
        with open(_world_path(work_dir, world_id), "w", encoding="utf-8") as f:
            json.dump(world, f, ensure_ascii=False, indent=2)

    return {"restored_version": version, "new_version": new_version, "content": content}


# ---------------------------------------------------------------------------
# 世界文档关键词提取（用于 A4 NPC 生成）
# ---------------------------------------------------------------------------


def extract_world_doc_keywords(work_dir: str, world_id: str) -> list[str]:
    """从世界文档中提取关键词，用于 A4 NPC 生成。"""
    world = get_world(work_dir, world_id)
    if not world:
        raise DevKitError(404, f"世界观 {world_id} 不存在", code="not_found")

    doc = world.get("world_doc", "")
    return _extract_world_doc_keywords(doc)


# 向后兼容 —— 暴露内部函数
extract_world_doc_keywords_from_text = _extract_world_doc_keywords
