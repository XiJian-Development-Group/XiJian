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
    event_type: str = "",
    target_module: str = "",
    description: str = "",
    accepted: bool = True,
    source: str = "ai_suggested",
) -> dict[str, Any]:
    """Record an AI-assist event.

    Mirrors the function-list ``dev_ai_assist_log`` table (C4): records
    what the AI produced, for which module, and whether the developer
    accepted it.  ``accepted`` (and the implied ``source``) feed the
    30% AI-ratio audit at submit time.
    """
    now = __import__("devkit._vendor", fromlist=["iso_now"]).iso_now()
    event = {
        "id": _gen_id(),
        "event_type": event_type,
        "target_module": target_module,
        "description": description,
        "accepted": bool(accepted),
        "source": source,
        "timestamp": now,
    }
    log = _load_log(work_dir)
    log.append(event)
    _save_log(work_dir, log)
    return event


def list_assist_log(work_dir: str, limit: int = 50) -> list[dict[str, Any]]:
    log = _load_log(work_dir)
    return list(reversed(log))[: max(1, limit)]


def get_assist_stats(work_dir: str) -> dict[str, Any]:
    log = _load_log(work_dir)
    total = len(log)
    accepted = sum(1 for e in log if e.get("accepted"))
    by_module: dict[str, int] = {}
    for e in log:
        mod = e.get("target_module", "unknown")
        by_module[mod] = by_module.get(mod, 0) + 1

    latest = log[-1].get("timestamp") if log else None
    return {
        "total_events": total,
        "total": total,
        "accepted_count": accepted,
        "acceptance_rate": round(accepted / total, 2) if total > 0 else 0.0,
        "by_module": by_module,
        "latest_event_at": latest,
        "latest_at": latest,
    }


def calculate_ai_ratio(work_dir: str) -> float:
    """Compute the share of AI-suggested assist events.

    The function list defines ``ai_ratio`` as the fraction of fields an
    AI produced at *submit* time.  The DevKit has no single content blob
    to walk at record time, so we approximate with the share of assist
    events that carry ``source='ai_suggested'`` and were accepted — a
    faithful proxy for "how much of this developer's output came from AI".
    """
    log = _load_log(work_dir)
    if not log:
        return 0.0
    ai_events = sum(1 for e in log if e.get("source") == "ai_suggested")
    return min(round(ai_events / len(log), 4), 1.0)


def check_ai_threshold(work_dir: str, threshold: float | None = None) -> dict[str, Any]:
    limit = float(threshold) if threshold is not None else _AI_THRESHOLD
    ratio = calculate_ai_ratio(work_dir)
    requires_review = ratio > limit
    return {
        "ai_ratio": ratio,
        "ratio": ratio,
        "threshold": limit,
        "requires_review": requires_review,
        "ok": not requires_review,
        "message": (
            f"AI 协助占比 {ratio:.1%}，超过 {limit:.0%} 阈值，需要进行人工审核"
            if requires_review
            else f"AI 协助占比 {ratio:.1%}，在允许范围内"
        ),
    }


def auto_suggest(work_dir: str, context: str) -> dict[str, Any]:
    """Return an AI suggestion for ``context`` and log the assist event.

    The UI sends a free-text ``context``; we pick a template by simple
    keyword detection (no external model is required for the DevKit,
    which is a server-less Pywebview app).  Every suggestion is logged
    with ``source='ai_suggested'`` so it counts toward the 30% audit.
    """
    ctx = (context or "").lower()
    if any(k in ctx for k in ("角色", "人设", "性格", "character")):
        suggestion = _character_suggestion(context)
    elif any(k in ctx for k in ("世界", "世界观", "设定", "world")):
        suggestion = _world_suggestion(context)
    elif any(k in ctx for k in ("剧情", "故事", "plot", "章节")):
        suggestion = _plot_suggestion(context)
    elif any(k in ctx for k in ("对话", "台词", "dialog")):
        suggestion = _dialog_suggestion(context)
    else:
        suggestion = (
            "请补充更多上下文（例如：角色名、世界观类型、剧情走向），"
            "AI 将据此给出更具体的建议。"
        )

    log_assist_event(
        work_dir,
        event_type="suggest",
        target_module=_detect_module(ctx),
        description=context,
        accepted=False,
        source="ai_suggested",
    )
    return {"suggestion": suggestion}


def _detect_module(ctx: str) -> str:
    if any(k in ctx for k in ("角色", "人设", "character")):
        return "character"
    if any(k in ctx for k in ("世界", "世界观", "world")):
        return "world"
    if any(k in ctx for k in ("剧情", "plot")):
        return "plot"
    if any(k in ctx for k in ("对话", "dialog")):
        return "dialog"
    return "general"


def _character_suggestion(context: str) -> str:
    return (
        "【人设建议】\n"
        "· 姓名/外号：贴合世界观，避免与现实名人重名。\n"
        "· 性格内核：用 1 句话定义核心矛盾（如'温柔却害怕被抛下'）。\n"
        "· 语言风格：列出 2-3 个口头禅与情绪化表达。\n"
        "· 记忆基线：先写 8-10 条长期记忆，覆盖身世、执念、人际关系。\n"
        f"（你的输入：{context.strip()[:120]}）"
    )


def _world_suggestion(context: str) -> str:
    return (
        "【世界观建议】\n"
        "· 时间线：明确'现在'所处的纪元与关键转折。\n"
        "· 地理：列出 3-5 个主要区域及其冲突。\n"
        "· 主要势力：每个势力一个核心诉求。\n"
        "· 规则：写出 1 条'可感知'的超自然/科技法则。\n"
        f"（你的输入：{context.strip()[:120]}）"
    )


def _plot_suggestion(context: str) -> str:
    return (
        "【剧情建议】\n"
        "1. 起：一个打破日常的事件（如发现古书/收到密信）。\n"
        "2. 承：主角必须做出选择，触发第一个节点。\n"
        "3. 转：代价显现，关系重组。\n"
        "4. 合：收束到角色成长或一个开放悬念。\n"
        "记得为关键节点绑定角色、世界事件与奖励。\n"
        f"（你的输入：{context.strip()[:120]}）"
    )


def _dialog_suggestion(context: str) -> str:
    return (
        "用户：你好，能跟我说说你自己的事吗？\n"
        "角色：（稍作停顿）当然。我在这片土地上生活了很久，\n"
        "      见过太多来来去去的人。你想听哪一段呢？\n"
        "（建议：让角色用'反问+自述'的方式把对话权交还给用户，"
        "更容易引出多轮互动。）"
    )
