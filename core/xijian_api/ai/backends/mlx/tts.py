"""MLX 文本转语音后端。

MLX text-to-speech backend.

包装可选的 ``mlx_audio`` 安装。MLX 不内置 TTS 模型；``mlx_audio``
是事实上的社区库，在 Apple Silicon 上实现 TTS 模型
（CosyVoice、Bark 等）。

当 ``mlx_audio`` 未安装时，此后端报告自身不可用，注册表透明地
回退到 GGUF（若 GGUF 也不存在则返回 503）。

Wraps an optional ``mlx_audio`` installation.  MLX doesn't ship a TTS
model in-tree; ``mlx_audio`` is the de-facto community library that
implements TTS models (CosyVoice, Bark, etc.) on Apple Silicon.

When ``mlx_audio`` is not installed this backend reports itself as
unavailable and the registry transparently falls back to GGUF (or
returns 503 if GGUF isn't either).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xijian_api.ai.base import (
    BackendError,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_tts
from xijian_api.ai.types import TTSBackend


def _probe() -> tuple[bool, Any]:
    """返回可选的 ``mlx_audio`` 库的 ``(available, generate_fn)``。

    Return ``(available, generate_fn)`` for the optional ``mlx_audio`` lib.
    """
    try:
        from mlx_audio import generate as mlx_audio_generate
    except Exception:
        return False, None
    return True, mlx_audio_generate


@register_tts("mlx")
class MLXTTSBackend(TTSBackend):
    """MLX 文本转语音后端。MLX text-to-speech backend."""
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
        # ``mlx_audio`` 通常在 ``generate`` 内部惰性加载模型。
        # 我们在此接受路径，让 ``synth`` 传递它；契约是"在
        # ``load()`` 之后后端知道使用哪个检查点"。
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
        # ``mlx_audio.generate`` 接受语音检查点目录。
        # 当提供 voice_clone_ref 时，我们将其作为 ``voice`` 转发；
        # 否则使用模型的默认语音。
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
    """将 ``mlx_audio.generate`` 返回的任何内容强制转换为原始 ``bytes``。

    ``mlx_audio`` 的不同版本返回不同的形状：
      * 原始 ``bytes``，
      * 带 ``"audio"``（类字节）+ ``"sampling_rate"`` 的字典，
      * 带 ``.audio`` 属性的数据类。

    Coerce whatever ``mlx_audio.generate`` returns into raw ``bytes``.

    Different versions of ``mlx_audio`` return different shapes:
      * raw ``bytes``,
      * a dict with ``"audio"`` (bytes-like) plus ``"sampling_rate"``,
      * a dataclass with an ``.audio`` attribute.
    """
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


__all__ = ["MLXTTSBackend"]