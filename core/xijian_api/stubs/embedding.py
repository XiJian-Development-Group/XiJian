"""Embedding stub — dispatches to the configured embedding backend.

The previous hash-seeded pseudo-vector implementation has been
removed.  :func:`embed` now calls the real backend (MLX → GGUF
fallback) and returns whatever the backend reports.  When no backend
is available the call raises
:class:`xijian_api.errors.BackendError` (status 503) so clients get a
real OAI error envelope rather than fake-but-deterministic vectors.

If a backend reports its own ``dimensions`` count, that value is
returned by :func:`dimensions`; otherwise 0.
"""

from __future__ import annotations

from typing import Any

from flask import current_app

from xijian_api.ai.base import BackendError as AIBackendError
from xijian_api.ai.base import BackendUnavailable as AIBackendUnavailable
from xijian_api.ai.registry import get_embedding_backend
from xijian_api.config import Config
from xijian_api.errors import BackendError as ApiBackendError
from xijian_api.utils.time import now_ts


# Re-exported for callers that referenced the old constant.  A real
# backend reports its own dimensionality, so the value is only used as
# a fallback when the backend doesn't disclose it.
DEFAULT_DIMENSIONS = 0


def _resolve_config() -> Config | None:
    try:
        return current_app.config.get("XIJIAN_CONFIG")
    except RuntimeError:
        return None


def embed(
    texts: list[str],
    *,
    model: str = "stub-embedding",
    dimensions: int | None = None,
    encoding_format: str = "float",
) -> dict[str, Any]:
    """Return an OAI-style embeddings payload via the backend."""
    if isinstance(texts, str):
        texts = [texts]
    config = _resolve_config()
    requested: str | None = None
    fallbacks: tuple[str, ...] = ()
    if config is not None:
        requested = config.backends.embeddings.default or None
        fallbacks = config.backends.embeddings.fallbacks or ()
    try:
        backend = get_embedding_backend(requested, fallbacks)
    except AIBackendUnavailable as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "no embedding backend available",
            type_="backend_unavailable",
            code="backend_unavailable",
        ) from exc

    try:
        # Real backend returns a list[list[float]]; we keep the OAI
        # envelope shape by re-wrapping per-index records here.
        vectors = backend.embed(texts, model_id=model)
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "embedding backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc

    data = [
        {
            "object": "embedding",
            "index": idx,
            "embedding": list(vec),
        }
        for idx, vec in enumerate(vectors)
    ]
    return {
        "object": "list",
        "data": data,
        "model": model,
        "usage": {
            "prompt_tokens": sum(len(t) for t in texts),
            "total_tokens": sum(len(t) for t in texts),
        },
        "xijian": {
            "dimensions": dimensions or (len(vectors[0]) if vectors else 0),
            "created": now_ts(),
        },
    }


def dimensions() -> int:
    """Return the embedding backend's reported dimensionality, or 0."""
    try:
        config = _resolve_config()
        requested: str | None = None
        fallbacks: tuple[str, ...] = ()
        if config is not None:
            requested = config.backends.embeddings.default or None
            fallbacks = config.backends.embeddings.fallbacks or ()
        backend = get_embedding_backend(requested, fallbacks)
    except AIBackendUnavailable:
        return DEFAULT_DIMENSIONS
    reported = getattr(backend, "dimensions", None)
    if callable(reported):
        try:
            return int(reported())
        except Exception:
            return DEFAULT_DIMENSIONS
    if isinstance(reported, int):
        return reported
    return DEFAULT_DIMENSIONS


__all__ = ["embed", "dimensions", "DEFAULT_DIMENSIONS"]