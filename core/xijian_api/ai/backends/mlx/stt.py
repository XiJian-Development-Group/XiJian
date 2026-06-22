"""MLX speech-to-text backend.

Wraps an optional ``mlx_audio`` (or ``mlx_whisper``) installation.
Both expose Whisper-style transcription on Apple Silicon.  We try
``mlx_audio`` first because it's the higher-level API, then fall back
to ``mlx_whisper``.
"""

from __future__ import annotations

from pathlib import Path

from xijian_api.ai.base import (
    BackendError,
    ModelNotFound,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_stt
from xijian_api.ai.types import STTBackend


def _probe_mlx_audio() -> tuple[bool, str | None]:
    """Return ``(available, attribute)`` for the optional ``mlx_audio`` STT."""
    try:
        import mlx_audio
    except Exception:
        return False, None
    # ``mlx_audio.stt.generate`` was added in 0.2; older releases only
    # exposed TTS.  We accept either.
    if hasattr(mlx_audio, "stt") and hasattr(mlx_audio.stt, "generate"):
        return True, "mlx_audio.stt.generate"
    if hasattr(mlx_audio, "transcribe"):
        return True, "mlx_audio.transcribe"
    return False, None


def _probe_mlx_whisper() -> bool:
    try:
        import mlx_whisper  # noqa: F401
        return True
    except Exception:
        return False


@register_stt("mlx")
class MLXSTTBackend(STTBackend):
    name = "mlx"

    def __init__(self) -> None:
        self._audio_available, self._audio_attr = _probe_mlx_audio()
        self._whisper_available = _probe_mlx_whisper()
        self._model_path: Path | None = None
        self._model_name: str = ""

    def is_available(self) -> bool:
        return self._audio_available or self._whisper_available

    def is_loaded(self) -> bool:
        return self._model_path is not None

    def load(self, model_path, **kwargs) -> None:
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")
        self._model_path = path
        self._model_name = path.name

    def unload(self) -> None:
        self._model_path = None
        self._model_name = ""

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str | None = None,
        prompt: str | None = None,
        response_format: str = "json",
    ):
        if not self.is_loaded():
            raise ModelNotLoaded("no MLX STT model loaded")
        if self._audio_available:
            result = self._transcribe_via_mlx_audio(audio, language=language, prompt=prompt)
        elif self._whisper_available:
            result = self._transcribe_via_mlx_whisper(audio, language=language, prompt=prompt)
        else:
            raise BackendError(
                "no MLX STT backend available (install mlx_audio or mlx_whisper)",
                code="backend_unavailable",
            )
        return _shape_response(result, response_format=response_format)

    # -- internals ----------------------------------------------------------

    def _transcribe_via_mlx_audio(self, audio: bytes, *, language, prompt) -> dict:
        import importlib

        parts = self._audio_attr.split(".")
        module = importlib.import_module(".".join(parts[:-1]))
        fn = getattr(module, parts[-1])
        try:
            return fn(
                self._audio_input(audio),
                path_or_hf_repo=str(self._model_path),
                language=language,
                initial_prompt=prompt,
            )
        except Exception as exc:
            raise BackendError(
                f"mlx_audio transcription failed: {exc}",
                code="backend_error",
            ) from exc

    def _transcribe_via_mlx_whisper(self, audio: bytes, *, language, prompt) -> dict:
        try:
            import mlx_whisper
        except Exception as exc:
            raise BackendError(
                f"mlx_whisper unavailable: {exc}",
                code="backend_unavailable",
            ) from exc
        try:
            result = mlx_whisper.transcribe(
                self._audio_input(audio),
                path_or_hf_repo=str(self._model_path),
                language=language,
                initial_prompt=prompt,
            )
        except Exception as exc:
            raise BackendError(
                f"mlx_whisper.transcribe failed: {exc}",
                code="backend_error",
            ) from exc
        # ``mlx_whisper`` already returns the OpenAI-style dict.
        return result

    @staticmethod
    def _audio_input(audio: bytes):
        """Adapt raw bytes to whatever input the underlying library wants.

        ``mlx_audio`` and ``mlx_whisper`` accept file paths or file-like
        objects.  We write to a temp file so both paths work without
        loading the entire audio into a numpy array ourselves.
        """
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            tmp.write(audio)
            tmp.flush()
        finally:
            tmp.close()
        return tmp.name


def _shape_response(result, *, response_format: str):
    """Coerce the STT library's output into the OAI ``transcriptions`` shape."""
    if response_format == "text":
        if isinstance(result, dict):
            return result.get("text", "")
        return str(result)

    if isinstance(result, dict):
        text = result.get("text", "")
        language = result.get("language")
        segments = result.get("segments") or []
        out: dict = {
            "task": "transcribe",
            "language": language,
            "duration": result.get("duration"),
            "text": text,
            "segments": [
                {
                    "id": seg.get("id", idx),
                    "start": seg.get("start", 0.0),
                    "end": seg.get("end", 0.0),
                    "text": seg.get("text", ""),
                }
                for idx, seg in enumerate(segments)
                if isinstance(seg, dict)
            ],
        }
        return out

    # Fallback: best-effort coercion.
    return {"text": str(result)}


__all__ = ["MLXSTTBackend"]
