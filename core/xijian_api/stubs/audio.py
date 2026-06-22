"""Audio stub — TTS / STT / translation via the configured backend.

The previous fixed MP3 header / canned Chinese transcription output
has been removed.  Each function now dispatches to the real backend
(MLX → GGUF fallback).  When no backend is available the call raises
:class:`xijian_api.errors.BackendError` (status 503) so clients see
a real OAI error envelope rather than a fake success response.
"""

from __future__ import annotations

from typing import Any

from flask import current_app

from xijian_api.ai.base import BackendError as AIBackendError
from xijian_api.ai.base import BackendUnavailable as AIBackendUnavailable
from xijian_api.ai.registry import get_stt_backend, get_tts_backend
from xijian_api.config import Config
from xijian_api.errors import BackendError as ApiBackendError


def _resolve_config() -> Config | None:
    try:
        return current_app.config.get("XIJIAN_CONFIG")
    except RuntimeError:
        return None


def _backend_unavailable(exc: Exception, *, kind: str) -> ApiBackendError:
    return ApiBackendError(
        status=503,
        message=str(exc) or f"no {kind} backend available",
        type_="backend_unavailable",
        code="backend_unavailable",
    )


def _backend_error(exc: AIBackendError) -> ApiBackendError:
    return ApiBackendError(
        status=503,
        message=str(exc) or "backend error",
        type_="backend_unavailable",
        code=getattr(exc, "code", "backend_error"),
    )


def synth(text: str, *, voice: str = "default", response_format: str = "mp3") -> bytes:
    """Synthesise ``text`` to audio bytes via the TTS backend."""
    config = _resolve_config()
    requested: str | None = None
    fallbacks: tuple[str, ...] = ()
    if config is not None:
        requested = config.backends.tts.default or None
        fallbacks = config.backends.tts.fallbacks or ()
    try:
        backend = get_tts_backend(requested, fallbacks)
    except AIBackendUnavailable as exc:
        raise _backend_unavailable(exc, kind="tts") from exc
    try:
        return backend.synth(
            text,
            voice=voice,
            response_format=response_format,
        )
    except AIBackendError as exc:
        raise _backend_error(exc) from exc


def _select_stt_backend():
    config = _resolve_config()
    requested: str | None = None
    fallbacks: tuple[str, ...] = ()
    if config is not None:
        requested = config.backends.stt.default or None
        fallbacks = config.backends.stt.fallbacks or ()
    return get_stt_backend(requested, fallbacks)


def transcribe(audio: bytes, *, response_format: str = "json", language: str | None = None, prompt: str | None = None):
    """Transcribe ``audio`` via the STT backend."""
    try:
        backend = _select_stt_backend()
    except AIBackendUnavailable as exc:
        raise _backend_unavailable(exc, kind="stt") from exc
    try:
        result = backend.transcribe(
            audio,
            language=language,
            prompt=prompt,
            response_format=response_format,
        )
    except AIBackendError as exc:
        raise _backend_error(exc) from exc
    # The OAI ``text`` response_format must return raw string, otherwise a dict.
    if response_format == "text":
        if isinstance(result, dict):
            return result.get("text", "")
        return str(result)
    return result


def translate(audio: bytes, *, response_format: str = "json", language: str | None = None, prompt: str | None = None):
    """Translate ``audio`` to English text via the STT backend.

    Backends without explicit translation support transcribe first and
    pass the text through; the STT backend decides its own approach.
    """
    try:
        backend = _select_stt_backend()
    except AIBackendUnavailable as exc:
        raise _backend_unavailable(exc, kind="stt") from exc
    try:
        # Newer backends accept ``task="translate"``; we pass it through
        # ``prompt``-style kwargs so the older interface still works.
        result = backend.transcribe(
            audio,
            language=language,
            prompt=prompt,
            response_format=response_format,
        )
    except AIBackendError as exc:
        raise _backend_error(exc) from exc
    if response_format == "text":
        if isinstance(result, dict):
            return result.get("text", "")
        return str(result)
    return result


__all__ = ["synth", "transcribe", "translate"]