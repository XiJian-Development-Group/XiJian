"""OpenAI 兼容远程 STT（语音转文本）后端。

OpenAI-compatible remote STT backend.

调用 ``POST /audio/transcriptions`` (OpenAI Whisper API)。接受原始音频字节，
返回 OAI 风格的转录字典。

Calls ``POST /audio/transcriptions`` (OpenAI Whisper API).  Accepts raw
audio bytes and returns an OAI-style transcription dict.
"""

from __future__ import annotations

from pathlib import Path

from xijian_api.ai.backends.openai._client import (
    remote_stt,
    resolve_config,
)
from xijian_api.ai.base import (
    BackendError,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_stt
from xijian_api.ai.types import STTBackend


@register_stt("openai")
class OpenAISTTBackend(STTBackend):
    """OpenAI 兼容 STT 后端实现。OpenAI-compatible STT backend implementation."""
    name = "openai"

    def __init__(self) -> None:
        self._cfg = None

    def is_available(self) -> bool:
        return True

    def is_loaded(self) -> bool:
        return self._cfg is not None

    def load(self, model_path, **kwargs) -> None:
        section = kwargs.pop("_openai_section", None)
        cfg = resolve_config(kwargs, section=section, default_model="whisper-1")
        if not cfg.model_name:
            raise BackendError(
                "openai STT backend requires a model_name",
                code="backend_error",
            )
        self._cfg = cfg

    def unload(self) -> None:
        self._cfg = None

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str | None = None,
        prompt: str | None = None,
        response_format: str = "json",
    ):
        if not self.is_loaded() or self._cfg is None:
            raise ModelNotLoaded("no openai STT model loaded")
        return remote_stt(
            self._cfg,
            audio_bytes=audio,
            filename="audio.wav",
            language=language,
            prompt=prompt,
            response_format=response_format,
        )


__all__ = ["OpenAISTTBackend"]