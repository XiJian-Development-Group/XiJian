"""OpenAI-compatible remote embedding backend.

Calls ``POST /embeddings`` on any OpenAI-compatible endpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from xijian_api.ai.backends.openai._client import (
    remote_embeddings,
    resolve_config,
)
from xijian_api.ai.base import (
    BackendError,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_embedding
from xijian_api.ai.types import EmbeddingBackend


@register_embedding("openai")
class OpenAIEmbeddingBackend(EmbeddingBackend):
    name = "openai"

    def __init__(self) -> None:
        self._cfg = None
        self._dimensions: int = 0

    def is_available(self) -> bool:
        return True

    def is_loaded(self) -> bool:
        return self._cfg is not None

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def load(self, model_path, **kwargs) -> None:
        section = kwargs.pop("_openai_section", None)
        cfg = resolve_config(kwargs, section=section, default_model="text-embedding-3-small")
        if not cfg.model_name:
            raise BackendError(
                "openai embedding backend requires a model_name",
                code="backend_error",
            )
        self._cfg = cfg

    def unload(self) -> None:
        self._cfg = None
        self._dimensions = 0

    def embed(self, texts: Sequence[str], *, model_id: str | None = None) -> list[list[float]]:
        if not self.is_loaded() or self._cfg is None:
            raise ModelNotLoaded("no openai embedding model loaded")
        if not texts:
            return []
        result = remote_embeddings(self._cfg, input=list(texts))
        data = result.get("data") or []
        vectors: list[list[float]] = []
        for entry in data:
            emb = entry.get("embedding") if isinstance(entry, dict) else None
            if not isinstance(emb, list):
                raise BackendError(
                    "remote embeddings returned unexpected shape",
                    code="backend_error",
                )
            vectors.append([float(x) for x in emb])
        if vectors and not self._dimensions:
            self._dimensions = len(vectors[0])
        return vectors


__all__ = ["OpenAIEmbeddingBackend"]
