"""Chat completion stub — now a thin facade over the configured backend.

The previous fixed-echo implementation ("收到你的消息: ...") has been
removed.  This module now:

* Resolves the configured chat backend (MLX → GGUF fallback) on each
  call so a newly-installed backend picks up immediately.
* Translates :class:`xijian_api.ai.base.BackendError` into the OAI
  ``backend_unavailable`` envelope so clients get a clear 503 when no
  backend can serve the request.
* Yields the backend's :class:`ChatChunk` stream verbatim to the
  route, which serialises it into OAI ``chat.completion.chunk`` JSON.

If the config has no usable backend, :func:`_select_backend` raises
:class:`xijian_api.errors.BackendError` with status 503.
"""

from __future__ import annotations

from typing import Any, Iterator

from flask import current_app

from xijian_api.ai.base import (
    BackendUnavailable as AIBackendUnavailable,
)
from xijian_api.ai.base import (
    BackendError as AIBackendError,
)
from xijian_api.ai.registry import get_chat_backend
from xijian_api.ai.types import (
    ChatBackend,
    ChatMessage,
    GenerationParams,
)
from xijian_api.config import Config
from xijian_api.errors import BackendError as ApiBackendError
from xijian_api.utils.ids import gen_chat_id


def _resolve_config() -> Config | None:
    """Return the active Flask app's :class:`Config`, or ``None``."""
    try:
        return current_app.config.get("XIJIAN_CONFIG")
    except RuntimeError:
        return None


def _select_backend() -> ChatBackend:
    """Pick a chat backend from the active config.

    Falls back through the configured ``fallbacks`` chain.  Raises
    :class:`xijian_api.errors.BackendError` (status 503) if no
    backend is reachable.
    """
    config = _resolve_config()
    requested: str | None = None
    fallbacks: tuple[str, ...] = ()
    if config is not None:
        requested = config.backends.chat.default or None
        fallbacks = config.backends.chat.fallbacks or ()
    try:
        return get_chat_backend(requested, fallbacks)
    except AIBackendUnavailable as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "no chat backend available",
            type_="backend_unavailable",
            code="backend_unavailable",
        ) from exc


def _normalise_messages(messages: list[Any]) -> list[ChatMessage]:
    """Coerce raw dicts into :class:`ChatMessage` instances."""
    out: list[ChatMessage] = []
    for m in messages:
        if isinstance(m, ChatMessage):
            out.append(m)
        else:
            out.append(
                ChatMessage(
                    role=str(m.get("role", "user")),
                    content=str(m.get("content", "")),
                    name=m.get("name"),
                    tool_call_id=m.get("tool_call_id"),
                    tool_calls=m.get("tool_calls"),
                )
            )
    return out


def _to_oai_chunk(chunk) -> dict[str, Any]:
    """Convert a backend :class:`ChatChunk` to an OAI streaming chunk dict."""
    payload: dict[str, Any] = {
        "id": chunk.id,
        "object": "chat.completion.chunk",
        "created": chunk.created,
        "model": chunk.model,
        "choices": [
            {
                "index": c.index,
                "delta": c.delta,
                "finish_reason": c.finish_reason,
            }
            for c in chunk.choices
        ],
    }
    if chunk.usage is not None:
        payload["usage"] = {
            "prompt_tokens": chunk.usage.prompt_tokens,
            "completion_tokens": chunk.usage.completion_tokens,
            "total_tokens": chunk.usage.total_tokens,
        }
    return payload


def _to_oai_response(backend_result, *, model: str) -> dict[str, Any]:
    """Convert a backend non-streaming result to an OAI completion dict.

    Backends return an iterable of :class:`ChatChunk` objects; for
    non-streaming we collapse them into a single message with a single
    finish_reason, mirroring how OpenAI returns ``chat.completion``.
    """
    completion_id = gen_chat_id()
    created = None
    content_parts: list[str] = []
    finish_reason: str | None = None
    usage_dict: dict[str, int] | None = None
    for chunk in backend_result:
        created = created or chunk.created
        for choice in chunk.choices:
            delta = choice.delta or {}
            content = ""
            if isinstance(delta, dict):
                content = delta.get("content") or ""
            elif isinstance(delta, str):
                content = delta
            if content:
                content_parts.append(content)
            if choice.finish_reason:
                finish_reason = choice.finish_reason
        if chunk.usage is not None:
            usage_dict = {
                "prompt_tokens": chunk.usage.prompt_tokens,
                "completion_tokens": chunk.usage.completion_tokens,
                "total_tokens": chunk.usage.total_tokens,
            }
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created or 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "".join(content_parts),
                },
                "finish_reason": finish_reason or "stop",
                "logprobs": None,
            }
        ],
        "usage": usage_dict or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "xijian": {"backend": getattr(backend_result, "backend", None) or ""},
    }


def complete(
    messages: list[dict],
    *,
    model: str = "stub-model",
    temperature: float = 0.7,
    top_p: float = 1.0,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
    n: int = 1,
    user: str | None = None,
    xijian: dict | None = None,
) -> dict[str, Any]:
    """Return a non-streaming OAI chat completion payload via the backend."""
    _ = (user, n)  # accepted for OAI parity; backends consume the rest
    backend = _select_backend()
    params = GenerationParams(
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        stop=stop,
        n=n,
    )
    try:
        result = backend.chat(
            _normalise_messages(messages),
            params,
            stream=False,
        )
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc
    response = _to_oai_response(result, model=model)
    response["model"] = model
    return response


def stream_chunks(
    messages: list[dict],
    *,
    model: str = "stub-model",
    temperature: float = 0.7,
    top_p: float = 1.0,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
    signal=None,
    include_usage: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield OAI streaming chunks via the backend.

    The backend yields :class:`ChatChunk` instances; this function
    serialises them into OAI ``chat.completion.chunk`` JSON.  The
    ``signal`` is forwarded so client cancels abort generation.
    """
    backend = _select_backend()
    params = GenerationParams(
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        stop=stop,
    )
    try:
        for chunk in backend.chat(
            _normalise_messages(messages),
            params,
            stream=True,
            abort_signal=signal,
        ):
            yield _to_oai_chunk(chunk)
        if include_usage:
            # Emit a trailing usage-only chunk if the backend didn't.
            yield {
                "id": gen_chat_id(),
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc


__all__ = ["complete", "stream_chunks"]