"""GGUF 嵌入后端 —— 包装 ``llama-cpp-python`` 嵌入模式。

llama-cpp 暴露两种嵌入 API：

* ``Llama.embed(text)`` — 单个字符串 → 1-D 向量。
* ``Llama.create_embedding(input=[...])`` — 批量 OAI 风格字典。

我们优先使用 ``create_embedding``，因为它返回 token 用量并镜像
OAI 信封，但对旧版本回退到逐文本 ``embed``。
加载器必须用 ``embedding=True`` 构造；这在 :meth:`load` 中透明完成。

GGUF embedding backend — wraps ``llama-cpp-python`` embedding mode.

llama-cpp exposes two embedding APIs:

* ``Llama.embed(text)`` — single string → 1-D vector.
* ``Llama.create_embedding(input=[...])`` — batch OAI-style dict.

We prefer ``create_embedding`` because it returns token usage and
mirrors the OAI envelope, but fall back to per-text ``embed`` for
older versions.  The loader must be constructed with
``embedding=True``; that's done transparently in :meth:`load`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from xijian_api.ai.base import (
    BackendError,
    ModelNotFound,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_embedding
from xijian_api.ai.types import EmbeddingBackend


def _build_llama(*, path: Path, n_ctx: int):
    """构造一个为嵌入配置的 ``Llama`` 实例。

    Construct a ``Llama`` instance configured for embeddings.
    """
    try:
        from llama_cpp import Llama
    except Exception as exc:
        raise BackendError(
            f"llama-cpp-python not importable: {exc}",
            code="backend_unavailable",
        ) from exc
    try:
        return Llama(model_path=str(path), embedding=True, n_ctx=n_ctx or 4096, verbose=False)
    except Exception as exc:
        raise BackendError(
            f"llama_cpp.Llama init failed: {exc}",
            code="backend_error",
        ) from exc


@register_embedding("gguf")
class GGUFEmbeddingBackend(EmbeddingBackend):
    """GGUF 嵌入后端。GGUF embedding backend."""
    name = "gguf"

    def __init__(self) -> None:
        self._llama = None
        self._model_path: Path | None = None
        self._dimensions: int = 0

    # -- introspection / 内省 ------------------------------------------------------

    def is_available(self) -> bool:
        try:
            import llama_cpp  # noqa: F401
            return True
        except Exception:
            return False

    def is_loaded(self) -> bool:
        return self._llama is not None

    @property
    def dimensions(self) -> int:
        return self._dimensions

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(self, model_path, **kwargs) -> None:
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")
        n_ctx = int(kwargs.get("n_ctx", 0) or 0)
        self._llama = _build_llama(path=path, n_ctx=n_ctx)
        self._model_path = path
        self._dimensions = 0

    def unload(self) -> None:
        self._llama = None
        self._model_path = None
        self._dimensions = 0

    # -- inference / 推理 ----------------------------------------------------------

    def embed(self, texts: Sequence[str], *, model_id: str | None = None) -> list[list[float]]:
        if not self.is_loaded():
            raise ModelNotLoaded("no GGUF embedding model loaded")
        if not texts:
            return []

        # 先尝试批量 OAI API（较新 llama-cpp-python）；对旧版本
        # 回退到逐文本 ``embed``。
        try:
            vectors, dim = self._embed_via_create(texts)
        except _NoBatchAPI:
            vectors, dim = self._embed_per_text(texts)

        if dim and not self._dimensions:
            self._dimensions = dim
        return vectors

    # -- internals / 内部 ----------------------------------------------------------

    def _embed_via_create(self, texts: Sequence[str]):
        """使用 ``Llama.create_embedding(input=...)`` 批量嵌入。

        Use ``Llama.create_embedding(input=...)`` for batch embedding.
        """
        if not hasattr(self._llama, "create_embedding"):
            raise _NoBatchAPI()
        try:
            result = self._llama.create_embedding(input=list(texts))
        except Exception as exc:
            raise BackendError(
                f"llama_cpp.create_embedding failed: {exc}",
                code="backend_error",
            ) from exc
        data = result.get("data") if isinstance(result, dict) else None
        if not data:
            raise _NoBatchAPI()
        vectors: list[list[float]] = []
        dim = 0
        for entry in data:
            emb = entry.get("embedding") if isinstance(entry, dict) else None
            if not isinstance(emb, list):
                raise BackendError(
                    "llama_cpp.create_embedding returned unexpected entry",
                    code="backend_error",
                )
            vectors.append([float(x) for x in emb])
            dim = dim or len(emb)
        return vectors, dim

    def _embed_per_text(self, texts: Sequence[str]):
        """使用 ``Llama.embed`` 逐文本回退（旧版 llama-cpp-python）。

        Per-text fallback using ``Llama.embed`` (older llama-cpp-python).
        """
        embed_fn = getattr(self._llama, "embed", None)
        if not callable(embed_fn):
            raise BackendError(
                "llama-cpp-python build does not support embed()",
                code="backend_unavailable",
            )
        vectors: list[list[float]] = []
        dim = 0
        for text in texts:
            try:
                emb = embed_fn(text)
            except Exception as exc:
                raise BackendError(
                    f"llama_cpp.embed failed: {exc}",
                    code="backend_error",
                ) from exc
            if isinstance(emb, list) and emb and isinstance(emb[0], list):
                emb = emb[0]
            if not isinstance(emb, list):
                raise BackendError(
                    "llama_cpp.embed returned unexpected shape",
                    code="backend_error",
                )
            vectors.append([float(x) for x in emb])
            dim = dim or len(emb)
        return vectors, dim


class _NoBatchAPI(Exception):
    """内部哨兵：此构建上批量嵌入 API 不可用。

    Internal sentinel: batch embedding API not available on this build.
    """


__all__ = ["GGUFEmbeddingBackend"]