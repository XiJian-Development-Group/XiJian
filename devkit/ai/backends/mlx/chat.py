"""MLX chat backend for DevKit — adapted from core/xijian_api/ai/backends/mlx/chat.py."""

from __future__ import annotations

import time
import warnings
from pathlib import Path
from typing import Iterator, Sequence

from devkit.ai.base import (
    BackendError,
    GenerationAborted,
    ModelNotLoaded,
)
from devkit.ai.registry import register_chat
from devkit.ai.types import (
    ChatBackend,
    ChatChunk,
    ChatChoice,
    ChatMessage,
    ChatUsage,
    GenerationParams,
)


# Token-budget default when ``params.max_tokens`` is ``None``.
_DEFAULT_MAX_TOKENS = 1024


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
    """Assemble a :class:`ChatChunk` from its OAI-style pieces."""
    choices = [
        ChatChoice(
            index=0,
            delta=delta if delta is not None else {},
            finish_reason=finish_reason,
        )
    ]
    return ChatChunk(
        id=chunk_id,
        model=model,
        created=_now_ts(),
        choices=choices,
        usage=usage,
        backend="mlx",
    )


def _resolve_max_tokens(params: GenerationParams) -> int:
    """Return ``max_tokens`` honouring ``None`` as ``_DEFAULT_MAX_TOKENS``."""
    if params.max_tokens is None or params.max_tokens <= 0:
        return _DEFAULT_MAX_TOKENS
    return int(params.max_tokens)


def _build_kwargs(
    params: GenerationParams,
    *,
    max_tokens: int,
) -> dict:
    """Translate :class:`GenerationParams` into the kwargs ``mlx_lm`` accepts."""
    kwargs: dict = {
        "max_tokens": max_tokens,
        "verbose": False,
    }
    temperature = float(params.temperature) if params.temperature is not None else 0.0
    top_p = float(params.top_p) if params.top_p is not None else 1.0
    if temperature != 0.0:
        kwargs["temperature"] = temperature
    if 0.0 < top_p < 1.0:
        kwargs["top_p"] = top_p
    stop = params.stop
    if stop:
        kwargs["stop"] = list(stop)
    return kwargs


def _resolve_generate_kwargs(
    mlx_generate,
    params: GenerationParams,
    *,
    max_tokens: int,
) -> dict:
    """Pick the parameter names accepted by the installed ``mlx_lm`` version."""
    import inspect

    sig = inspect.signature(mlx_generate)
    accepts = set(sig.parameters.keys())

    base = _build_kwargs(params, max_tokens=max_tokens)

    # Newer API: ``temperature``.  Older API: ``temp``.
    if "temperature" in base and "temperature" not in accepts and "temp" in accepts:
        base["temp"] = base.pop("temperature")

    # ``stop`` was a positional/keyword in older versions; keep it on
    # only when present.
    if "stop" in base and "stop" not in accepts:
        base.pop("stop")

    return base


def _extract_generation(response) -> str:
    """Pull the cumulative text out of a ``mlx_lm`` generation response."""
    if isinstance(response, str):
        return response
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    raise BackendError(
        f"unexpected mlx_lm response type: {type(response).__name__}",
        code="backend_error",
    )


def _extract_response_meta(response) -> dict:
    """Pull optional prompt/completion token counts out of a ``mlx_lm`` response."""
    meta: dict = {}
    for key in (
        "prompt_tokens",
        "generation_tokens",
        "completion_tokens",
    ):
        value = getattr(response, key, None)
        if isinstance(value, int):
            meta[key] = value
    finish_reason = getattr(response, "finish_reason", None)
    if isinstance(finish_reason, str):
        meta["finish_reason"] = finish_reason
    return meta


def _count_tokens(tokenizer, text: str) -> int:
    """Best-effort token count via the tokenizer; 0 on failure."""
    try:
        return len(tokenizer.encode(text))
    except Exception:
        return 0


def _resolve_aborted(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` is the abort signal raised internally."""
    return isinstance(exc, GenerationAborted)


@register_chat("mlx")
class MLXChatBackend:
    name = "mlx"

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._model_path: Path | None = None

    # -- introspection ------------------------------------------------------

    def is_available(self) -> bool:
        try:
            import mlx.core  # noqa: F401
            import mlx_lm  # noqa: F401
            return True
        except Exception:
            return False

    def is_loaded(self) -> bool:
        return self._model is not None and self._tokenizer is not None

    # -- lifecycle ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        try:
            from mlx_lm import load
        except Exception as exc:
            raise BackendError(
                f"mlx_lm not importable: {exc}",
                code="backend_unavailable",
            ) from exc
        path = Path(model_path)
        if not path.exists():
            raise BackendError(
                f"model path does not exist: {path}",
                code="model_not_found",
            )
        try:
            self._model, self._tokenizer = load(str(path))
        except Exception as exc:
            raise BackendError(
                f"mlx_lm.load failed: {exc}",
                code="backend_error",
            ) from exc
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

    # -- generation ---------------------------------------------------------

    def chat(
        self,
        messages: Sequence,
        params: GenerationParams,
        *,
        stream: bool = False,
        abort_signal=None,
    ) -> Iterator[ChatChunk]:
        if not self.is_loaded():
            raise ModelNotLoaded("no MLX chat model loaded")
        try:
            from mlx_lm import generate as mlx_generate
            from mlx_lm import stream_generate as mlx_stream
        except Exception as exc:
            raise BackendError(
                f"mlx_lm not importable: {exc}",
                code="backend_unavailable",
            ) from exc

        prompt = self._tokenizer.apply_chat_template(
            [m.to_dict() if hasattr(m, 'to_dict') else m for m in messages],
            tokenize=False,
            add_generation_prompt=True,
        )
        max_tokens = _resolve_max_tokens(params)
        chunk_id = f"chatcmpl-mlx-{int(time.time() * 1000)}"
        model_id = str(self._model_path) if self._model_path else "mlx"

        if stream:
            return self._streaming(
                prompt=prompt,
                params=params,
                max_tokens=max_tokens,
                chunk_id=chunk_id,
                model_id=model_id,
                mlx_stream=mlx_stream,
                abort_signal=abort_signal,
            )
        return self._blocking(
            prompt=prompt,
            params=params,
            max_tokens=max_tokens,
            chunk_id=chunk_id,
            model_id=model_id,
            mlx_generate=mlx_generate,
            abort_signal=abort_signal,
        )

    # -- internals ----------------------------------------------------------

    def _blocking(
        self,
        *,
        prompt: str,
        params: GenerationParams,
        max_tokens: int,
        chunk_id: str,
        model_id: str,
        mlx_generate,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        """Generate one full response and yield a single :class:`ChatChunk`."""
        kwargs = _resolve_generate_kwargs(
            mlx_generate, params, max_tokens=max_tokens
        )

        try:
            text = mlx_generate(self._model, self._tokenizer, prompt, **kwargs)
        except Exception as exc:
            raise BackendError(f"mlx_lm.generate failed: {exc}", code="backend_error") from exc

        if abort_signal is not None:
            try:
                abort_signal.raise_if_aborted()
            except AttributeError:
                pass

        content = _extract_generation(text)

        prompt_tokens = _count_tokens(self._tokenizer, prompt)
        completion_tokens = _count_tokens(self._tokenizer, content)
        usage = ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        finish_reason = "length" if completion_tokens >= max_tokens else "stop"

        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={
                "role": "assistant",
                "content": content,
            },
            finish_reason=finish_reason,
            usage=usage,
        )

    def _streaming(
        self,
        *,
        prompt: str,
        params: GenerationParams,
        max_tokens: int,
        chunk_id: str,
        model_id: str,
        mlx_stream,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        """Yield incremental :class:`ChatChunk` instances as tokens arrive."""
        kwargs = _resolve_generate_kwargs(
            mlx_stream, params, max_tokens=max_tokens
        )

        # First chunk announces the role.
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant"},
        )

        seen_text = ""
        prompt_tokens = _count_tokens(self._tokenizer, prompt)
        last_response = None
        aborted = False
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                for response in mlx_stream(
                    self._model, self._tokenizer, prompt, **kwargs
                ):
                    if abort_signal is not None:
                        try:
                            abort_signal.raise_if_aborted()
                        except AttributeError:
                            pass
                    last_response = response
                    cumulative = _extract_generation(response)
                    delta_text = cumulative[len(seen_text):]
                    if delta_text:
                        yield _build_chunk(
                            chunk_id=chunk_id,
                            model=model_id,
                            delta={"content": delta_text},
                        )
                    seen_text = cumulative
        except Exception as exc:
            raise BackendError(
                f"mlx_lm.stream_generate failed: {exc}",
                code="backend_error",
            ) from exc

        # Final chunk carries finish_reason + usage.
        meta = _extract_response_meta(last_response) if last_response is not None else {}
        finish_reason = meta.get("finish_reason")
        if finish_reason not in {"stop", "length", "abort"}:
            completion_tokens = _count_tokens(self._tokenizer, seen_text)
            finish_reason = "length" if completion_tokens >= max_tokens else "stop"

        completion_tokens = _count_tokens(self._tokenizer, seen_text)
        if last_response is not None:
            meta = _extract_response_meta(last_response)
            reported = meta.get("generation_tokens")
            if isinstance(reported, int) and reported > 0:
                completion_tokens = reported
        usage = ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason=finish_reason,
            usage=usage,
        )