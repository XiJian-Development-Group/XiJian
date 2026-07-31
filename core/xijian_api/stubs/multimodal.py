"""全模态理解存根 —— 通过注册表调度到全模态后端。

提供统一的多模态处理入口点，将路由层请求转换为后端调用。

Multimodal understanding stub — dispatches to the multimodal backend
via the registry.

Provides a unified multimodal processing entry point that translates
route layer requests into backend calls.
"""

from __future__ import annotations

from typing import Any, Iterator

from flask import current_app

from xijian_api.ai.base import (
    BackendError as AIBackendError,
    BackendUnavailable as AIBackendUnavailable,
)
from xijian_api.ai.model_registry import get_registry
from xijian_api.ai.registry import get_multimodal_backend
from xijian_api.ai.types import (
    GenerationParams,
    MultimodalBackend,
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


def _resolve_backend_for(model_id: str) -> MultimodalBackend:
    """Return a ready-to-call multimodal backend for ``model_id``.

    * When ``model_id`` matches a registered :class:`ModelEntry` of type
      ``"multimodal"``, the entry's declared backend is loaded through
      the process-wide :class:`ModelRegistry` and the cached instance
      is returned.
    * Otherwise the configured default multimodal chain is tried.
    """
    config = _resolve_config()
    if config is not None:
        entry = config.model_by_id(model_id)
        if entry is not None and entry.type == "multimodal":
            try:
                registry = get_registry()
                loaded = registry.load(model_id, config=config)
                return loaded.instance
            except AIBackendUnavailable as exc:
                raise ApiBackendError(
                    status=503,
                    message=str(exc) or "no multimodal backend available",
                    type_="backend_unavailable",
                    code="backend_unavailable",
                ) from exc
            except AIBackendError as exc:
                raise ApiBackendError(
                    status=503,
                    message=str(exc) or "backend error",
                    type_="backend_unavailable",
                    code=getattr(exc, "code", "backend_error"),
                ) from exc

    # Fallback: use default multimodal backend
    config = _resolve_config()
    requested: str | None = None
    fallbacks: tuple[str, ...] = ()
    if config is not None:
        requested = config.backends.multimodal.default or None
        fallbacks = config.backends.multimodal.fallbacks or ()
    try:
        backend = get_multimodal_backend(requested, fallbacks)
    except AIBackendUnavailable as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "no multimodal backend available",
            type_="backend_unavailable",
            code="backend_unavailable",
        ) from exc
    return backend


def _to_oai_chunk(chunk) -> dict[str, Any]:
    """Convert a backend :class:`ChatChunk` to an OAI streaming chunk dict."""
    payload: dict[str, Any] = {
        "id": chunk.id,
        "object": "multimodal.completion.chunk",
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
    """Convert a backend non-streaming result to an OAI-like completion dict."""
    completion_id = gen_chat_id()
    created = None
    content_parts: list[str] = []
    finish_reason: str | None = None
    usage_dict: dict[str, int] | None = None
    backend_name = ""
    for chunk in backend_result:
        created = created or chunk.created
        backend_name = backend_name or getattr(chunk, "backend", "")
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
        "object": "multimodal.completion",
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
        "xijian": {"backend": backend_name or ""},
    }


def understand(
    messages: list[dict],
    *,
    model: str = "stub-multimodal",
    temperature: float = 0.7,
    top_p: float = 1.0,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
) -> dict[str, Any]:
    """Send multimodal messages to the backend and return the understanding result.

    Unlike the chat completion endpoint which primarily operates on text,
    this endpoint accepts any combination of text, image, audio, video,
    and file content parts.  The multimodal backend handles modality
    conversion internally.

    Returns an OAI-like completion dict.
    """
    backend = _resolve_backend_for(model)
    params = GenerationParams(
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        stop=stop,
    )

    try:
        result = backend.understand(messages, params, stream=False)
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc

    return _to_oai_response(result, model=model)


def understand_stream(
    messages: list[dict],
    *,
    model: str = "stub-multimodal",
    temperature: float = 0.7,
    top_p: float = 1.0,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
    signal=None,
    include_usage: bool = False,
) -> Iterator[dict[str, Any]]:
    """Stream multimodal understanding results from the backend.

    Yields OAI streaming chunk dicts.
    """
    backend = _resolve_backend_for(model)
    params = GenerationParams(
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        stop=stop,
    )

    try:
        for chunk in backend.understand(messages, params, stream=True, abort_signal=signal):
            yield _to_oai_chunk(chunk)
        if include_usage:
            yield {
                "id": gen_chat_id(),
                "object": "multimodal.completion.chunk",
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


__all__ = ["understand", "understand_stream"]
