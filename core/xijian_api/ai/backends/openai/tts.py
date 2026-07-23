"""OpenAI-compatible remote TTS backend.

Calls ``POST /audio/speech`` (OpenAI TTS API).  Returns raw audio bytes
in the requested format.
"""

from __future__ import annotations

from pathlib import Path

from xijian_api.ai.backends.openai._client import (
    remote_tts,
    resolve_config,
)
from xijian_api.ai.base import (
    BackendError,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_tts
from xijian_api.ai.types import TTSBackend


@register_tts("openai")
class OpenAITTSBackend(TTSBackend):
    name = "openai"

    def __init__(self) -> None:
        self._cfg = None

    def is_available(self) -> bool:
        return True

    def is_loaded(self) -> bool:
        return self._cfg is not None

    def load(self, model_path, **kwargs) -> None:
        section = kwargs.pop("_openai_section", None)
        cfg = resolve_config(kwargs, section=section, default_model="tts-1")
        if not cfg.model_name:
            raise BackendError(
                "openai TTS backend requires a model_name",
                code="backend_error",
            )
        self._cfg = cfg

    def unload(self) -> None:
        self._cfg = None

    def synth(
        self,
        text: str,
        *,
        voice: str = "alloy",
        response_format: str = "mp3",
        speed: float = 1.0,
        emotion: str | None = None,
        voice_clone_ref: str | None = None,
        abort_signal=None,
    ) -> bytes:
        if not self.is_loaded() or self._cfg is None:
            raise ModelNotLoaded("no openai TTS model loaded")
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        # ``voice_clone_ref`` and ``emotion`` are not part of the
        # standard OpenAI TTS API; we ignore them gracefully (some
        # compatible providers accept them as extra fields, but the
        # canonical endpoint does not).
        return remote_tts(
            self._cfg,
            text=text,
            voice=voice,
            response_format=response_format,
            speed=float(speed) if speed else 1.0,
        )


__all__ = ["OpenAITTSBackend"]
