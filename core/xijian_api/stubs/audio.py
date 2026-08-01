"""Audio stub — TTS / STT / translation via the configured backend.
音频存根 — 通过配置的后端进行 TTS / STT / 翻译。

The previous fixed MP3 header / canned Chinese transcription output
has been removed.  Each function now dispatches to the real backend
(MLX → GGUF fallback).  When no backend is available the call raises
:class:`xijian_api.errors.BackendError` (status 503) so clients see
a real OAI error envelope rather than a fake success response.

之前的固定 MP3 头部 / 固定中文转录输出已移除。每个函数现在调度到真实后端
(MLX → GGUF 回退)。当没有后端可用时，调用会抛出
:class:`xijian_api.errors.BackendError` (状态码 503)，以便客户端看到真实的 OAI 错误信封，
而不是虚假的成功响应。
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
    """Resolve the XiJian config from Flask's current_app. 从 Flask 的 current_app 解析 XiJian 配置。"""
    try:
        return current_app.config.get("XIJIAN_CONFIG")
    except RuntimeError:
        return None


def _backend_unavailable(exc: Exception, *, kind: str) -> ApiBackendError:
    """Wrap backend-unavailable exception into API backend error. 将后端不可用异常包装为 API 后端错误。"""
    return ApiBackendError(
        status=503,
        message=str(exc) or f"no {kind} backend available",
        type_="backend_unavailable",
        code="backend_unavailable",
    )


def _backend_error(exc: AIBackendError) -> ApiBackendError:
    """Wrap generic backend error into API backend error. 将通用后端错误包装为 API 后端错误。"""
    return ApiBackendError(
        status=503,
        message=str(exc) or "backend error",
        type_="backend_unavailable",
        code=getattr(exc, "code", "backend_error"),
    )


def synth(
    text: str,
    *,
    voice: str = "default",
    response_format: str = "mp3",
    emotion: str | None = None,
) -> bytes:
    """Synthesise ``text`` to audio bytes via the TTS backend.
    通过 TTS 后端将 ``text`` 合成为音频字节。

    ``emotion`` allows specifying an emotion/tone for the speech
    (e.g. "happy", "sad", "angry", "cheerful", "calm").  Not all
    backends support this; unsupported backends will ignore it.
    ``emotion`` 允许指定语音的情感/语调
    (如 "happy", "sad", "angry", "cheerful", "calm")。
    并非所有后端都支持此参数，不支持的后端会忽略它。
    """
    config = _resolve_config()
    requested: str | None = None
    fallbacks: tuple[str, ...] = ()
    if config is not None:
        requested = config.backends.tts.default or None
        fallbacks = config.backends.tts.fallbacks or ()
    # A5.4 cross-link: when the overload guard has degraded TTS, note
    # it on the synthesis path so operators can correlate degraded
    # audio with overload events.  The actual voice-quality switch is
    # a backend concern; the flag is the authoritative signal.
    try:
        from xijian_api.stubs import tts_guard
        if tts_guard.is_degraded():
            current_app.logger.warning(
                "TTS synthesis while degraded (overload): %r",
                tts_guard.degradation(),
            )
    except Exception:  # noqa: BLE001 — guard must never break synthesis
        pass
    try:
        backend = get_tts_backend(requested, fallbacks)
    except AIBackendUnavailable as exc:
        raise _backend_unavailable(exc, kind="tts") from exc
    try:
        return backend.synth(
            text,
            voice=voice,
            response_format=response_format,
            emotion=emotion,
        )
    except AIBackendError as exc:
        raise _backend_error(exc) from exc


def _select_stt_backend():
    """Select the STT backend per config. 按配置选择 STT 后端。"""
    config = _resolve_config()
    requested: str | None = None
    fallbacks: tuple[str, ...] = ()
    if config is not None:
        requested = config.backends.stt.default or None
        fallbacks = config.backends.stt.fallbacks or ()
    return get_stt_backend(requested, fallbacks)


def transcribe(audio: bytes, *, response_format: str = "json", language: str | None = None, prompt: str | None = None):
    """Transcribe ``audio`` via the STT backend. 通过 STT 后端转录 ``audio``。"""
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
    # OAI 的 ``text`` response_format 必须返回原始字符串，否则返回字典。
    if response_format == "text":
        if isinstance(result, dict):
            return result.get("text", "")
        return str(result)
    return result


def translate(audio: bytes, *, response_format: str = "json", language: str | None = None, prompt: str | None = None):
    """Translate ``audio`` to English text via the STT backend.
    通过 STT 后端将 ``audio`` 翻译为英文文本。

    Backends without explicit translation support transcribe first and
    pass the text through; the STT backend decides its own approach.
    无显式翻译支持的后端会先转录再传递文本；STT 后端自行决定方法。
    """
    try:
        backend = _select_stt_backend()
    except AIBackendUnavailable as exc:
        raise _backend_unavailable(exc, kind="stt") from exc
    try:
        # Newer backends accept ``task="translate"``; we pass it through
        # ``prompt``-style kwargs so the older interface still works.
        # 较新的后端接受 ``task="translate"``；我们通过 ``prompt`` 风格的 kwargs 传递，
        # 以便旧接口仍能工作。
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