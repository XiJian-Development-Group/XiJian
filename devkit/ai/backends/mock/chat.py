"""确定性模拟聊天后端 —— DevKit 版。

用于无本地模型（MLX/GGUF）的 stub 环境：始终 ``is_available()``，
无需加载任何权重即可回答问题。``chat()`` 根据输入上下文（最后一条
用户消息）**提取特征**并生成确定性的多样化建议 —— 相同输入产出
相同输出，不同输入产出不同输出（建议内容引用真实上下文特征，而非
固定字符串），使 C4 AI 设计辅助在离线/测试环境下可复现、可断言。

契约镜像 :class:`devkit.ai.types.ChatBackend`：

* :meth:`chat` 在阻塞（``stream=False``）与流式（``stream=True``）
  模式下都返回 :class:`ChatChunk` 的可迭代对象。
* ``load()`` 接受任意 ``model_path``（从不打开文件系统 —— 模拟器
  没有权重可加载）。

DevKit 版模拟聊天后端。

用于没有本地 MLX/GGUF 模型的 stub 环境：始终 ``is_available()``
且从不接触文件系统。``chat()`` 从输入上下文（最后一条用户消息）
中提取特征，并生成确定性的多样化建议 —— 相同输入始终产生相同
输出，不同输入产生不同输出，因此 C4 AI 设计辅助在离线/测试
环境下可复现、可断言。
"""

from __future__ import annotations

import re
import time
from typing import Iterator, Sequence

from devkit.ai.registry import register_chat
from devkit.ai.types import (
    ChatBackend,
    ChatChunk,
    ChatChoice,
    ChatUsage,
    GenerationParams,
)


# ---------------------------------------------------------------------------
# Helpers / 辅助函数
# ---------------------------------------------------------------------------


def _now_ts() -> int:
    return int(time.time())


def _build_chunk(
    *,
    chunk_id: str,
    model: str,
    delta: dict | None = None,
    finish_reason: str | None = None,
    usage: ChatUsage | None = None,
) -> ChatChunk:
    """根据 OAI 风格片段组装一个 :class:`ChatChunk`。"""
    return ChatChunk(
        id=chunk_id,
        model=model,
        created=_now_ts(),
        choices=[
            ChatChoice(
                index=0,
                delta=delta if delta is not None else {},
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
        backend="mock",
    )


def _content_text(content) -> str:
    """从可能多模态的内容（str 或 list）中提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                t = p.get("text", "")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return ""


def _msg_text(m) -> str:
    """从 ChatMessage 或 dict 中提取文本。"""
    if hasattr(m, "content"):
        return _content_text(m.content)
    if isinstance(m, dict):
        return _content_text(m.get("content"))
    return ""


def _msg_role(m) -> str:
    if hasattr(m, "role"):
        return m.role
    if isinstance(m, dict):
        return str(m.get("role", ""))
    return ""


def _last_user_text(messages: Sequence) -> str:
    """返回最近一条用户消息的文本。没有则返回空字符串。"""
    for m in reversed(messages):
        if _msg_role(m) == "user":
            return _msg_text(m)
    return ""


# ---------------------------------------------------------------------------
# 上下文特征提取（确定性）
# ---------------------------------------------------------------------------

_MODULE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("character", ("角色", "人设", "性格", "character")),
    ("world", ("世界", "世界观", "设定", "world")),
    ("plot", ("剧情", "故事", "plot", "章节")),
    ("dialog", ("对话", "台词", "dialog")),
)

_NAME_PATTERNS: tuple[tuple[str, str], ...] = (
    # 「名字」 / "名字" / 『名字』
    (r"[「『\"']([^」』\"']{1,12})[」』\"']", "quoted"),
    # 名字：X / 名称: X / 姓名：X / 角色 X
    (r"(?:名字|名称|姓名|角色名|角色)[：:]\s*([^\s，,。；;]{1,12})", "named"),
    (r"角色\s+([A-Za-z0-9_\u4e00-\u9fff]{1,12})", "role"),
)

_STOPWORDS = frozenset(
    "的 了 是 在 我 你 他 她 它 和 与 及 或 一个 这个 那个 请 帮忙 帮我 设计 建议 需要 想要 希望 关于 如何 怎么 什么 一些 一下 可以 会 很 也 都 就 才 但 而".split()
)


def _detect_module(text: str) -> str:
    """按关键词检测设计模块（character/world/plot/dialog/general）。"""
    for module, keywords in _MODULE_KEYWORDS:
        if any(k in text for k in keywords):
            return module
    return "general"


def _extract_names(text: str) -> list[str]:
    """提取上下文中的名字/引号片段（去重，保持出现顺序）。"""
    names: list[str] = []
    for pattern, _kind in _NAME_PATTERNS:
        for m in re.finditer(pattern, text):
            name = m.group(1).strip()
            if name and name not in names:
                names.append(name)
    return names


def _extract_keywords(text: str) -> list[str]:
    """提取非停用词的短关键词（用于让建议引用真实上下文）。"""
    tokens = re.split(r"[\s，。；、,.!?！？:：()（）\[\]【】\"'「」『』\n]+", text)
    out: list[str] = []
    for tok in tokens:
        tok = tok.strip()
        if not tok or tok in _STOPWORDS:
            continue
        if tok in out:
            continue
        out.append(tok)
        if len(out) >= 4:
            break
    return out


def _build_suggestion(text: str) -> str:
    """基于上下文特征组装确定性建议。

    输出包含提取到的名字/关键词，因此不同输入会产生不同输出；
    同一输入始终产生同一输出（无随机源）。
    """
    module = _detect_module(text)
    names = _extract_names(text)
    keywords = _extract_keywords(text)
    name_part = "、".join(names) if names else "（未提及具体名字，请补充）"
    kw_part = "、".join(keywords) if keywords else "（未提取到明确关键词）"

    module_lines = {
        "character": (
            "· 性格内核：围绕一个核心矛盾设计（如\"温柔却害怕被抛下\"），"
            "并写一条长期记忆锚定它。\n"
            "· 语言风格：给角色 2-3 个口头禅，情绪高点与低点用不同句式。"
        ),
        "world": (
            "· 时间线：明确\"现在\"所处的纪元与上一场大转折。\n"
            "· 冲突引擎：两个主要势力各持一个不可妥协的诉求。"
        ),
        "plot": (
            "· 起：一个打破日常的事件（对应第一个节点）。\n"
            "· 转：代价显现，关系重组；为关键节点绑定角色与世界事件。"
        ),
        "dialog": (
            "· 开场：用\"反问+自述\"把对话权交还用户。\n"
            "· 情绪线：每轮对话推进一个情绪刻度，避免原地打转。"
        ),
        "general": (
            "· 先补全上下文（角色名/世界观类型/剧情走向），再给出具体建议。"
        ),
    }[module]

    return (
        f"【{module} 建议 · 基于你的输入】\n"
        f"· 识别到名字/片段：{name_part}\n"
        f"· 识别到关键词：{kw_part}\n"
        f"{module_lines}\n"
        f"（此建议由确定性 mock 后端从上下文特征生成，可用于离线验证 C4 流程。）"
    )


# ---------------------------------------------------------------------------
# Backend / 后端
# ---------------------------------------------------------------------------


@register_chat("mock")
class MockChatBackend(ChatBackend):
    """确定性模拟聊天后端（DevKit 版）。"""

    name = "mock"

    def __init__(self) -> None:
        self._loaded: bool = False

    # -- 自省 ----------------------------------------------------------

    def is_available(self) -> bool:
        # 始终可用 —— 这就是模拟器的意义。
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    # -- 生命周期 ----------------------------------------------------------

    def load(self, model_path, **kwargs) -> None:
        """记录路径；从不接触文件系统。"""
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    # -- 生成 ---------------------------------------------------------

    def chat(
        self,
        messages: Sequence,
        params: GenerationParams,
        *,
        stream: bool = False,
        abort_signal=None,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        user_text = _last_user_text(messages)
        full_content = _build_suggestion(user_text)

        chunk_id = f"chatcmpl-mock-{int(time.time() * 1000)}"
        model_id = "mock"

        if stream:
            # 首个 chunk：仅角色 —— OAI 惯例。
            yield _build_chunk(
                chunk_id=chunk_id,
                model=model_id,
                delta={"role": "assistant"},
            )
            # 逐字符发射：确定性，且演练流式组装器。
            for ch in full_content:
                if abort_signal is not None:
                    abort_signal.raise_if_aborted()
                yield _build_chunk(
                    chunk_id=chunk_id,
                    model=model_id,
                    delta={"content": ch},
                )
            words = len(full_content.split())
            yield _build_chunk(
                chunk_id=chunk_id,
                model=model_id,
                delta={},
                finish_reason="stop",
                usage=ChatUsage(
                    prompt_tokens=0,
                    completion_tokens=words,
                    total_tokens=words,
                ),
            )
            return

        # 阻塞模式：单个 chunk。
        words = len(full_content.split())
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant", "content": full_content},
            finish_reason="stop",
            usage=ChatUsage(
                prompt_tokens=0,
                completion_tokens=words,
                total_tokens=words,
            ),
        )


__all__ = ["MockChatBackend"]
