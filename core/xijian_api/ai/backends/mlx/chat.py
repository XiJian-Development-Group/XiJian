"""MLX chat backend."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

from xijian_api.ai.base import (
    BackendError,
    GenerationAborted,
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


@register_chat("mlx")
class MLXChatBackend(ChatBackend):
    name = "mlx"

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._model_path: Path | None = None

    def is_available(self) -> bool:
        try:
            import mlx.core  # noqa: F401
            import mlx_lm  # noqa: F401
            return True
        except Exception:
            return False

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        from mlx_lm import load
        path = Path(model_path)
        if not path.exists():
            raise BackendError(f"model path does not exist: {path}", code="model_not_found")
        self._model, self._tokenizer = load(str(path))
        self._model_path = path

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        self._model_path = None
        try:
            import mlx.core as mx
            mx.metal.clear_cache()
        except Exception:
            pass

    def is_loaded(self) -> bool:
        return self._model is not None

    def chat(self, messages, params: GenerationParams, *, stream: bool = False, abort_signal=None):
        if not self.is_loaded():
            raise ModelNotLoaded("no MLX chat model loaded")
        from mlx_lm import generate as mlx_generate, stream_generate as mlx_stream
        prompt = self._tokenizer.apply_chat_template(
            [m.to_dict() if isinstance(m, ChatMessage) else m for m in messages],
            tokenize=False,
            add_generation_prompt=True,
        )
        if stream:
            return self._streaming(prompt, params, mlx_stream, abort_signal)
        return self._blocking(prompt, params, mlx_generate, abort_signal)
