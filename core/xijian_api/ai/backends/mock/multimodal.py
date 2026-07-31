"""模拟全模态理解后端 —— 测试套件和本地开发用。

目标
-----

* 始终 ``is_available()``，以便 ``tests`` 和 CI 可以在没有安装
  ``mlx``/``llama_cpp`` 且磁盘上没有真正检查点的情况下运行。
* 在 :meth:`load` 中接受任何 ``model_path``（路径会被记录但从不打开）。
* 输出确定的 token 序列，以便测试可以断言输出形状，而无需依赖权重、
  prompt 格式或平台后端。
* 尊重 :class:`AbortSignal`，与 ``mock/chat.py`` 的契约一致。

本模块注册两个后端类：

* :class:`MockMultimodalBackend` — ``register_multimodal("mock")``，
  实现 :class:`xijian_api.ai.types.MultimodalBackend` 的
  :meth:`understand` 接口（返回 :class:`ChatChunk` 迭代器）。
* :class:`MockVideoUnderstandingBackend` —
  ``register_video_understanding("mock")``，实现
  :class:`xijian_api.ai.types.VideoUnderstandingBackend` 的
  :meth:`understand` 接口（返回纯文本）。

Mock multimodal backends — used by the test suite and local development.

Goals
-----

* Always ``is_available()`` so ``tests`` and CI can run without
  ``mlx``/``llama_cpp`` installed and without a real checkpoint on disk.
* Accept any ``model_path`` in :meth:`load` (the path is recorded but
  never opened).
* Emit a deterministic token sequence so tests can assert on output
  shape without depending on weights, prompt formatting, or platform
  backends.
* Honour :class:`AbortSignal`, matching the contract of ``mock/chat.py``.

This module registers two backend classes:

* :class:`MockMultimodalBackend` — ``register_multimodal("mock")``,
  implementing the :meth:`understand` interface of
  :class:`xijian_api.ai.types.MultimodalBackend` (returns a
  :class:`ChatChunk` iterator).
* :class:`MockVideoUnderstandingBackend` —
  ``register_video_understanding("mock")``, implementing the
  :meth:`understand` interface of
  :class:`xijian_api.ai.types.VideoUnderstandingBackend` (returns plain text).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator, Sequence

from xijian_api.ai.base import ModelNotLoaded
from xijian_api.ai.registry import (
    register_multimodal,
    register_video_understanding,
)
from xijian_api.ai.types import (
    ChatChunk,
    ChatChoice,
    ChatUsage,
    GenerationParams,
    MultimodalBackend,
    VideoUnderstandingBackend,
)
from xijian_api.errors import GenerationAborted as ApiGenerationAborted


# 当调用者未传递 ``max_tokens`` 时的默认 token 预算。
# Default token budget when the caller does not pass ``max_tokens``.
_DEFAULT_MAX_TOKENS = 64

# 模拟 token 序列。每个条目是*新*的后缀用于追加，拼接起来就重现
# 了规范的模拟文本。需要已知输出的测试可以直接按原样 join 此列表。
# Mock token sequence. Each entry is a *new* suffix to append; joining
# them reproduces the canonical mock text. Tests that need known output
# can join this list verbatim.
_MOCK_TOKENS: tuple[str, ...] = (
    "Mock", " multimodal", " understanding", " from", " the", " mock",
    " backend", ".", " This", " backend", " is", " intended", " for",
    " tests", " and", " local", " development", " only", ".", " It",
    " does", " not", " load", " any", " real", " model", " weights", ".",
)


def _now_ts() -> int:
    return int(time.time())


def _resolve_max_tokens(params: GenerationParams) -> int:
    """解析 ``max_tokens``，``None``/0 时使用默认预算。

    Resolve ``max_tokens`` honouring ``None`` / 0 as a default budget.
    """
    mt = params.max_tokens
    # 防御：容忍数字字符串（如 "50"），非法值回退默认预算。
    # Defensive: tolerate numeric strings (e.g. "50"); fall back on bad values.
    if isinstance(mt, bool):
        mt = int(mt)
    elif mt is not None:
        try:
            mt = int(mt)
        except (TypeError, ValueError):
            mt = None
    if mt is None or mt <= 0:
        return _DEFAULT_MAX_TOKENS
    return mt


def _build_chunk(
    *,
    chunk_id: str,
    model: str,
    delta: dict | None = None,
    finish_reason: str | None = None,
    usage: ChatUsage | None = None,
) -> ChatChunk:
    """从 OAI 风格的组件组装一个 :class:`ChatChunk`。

    Assemble a :class:`ChatChunk` from its OAI-style pieces.
    """
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
    """从可能的多模态内容（str 或 list）中提取纯文本。

    Extract plain text from possibly-multimodal content (str or list).
    """
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
    """从消息（dict 或带 content 的对象）中提取文本。

    Extract text from a message (dict or object with ``content``).
    """
    if isinstance(m, dict):
        return _content_text(m.get("content"))
    return _content_text(getattr(m, "content", ""))


def _msg_role(m) -> str:
    if isinstance(m, dict):
        return str(m.get("role", ""))
    return str(getattr(m, "role", ""))


def _last_user_text(messages: Sequence) -> str:
    """返回最近一条用户消息的文本。没有则返回空字符串。

    Return the most recent user message's text.  Empty string if none.
    """
    for m in reversed(messages):
        if _msg_role(m) == "user":
            return _msg_text(m)
    return ""


def _build_echo_prefix(messages: Sequence) -> str:
    """从最后一条用户消息返回短的 ``[echo: ...]`` 前缀。

    Return a short ``[echo: ...]`` prefix from the last user message.
    """
    text = _last_user_text(messages).strip()
    if not text:
        return ""
    snippet = text[:120]
    return f"[echo: {snippet}] "


@register_multimodal("mock")
class MockMultimodalBackend(MultimodalBackend):
    """测试和本地开发用的确定性全模态理解后端。

    Deterministic multimodal understanding backend for tests + local development.

    契约镜像 :class:`xijian_api.ai.types.MultimodalBackend`：
    无论 ``stream`` 取值，:meth:`understand` 都返回
    :class:`ChatChunk` 实例的*可迭代对象*；提供 :class:`AbortSignal`
    时在每次发射之间轮询，最终 chunk 的 ``finish_reason`` 为
    ``"abort"``。

    Contract mirrors :class:`xijian_api.ai.types.MultimodalBackend`:
    :meth:`understand` returns an *iterable* of :class:`ChatChunk`
    instances in both blocking (``stream=False``) and streaming
    (``stream=True``) modes.  An :class:`AbortSignal`, when supplied,
    is polled between emissions and the final chunk's
    ``finish_reason`` is ``"abort"`` in that case.
    """

    name = "mock"

    def __init__(self) -> None:
        self._model_path: Path | None = None
        self._context_length: int = 0
        self._loaded: bool = False

    # -- introspection / 内省 ------------------------------------------------------

    def is_available(self) -> bool:
        # 始终可用 —— 这就是模拟器的全部意义。
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    def modalities(self) -> dict[str, bool]:
        """返回此后端支持哪些模态。

        Return which modalities this backend supports.
        """
        return {
            "text": True,
            "image": True,
            "audio": True,
            "video": True,
            "file": True,
        }

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        """记录路径；从不接触文件系统。

        Record the path; never touch the filesystem.

        与 ``mock/chat.py`` 相同：盲目接受路径使契约保持简单，
        额外的 kwargs（模型的 ``extra`` 块 + 调用者覆盖）被静默忽略。

        Same as ``mock/chat.py``: accepting the path blindly keeps the
        contract simple; extra kwargs (the model's ``extra`` block +
        caller overrides) are silently ignored.
        """
        self._model_path = Path(model_path) if model_path is not None else None
        self._context_length = int(context_length) if context_length else 0
        self._loaded = True

    def unload(self) -> None:
        self._model_path = None
        self._context_length = 0
        self._loaded = False

    # -- multimodal understanding / 全模态理解 ---------------------------------------------

    def understand(
        self,
        messages: Sequence,
        params: GenerationParams,
        *,
        stream: bool = False,
        abort_signal=None,
    ) -> Iterator[ChatChunk]:
        """确定性全模态理解输出。

        Deterministic multimodal understanding output.

        与 ``mock/chat.py`` 相同：echo 前缀反映最后一条用户消息，
        以便调用者可验证"请求确实到达了后端"。
        """
        if not self.is_loaded():
            raise ModelNotLoaded("no mock multimodal model loaded")

        max_tokens = _resolve_max_tokens(params)
        chunk_id = f"multimodal-mock-{int(time.time() * 1000)}"
        model_id = str(self._model_path) if self._model_path else "mock"

        tail_count = min(max_tokens, len(_MOCK_TOKENS))
        mock_tail = "".join(_MOCK_TOKENS[:tail_count])
        full_content = _build_echo_prefix(messages) + mock_tail

        if stream:
            return self._streaming(
                full_content=full_content,
                chunk_id=chunk_id,
                model_id=model_id,
                abort_signal=abort_signal,
            )
        return self._blocking(
            full_content=full_content,
            chunk_id=chunk_id,
            model_id=model_id,
            abort_signal=abort_signal,
        )

    # -- internals / 内部 ----------------------------------------------------------

    def _blocking(
        self,
        *,
        full_content: str,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        # 模拟器没有 tokenizer；将单词数报告为替代品，
        # 使显示 token 计数器的调用者能获得非零值。
        words = len(full_content.split())
        usage = ChatUsage(
            prompt_tokens=0,
            completion_tokens=words,
            total_tokens=words,
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant", "content": full_content},
            finish_reason="stop",
            usage=usage,
        )

    def _streaming(
        self,
        *,
        full_content: str,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        # 首个 chunk：仅角色 —— OAI 惯例。
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant"},
        )

        # 逐字符发射：确定性，且便于测试验证增量。
        aborted = False
        emitted = 0
        try:
            for ch in full_content:
                if abort_signal is not None:
                    abort_signal.raise_if_aborted()
                yield _build_chunk(
                    chunk_id=chunk_id,
                    model=model_id,
                    delta={"content": ch},
                )
                emitted += 1
        except ApiGenerationAborted:
            aborted = True

        # 最终 chunk：finish_reason + usage。
        words = len(full_content.split()) if emitted else 0
        usage = ChatUsage(
            prompt_tokens=0,
            completion_tokens=words,
            total_tokens=words,
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason="abort" if aborted else "stop",
            usage=usage,
        )


@register_video_understanding("mock")
class MockVideoUnderstandingBackend(VideoUnderstandingBackend):
    """测试和本地开发用的确定性视频理解后端。

    Deterministic video understanding backend for tests + local development.

    契约镜像 :class:`xijian_api.ai.types.VideoUnderstandingBackend`：
    :meth:`understand` 返回纯文本字符串。
    """

    name = "mock"

    def __init__(self) -> None:
        self._model_path: Path | None = None
        self._loaded: bool = False

    def is_available(self) -> bool:
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    def load(self, model_path, **kwargs) -> None:
        """记录路径；从不接触文件系统。Record the path; never touch the filesystem."""
        self._model_path = Path(model_path) if model_path is not None else None
        self._loaded = True

    def unload(self) -> None:
        self._model_path = None
        self._loaded = False

    def understand(
        self,
        video,
        *,
        prompt: str = "",
        fps: int = 1,
        max_frames: int = 10,
        abort_signal=None,
    ) -> str:
        """确定性视频理解输出。

        Deterministic video understanding output.

        回显 prompt 与视频输入的摘要，以便测试可验证"请求确实到达了后端"。
        """
        if not self.is_loaded():
            raise ModelNotLoaded("no mock video understanding model loaded")
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        if isinstance(video, dict):
            if "url" in video:
                video_summary = str(video["url"])[:120]
            else:
                spec = video.get("video_url") or video.get("file_url") or {}
                if isinstance(spec, dict):
                    video_summary = str(spec.get("url", ""))[:120]
                else:
                    video_summary = str(spec)[:120]
        elif isinstance(video, bytes):
            video_summary = f"<{len(video)} bytes>"
        else:
            video_summary = str(video)[:120]

        prompt_text = prompt or "Describe what is happening in this video."
        return (
            f"[mock-video] prompt: {prompt_text} | video: {video_summary} | "
            f"fps={fps} max_frames={max_frames}"
        )


__all__ = ["MockMultimodalBackend", "MockVideoUnderstandingBackend"]
