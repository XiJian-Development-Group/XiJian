"""MLX 语音转文本后端。

MLX speech-to-text backend.

包装可选的 ``mlx_audio``（或 ``mlx_whisper``）安装。
两者都在 Apple Silicon 上提供 Whisper 风格的转录。我们优先尝试
``mlx_audio``，因为它是更高级的 API，然后回退到 ``mlx_whisper``。

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
    """返回可选的 ``mlx_audio`` STT 的 ``(available, attribute)``。

    Return ``(available, attribute)`` for the optional ``mlx_audio`` STT.
    """
    try:
        import mlx_audio
    except Exception:
        return False, None
    # ``mlx_audio.stt.generate`` 在 0.2 中添加；旧版本只暴露 TTS。
    # 我们接受任一接口。
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
    """MLX 语音转文本后端。MLX speech-to-text backend."""
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

    # -- internals / 内部 ----------------------------------------------------------

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
        # ``mlx_whisper`` 已返回 OpenAI 风格字典。
        return result

    @staticmethod
    def _audio_input(audio: bytes):
        """将原始字节适配为底层库所需的任何输入类型。

        ``mlx_audio`` 和 ``mlx_whisper`` 接受文件路径或类文件对象。
        我们写入临时文件，以便两条路径都能工作，而无需自己将整个音频
        加载到 numpy 数组中。

        Adapt raw bytes to whatever input the underlying library wants.

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
    """将 STT 库的输出强制转换为 OAI ``transcriptions`` 形状。

    Coerce the STT library's output into the OAI ``transcriptions`` shape.
    """
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

    # 回退：尽力强制转换。
    return {"text": str(result)}


__all__ = ["MLXSTTBackend"]