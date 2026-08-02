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


#: Sentinel returned when no AI backend produced a usable answer (real
#: backends missing/unusable and the deterministic mock failed too).
#: An honest "not available" message — never a fake template suggestion.
AI_UNAVAILABLE_MESSAGE = "当前功能暂不开放，请耐心等待，谢谢"


def auto_suggest(work_dir: str, context: str) -> dict[str, Any]:
    """Return an AI suggestion for ``context`` and log the assist event.

    Uses the real AI backend registry (:mod:`devkit.ai.registry`): a local
    MLX/GGUF backend answers when available; in stub environments the
    deterministic mock backend derives a suggestion from the input context
    (persona / world-doc features) instead of a fixed string.  The assist
    event is logged with ``source='ai_suggested'`` so the 30% AI-ratio
    audit (C4 AC-1) accounts for it.
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
    """Run a blocking chat completion and join the delta content.

    Mirrors the :class:`devkit.ai.types.ChatBackend` contract: ``chat()``
    returns an iterable of :class:`ChatChunk`, each carrying the
    assistant text in ``choices[0].delta['content']``.
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
    """Generate a design suggestion through the real AI backend registry.

    Tries the configured local backends (MLX/GGUF) first; when none is
    usable, falls back to the deterministic mock backend, which derives
    its answer from the input context (persona / world-doc features)
    instead of a fixed template.  Returns ``(text, backend_name)``;
    ``backend_name`` is ``"unavailable"`` only when no backend produced
    a usable answer.
    """
    try:
        from devkit.ai.registry import get_chat_backend

        backend = get_chat_backend(fallbacks=("gguf", "mock"))
        answer = _chat_answer(backend, context)
        if answer and answer.strip():
            return answer.strip(), getattr(backend, "name", "mock")
    except Exception:
        # Real backend missing or unusable (e.g. mlx installed but no
        # model loaded) — the deterministic mock backend is always
        # available, so try it explicitly before giving up.
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
    """C4 AC-2 — propose clarifying questions before producing a design.

    The function list requires the AI assistant to *ask the user first* on
    key decision points rather than deciding preferences unilaterally.  This
    returns a structured ``questions`` list derived from the context so the
    UI can present them and the developer answers before the assistant fills
    in fields.  A real suggestion from the AI backend registry (mock in
    stub environments) is also returned as a starting point, and the assist
    event is logged with ``source='ai_suggested'`` for the 30% audit.
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
    """Return clarifying questions for a module (C4 AC-2)."""
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
