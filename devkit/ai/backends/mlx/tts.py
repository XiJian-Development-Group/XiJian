"""MLX text-to-speech backend for DevKit — adapted from core/xijian_api/ai/backends/mlx/tts.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from devkit.ai.base import (
    BackendError,
    ModelNotLoaded,
)
from devkit.ai.registry import register_tts
from devkit.ai.types import TTSBackend


def _probe() -> tuple[bool, Any]:
    """Return ``(available, generate_fn)`` for the optional ``mlx_audio`` lib."""
    try:
        from mlx_audio import generate as mlx_audio_generate
    except Exception:
        return False, None
    return True, mlx_audio_generate


@register_tts("mlx")
class MLXTTSBackend:
    name = "mlx"

    def __init__(self) -> None:
        self._available, self._generate_fn = _probe()
        self._model = None
        self._model_path: Path | None = None

    def is_available(self) -> bool:
        return self._available

    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self, model_path, **kwargs) -> None:
        if not self._available:
            raise BackendError(
                "mlx_audio is not installed; install it to enable MLX TTS",
                code="backend_unavailable",
            )
        path = Path(model_path)
        if not path.exists():
            raise BackendError(
                f"model path does not exist: {path}",
                code="model_not_found",
            )
        # ``mlx_audio`` typically lazy-loads the model inside ``generate``.
        self._model = str(path)
        self._model_path = path

    def unload(self) -> None:
        self._model = None
        self._model_path = None
        try:
            import mlx.core as mx
            mx.metal.clear_cache()
        except Exception:
            pass

    def synth(
        self,
        text: str,
        *,
        voice: str = "default",
        response_format: str = "mp3",
        speed: float = 1.0,
        emotion: str | None = None,
        voice_clone_ref: str | None = None,
        abort_signal=None,
    ) -> bytes:
        if not self.is_loaded():
            raise ModelNotLoaded("no MLX TTS model loaded")
        if self._generate_fn is None:
            raise BackendError(
                "mlx_audio.generate is unavailable",
                code="backend_unavailable",
            )
        chosen_voice = voice_clone_ref or voice
        kwargs: dict[str, Any] = {
            "text": text,
            "model_path": self._model,
            "voice": chosen_voice,
            "speed": float(speed) if speed else 1.0,
            "response_format": response_format,
        }
        if emotion:
            kwargs["emotion"] = emotion
        try:
            result = self._generate_fn(**kwargs)
        except Exception as exc:
            raise BackendError(
                f"mlx_audio.generate failed: {exc}",
                code="backend_error",
            ) from exc
        return _extract_audio_bytes(result, response_format=response_format)


def _extract_audio_bytes(result, *, response_format: str) -> bytes:
    """Coerce whatever ``mlx_audio.generate`` returns into raw ``bytes``."""
    if isinstance(result, (bytes, bytearray)):
        return bytes(result)
    if isinstance(result, dict):
        audio = result.get("audio") or result.get("bytes")
        if isinstance(audio, (bytes, bytearray)):
            return bytes(audio)
        if isinstance(audio, str):
            import base64
            return base64.b64decode(audio)
    audio = getattr(result, "audio", None)
    if isinstance(audio, (bytes, bytearray)):
        return bytes(audio)
    raise BackendError(
        f"unsupported mlx_audio output: {type(result).__name__}",
        code="backend_error",
    )