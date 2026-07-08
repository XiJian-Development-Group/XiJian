from __future__ import annotations

import json
import os
import secrets
from typing import Any

from devkit import DevKitError

_AI_ASSIST_SUBDIR = "ai_assist"
_AI_THRESHOLD = 0.30


def _gen_id() -> str:
    return f"ai_assist_{secrets.token_hex(8)}"


def _log_path(work_dir: str) -> str:
    base = os.path.join(work_dir, _AI_ASSIST_SUBDIR)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "assist_log.json")


def _load_log(work_dir: str) -> list[dict[str, Any]]:
    fpath = _log_path(work_dir)
    if not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_log(work_dir: str, log: list[dict[str, Any]]) -> None:
    with open(_log_path(work_dir), "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def log_assist_event(
    work_dir: str,
    *,
    module: str,
    action: str,
    prompt: str,
    output: str,
    accepted: bool,
    source: str = "ai_suggested",
) -> dict[str, Any]:
    now = __import__("devkit._vendor", fromlist=["iso_now"]).iso_now()
    event = {
        "id": _gen_id(),
        "module": module,
        "action": action,
        "prompt": prompt,
        "output": output,
        "accepted": accepted,
        "source": source,
        "timestamp": now,
    }

    log = _load_log(work_dir)
    log.append(event)
    _save_log(work_dir, log)
    return event


def list_assist_log(work_dir: str, limit: int = 50) -> list[dict[str, Any]]:
    log = _load_log(work_dir)
    return list(reversed(log))[:max(1, limit)]


def get_assist_stats(work_dir: str) -> dict[str, Any]:
    log = _load_log(work_dir)
    total = len(log)
    accepted = sum(1 for e in log if e.get("accepted"))
    by_module: dict[str, int] = {}
    for e in log:
        mod = e.get("module", "unknown")
        by_module[mod] = by_module.get(mod, 0) + 1

    return {
        "total_assists": total,
        "accepted_count": accepted,
        "acceptance_rate": round(accepted / total, 2) if total > 0 else 0.0,
        "by_module": by_module,
    }


def calculate_ai_ratio(work_dir: str, content: dict[str, Any]) -> float:
    log = _load_log(work_dir)
    if not log:
        return 0.0

    assist_keys = {e["id"] for e in log if e.get("source") == "ai_suggested"}
    fields: list[str] = []

    def _walk(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                child = f"{path}.{k}" if path else k
                if isinstance(v, (dict, list)):
                    _walk(v, child)
                elif isinstance(v, str) and v and "ai_suggested_" in str(obj.get("source", "")):
                    fields.append(child)
                elif isinstance(v, str) and v:
                    pass
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, path)

    _walk(content)

    total_chars = len(json.dumps(content, ensure_ascii=False))
    ai_chars = len(json.dumps({f: True for f in fields}, ensure_ascii=False))

    if total_chars == 0:
        return 0.0
    ratio = ai_chars / total_chars
    return min(round(ratio, 4), 1.0)


def check_ai_threshold(work_dir: str, content: dict[str, Any]) -> dict[str, Any]:
    ratio = calculate_ai_ratio(work_dir, content)
    return {
        "ai_ratio": ratio,
        "threshold": _AI_THRESHOLD,
        "requires_review": ratio > _AI_THRESHOLD,
        "message": (
            f"AI 协助占比 {ratio:.1%}，超过 {_AI_THRESHOLD:.0%} 阈值，需要进行人工审核"
            if ratio > _AI_THRESHOLD
            else f"AI 协助占比 {ratio:.1%}，在允许范围内"
        ),
    }


def auto_suggest(work_dir: str, module: str, field: str, context: str) -> str:
    if module == "character":
        suggestions = _character_suggestions(field, context)
    elif module == "world":
        suggestions = _world_suggestions(field, context)
    elif module == "plot":
        suggestions = _plot_suggestions(field, context)
    elif module == "dialog":
        suggestions = _dialog_suggestions(field, context)
    else:
        suggestions = f"[AI 辅助] 请为 {module}.{field} 提供内容"

    log_assist_event(
        work_dir,
        module=module,
        action=f"suggest_{field}",
        prompt=context,
        output=suggestions,
        accepted=False,
    )
    return suggestions


def _character_suggestions(field: str, context: str) -> str:
    suggestions = {
        "persona": (
            "温柔善良的少女，拥有感知他人情绪的能力。"
            "喜欢在清晨的花园里照料植物，说话轻声细语，"
            "总是试图理解每个人的立场。内心坚韧，"
            "在关键时刻能展现出意想不到的勇气。"
        ),
        "memory_config": (
            "短期记忆上限：50 条\n"
            "长期记忆上限：200 条\n"
            "记忆衰减率：0.1\n"
            "建议：如果角色有复杂的背景故事，增加长期记忆上限。"
        ),
        "language_style": (
            "语调温柔，常用敬语，偶尔使用口语化的感叹词。"
            "思考时习惯使用'嗯…'作为开场。"
            "情绪激动时会不自觉地加快语速。"
        ),
    }
    return suggestions.get(field, f"请描述角色的{field}设定。")


def _world_suggestions(field: str, context: str) -> str:
    suggestions = {
        "setting": (
            "一个悬浮在云海之上的浮空岛屿群，每个岛屿拥有独特的生态系统。"
            "岛屿之间通过古老的传送门网络连接。"
            "天空永远呈现出黄昏时分的橙紫色。"
        ),
        "rules": (
            "1. 岛屿间的传送需要消耗'星能'\n"
            "2. 每个岛屿都有其独特的'领域法则'\n"
            "3. 外来者需要适应当地法则才能使用力量\n"
            "4. 星能在日落时会自然恢复"
        ),
    }
    return suggestions.get(field, f"请描述世界观的{field}设定。")


def _plot_suggestions(field: str, context: str) -> str:
    suggestions = {
        "description": (
            "主角在一次意外中发现了隐藏在古书中的秘密地图，"
            "地图指向传说中失落的'星之图书馆'。"
            "与此同时，一股神秘势力也在寻找同一目标。"
        ),
        "trigger": (
            "主角在整理旧物时发现一本泛黄的古书\n"
            "条件：已完成'新手引导'任务\n"
            "触发方式：与 NPC 对话选择'关于这本书...'"
        ),
    }
    return suggestions.get(field, f"请描述剧情的{field}设定。")


def _dialog_suggestions(field: str, context: str) -> str:
    return (
        "用户：你好，能告诉我关于你的事情吗？\n"
        "角色：（微笑着）当然可以。我在这里生活了很久，"
        "见证了这座城市的许多变化。你想知道些什么呢？"
    )
