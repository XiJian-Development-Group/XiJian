"""GGUF 文本转语音后端。

GGUF text-to-speech backend.

没有单一的规范 GGUF TTS 库。我们探测一小批已知绑定并使用已安装的那个：

* ``piper`` — Piper.cpp 绑定（``piper-tts`` 包）。
* ``TTS`` — Coqui TTS（近期版本支持 GGUF 语音）。

当都不存在时，此后端报告自身不可用，注册表回退（或若没有其他 TTS
可服务则返回 503）。

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


# 按偏好顺序的 (module_name, attribute_path) 对。
# 第一个可导入的胜出。在此添加新绑定就足以启用新后端，
# 无需修改类的其余部分。
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
    """GGUF 文本转语音后端。GGUF text-to-speech backend."""
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
        # Piper 期望 ``.onnx``（或 ``.gguf``）检查点加上
        # ``.onnx.json`` 配置文件。我们接受任一 —— 运维者指向
        # 检查点，我们查找同级配置文件。
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

    # -- internals / 内部 ----------------------------------------------------------

    def _build_voice(self, path: Path, **kwargs) -> Any:
        """使用发现的绑定构造语音对象。

        Construct a voice object using the discovered binding.
        """
        assert self._attr_path is not None
        import importlib

        module_name, *attrs = self._attr_path
        module = importlib.import_module(module_name)
        cls = module
        for attr in attrs:
            cls = getattr(cls, attr)
        # Piper: ``PiperVoice.load(ckpt_path, config_path=...)``。
        # 先查找同级 ``.json``；回退到让绑定自动发现。
        if module_name == "piper":
            config_path = path.with_suffix(".onnx.json")
            if not config_path.exists():
                config_path = path.with_suffix(".json")
            try:
                return cls.load(str(path), config_path=str(config_path) if config_path.exists() else None)
            except TypeError:
                return cls.load(str(path))
        # Coqui TTS: ``TTS(...).tts_to_file()`` 风格 —— 为保持
        # 一致性将整个模型实例包装为"voice"。
        if module_name == "TTS":
            return cls(model_path=str(path), progress_bar=False, gpu=False)
        # 通用：尝试位置构造函数。
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
        """通过 Piper 合成，返回 WAV 字节，若请求则转码。

        Synth via Piper, returning WAV bytes that we transcode if asked.
        """
        import io
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            # Piper 的 ``synthesize`` 直接写入 wave_write 对象 ——
            # 这是跨版本最简单的方式。
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
    """当请求格式不是 WAV 时将 WAV 转码为 ``response_format``。

    Transcode WAV → ``response_format`` when the format isn't WAV.
    """
    fmt = (response_format or "wav").lower()
    if fmt in {"wav", "pcm"}:
        return wav_bytes
    try:
        import pydub
    except Exception:
        # 没有 ``pydub`` 无法转码 —— 返回 WAV 让调用者记录警告。
        # 对许多客户端 mp3 播放仍然有效（浏览器通常原生解码 WAV）。
        return wav_bytes
    from io import BytesIO

    segment = pydub.AudioSegment.from_wav(BytesIO(wav_bytes))
    buf = BytesIO()
    fmt = "mp3" if fmt == "mp3" else fmt
    segment.export(buf, format=fmt)
    return buf.getvalue()


__all__ = ["GGUFTTSBackend"]