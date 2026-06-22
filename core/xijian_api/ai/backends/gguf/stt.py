"""GGUF speech-to-text backend.

Wraps ``pywhispercpp`` — the canonical binding for whisper.cpp's
GGUF models.  whisper.cpp is the de-facto standard for on-device
transcription; GGUF models are widely distributed on Hugging Face.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from xijian_api.ai.base import (
    BackendError,
    ModelNotFound,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_stt
from xijian_api.ai.types import STTBackend


def _probe() -> tuple[bool, str | None]:
    """Return ``(available, attribute)`` for ``pywhispercpp``."""
    try:
        import pywhispercpp
    except Exception:
        return False, None
    for attr in ("transcribe", "Whisper"):
        if hasattr(pywhispercpp, attr):
            return True, attr
    return False, None


@register_stt("gguf")
class GGUFSTTBackend(STTBackend):
    name = "gguf"

    def __init__(self) -> None:
        self._available, self._attr = _probe()
        self._model: object = None
        self._model_path: Path | None = None

    def is_available(self) -> bool:
        return self._available

    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self, model_path, **kwargs) -> None:
        if not self._available:
            raise BackendError(
                "pywhispercpp is not installed",
                code="backend_unavailable",
            )
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")
        # ``pywhispercpp`` accepts either a file path or a HF repo id.
        # When ``path`` is a directory (the usual case for whisper.cpp
        # GGUF models) we hand it directly; otherwise it's a single
        # ``.bin``/``.gguf`` file.
        model_ref = str(path)
        try:
            from pywhispercpp.model import Model
            self._model = Model(model_ref, print_progress=False)
        except Exception as exc:
            raise BackendError(
                f"pywhispercpp.Model init failed: {exc}",
                code="backend_error",
            ) from exc
        self._model_path = path

    def unload(self) -> None:
        self._model = None
        self._model_path = None

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str | None = None,
        prompt: str | None = None,
        response_format: str = "json",
    ):
        if not self.is_loaded():
            raise ModelNotLoaded("no GGUF STT model loaded")
        tmp_path = self._write_temp_audio(audio)
        try:
            try:
                segments = self._model.transcribe(
                    tmp_path,
                    language=language,
                    initial_prompt=prompt,
                )
            except Exception as exc:
                raise BackendError(
                    f"pywhispercpp transcription failed: {exc}",
                    code="backend_error",
                ) from exc
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass
        return _shape_response(segments, response_format=response_format)

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _write_temp_audio(audio: bytes) -> str:
        """Persist ``audio`` bytes to a temp WAV file for ``pywhispercpp``.

        whisper.cpp reads 16-bit mono PCM at 16 kHz; most clients send
        WAV blobs already.  When they don't we still write a WAV
        header — whisper.cpp's loader is permissive.
        """
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            tmp.write(audio)
            tmp.flush()
        finally:
            tmp.close()
        return tmp.name


def _shape_response(segments, *, response_format: str):
    """Coerce ``pywhispercpp`` segments into the OAI ``transcriptions`` shape."""
    if response_format == "text":
        if not segments:
            return ""
        return "".join(getattr(s, "text", "") for s in segments).strip()

    out_segments: list[dict] = []
    full_text_parts: list[str] = []
    detected_language: str | None = None
    for idx, seg in enumerate(segments):
        text = getattr(seg, "text", "")
        t0 = getattr(seg, "t0", None)
        t1 = getattr(seg, "t1", None)
        out_segments.append(
            {
                "id": idx,
                "start": (float(t0) / 100.0) if t0 is not None else 0.0,
                "end": (float(t1) / 100.0) if t1 is not None else 0.0,
                "text": text,
            }
        )
        full_text_parts.append(text)
        if detected_language is None:
            lang = getattr(seg, "language", None)
            if isinstance(lang, str):
                detected_language = lang

    return {
        "task": "transcribe",
        "language": detected_language,
        "text": "".join(full_text_parts).strip(),
        "segments": out_segments,
    }


__all__ = ["GGUFSTTBackend"]
