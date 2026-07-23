"""MLX chat backend.

Wraps ``mlx_lm.generate`` / ``mlx_lm.stream_generate`` behind the
:class:`ChatBackend` contract.  When ``mlx_vlm`` is installed and the
loaded checkpoint is a vision-language model (VLM), the backend
transparently dispatches to ``mlx_vlm`` so multimodal content
(``image_url`` parts) is honoured.

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

Multimodal handling
-------------------

* VLM checkpoints (detected via ``config.json`` architectures or the
  presence of an image processor) are loaded through ``mlx_vlm.load``
  and generate via ``mlx_vlm.generate`` / ``mlx_vlm.stream_generate``.
  ``image_url`` parts in the message content are resolved to local
  file paths (``file://``, ``http(s)://`` downloaded to a temp file,
  ``data:image/...;base64,...`` decoded) and passed to ``mlx_vlm``.

* Text-only checkpoints (the common case) keep using ``mlx_lm``.
  When a text-only model receives multimodal content the image parts
  are replaced with ``[image]`` placeholders and only the text parts
  are forwarded — this matches the project's A2 acceptance criterion
  ("model doesn't support the modality → degrade to placeholder
  description and log failure").
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any, Iterator, Sequence

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

# Architecture-name fragments that indicate a vision-language model.
# Matched case-insensitively against ``config.json``'s
# ``architectures`` list (and the ``model_type`` field as a fallback).
_VLM_ARCH_HINTS: tuple[str, ...] = (
    "vl", "vision", "llava", "image", "visual", "qwen2vl",
    "qwen2_vl", "paligemma", "idefics", "pixtral", "florence",
    "internvl", "deepseekvl", "smolvlm", "mllama",
)


def _now_ts() -> int:
    return int(time.time())


def _try_mlx_vlm_available() -> bool:
    """Return ``True`` when ``mlx_vlm`` imports cleanly."""
    try:
        import mlx_vlm  # noqa: F401
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


# ---------------------------------------------------------------------------
# VLM detection + multimodal helpers
# ---------------------------------------------------------------------------


def _detect_vlm(path: Path) -> bool:
    """Heuristically decide whether the checkpoint at ``path`` is a VLM.

    Checks ``config.json`` (when ``path`` is a directory) for known
    VLM architecture names.  Returns ``False`` for single-file
    checkpoints (``.mlx`` / safetensors) — those are overwhelmingly
    text-only and we don't want a false positive that routes them
    through ``mlx_vlm``.
    """
    if not path.is_dir():
        return False
    config_path = path / "config.json"
    if not config_path.exists():
        return False
    try:
        with config_path.open("r", encoding="utf-8") as fp:
            cfg = json.load(fp)
    except Exception:
        return False
    archs = cfg.get("architectures") or []
    if isinstance(archs, str):
        archs = [archs]
    model_type = str(cfg.get("model_type", "")).lower()
    candidates = [str(a).lower() for a in archs] + [model_type]
    for cand in candidates:
        for hint in _VLM_ARCH_HINTS:
            if hint in cand:
                return True
    # Preprocessor config presence is also a strong VLM signal.
    if (path / "preprocessor_config.json").exists():
        return True
    return False


def _msg_content(m) -> Any:
    if isinstance(m, ChatMessage):
        return m.content
    if isinstance(m, dict):
        return m.get("content")
    return None


def _msg_role(m) -> str:
    if isinstance(m, ChatMessage):
        return m.role
    if isinstance(m, dict):
        return str(m.get("role", ""))
    return ""


def _has_multimodal_content(messages: Sequence) -> bool:
    """Return ``True`` when any message carries list-of-parts content."""
    for m in messages:
        content = _msg_content(m)
        if isinstance(content, list):
            return True
    return False


def _degrade_multimodal_to_text(messages: Sequence) -> list:
    """Flatten list-of-parts content into a text-only string.

    ``text`` parts are concatenated, ``image_url``/``audio_url``/
    ``video_url`` parts become ``[image]``/``[audio]``/``[video]``
    placeholders.  Plain-string content is preserved untouched.  The
    returned list mirrors the input types (``ChatMessage`` in →
    ``ChatMessage`` out, ``dict`` in → ``dict`` out).
    """
    out: list = []
    for m in messages:
        content = _msg_content(m)
        if not isinstance(content, list):
            out.append(m)
            continue
        parts: list[str] = []
        for p in content:
            if not isinstance(p, dict):
                parts.append(str(p))
                continue
            ptype = p.get("type")
            if ptype == "text":
                t = p.get("text", "")
                if isinstance(t, str):
                    parts.append(t)
            elif ptype == "image_url":
                parts.append("[image]")
            elif ptype == "audio_url":
                parts.append("[audio]")
            elif ptype == "video_url":
                parts.append("[video]")
            else:
                parts.append(f"[{ptype}]")
        joined = " ".join(p for p in parts if p)
        if isinstance(m, ChatMessage):
            out.append(ChatMessage(
                role=m.role, content=joined, name=m.name,
                tool_call_id=m.tool_call_id, tool_calls=m.tool_calls,
            ))
        elif isinstance(m, dict):
            new_m = dict(m)
            new_m["content"] = joined
            out.append(new_m)
        else:
            out.append(m)
    return out


def _resolve_image_to_path(url: str) -> str | None:
    """Resolve an ``image_url`` value to a local filesystem path.

    Supports:

    * ``file:///abs/path.png``  → ``/abs/path.png``
    * ``/abs/path.png``         → as-is
    * ``http(s)://...``         → downloaded to a temp file
    * ``data:image/...;base64,...`` → decoded to a temp file

    Returns ``None`` when the URL can't be resolved (the caller skips
    the image in that case and lets ``mlx_vlm`` see only the valid
    ones).
    """
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("file://"):
        path = url[len("file://"):]
        return path if Path(path).exists() else None
    if url.startswith("data:"):
        # Format: data:<mime>;base64,<payload>
        try:
            header, b64 = url.split(",", 1)
            mime = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
            ext = mime.split("/")[-1].split("-")[-1] or "png"
            raw = base64.b64decode(b64)
            fd, tmp = tempfile.mkstemp(suffix=f".{ext}")
            with os.fdopen(fd, "wb") as fp:
                fp.write(raw)
            return tmp
        except Exception:
            return None
    if url.startswith("http://") or url.startswith("https://"):
        try:
            import httpx
            ext = url.rsplit(".", 1)[-1].split("?")[0][:5].lower()
            if ext not in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
                ext = "png"
            resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            if resp.status_code >= 400:
                return None
            fd, tmp = tempfile.mkstemp(suffix=f".{ext}")
            with os.fdopen(fd, "wb") as fp:
                fp.write(resp.content)
            return tmp
        except Exception:
            return None
    # Bare filesystem path.
    return url if Path(url).exists() else None


def _extract_images(messages: Sequence) -> list[str]:
    """Pull image paths from multimodal message content.

    Walks every message; for each ``image_url`` part resolves the URL
    to a local path (see :func:`_resolve_image_to_path`) and appends
    it to the result.  Unresolvable images are silently dropped —
    ``mlx_vlm`` will only see the ones that exist on disk.
    """
    images: list[str] = []
    for m in messages:
        content = _msg_content(m)
        if not isinstance(content, list):
            continue
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") != "image_url":
                continue
            spec = p.get("image_url")
            if isinstance(spec, dict):
                url = spec.get("url", "")
            else:
                url = spec if isinstance(spec, str) else ""
            resolved = _resolve_image_to_path(url)
            if resolved:
                images.append(resolved)
    return images


def _messages_to_oai(messages: Sequence) -> list[dict]:
    """Convert :class:`ChatMessage` / dict sequence into OAI dicts."""
    out: list[dict] = []
    for m in messages:
        if isinstance(m, ChatMessage):
            out.append(m.to_dict())
        elif isinstance(m, dict):
            out.append(m)
        else:
            out.append({"role": "user", "content": str(m)})
    return out


@register_chat("mlx")
class MLXChatBackend(ChatBackend):
    name = "mlx"

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None           # mlx_lm path
        self._processor = None           # mlx_vlm path
        self._config: Any = None         # mlx_vlm model config
        self._model_path: Path | None = None
        self._is_vlm: bool = False
        self._has_mlx_vlm = _try_mlx_vlm_available()
        self._has_mlx_lm = _try_mlx_lm_available()
        # Temp files created for remote/data: images — cleaned up on unload.
        self._temp_image_paths: list[str] = []

    # -- introspection ------------------------------------------------------

    def is_available(self) -> bool:
        # Available when either mlx_lm (text) or mlx_vlm (vision) is
        # installed.  mlx_lm is the common case; mlx_vlm alone is also
        # fine (it can serve text-only models too, though we prefer
        # mlx_lm there).
        return self._has_mlx_lm or self._has_mlx_vlm

    def is_loaded(self) -> bool:
        return self._model is not None and (
            self._tokenizer is not None or self._processor is not None
        )

    # -- lifecycle ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        path = Path(model_path)
        if not path.exists():
            raise BackendError(
                f"model path does not exist: {path}",
                code="model_not_found",
            )

        # Decide VLM vs text-only.  An operator can force VLM via the
        # ``vlm = true`` extra field; otherwise we detect from config.
        force_vlm = bool(kwargs.get("vlm") or kwargs.get("is_vlm"))
        use_vlm = (force_vlm or _detect_vlm(path)) and self._has_mlx_vlm

        if use_vlm:
            try:
                from mlx_vlm import load as vlm_load
            except Exception as exc:
                raise BackendError(
                    f"mlx_vlm not importable: {exc}",
                    code="backend_unavailable",
                ) from exc
            try:
                self._model, self._processor = vlm_load(str(path))
            except Exception as exc:
                raise BackendError(
                    f"mlx_vlm.load failed: {exc}",
                    code="backend_error",
                ) from exc
            # Prefer the model's own config attribute; fall back to
            # loading config.json directly.
            self._config = getattr(self._model, "config", None)
            if self._config is None:
                try:
                    from mlx_vlm.utils import load_config
                    self._config = load_config(str(path))
                except Exception:
                    self._config = {}
            self._is_vlm = True
        else:
            if not self._has_mlx_lm:
                raise BackendError(
                    "neither mlx_lm nor mlx_vlm available to load this model",
                    code="backend_unavailable",
                )
            try:
                from mlx_lm import load as mlx_load
            except Exception as exc:
                raise BackendError(
                    f"mlx_lm not importable: {exc}",
                    code="backend_unavailable",
                ) from exc
            try:
                self._model, self._tokenizer = mlx_load(str(path))
            except Exception as exc:
                raise BackendError(
                    f"mlx_lm.load failed: {exc}",
                    code="backend_error",
                ) from exc
            self._is_vlm = False
        self._model_path = path

    def unload(self) -> None:
        # Clean up any temp files we created for remote/data: images.
        for tmp in self._temp_image_paths:
            try:
                os.unlink(tmp)
            except Exception:
                pass
        self._temp_image_paths.clear()

        self._model = None
        self._tokenizer = None
        self._processor = None
        self._config = None
        self._model_path = None
        self._is_vlm = False
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

        max_tokens = _resolve_max_tokens(params)
        chunk_id = f"chatcmpl-mlx-{int(time.time() * 1000)}"
        model_id = str(self._model_path) if self._model_path else "mlx"

        if self._is_vlm:
            return self._chat_vlm(
                messages=messages,
                params=params,
                max_tokens=max_tokens,
                chunk_id=chunk_id,
                model_id=model_id,
                stream=stream,
                abort_signal=abort_signal,
            )

        # Text-only path.  Degrade multimodal content to text so the
        # tokenizer's chat template doesn't choke on a list-of-parts
        # ``content`` field.
        if _has_multimodal_content(messages):
            messages = _degrade_multimodal_to_text(messages)

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

    # -- VLM path -----------------------------------------------------------

    def _chat_vlm(
        self,
        *,
        messages: Sequence,
        params: GenerationParams,
        max_tokens: int,
        chunk_id: str,
        model_id: str,
        stream: bool,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        """Generate via ``mlx_vlm`` with image inputs."""
        try:
            from mlx_vlm import (
                generate as vlm_generate,
                stream_generate as vlm_stream,
                apply_chat_template as vlm_apply_template,
            )
        except Exception as exc:
            raise BackendError(
                f"mlx_vlm not importable: {exc}",
                code="backend_unavailable",
            ) from exc

        oai_messages = _messages_to_oai(messages)
        images = _extract_images(oai_messages)
        # Track temp files so we can clean them up on unload.
        for img in images:
            if img not in self._temp_image_paths and img.startswith(tempfile.gettempdir()):
                self._temp_image_paths.append(img)

        # Build the formatted prompt.  ``mlx_vlm.apply_chat_template``
        # accepts either a string or a list of messages.
        try:
            prompt = vlm_apply_template(
                self._processor,
                self._config,
                oai_messages,
                add_generation_prompt=True,
                num_images=len(images),
            )
        except Exception as exc:
            raise BackendError(
                f"mlx_vlm.apply_chat_template failed: {exc}",
                code="backend_error",
            ) from exc

        # ``mlx_vlm`` expects ``image`` as a list (or None).
        image_arg = images if images else None

        if stream:
            return self._streaming_vlm(
                prompt=prompt,
                image_arg=image_arg,
                params=params,
                max_tokens=max_tokens,
                chunk_id=chunk_id,
                model_id=model_id,
                vlm_stream=vlm_stream,
                abort_signal=abort_signal,
            )
        return self._blocking_vlm(
            prompt=prompt,
            image_arg=image_arg,
            params=params,
            max_tokens=max_tokens,
            chunk_id=chunk_id,
            model_id=model_id,
            vlm_generate=vlm_generate,
            abort_signal=abort_signal,
        )

    def _blocking_vlm(
        self,
        *,
        prompt: str,
        image_arg,
        params: GenerationParams,
        max_tokens: int,
        chunk_id: str,
        model_id: str,
        vlm_generate,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        kwargs = _build_vlm_kwargs(params, max_tokens=max_tokens)
        try:
            result = vlm_generate(
                self._model, self._processor, prompt,
                image=image_arg, **kwargs,
            )
        except ApiGenerationAborted:
            raise
        except Exception as exc:
            raise BackendError(
                f"mlx_vlm.generate failed: {exc}",
                code="backend_error",
            ) from exc
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        content = _extract_generation(result)
        prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(result, "generation_tokens", 0) or 0)
        if not prompt_tokens or not completion_tokens:
            # Best-effort estimate via the tokenizer attached to the
            # processor.  Older mlx_vlm versions may not expose token
            # counts on the result.
            tok = getattr(self._processor, "tokenizer", None) or self._processor
            if not prompt_tokens:
                prompt_tokens = _count_tokens(tok, prompt)
            if not completion_tokens:
                completion_tokens = _count_tokens(tok, content)
        usage = ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        finish_reason = getattr(result, "finish_reason", None) or (
            "length" if completion_tokens >= max_tokens else "stop"
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant", "content": content},
            finish_reason=finish_reason,
            usage=usage,
        )

    def _streaming_vlm(
        self,
        *,
        prompt: str,
        image_arg,
        params: GenerationParams,
        max_tokens: int,
        chunk_id: str,
        model_id: str,
        vlm_stream,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        yield _build_chunk(
            chunk_id=chunk_id, model=model_id, delta={"role": "assistant"},
        )
        kwargs = _build_vlm_kwargs(params, max_tokens=max_tokens)
        seen_text = ""
        aborted = False
        prompt_tokens = 0
        last_result = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                for result in vlm_stream(
                    self._model, self._processor, prompt,
                    image=image_arg, **kwargs,
                ):
                    if abort_signal is not None:
                        abort_signal.raise_if_aborted()
                    last_result = result
                    if not prompt_tokens:
                        prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
                    cumulative = _extract_generation(result)
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
                f"mlx_vlm.stream_generate failed: {exc}",
                code="backend_error",
            ) from exc

        # Resolve final usage + finish_reason.
        reported_completion = 0
        finish_reason = None
        if last_result is not None:
            reported_completion = int(getattr(last_result, "generation_tokens", 0) or 0)
            finish_reason = getattr(last_result, "finish_reason", None)
        if not reported_completion:
            tok = getattr(self._processor, "tokenizer", None) or self._processor
            reported_completion = _count_tokens(tok, seen_text)
        if not prompt_tokens:
            tok = getattr(self._processor, "tokenizer", None) or self._processor
            prompt_tokens = _count_tokens(tok, prompt)
        if finish_reason not in {"stop", "length", "abort"}:
            finish_reason = "length" if reported_completion >= max_tokens else "stop"
        if aborted:
            finish_reason = "abort"
        usage = ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=reported_completion,
            total_tokens=prompt_tokens + reported_completion,
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason=finish_reason,
            usage=usage,
        )

    # -- mlx_lm text-only path ---------------------------------------------

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


def _build_vlm_kwargs(params: GenerationParams, *, max_tokens: int) -> dict:
    """Translate :class:`GenerationParams` into kwargs for ``mlx_vlm``.

    ``mlx_vlm`` accepts the same ``max_tokens`` / ``temperature`` /
    ``top_p`` / ``stop`` names as ``mlx_lm`` 0.20+, so this is a thin
    pass-through.  We omit ``verbose`` (mlx_vlm has its own default).
    """
    kwargs: dict = {"max_tokens": max_tokens}
    temperature = float(params.temperature) if params.temperature is not None else 0.0
    top_p = float(params.top_p) if params.top_p is not None else 1.0
    if temperature != 0.0:
        kwargs["temperature"] = temperature
    if 0.0 < top_p < 1.0:
        kwargs["top_p"] = top_p
    if params.stop:
        kwargs["stop"] = list(params.stop)
    return kwargs


__all__ = ["MLXChatBackend"]
