"""OpenAI-compatible remote chat backend.

Connects to any endpoint that implements the OpenAI ``/chat/completions``
API (OpenAI itself, Azure OpenAI, vLLM, Ollama, LM Studio, llama.cpp
server, etc.).  Both streaming (SSE) and non-streaming modes are
supported, as is multimodal content (images via ``image_url`` parts).

Config is resolved per-call via :func:`resolve_config` — the backend
reads ``[[models]].extra`` (per-model override) merged with the
``[backends.openai]`` global section.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

from xijian_api.ai.backends.openai._client import (
    remote_chat_completion,
    resolve_config,
)
from xijian_api.ai.base import (
    BackendError,
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
        backend="openai",
    )


def _messages_to_oai(messages: Sequence) -> list[dict]:
    """Convert :class:`ChatMessage` / dict sequence into OAI dicts.

    Multimodal content (``list[dict]``) is passed through as-is; plain
    strings are forwarded untouched.
    """
    out: list[dict] = []
    for m in messages:
        if isinstance(m, ChatMessage):
            out.append(m.to_dict())
        elif isinstance(m, dict):
            out.append(m)
        else:
            out.append({"role": "user", "content": str(m)})
    return out


@register_chat("openai")
class OpenAIChatBackend(ChatBackend):
    name = "openai"

    def __init__(self) -> None:
        self._cfg = None
        self._model_path: Path | None = None
        self._loaded: bool = False

    # -- introspection ------------------------------------------------------

    def is_available(self) -> bool:
        # Always available — httpx is a hard dependency of the project.
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    # -- lifecycle ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        """Resolve the remote config from kwargs and mark as loaded.

        ``model_path`` is unused for remote backends but accepted to
        satisfy the :class:`ChatBackend` contract (the registry passes
        it regardless of backend type).

        ``kwargs`` carries the merged ``[[models]].extra`` fields plus
        any caller overrides.  At minimum ``base_url`` and
        ``model_name`` must be resolvable (via kwargs, the
        ``[backends.openai]`` section, or env vars).
        """
        section = kwargs.pop("_openai_section", None)
        cfg = resolve_config(kwargs, section=section)
        if not cfg.model_name:
            raise BackendError(
                "openai chat backend requires a model_name (set in "
                "[[models]].extra.model_name or [backends.openai].default_model)",
                code="backend_error",
            )
        self._cfg = cfg
        self._model_path = Path(model_path) if model_path else None
        self._loaded = True

    def unload(self) -> None:
        self._cfg = None
        self._model_path = None
        self._loaded = False

    # -- generation ---------------------------------------------------------

    def chat(
        self,
        messages: Sequence,
        params: GenerationParams,
        *,
        stream: bool = False,
        abort_signal=None,
    ) -> Iterator[ChatChunk]:
        if not self.is_loaded() or self._cfg is None:
            raise ModelNotLoaded("no openai chat model loaded")
        oai_messages = _messages_to_oai(messages)
        kwargs: dict[str, Any] = {}
        if params.temperature is not None:
            kwargs["temperature"] = float(params.temperature)
        if params.top_p is not None:
            kwargs["top_p"] = float(params.top_p)
        if params.max_tokens is not None and params.max_tokens > 0:
            kwargs["max_tokens"] = int(params.max_tokens)
        if params.stop:
            kwargs["stop"] = list(params.stop)

        chunk_id = f"chatcmpl-openai-{int(time.time() * 1000)}"
        model_id = self._cfg.model_name

        if stream:
            return self._streaming(
                oai_messages=oai_messages,
                kwargs=kwargs,
                chunk_id=chunk_id,
                model_id=model_id,
                abort_signal=abort_signal,
            )
        return self._blocking(
            oai_messages=oai_messages,
            kwargs=kwargs,
            chunk_id=chunk_id,
            model_id=model_id,
            abort_signal=abort_signal,
        )

    # -- internals ----------------------------------------------------------

    def _blocking(
        self,
        *,
        oai_messages: list[dict],
        kwargs: dict,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        result = remote_chat_completion(
            self._cfg, messages=oai_messages, stream=False, **kwargs,
        )
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        choices = result.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls")
        finish_reason = choice.get("finish_reason") or "stop"
        usage = self._usage(result.get("usage"))

        delta: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            delta["tool_calls"] = tool_calls
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta=delta,
            finish_reason=finish_reason,
            usage=usage,
        )

    def _streaming(
        self,
        *,
        oai_messages: list[dict],
        kwargs: dict,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        # First chunk: role announcement.
        yield _build_chunk(
            chunk_id=chunk_id, model=model_id, delta={"role": "assistant"},
        )

        aborted = False
        last_usage: ChatUsage | None = None
        try:
            for piece in remote_chat_completion(
                self._cfg, messages=oai_messages, stream=True, **kwargs,
            ):
                if abort_signal is not None:
                    abort_signal.raise_if_aborted()
                choices = piece.get("choices") or []
                choice = choices[0] if choices else {}
                delta = choice.get("delta") or {}
                finish_reason = choice.get("finish_reason")
                # Some providers include usage in the final chunk.
                if "usage" in piece and piece["usage"]:
                    last_usage = self._usage(piece["usage"])
                content = delta.get("content")
                tool_calls = delta.get("tool_calls")
                if content:
                    yield _build_chunk(
                        chunk_id=chunk_id,
                        model=model_id,
                        delta={"content": content},
                    )
                if tool_calls:
                    yield _build_chunk(
                        chunk_id=chunk_id,
                        model=model_id,
                        delta={"tool_calls": tool_calls},
                    )
                if finish_reason:
                    yield _build_chunk(
                        chunk_id=chunk_id,
                        model=model_id,
                        delta={},
                        finish_reason=finish_reason,
                        usage=last_usage,
                    )
                    return
        except ApiGenerationAborted:
            aborted = True

        # If the stream ended without a finish_reason chunk, emit one.
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason="abort" if aborted else "stop",
            usage=last_usage,
        )

    @staticmethod
    def _usage(raw) -> ChatUsage | None:
        if not isinstance(raw, dict):
            return None
        return ChatUsage(
            prompt_tokens=int(raw.get("prompt_tokens", 0) or 0),
            completion_tokens=int(raw.get("completion_tokens", 0) or 0),
            total_tokens=int(raw.get("total_tokens", 0) or 0),
        )


__all__ = ["OpenAIChatBackend"]
