"""MLX embedding backend.

Computes dense vector representations of input texts using an MLX
model.  Embedding backends are intentionally simpler than chat: they
return a list-of-floats per text rather than streaming chunks.

Two implementation paths
------------------------

1. ``mlx_embeddings`` — the canonical MLX embedding library.  When
   installed we import it and use its high-level ``generate`` API.
2. Hand-rolled fallback — many MLX chat models (Qwen, LLaMA, Phi,
   Mistral …) expose ``model.model.embed_tokens`` /
   ``model.model.layers`` following the Hugging Face convention.  We
   run a forward pass ourselves, mean-pool over the sequence axis,
   and return the resulting vector.

If neither path produces a working backend, :meth:`is_available`
returns ``False`` so the registry falls through to GGUF.
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


# Default dimensionality reported when the backend is loaded but
# hasn't yet run any inference (e.g. when ``dimensions()`` is called
# before the first ``embed`` call).  The real value is overwritten as
# soon as the first embed completes.
_DEFAULT_DIMENSIONS = 0


def _try_mlx_embeddings_available() -> bool:
    """Return ``True`` when the optional ``mlx_embeddings`` library imports."""
    try:
        import mlx_embeddings  # noqa: F401
        return True
    except Exception:
        return False


def _try_mlx_lm_available() -> bool:
    try:
        import mlx.core  # noqa: F401
        import mlx_lm  # noqa: F401
        return True
    except Exception:
        return False


def _is_qwen_style(model) -> bool:
    """Detect the Qwen/HF-Transformers style architecture."""
    inner = getattr(model, "model", None)
    if inner is None:
        return False
    return hasattr(inner, "embed_tokens") and hasattr(inner, "layers")


def _run_qwen_style(model, input_ids) -> "object":
    """Run a HF-style model and return the last hidden state."""
    import mlx.core as mx

    inner = model.model
    h = inner.embed_tokens(input_ids[None])  # [1, seq, dim]
    for layer in inner.layers:
        h = layer(h)
    return h  # [1, seq, dim]


def _mean_pool(hidden) -> list[float]:
    """Mean-pool ``[1, seq, dim]`` → ``[dim]`` as plain Python floats."""
    import mlx.core as mx

    pooled = mx.mean(hidden, axis=1)  # [1, dim]
    arr = pooled.squeeze(0)  # [dim]
    if hasattr(arr, "tolist"):
        return [float(x) for x in arr.tolist()]
    # numpy / list fallback
    return [float(x) for x in arr]


@register_embedding("mlx")
class MLXEmbeddingBackend(EmbeddingBackend):
    name = "mlx"

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._model_path: Path | None = None
        self._strategy: str = ""  # "mlx_embeddings" | "qwen_style"
        self._dimensions: int = _DEFAULT_DIMENSIONS
        # Saved lazily the first time we encode, to support callers
        # that ask for ``dimensions()`` before ``embed()``.
        self._has_mlx_embeddings = _try_mlx_embeddings_available()
        self._has_mlx_lm = _try_mlx_lm_available()

    # -- introspection ------------------------------------------------------

    def is_available(self) -> bool:
        # Need at least one of the two strategies.  ``mlx_embeddings``
        # is preferred when present because it bundles the right
        # pooling for embedding-specific architectures; ``mlx_lm`` is
        # the always-on fallback for any generative MLX checkpoint.
        return self._has_mlx_embeddings or self._has_mlx_lm

    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def dimensions(self) -> int:
        return self._dimensions

    # -- lifecycle ----------------------------------------------------------

    def load(self, model_path, **kwargs) -> None:
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")

        # Prefer ``mlx_embeddings`` — its loader knows how to handle
        # embedding-specific architectures (BGE, E5, Qwen-Embed, …).
        if self._has_mlx_embeddings:
            try:
                from mlx_embeddings import load as mlx_emb_load
                model, tokenizer = mlx_emb_load(str(path))
            except Exception as exc:
                # Fall through to the hand-rolled path; if both fail we
                # surface the mlx_embeddings error since it's the one
                # the operator expected to work.
                if not self._has_mlx_lm:
                    raise BackendError(
                        f"mlx_embeddings.load failed: {exc}",
                        code="backend_error",
                    ) from exc
            else:
                self._model = model
                self._tokenizer = tokenizer
                self._model_path = path
                self._strategy = "mlx_embeddings"
                return

        # Fallback: load via ``mlx_lm`` and run the forward pass
        # ourselves.  Works for any HF-Transformers-style generative
        # model (Qwen2, LLaMA, Phi, Mistral, …).
        if not self._has_mlx_lm:
            raise BackendError(
                "neither mlx_embeddings nor mlx_lm available",
                code="backend_unavailable",
            )
        try:
            from mlx_lm import load as mlx_lm_load
            model, tokenizer = mlx_lm_load(str(path))
        except Exception as exc:
            raise BackendError(
                f"mlx_lm.load failed: {exc}",
                code="backend_error",
            ) from exc
        if not _is_qwen_style(model):
            raise BackendError(
                "MLX embedding fallback requires a HF-style model "
                "(model.model.embed_tokens + layers); install "
                "mlx_embeddings for richer architecture support",
                code="backend_error",
            )
        self._model = model
        self._tokenizer = tokenizer
        self._model_path = path
        self._strategy = "qwen_style"

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        self._model_path = None
        self._strategy = ""
        self._dimensions = _DEFAULT_DIMENSIONS
        try:
            import mlx.core as mx
            mx.metal.clear_cache()
        except Exception:
            pass

    # -- inference ----------------------------------------------------------

    def embed(self, texts: Sequence[str], *, model_id: str | None = None) -> list[list[float]]:
        if not self.is_loaded():
            raise ModelNotLoaded("no MLX embedding model loaded")
        if not texts:
            return []

        if self._strategy == "mlx_embeddings":
            return self._embed_mlx_embeddings(texts)
        return self._embed_qwen_style(texts)

    # -- internals ----------------------------------------------------------

    def _embed_mlx_embeddings(self, texts: Sequence[str]) -> list[list[float]]:
        """Use ``mlx_embeddings.generate`` to compute embeddings."""
        try:
            from mlx_embeddings import generate as mlx_emb_generate
        except Exception as exc:
            raise BackendError(
                f"mlx_embeddings.generate unavailable: {exc}",
                code="backend_error",
            ) from exc
        results: list[list[float]] = []
        for text in texts:
            try:
                output = mlx_emb_generate(self._model, self._tokenizer, text)
            except Exception as exc:
                raise BackendError(
                    f"mlx_embeddings.generate failed: {exc}",
                    code="backend_error",
                ) from exc
            vector = self._vector_from_mlx_embeddings_output(output)
            results.append(vector)
        if results and self._dimensions == 0:
            self._dimensions = len(results[0])
        return results

    def _vector_from_mlx_embeddings_output(self, output) -> list[float]:
        """Coerce ``mlx_embeddings.generate`` output into a ``list[float]``.

        ``mlx_embeddings`` historically returns a 1-D tensor / numpy
        array of length ``dim``.  We tolerate nested structures too:
        take the first element if it's array-like, then iterate.
        """
        if isinstance(output, (list, tuple)):
            if not output:
                raise BackendError(
                    "mlx_embeddings returned an empty embedding",
                    code="backend_error",
                )
            return self._vector_from_mlx_embeddings_output(output[0])
        if hasattr(output, "tolist"):
            data = output.tolist()
            if data and isinstance(data[0], (list, tuple)):
                data = data[0]
            return [float(x) for x in data]
        if isinstance(output, (int, float)):
            return [float(output)]
        raise BackendError(
            f"unsupported mlx_embeddings output: {type(output).__name__}",
            code="backend_error",
        )

    def _embed_qwen_style(self, texts: Sequence[str]) -> list[list[float]]:
        """Forward-pass + mean-pool for any HF-style generative model."""
        import mlx.core as mx

        results: list[list[float]] = []
        for text in texts:
            try:
                token_ids = self._tokenizer.encode(text)
            except Exception as exc:
                raise BackendError(
                    f"tokenizer.encode failed: {exc}",
                    code="backend_error",
                ) from exc
            if not token_ids:
                results.append([])
                continue
            input_ids = mx.array(token_ids)
            try:
                hidden = _run_qwen_style(self._model, input_ids)
            except Exception as exc:
                raise BackendError(
                    f"forward pass failed: {exc}",
                    code="backend_error",
                ) from exc
            results.append(_mean_pool(hidden))

        if results and self._dimensions == 0:
            nonzero = next((len(v) for v in results if v), 0)
            self._dimensions = nonzero
        return results


__all__ = ["MLXEmbeddingBackend"]
