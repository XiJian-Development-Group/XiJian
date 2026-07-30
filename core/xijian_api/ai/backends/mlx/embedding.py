"""MLX 嵌入后端。

MLX embedding backend.

使用 MLX 模型计算输入文本的密集向量表示。嵌入后端比聊天后端有意简化：
它们为每段文本返回浮点数列表，而不是流式块。

两条实现路径
------------------------

1. ``mlx_embeddings`` — 规范的 MLX 嵌入库。安装后我们导入并使用其
   高级 ``generate`` API。
2. 手工回退实现 —— 许多 MLX 聊天模型（Qwen、LLaMA、Phi、Mistral...）
   遵循 Hugging Face 惯例暴露 ``model.model.embed_tokens`` /
   ``model.model.layers``。我们自己运行前向传播，对序列轴做均值池化，
   返回结果向量。

如果两条路径都产生不了工作后端，:meth:`is_available` 返回 ``False``，
注册表就会回退到 GGUF。

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


# 后端加载后但在首次推理前的默认维度报告（例如在首次 ``embed`` 调用
# 之前调用 ``dimensions()``）。首次嵌入完成后覆盖为真实值。
_DEFAULT_DIMENSIONS = 0


def _try_mlx_embeddings_available() -> bool:
    """可选 ``mlx_embeddings`` 库可导入时返回 ``True``。

    Return ``True`` when the optional ``mlx_embeddings`` library imports.
    """
    try:
        import mlx_embeddings  # noqa: F401
        return True
    except Exception:
        return False


def _try_mlx_lm_available() -> bool:
    """``mlx_lm`` 可导入时返回 ``True``。

    Return ``True`` when ``mlx_lm`` imports.
    """
    try:
        import mlx.core  # noqa: F401
        import mlx_lm  # noqa: F401
        return True
    except Exception:
        return False


def _is_qwen_style(model) -> bool:
    """检测 Qwen/HF-Transformers 风格的架构。

    Detect the Qwen/HF-Transformers style architecture.
    """
    inner = getattr(model, "model", None)
    if inner is None:
        return False
    return hasattr(inner, "embed_tokens") and hasattr(inner, "layers")


def _run_qwen_style(model, input_ids) -> "object":
    """运行 HF 风格模型并返回最后的隐藏状态。

    Run a HF-style model and return the last hidden state.
    """
    import mlx.core as mx

    inner = model.model
    h = inner.embed_tokens(input_ids[None])  # [1, seq, dim]
    for layer in inner.layers:
        h = layer(h)
    return h  # [1, seq, dim]


def _mean_pool(hidden) -> list[float]:
    """均值池化 ``[1, seq, dim]`` → ``[dim]``，返回 Python 浮点列表。

    Mean-pool ``[1, seq, dim]`` → ``[dim]`` as plain Python floats.
    """
    import mlx.core as mx

    pooled = mx.mean(hidden, axis=1)  # [1, dim]
    arr = pooled.squeeze(0)  # [dim]
    if hasattr(arr, "tolist"):
        return [float(x) for x in arr.tolist()]
    # numpy / list 回退
    return [float(x) for x in arr]


@register_embedding("mlx")
class MLXEmbeddingBackend(EmbeddingBackend):
    """MLX 嵌入后端。MLX embedding backend."""
    name = "mlx"

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._model_path: Path | None = None
        self._strategy: str = ""  # "mlx_embeddings" | "qwen_style"
        self._dimensions: int = _DEFAULT_DIMENSIONS
        # 首次编码时惰性保存，以支持调用者在 ``embed()`` 之前查询 ``dimensions()``。
        self._has_mlx_embeddings = _try_mlx_embeddings_available()
        self._has_mlx_lm = _try_mlx_lm_available()

    # -- introspection / 内省 ------------------------------------------------------

    def is_available(self) -> bool:
        # 至少需要两条策略之一。``mlx_embeddings`` 优先，因为它为
        # 嵌入专用架构捆绑了正确的池化；``mlx_lm`` 是任何生成式
        # MLX 检查点的始终可用回退。
        return self._has_mlx_embeddings or self._has_mlx_lm

    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def dimensions(self) -> int:
        return self._dimensions

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(self, model_path, **kwargs) -> None:
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")

        # 优先使用 ``mlx_embeddings`` —— 其加载器知道如何处理
        # 嵌入专用架构（BGE、E5、Qwen-Embed...）。
        if self._has_mlx_embeddings:
            try:
                from mlx_embeddings import load as mlx_emb_load
                model, tokenizer = mlx_emb_load(str(path))
            except Exception as exc:
                # 回退到手写路径；若两者都失败，则暴露 mlx_embeddings
                # 错误，因为那是操作者期望工作的工具。
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

        # 回退：通过 ``mlx_lm`` 加载并自行运行前向传播。
        # 适用于任何 HF-Transformers 风格的生成式模型
        #（Qwen2、LLaMA、Phi、Mistral...）。
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

    # -- inference / 推理 ----------------------------------------------------------

    def embed(self, texts: Sequence[str], *, model_id: str | None = None) -> list[list[float]]:
        if not self.is_loaded():
            raise ModelNotLoaded("no MLX embedding model loaded")
        if not texts:
            return []

        if self._strategy == "mlx_embeddings":
            return self._embed_mlx_embeddings(texts)
        return self._embed_qwen_style(texts)

    # -- internals / 内部 ----------------------------------------------------------

    def _embed_mlx_embeddings(self, texts: Sequence[str]) -> list[list[float]]:
        """使用 ``mlx_embeddings.generate`` 计算嵌入。

        Use ``mlx_embeddings.generate`` to compute embeddings.
        """
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
        """将 ``mlx_embeddings.generate`` 输出强制转换为 ``list[float]``。

        ``mlx_embeddings`` 历史上返回长度为 ``dim`` 的 1-D 张量/numpy 数组。
        我们也容忍嵌套结构：如果是类数组则取第一个元素，然后迭代。

        Coerce ``mlx_embeddings.generate`` output into a ``list[float]``.

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
        """任何 HF 风格生成式模型的前向传播 + 均值池化。

        Forward-pass + mean-pool for any HF-style generative model.
        """
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