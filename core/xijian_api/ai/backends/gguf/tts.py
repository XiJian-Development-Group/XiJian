"""GGUF text-to-speech backend.

There is no single canonical GGUF TTS library.  We probe a small
list of known bindings and surface whichever one is installed:

* ``piper`` — Piper.cpp bindings (``piper-tts`` package).
* ``TTS`` — Coqui TTS (supports GGUF voices in recent versions).

When none of them is present this backend reports itself as
unavailable and the registry falls back (or returns 503 if nothing
else can serve TTS).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xijian_api.ai.base import (
    BackendError,
    ModelNotFound,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_tts
from xijian_api.ai.types import TTSBackend


# (module_name, attribute_path) pairs in preference order.  The first
# importable one wins.  Adding a new binding here is enough to enable
# a new backend without touching the rest of the class.
_CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("piper", ("PiperVoice",)),
    ("piper", ("voice", "PiperVoice")),
    ("TTS", ("api", "TTS")),
)


def _probe() -> tuple[bool, tuple[str, ...] | None]:
    for module_name, attr_path in _CANDIDATES:
        try:
            module = __import__(module_name, fromlist=["*"])
        except Exception:
            continue
        obj: Any = module
        try:
            for attr in attr_path:
                obj = getattr(obj, attr)
            return True, (module_name, *attr_path)
        except AttributeError:
            continue
    return False, None


@register_tts("gguf")
class GGUFTTSBackend(TTSBackend):
    name = "gguf"

    def __init__(self) -> None:
        self._available, self._attr_path = _probe()
        self._voice: Any = None
        self._model_path: Path | None = None

    def is_available(self) -> bool:
        return self._available

    def is_loaded(self) -> bool:
        return self._voice is not None

    def load(self, model_path, **kwargs) -> None:
        if not self._available or self._attr_path is None:
            raise BackendError(
                "no GGUF TTS library installed (tried piper, TTS)",
                code="backend_unavailable",
            )
        path = Path(model_path)
        # Piper expects a ``.onnx`` (or ``.gguf``) checkpoint plus a
        # ``.onnx.json`` config file.  We accept either — operators
        # point at the checkpoint and we look for the sibling config.
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")
        self._model_path = path
        try:
            self._voice = self._build_voice(path, **kwargs)
        except Exception as exc:
            raise BackendError(
                f"failed to construct TTS voice: {exc}",
                code="backend_error",
            ) from exc

    def unload(self) -> None:
        self._voice = None
        self._model_path = None

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
            raise ModelNotLoaded("no GGUF TTS model loaded")
        try:
            return self._synth(
                text=text,
                voice_name=voice,
                response_format=response_format,
                speed=speed,
                emotion=emotion,
                voice_clone_ref=voice_clone_ref,
            )
        except Exception as exc:
            raise BackendError(
                f"GGUF TTS synth failed: {exc}",
                code="backend_error",
            ) from exc

    # -- internals ----------------------------------------------------------

    def _build_voice(self, path: Path, **kwargs) -> Any:
        """Construct a voice object using the discovered binding."""
        assert self._attr_path is not None
        import importlib

        module_name, *attrs = self._attr_path
        module = importlib.import_module(module_name)
        cls = module
        for attr in attrs:
            cls = getattr(cls, attr)
        # Piper: ``PiperVoice.load(ckpt_path, config_path=...)``.
        # We look for the sibling ``.json`` first; fall back to
        # letting the binding auto-discover it.
        if module_name == "piper":
            config_path = path.with_suffix(".onnx.json")
            if not config_path.exists():
                config_path = path.with_suffix(".json")
            try:
                return cls.load(str(path), config_path=str(config_path) if config_path.exists() else None)
            except TypeError:
                return cls.load(str(path))
        # Coqui TTS: ``TTS(...).tts_to_file()`` style — we wrap the
        # whole model instance as the "voice" for parity.
        if module_name == "TTS":
            return cls(model_path=str(path), progress_bar=False, gpu=False)
        # Generic: try a positional ctor.
        return cls(str(path))

    def _synth(
        self,
        *,
        text: str,
        voice_name: str,
        response_format: str,
        speed: float,
        emotion: str | None,
        voice_clone_ref: str | None,
    ) -> bytes:
        assert self._attr_path is not None
        module_name = self._attr_path[0]

        if module_name == "piper":
            return self._synth_piper(
                text=text,
                speed=speed,
            )
        if module_name == "TTS":
            return self._synth_coqui(
                text=text,
                voice_name=voice_name,
                response_format=response_format,
                speed=speed,
                emotion=emotion,
            )
        raise BackendError(
            f"unsupported GGUF TTS binding: {module_name}",
            code="backend_error",
        )

    def _synth_piper(self, *, text: str, speed: float) -> bytes:
        """Synth via Piper, returning WAV bytes that we transcode if asked."""
        import io
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            # Piper's ``synthesize`` writes directly to a wave_write
            # object — the easiest cross-version path.
            self._voice.synthesize(text, wf, length_scale=1.0 / max(0.1, float(speed)))
        wav_bytes = buf.getvalue()
        return _maybe_transcode(wav_bytes, response_format="wav")

    def _synth_coqui(
        self,
        *,
        text: str,
        voice_name: str,
        response_format: str,
        speed: float,
        emotion: str | None,
    ) -> bytes:
        import tempfile
        import os

        suffix = _ext_for_format(response_format)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        try:
            kwargs: dict[str, Any] = {"text": text, "file_path": tmp_path}
            if speed:
                kwargs["speed"] = float(speed)
            if emotion:
                kwargs["emotion"] = emotion
            self._voice.tts_to_file(**kwargs)
            with open(tmp_path, "rb") as fp:
                return fp.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _ext_for_format(fmt: str) -> str:
    fmt = (fmt or "mp3").lower()
    return { "wav": ".wav", "ogg": ".ogg", "opus": ".opus", "flac": ".flac", "pcm": ".pcm" }.get(fmt, ".mp3")


def _maybe_transcode(wav_bytes: bytes, *, response_format: str) -> bytes:
    """Transcode WAV → ``response_format`` when the format isn't WAV."""
    fmt = (response_format or "wav").lower()
    if fmt in {"wav", "pcm"}:
        return wav_bytes
    try:
        import pydub
    except Exception:
        # Without ``pydub`` we can't transcode — return WAV and let the
        # caller log a warning.  mp3 playback will still work for many
        # clients (browsers usually decode WAV natively).
        return wav_bytes
    from io import BytesIO

    segment = pydub.AudioSegment.from_wav(BytesIO(wav_bytes))
    buf = BytesIO()
    fmt = "mp3" if fmt == "mp3" else fmt
    segment.export(buf, format=fmt)
    return buf.getvalue()


__all__ = ["GGUFTTSBackend"]
