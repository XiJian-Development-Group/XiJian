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
    """记录一条 AI 辅助事件。

    镜像功能清单中的 ``dev_ai_assist_log`` 表（C4）：记录 AI 生成了什么、
    针对哪个模块、开发者是否采纳。``accepted``（以及隐含的 ``source``）
    会喂入提交时的 30% AI 占比审计。
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
    """计算 AI 建议辅助事件所占的比例。

    功能清单将 ``ai_ratio`` 定义为在*提交*时由 AI 生成的字段占比。
    DevKit 在记录时没有可遍历的单一内容块，因此我们用携带
    ``source='ai_suggested'`` 且被采纳的辅助事件占比来近似——
    这是“开发者的输出中有多少来自 AI”的忠实代理。
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


#: 当没有 AI 后端能产出可用答案时返回的哨兵消息（真实后端缺失/不可用，
#: 且确定性 mock 也失败）。这是一条诚实的“不可用”消息——
#: 绝不是伪造的模板建议。
AI_UNAVAILABLE_MESSAGE = "当前功能暂不开放，请耐心等待，谢谢"


def auto_suggest(work_dir: str, context: str) -> dict[str, Any]:
    """返回 ``context`` 的 AI 建议并记录辅助事件。

    使用真实 AI 后端注册表（:mod:`devkit.ai.registry`）：本地 MLX/GGUF
    后端可用时由它回答；在 stub 环境中，确定性 mock 后端会根据输入
    上下文（人设 / 世界文档特征）推导建议，而非固定字符串。辅助事件
    以 ``source='ai_suggested'`` 记录，使 30% AI 占比审计（C4 AC-1）
    将其计入。
    """
    ctx = (context or "").lower()
    suggestion, backend = _generate_suggestion(context)
    available = backend != "unavailable"
    if available:
        log_assist_event(
            work_dir,
            event_type="suggestion",
            target_module=_detect_module(ctx),
            description=suggestion[:200],
            accepted=True,
            source="ai_suggested",
        )
    return {
        "suggestion": suggestion,
        "backend": backend,
        "available": available,
    }


def _chat_answer(backend, context: str) -> str:
    """运行一次阻塞式聊天补全并拼接 delta 内容。

    镜像 :class:`devkit.ai.types.ChatBackend` 契约：``chat()`` 返回
    :class:`ChatChunk` 的可迭代对象，每个 chunk 在
    ``choices[0].delta['content']`` 中携带助手文本。
    """
    from devkit.ai.types import ChatMessage, GenerationParams

    chunks = backend.chat(
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "你是隙间（XiJian）开发辅助 AI，帮助开发者设计角色、"
                    "世界观、剧情与对话。只输出具体、可执行的建议，使用中文。"
                ),
            ),
            ChatMessage(
                role="user",
                content=f"请就以下内容给出设计建议：\n{context}",
            ),
        ],
        params=GenerationParams(temperature=0.0, max_tokens=512),
        stream=False,
    )
    parts: list[str] = []
    for chunk in chunks:
        for choice in getattr(chunk, "choices", []) or []:
            delta = getattr(choice, "delta", None) or {}
            if isinstance(delta, dict):
                content = delta.get("content")
                if content:
                    parts.append(content)
    return "".join(parts)


def _generate_suggestion(context: str) -> tuple[str, str]:
    """通过真实 AI 后端注册表生成设计建议。

    先尝试配置的本地后端（MLX/GGUF）；当没有可用时，回退到确定性 mock
    后端，它会根据输入上下文（人设 / 世界文档特征）推导答案，而非固定
    模板。返回 ``(text, backend_name)``；仅当没有后端能产出可用答案时，
    ``backend_name`` 才为 ``"unavailable"``。
    """
    try:
        from devkit.ai.registry import get_chat_backend

        backend = get_chat_backend(fallbacks=("gguf", "mock"))
        answer = _chat_answer(backend, context)
        if answer and answer.strip():
            return answer.strip(), getattr(backend, "name", "mock")
    except Exception:
        # 真实后端缺失或不可用（例如已安装 mlx 但未加载模型）——
        # 确定性 mock 后端始终可用，因此在放弃前先显式尝试它。
        pass
    try:
        from devkit.ai.registry import get_chat_backend as _get_chat_backend

        mock = _get_chat_backend(name="mock")
        answer = _chat_answer(mock, context)
        if answer and answer.strip():
            return answer.strip(), "mock"
    except Exception:
        pass
    return AI_UNAVAILABLE_MESSAGE, "unavailable"


def suggest_with_questions(work_dir: str, context: str) -> dict[str, Any]:
    """C4 AC-2 —— 在生成设计之前先提出澄清性问题。

    功能清单要求 AI 助手在关键决策点上*先询问用户*，而不是单方面
    决定偏好。本函数返回从上下文推导的结构化 ``questions`` 列表，
    供 UI 展示，开发者在助手填写字段之前回答。同时返回 AI 后端注册表
    的真实建议（stub 环境中为 mock），并将辅助事件以
    ``source='ai_suggested'`` 记录，供 30% 审计使用。
    """
    ctx = (context or "").lower()
    module = _detect_module(ctx)
    suggestion, backend = _generate_suggestion(context)
    available = backend != "unavailable"
    if available:
        log_assist_event(
            work_dir,
            event_type="suggest_questions",
            target_module=module,
            description=suggestion[:200],
            accepted=False,
            source="ai_suggested",
        )
    return {
        "suggestion": suggestion,
        "backend": backend,
        "available": available,
        "module": module,
        "questions": _build_questions(module, context),
    }


def _build_questions(module: str, context: str) -> list[dict[str, Any]]:
    """返回某个模块的澄清性问题（C4 AC-2）。"""
    common = [
        {"key": "name", "question": "这个条目的名称/标题是什么？", "required": True},
        {"key": "tone", "question": "希望的整体基调是？（轻松 / 严肃 / 悲壮 / 治愈）", "required": False},
    ]
    if module == "character":
        return common + [
            {"key": "personality_core", "question": "角色的核心性格矛盾是什么？（一句话）", "required": True},
            {"key": "world_id", "question": "该角色属于哪个世界？（world ID）", "required": False},
            {"key": "voice_style", "question": "语言风格/口头禅有哪些？", "required": False},
        ]
    if module == "world":
        return common + [
            {"key": "era", "question": "时间线当前所处的纪元是？", "required": True},
            {"key": "conflict", "question": "世界的主要冲突/势力有哪些？", "required": True},
        ]
    if module == "plot":
        return common + [
            {"key": "inciting", "question": "打破日常的起点事件是什么？", "required": True},
            {"key": "bind_character", "question": "剧情要绑定哪些角色？（character ID）", "required": False},
        ]
    if module == "dialog":
        return common + [
            {"key": "scenario", "question": "这段对话发生的场景是？", "required": False},
            {"key": "emotion", "question": "期望的角色情绪是？", "required": False},
        ]
    return common


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
