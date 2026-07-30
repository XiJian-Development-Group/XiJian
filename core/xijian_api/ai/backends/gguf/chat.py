"""GGUF 聊天后端 —— 包装 ``llama-cpp-python``（``llama.cpp`` GGUF 模型的规范绑定）。

契约镜像 :class:`xijian_api.ai.types.ChatBackend`：

* :meth:`chat` 在流式和非流式模式下都产生 :class:`ChatChunk` 实例。
* :class:`AbortSignal` 在 token 发射之间轮询，以便客户端中止能及时停止生成。

llama-cpp-python 特有细节
--------------------------

* ``Llama.create_chat_completion(messages=..., stream=True)`` 返回
  OAI 风格字典的生成器（``{"choices": [{"delta": {...}}]}``）。
* 非流式返回具有相同 ``choices`` 形状的单个字典。
* Token 计数来自 ``usage``（较新版本）或未暴露时从 tokenizer 推断。

GGUF chat backend — wraps ``llama-cpp-python`` (the canonical
binding for ``llama.cpp`` GGUF models).

Contract mirrors :class:`xijian_api.ai.types.ChatBackend`:

* :meth:`chat` yields :class:`ChatChunk` instances in both streaming
  and non-streaming modes.
* :class:`AbortSignal` is polled between token emissions so a client
  abort halts generation promptly.

llama-cpp-python specifics
--------------------------

* ``Llama.create_chat_completion(messages=..., stream=True)`` returns a
  generator of OAI-style dicts (``{"choices": [{"delta": {...}}]}``).
* Non-streaming returns a single dict with the same ``choices`` shape.
* Token counts come from ``usage`` (newer versions) or are inferred
  from the tokenizer when not exposed.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator, Sequence

from xijian_api.ai.base import (
    BackendError,
    ContextLengthExceeded,
    GenerationAborted,
    ModelNotFound,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_chat
from xijian_api.ai.types import (
    ChatBackend,
    ChatChunk,
    ChatChoice,
    ChatMessage,
    ChatUsage,
    GenerationParams,
)
from xijian_api.errors import GenerationAborted as ApiGenerationAborted


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
        backend="gguf",
    )


def _extract_delta_content(delta) -> str:
    """从 OAI delta 字典中提取 ``content`` 字段。

    Pull the ``content`` field out of an OAI delta dict.
    """
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    return content if isinstance(content, str) else ""


def _build_gguf_kwargs(params: GenerationParams, *, max_tokens: int) -> dict:
    kwargs: dict = {
        "temperature": float(params.temperature) if params.temperature is not None else 0.7,
        "top_p": float(params.top_p) if params.top_p is not None else 1.0,
        "max_tokens": max(1, int(max_tokens)),
    }
    if params.stop:
        kwargs["stop"] = list(params.stop)
    return kwargs


@register_chat("gguf")
class GGUFChatBackend(ChatBackend):
    """GGUF 聊天后端。GGUF chat backend."""
    name = "gguf"

    def __init__(self) -> None:
        self._llama = None
        self._model_path: Path | None = None
        self._n_ctx: int = 0

    # -- introspection / 内省 ------------------------------------------------------

    def is_available(self) -> bool:
        try:
            import llama_cpp  # noqa: F401
            return True
        except Exception:
            return False

    def is_loaded(self) -> bool:
        return self._llama is not None

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        try:
            from llama_cpp import Llama
        except Exception as exc:
            raise BackendError(
                f"llama-cpp-python not importable: {exc}",
                code="backend_unavailable",
            ) from exc
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")
        n_ctx = int(context_length) if context_length else 0
        # 当 context_length 为 0 时让 llama.cpp 选择自己的默认值（通常 4096）。
        # 调用者可以通过 kwargs 覆盖。
        try:
            self._llama = Llama(model_path=str(path), n_ctx=n_ctx or 4096, verbose=False)
        except Exception as exc:
            raise BackendError(
                f"llama_cpp.Llama init failed: {exc}",
                code="backend_error",
            ) from exc
        self._model_path = path
        self._n_ctx = n_ctx

    def unload(self) -> None:
        self._llama = None
        self._model_path = None
        self._n_ctx = 0

    # -- generation / 生成 ---------------------------------------------------------

    def chat(
        self,
        messages: Sequence,
        params: GenerationParams,
        *,
        stream: bool = False,
        abort_signal=None,
    ) -> Iterator[ChatChunk]:
        if not self.is_loaded():
            raise ModelNotLoaded("no GGUF chat model loaded")
        messages_dict = [
            m.to_dict() if isinstance(m, ChatMessage) else m for m in messages
        ]
        max_tokens = params.max_tokens or 1024
        chunk_id = f"chatcmpl-gguf-{int(time.time() * 1000)}"
        model_id = str(self._model_path) if self._model_path else "gguf"
        kwargs = _build_gguf_kwargs(params, max_tokens=max_tokens)

        if stream:
            return self._streaming(
                messages=messages_dict,
                kwargs=kwargs,
                chunk_id=chunk_id,
                model_id=model_id,
                abort_signal=abort_signal,
            )
        return self._blocking(
            messages=messages_dict,
            kwargs=kwargs,
            chunk_id=chunk_id,
            model_id=model_id,
            abort_signal=abort_signal,
        )

    # -- internals / 内部 ----------------------------------------------------------

    def _blocking(
        self,
        *,
        messages,
        kwargs,
        chunk_id,
        model_id,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        try:
            result = self._llama.create_chat_completion(
                messages=messages,
                stream=False,
                **kwargs,
            )
        except ApiGenerationAborted:
            raise
        except Exception as exc:
            self._map_llama_exception(exc)
            raise  # pragma: no cover
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        # 结果是 OAI 字典：``{"choices": [{"message": {...}, ...}]}``。
        try:
            choice = result["choices"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError(
                f"llama_cpp returned unexpected shape: {exc}",
                code="backend_error",
            ) from exc
        message = choice.get("message") or {}
        content = message.get("content") or ""
        finish_reason = choice.get("finish_reason") or "stop"
        usage = self._usage_to_chat(result.get("usage"))
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant", "content": content},
            finish_reason=finish_reason,
            usage=usage,
        )

    def _streaming(
        self,
        *,
        messages,
        kwargs,
        chunk_id,
        model_id,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        # 首个 chunk 宣告角色，以便 OAI 客户端能立即开始渲染。
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant"},
        )
        aborted = False
        try:
            for piece in self._llama.create_chat_completion(
                messages=messages,
                stream=True,
                **kwargs,
            ):
                if abort_signal is not None:
                    abort_signal.raise_if_aborted()
                try:
                    choice = piece["choices"][0]
                except (KeyError, IndexError, TypeError) as exc:
                    raise BackendError(
                        f"llama_cpp stream returned bad chunk: {exc}",
                        code="backend_error",
                    ) from exc
                delta = choice.get("delta") or {}
                content = _extract_delta_content(delta)
                finish_reason = choice.get("finish_reason")
                if content:
                    yield _build_chunk(
                        chunk_id=chunk_id,
                        model=model_id,
                        delta={"content": content},
                    )
                if finish_reason:
                    # 最终 chunk 携带 ``finish_reason``。发一次就停止迭代。
                    # llama-cpp-python 通常会在此 chunk 后关闭迭代器。
                    usage = self._usage_to_chat(piece.get("usage"))
                    yield _build_chunk(
                        chunk_id=chunk_id,
                        model=model_id,
                        delta={},
                        finish_reason=(
                            "abort" if aborted else (finish_reason or "stop")
                        ),
                        usage=usage,
                    )
                    return
        except ApiGenerationAborted:
            aborted = True
        except Exception as exc:
            self._map_llama_exception(exc)
            raise  # pragma: no cover

        # 若流式结束未收到 ``finish_reason`` chunk，补发一个终止帧，
        # 确保客户端始终看到终端标记。
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason="abort" if aborted else "stop",
            usage=None,
        )

    # -- helpers / 辅助 ------------------------------------------------------------

    @staticmethod
    def _usage_to_chat(raw) -> ChatUsage | None:
        if not isinstance(raw, dict):
            return None
        prompt_tokens = int(raw.get("prompt_tokens", 0) or 0)
        completion_tokens = int(raw.get("completion_tokens", 0) or 0)
        total = raw.get("total_tokens")
        if total is None:
            total = prompt_tokens + completion_tokens
        return ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(total),
        )

    @staticmethod
    def _map_llama_exception(exc: Exception) -> None:
        """将 llama.cpp 错误翻译为 AI 层的异常类型。

        Translate llama.cpp errors into the AI layer's exception types.
        """
        msg = str(exc).lower()
        if "context" in msg and ("exceed" in msg or "length" in msg or "full" in msg):
            raise ContextLengthExceeded(str(exc)) from exc
        raise BackendError(f"llama_cpp error: {exc}", code="backend_error") from exc


__all__ = ["GGUFChatBackend"]