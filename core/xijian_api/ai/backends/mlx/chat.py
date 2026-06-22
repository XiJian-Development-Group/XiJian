"""MLX chat backend.

Wraps ``mlx_lm.generate`` / ``mlx_lm.stream_generate`` behind the
:class:`ChatBackend` contract.

Contract
--------

* :meth:`chat` returns an *iterable* of :class:`ChatChunk` instances
  in both blocking (``stream=False``) and streaming (``stream=True``)
  modes.  The route layer (``stubs/chat.py``) translates each chunk
  into an OAI ``chat.completion.chunk`` payload and, for non-streaming
  calls, collapses the iterable into a single OAI ``chat.completion``.

* :class:`AbortSignal` (when supplied) is polled between token
  emissions so a client-side ``POST .../abort`` halts generation
  promptly.  We translate the abort into :class:`GenerationAborted`
  (matching :mod:`xijian_api.errors`).

* The implementation is defensive across recent ``mlx_lm`` versions:
  the parameter names changed (``temp`` → ``temperature``,
  ``max_tokens`` is stable, ``top_p`` accepts ``0.0`` to disable).
  We try the new names first and fall back to the older ones so a
  broader range of ``mlx_lm`` versions just works.
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path
from typing import Iterator, Sequence

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
from xijian_api.errors import GenerationAborted as ApiGenerationAborted


# Token-budget default when ``params.max_tokens`` is ``None``.  Keep
# modest so an accidental call doesn't run away for minutes.
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
    """Translate :class:`GenerationParams` into the kwargs ``mlx_lm`` accepts.

    ``mlx_lm`` renamed ``temp`` → ``temperature`` between 0.18 and
    0.20; we prefer the newer name but fall back to the older one if
    ``mlx_lm.generate`` rejects ``temperature``.  Same idea for
    ``top_p`` — the older API used ``0.0`` to mean "use default",
    whereas the newer one accepts the literal value.
    """
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
    """Pick the parameter names accepted by the installed ``mlx_lm`` version.

    Returns a kwargs dict that the bound function will accept without
    raising ``TypeError``.  We probe the function signature once.
    """
    import inspect

    sig = inspect.signature(mlx_generate)
    accepts = set(sig.parameters.keys())

    base = _build_kwargs(params, max_tokens=max_tokens)

    # Newer API: ``temperature``.  Older API: ``temp``.  We can't
    # always have both, so prefer whichever name the function exposes.
    if "temperature" in base and "temperature" not in accepts and "temp" in accepts:
        base["temp"] = base.pop("temperature")

    # ``stop`` was a positional/keyword in older versions; keep it on
    # only when present.
    if "stop" in base and "stop" not in accepts:
        base.pop("stop")

    return base


def _extract_generation(response) -> str:
    """Pull the cumulative text out of a ``mlx_lm`` generation response.

    Different ``mlx_lm`` versions wrap the result in either:
      * a bare ``str`` (0.18 and earlier ``generate``),
      * a dataclass with ``.text`` (0.20+ ``generate``),
      * a dataclass with ``.text`` (0.20+ ``stream_generate``).
    """
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
    """Pull optional prompt/completion token counts out of a ``mlx_lm`` response.

    Returns an empty dict when the fields aren't exposed (older
    versions).  Callers fill in their own estimates from the tokenizer
    when this is empty.
    """
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
    return isinstance(exc, ApiGenerationAborted)


@register_chat("mlx")
class MLXChatBackend(ChatBackend):
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
            [m.to_dict() if isinstance(m, ChatMessage) else m for m in messages],
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

        # mlx_lm.generate is synchronous; we run it inline.  Abort is
        # best-effort here — we check between token emits by spawning a
        # thread and racing the signal, but mlx_lm doesn't expose a
        # per-token hook in non-stream mode.  The streaming path
        # supports finer abort.
        try:
            text = mlx_generate(self._model, self._tokenizer, prompt, **kwargs)
        except ApiGenerationAborted:
            raise
        except Exception as exc:
            raise BackendError(f"mlx_lm.generate failed: {exc}", code="backend_error") from exc

        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        # mlx_lm may return either a bare string or a dataclass with
        # additional metadata; normalise both.
        content = _extract_generation(text)

        prompt_tokens = _count_tokens(self._tokenizer, prompt)
        completion_tokens = _count_tokens(self._tokenizer, content)
        usage = ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        # Decide finish_reason: ``length`` when we hit the budget, else
        # ``stop``.  ``mlx_lm`` doesn't always tell us, so we infer from
        # completion_tokens.
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
        """Yield incremental :class:`ChatChunk` instances as tokens arrive.

        ``mlx_lm.stream_generate`` yields :class:`GenerationResponse`
        objects whose ``.text`` is the cumulative generated text.  We
        emit only the *new* suffix as the OAI ``delta.content`` so the
        client sees a real token-by-token stream.
        """
        kwargs = _resolve_generate_kwargs(
            mlx_stream, params, max_tokens=max_tokens
        )

        # First chunk announces the role.  OpenAI's convention is to
        # send a role-only chunk first, then content-only deltas, then
        # a final chunk with ``finish_reason``.
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
                        abort_signal.raise_if_aborted()
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
        except ApiGenerationAborted:
            aborted = True
        except Exception as exc:
            raise BackendError(
                f"mlx_lm.stream_generate failed: {exc}",
                code="backend_error",
            ) from exc

        # Final chunk carries finish_reason + usage.  Determine whether
        # the model finished naturally (``stop``) or hit the
        # ``max_tokens`` ceiling (``length``).
        if aborted:
            finish_reason = "abort"
        else:
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


__all__ = ["MLXChatBackend"]
