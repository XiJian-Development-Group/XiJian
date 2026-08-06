"""带 MLX / GGUF / MeloTTS / 回退后端的 TTS 引擎抽象。

DevKit 默认保持轻量（不引入重型 ML 依赖）。当用户安装可选扩展时
启用真实 TTS：
    pip install "xijian-api[devkit-mlx]"     # Apple Silicon MLX 后端
    pip install "xijian-api[devkit-gguf]"    # GGUF / llama.cpp 后端
    pip install "xijian-api[devkit-melo]"    # MeloTTS 后端（v2.1 推荐）
"""

from __future__ import annotations

import abc
import math
import os
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.request import urlretrieve
import zipfile
import tarfile


@dataclass
class TTSRequest:
    text: str
    voice_id: Optional[str] = None
    language: str = "zh"
    speed: float = 1.0
    pitch: float = 1.0
    energy: float = 1.0
    output_path: Optional[str] = None
    params: Optional[dict[str, Any]] = None


@dataclass
class TTSResult:
    success: bool
    audio_path: Optional[str] = None
    duration_sec: float = 0.0
    error: Optional[str] = None
    engine: str = ""


class TTSEngine(abc.ABC):
    """抽象 TTS 引擎接口。"""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """引擎标识符（例如 'mlx'、'gguf'、'fallback'）。"""

    @abc.abstractmethod
    def is_available(self) -> bool:
        """如果该引擎能在当前环境中运行，返回 True。"""

    @abc.abstractmethod
    def synthesize(self, request: TTSRequest) -> TTSResult:
        """从文本生成语音。返回 WAV 文件路径。"""

    @abc.abstractmethod
    def list_voices(self) -> list[dict[str, Any]]:
        """返回该引擎可用的声音列表。"""


class FallbackTTSEngine(TTSEngine):
    """纯 Python 正弦波回退——始终可用。

    产生一个模拟语音节奏的简单调频音调。
    不是真正的语音，但可用于测试和作为最后手段。
    """

    @property
    def name(self) -> str:
        return "fallback"

    def is_available(self) -> bool:
        return True

    def list_voices(self) -> list[dict[str, Any]]:
        return [
            {"id": "fallback_zh_female", "name": "中文女声 (回退)", "language": "zh"},
            {"id": "fallback_zh_male", "name": "中文男声 (回退)", "language": "zh"},
            {"id": "fallback_en_female", "name": "English Female (fallback)", "language": "en"},
            {"id": "fallback_en_male", "name": "English Male (fallback)", "language": "en"},
        ]

    def synthesize(self, request: TTSRequest) -> TTSResult:
        out_path = request.output_path or self._default_path(request.voice_id)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        sample_rate = 22050
        duration = max(1.0, len(request.text) * 0.08)  # 粗略启发式
        n_samples = int(sample_rate * duration)

        voice = request.voice_id or "fallback_zh_female"
        base_freq = self._voice_base_freq(voice)

        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)

            # 简单的 FM 合成，振幅包络由文本长度塑造
            for i in range(n_samples):
                t = i / sample_rate
                # 载波频率由慢正弦调制（韵律）
                mod = math.sin(2 * math.pi * 0.5 * t) * 0.15
                freq = base_freq * (1.0 + mod)
                # 振幅包络：起音-保持-释音
                env = 1.0
                if t < 0.05:
                    env = t / 0.05
                elif t > duration - 0.1:
                    env = max(0.0, (duration - t) / 0.1)
                sample = int(16000 * env * math.sin(2 * math.pi * freq * t))
                wf.writeframes(sample.to_bytes(2, "little", signed=True))

        return TTSResult(
            success=True,
            audio_path=out_path,
            duration_sec=duration,
            engine=self.name,
        )

    def _voice_base_freq(self, voice_id: str) -> float:
        if "male" in voice_id:
            return 120.0
        if "en" in voice_id:
            return 180.0
        return 220.0

    def _default_path(self, voice_id: Optional[str]) -> str:
        from devkit._vendor import iso_now
        ts = iso_now().replace(":", "-")
        vid = voice_id or "fallback"
        return os.path.join(
            os.environ.get("XIJIAN_DEV_WORK_DIR", os.path.expanduser("~/Library/Application Support/XiJian/DevKit")),
            "tts_output",
            f"{vid}_{ts}.wav",
        )


class MlxTTSEngine(TTSEngine):
    """Apple Silicon 的 MLX 后端（通过 mlx-audio）。

    需要：pip install mlx-audio
    """

    @property
    def name(self) -> str:
        return "mlx"

    def __init__(self):
        self._mlx = None
        self._voices_cache: list[dict[str, Any]] = []

    def is_available(self) -> bool:
        if sys.platform != "darwin":
            return False
        try:
            import mlx_audio  # noqa: F401
            return True
        except ImportError:
            return False

    def _load_mlx(self):
        if self._mlx is None:
            from mlx_audio.tts import TTS as MlxTTS
            self._mlx = MlxTTS()

    def list_voices(self) -> list[dict[str, Any]]:
        if not self.is_available():
            return []
        if not self._voices_cache:
            try:
                self._load_mlx()
                # mlx-audio TTS 不暴露声音列表 API；我们返回已知模型
                self._voices_cache = [
                    {"id": "mlx_zh_female", "name": "中文女声 (MLX)", "language": "zh", "model": "zh_female"},
                    {"id": "mlx_en_female", "name": "English Female (MLX)", "language": "en", "model": "en_female"},
                ]
            except Exception:
                self._voices_cache = []
        return self._voices_cache

    def synthesize(self, request: TTSRequest) -> TTSResult:
        if not self.is_available():
            return TTSResult(success=False, error="MLX not available", engine=self.name)

        out_path = request.output_path or self._default_path(request.voice_id)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        try:
            self._load_mlx()
            voice = request.voice_id or "mlx_zh_female"
            model_name = self._voice_to_model(voice)
            # mlx-audio TTS API：tts(text, voice=..., output_path=...)
            self._mlx.tts(
                request.text,
                voice=model_name,
                output_path=out_path,
                speed=request.speed,
            )
            duration = self._wav_duration(out_path)
            return TTSResult(
                success=True,
                audio_path=out_path,
                duration_sec=duration,
                engine=self.name,
            )
        except Exception as e:
            return TTSResult(success=False, error=str(e), engine=self.name)

    def _voice_to_model(self, voice_id: str) -> str:
        if "en" in voice_id:
            return "en_female"
        return "zh_female"

    def _default_path(self, voice_id: Optional[str]) -> str:
        from devkit._vendor import iso_now
        ts = iso_now().replace(":", "-")
        vid = voice_id or "mlx"
        return os.path.join(
            os.environ.get("XIJIAN_DEV_WORK_DIR", os.path.expanduser("~/Library/Application Support/XiJian/DevKit")),
            "tts_output",
            f"{vid}_{ts}.wav",
        )

    def _wav_duration(self, path: str) -> float:
        try:
            with wave.open(path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return frames / float(rate)
        except Exception:
            return 0.0


class GgufTTSEngine(TTSEngine):
    """用于 TTS 的 GGUF / llama.cpp 后端。

    需要：pip install llama-cpp-python
    模型：任意 GGUF TTS 模型（例如 bark-gguf、piper-gguf、whisper.cpp TTS 变体）。
    """

    @property
    def name(self) -> str:
        return "gguf"

    def __init__(self):
        self._model_path: Optional[str] = None
        self._llama = None

    def is_available(self) -> bool:
        try:
            import llama_cpp  # noqa: F401
            return self._model_path is not None and os.path.isfile(self._model_path)
        except ImportError:
            return False

    def load_model(self, model_path: str) -> bool:
        """加载 GGUF TTS 模型。成功返回 True。"""
        if not os.path.isfile(model_path):
            return False
        try:
            from llama_cpp import Llama
            self._llama = Llama(model_path=model_path, n_ctx=2048, verbose=False)
            self._model_path = model_path
            return True
        except Exception:
            self._llama = None
            self._model_path = None
            return False

    def list_voices(self) -> list[dict[str, Any]]:
        if not self.is_available():
            return []
        return [
            {"id": "gguf_default", "name": "GGUF Default", "language": "zh"},
        ]

    def synthesize(self, request: TTSRequest) -> TTSResult:
        if not self.is_available():
            return TTSResult(success=False, error="GGUF model not loaded", engine=self.name)

        out_path = request.output_path or self._default_path(request.voice_id)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        try:
            # llama-cpp-python 没有内置 TTS API；我们通过提示模板将模型
            # 作为文本转语音生成器调用。这是占位实现——真正的集成
            # 取决于具体的 GGUF 模型。
            prompt = f"[TTS] {request.text}"
            output = self._llama(prompt, max_tokens=512, temperature=0.7)
            generated = output["choices"][0]["text"]
            # 上述只是文本生成。实际的 TTS GGUF 模型会返回需要声码器的
            # 音频 token。目前先回退。
            return TTSResult(success=False, error="GGUF TTS requires model-specific vocoder; not implemented", engine=self.name)
        except Exception as e:
            return TTSResult(success=False, error=str(e), engine=self.name)

    def _default_path(self, voice_id: Optional[str]) -> str:
        from devkit._vendor import iso_now
        ts = iso_now().replace(":", "-")
        vid = voice_id or "gguf"
        return os.path.join(
            os.environ.get("XIJIAN_DEV_WORK_DIR", os.path.expanduser("~/Library/Application Support/XiJian/DevKit")),
            "tts_output",
            f"{vid}_{ts}.wav",
        )


class MeloTTSEngine(TTSEngine):
    """MeloTTS 引擎——实现 v2.1 所需的对话 TTS MeloTTS。

    MeloTTS 是 MyShell 出品的高质量多语言 TTS 模型。
    该引擎从 Hugging Face 下载模型（支持镜像）并用于合成。

    模型：myshell-ai/MeloTTS-Chinese（或 myshell-ai/MeloTTS-English）
    """

    # Hugging Face 上的默认模型仓库
    MELO_MODELS = {
        "zh": "myshell-ai/MeloTTS-Chinese",
        "en": "myshell-ai/MeloTTS-English",
    }

    def __init__(self):
        self._model = None
        self._model_path: Optional[str] = None
        self._language = "zh"
        self._voices_cache: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "melo"

    def is_available(self) -> bool:
        """检查 MeloTTS 是否可用（模型已下载且依赖已安装）。"""
        try:
            import melo  # noqa: F401
            return self._model_path is not None and os.path.isdir(self._model_path)
        except ImportError:
            return False

    def _get_cache_dir(self) -> str:
        """获取 MeloTTS 模型的缓存目录。"""
        base = os.environ.get("XIJIAN_DEV_WORK_DIR", os.path.expanduser("~/Library/Application Support/XiJian/DevKit"))
        return os.path.join(base, "models", "melo")

    def _get_mirror_url(self, original_url: str) -> str:
        """如果配置了镜像，将 Hugging Face URL 转换为镜像地址。"""
        mirror = os.environ.get("HF_MIRROR", "https://hf-mirror.com")
        if "huggingface.co" in original_url:
            return original_url.replace("https://huggingface.co", mirror)
        return original_url

    def _download_model(self, language: str = "zh") -> bool:
        """从 Hugging Face 下载 MeloTTS 模型（支持镜像）。"""
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            return False

        model_repo = self.MELO_MODELS.get(language, self.MELO_MODELS["zh"])
        cache_dir = self._get_cache_dir()
        local_dir = os.path.join(cache_dir, model_repo.replace("/", "--"))

        if os.path.isdir(local_dir) and os.listdir(local_dir):
            self._model_path = local_dir
            self._language = language
            return True

        try:
            os.makedirs(cache_dir, exist_ok=True)
            # 如果配置了镜像则使用镜像
            original_repo = model_repo
            if os.environ.get("HF_MIRROR"):
                # 通过设置端点来使用镜像
                os.environ["HF_ENDPOINT"] = os.environ.get("HF_MIRROR", "https://hf-mirror.com")

            snapshot_download(
                repo_id=model_repo,
                local_dir=local_dir,
                local_dir_use_symlinks=False,
                resume_download=True,
            )
            self._model_path = local_dir
            self._language = language
            return True
        except Exception as e:
            print(f"Failed to download MeloTTS model: {e}")
            return False

    def ensure_model(self, language: str = "zh") -> bool:
        """确保模型已下载并准备就绪。成功返回 True。"""
        if self.is_available() and self._language == language:
            return True
        return self._download_model(language)

    def list_voices(self) -> list[dict[str, Any]]:
        if not self.is_available():
            return []
        if not self._voices_cache:
            # MeloTTS 通常有多个说话人
            self._voices_cache = [
                {"id": "melo_zh_female_0", "name": "中文女声 0 (MeloTTS)", "language": "zh", "speaker_id": 0},
                {"id": "melo_zh_female_1", "name": "中文女声 1 (MeloTTS)", "language": "zh", "speaker_id": 1},
                {"id": "melo_zh_male_0", "name": "中文男声 0 (MeloTTS)", "language": "zh", "speaker_id": 2},
                {"id": "melo_en_female_0", "name": "English Female 0 (MeloTTS)", "language": "en", "speaker_id": 0},
                {"id": "melo_en_male_0", "name": "English Male 0 (MeloTTS)", "language": "en", "speaker_id": 1},
            ]
        return self._voices_cache

    def synthesize(self, request: TTSRequest) -> TTSResult:
        if not self.is_available():
            # 尝试自动下载
            lang = request.language or "zh"
            if not self.ensure_model(lang):
                return TTSResult(success=False, error="MeloTTS model not available. Please download first.", engine=self.name)

        out_path = request.output_path or self._default_path(request.voice_id)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        try:
            from melo.api import TTS as MeloTTS

            # 如果尚未初始化则初始化 TTS
            if self._model is None:
                self._model = MeloTTS(language=self._language, device="auto")

            voice = request.voice_id or f"melo_{self._language}_female_0"
            # 如果可能，从 voice_id 提取 speaker_id
            speaker_id = 0
            if "speaker_id" in voice:
                try:
                    speaker_id = int(voice.split("_")[-1])
                except (ValueError, IndexError):
                    pass

            # 使用 MeloTTS 合成
            self._model.tts_to_file(
                request.text,
                speaker_id=speaker_id,
                output_path=out_path,
                speed=request.speed,
            )

            duration = self._wav_duration(out_path)
            return TTSResult(
                success=True,
                audio_path=out_path,
                duration_sec=duration,
                engine=self.name,
            )
        except Exception as e:
            return TTSResult(success=False, error=str(e), engine=self.name)

    def _voice_to_model(self, voice_id: str) -> str:
        """将 voice_id 映射到 MeloTTS 说话人。"""
        return voice_id

    def _default_path(self, voice_id: Optional[str]) -> str:
        from devkit._vendor import iso_now
        ts = iso_now().replace(":", "-")
        vid = voice_id or "melo"
        return os.path.join(
            os.environ.get("XIJIAN_DEV_WORK_DIR", os.path.expanduser("~/Library/Application Support/XiJian/DevKit")),
            "tts_output",
            f"{vid}_{ts}.wav",
        )

    def _wav_duration(self, path: str) -> float:
        try:
            with wave.open(path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return frames / float(rate)
        except Exception:
            return 0.0


class DiffSingerEngine(TTSEngine):
    """DiffSinger 引擎——实现 v2.1 所需的歌唱合成 DiffSinger。

    DiffSinger 是 OpenVPI 的歌唱语音合成系统。
    该引擎从 Hugging Face 下载模型（支持镜像）并用于歌唱合成。

    模型：openvpi/DiffSinger（或特定的声学/声码器模型）
    """

    # Hugging Face 上的默认模型仓库
    DIFFSINGER_MODELS = {
        "zh": "openvpi/DiffSinger-Chinese",
        "en": "openvpi/DiffSinger-English",
        "jp": "openvpi/DiffSinger-Japanese",
    }

    def __init__(self):
        self._model = None
        self._model_path: Optional[str] = None
        self._language = "zh"
        self._voices_cache: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "diffsinger"

    def is_available(self) -> bool:
        """检查 DiffSinger 是否可用（模型已下载且依赖已安装）。"""
        try:
            import diffsinger  # noqa: F401
            import torch  # noqa: F401
            return self._model_path is not None and os.path.isdir(self._model_path)
        except ImportError:
            return False

    def _get_cache_dir(self) -> str:
        """获取 DiffSinger 模型的缓存目录。"""
        base = os.environ.get("XIJIAN_DEV_WORK_DIR", os.path.expanduser("~/Library/Application Support/XiJian/DevKit"))
        return os.path.join(base, "models", "diffsinger")

    def _get_mirror_url(self, original_url: str) -> str:
        """如果配置了镜像，将 Hugging Face URL 转换为镜像地址。"""
        mirror = os.environ.get("HF_MIRROR", "https://hf-mirror.com")
        if "huggingface.co" in original_url:
            return original_url.replace("https://huggingface.co", mirror)
        return original_url

    def ensure_model(self, language: str = "zh") -> bool:
        """确保给定语言的 DiffSinger 模型已下载。

        模型就绪返回 True，否则返回 False。
        """
        if self.is_available() and self._language == language:
            return True

        self._language = language
        model_repo = self.DIFFSINGER_MODELS.get(language, self.DIFFSINGER_MODELS["zh"])
        cache_dir = self._get_cache_dir()
        model_dir = os.path.join(cache_dir, f"diffsinger_{language}")

        if os.path.isdir(model_dir) and os.listdir(model_dir):
            self._model_path = model_dir
            return True

        # 从 Hugging Face 下载
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            return False

        mirror = os.environ.get("HF_MIRROR", "https://hf-mirror.com")
        os.makedirs(cache_dir, exist_ok=True)

        try:
            snapshot_download(
                repo_id=model_repo,
                local_dir=model_dir,
                local_dir_use_symlinks=False,
                resume_download=True,
                endpoint=mirror,
            )
            self._model_path = model_dir
            return True
        except Exception:
            return False

    def list_voices(self) -> list[dict[str, Any]]:
        if not self.is_available():
            # 即使不可用也返回预期声音（供 UI 使用）
            return [
                {"id": f"diffsinger_{lang}_singer_{i}", "name": f"{lang.upper()} 歌手 {i+1}", "language": lang}
                for lang in ["zh", "en", "jp"]
                for i in range(3)
            ]
        if not self._voices_cache:
            self._voices_cache = [
                {"id": f"diffsinger_{self._language}_singer_{i}", "name": f"{self._language.upper()} 歌手 {i+1}", "language": self._language}
                for i in range(3)
            ]
        return self._voices_cache

    def synthesize(self, request: TTSRequest) -> TTSResult:
        """从歌词和旋律合成歌唱。

        对于 DiffSinger，请求应包含：
        - text：歌词（可带可选的音高/时长标注）
        - params：包含 'midi_path' 或 'melody'（{note, duration} 列表）的 dict
        """
        if not self.is_available():
            # 尝试自动下载
            lang = request.language or "zh"
            if not self.ensure_model(lang):
                return TTSResult(success=False, error="DiffSinger model not available. Please download first.", engine=self.name)

        out_path = request.output_path or self._default_path(request.voice_id)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        try:
            import torch
            from diffsinger.infer import DiffSingerInfer

            # 如果尚未初始化则初始化推理
            if self._model is None:
                self._model = DiffSingerInfer(self._model_path, device="auto")

            voice = request.voice_id or f"diffsinger_{self._language}_singer_0"

            # 从 params 获取旋律
            params = request.params or {}
            midi_path = params.get("midi_path")
            melody = params.get("melody")  # {note, duration} 列表

            if midi_path and os.path.isfile(midi_path):
                # 使用 MIDI 文件作为旋律
                self._model.sing_from_midi(
                    lyrics=request.text,
                    midi_path=midi_path,
                    speaker=voice,
                    output_path=out_path,
                )
            elif melody:
                # 使用程序化旋律
                self._model.sing(
                    lyrics=request.text,
                    melody=melody,
                    speaker=voice,
                    output_path=out_path,
                )
            else:
                return TTSResult(success=False, error="DiffSinger requires 'midi_path' or 'melody' in params", engine=self.name)

            duration = self._wav_duration(out_path)
            return TTSResult(
                success=True,
                audio_path=out_path,
                duration_sec=duration,
                engine=self.name,
            )
        except Exception as e:
            return TTSResult(success=False, error=str(e), engine=self.name)

    def _default_path(self, voice_id: Optional[str]) -> str:
        from devkit._vendor import iso_now
        ts = iso_now().replace(":", "-")
        vid = voice_id or "diffsinger"
        return os.path.join(
            os.environ.get("XIJIAN_DEV_WORK_DIR", os.path.expanduser("~/Library/Application Support/XiJian/DevKit")),
            "tts_output",
            f"{vid}_{ts}.wav",
        )

    def _wav_duration(self, path: str) -> float:
        try:
            with wave.open(path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return frames / float(rate)
        except Exception:
            return 0.0


class TTSManager:
    """选择最佳可用引擎的单例管理器。

    按 v2.1 规范的优先级：
    - 对话 TTS：MeloTTS > MLX > GGUF > 回退
    - 歌唱：DiffSinger > 回退
    """

    def __init__(self):
        # 对话 TTS 引擎（由 synthesize_text 使用）
        self._tts_engines: list[TTSEngine] = [
            MeloTTSEngine(),      # 优先级 1：v2.1 对话所需的引擎
            MlxTTSEngine(),       # 优先级 2：Apple Silicon 原生
            GgufTTSEngine(),      # 优先级 3：如果用户加载了 GGUF 模型
            FallbackTTSEngine(),  # 优先级 4：始终可用
        ]
        # 歌唱合成引擎（由 generate_singing 使用）
        self._singing_engines: list[TTSEngine] = [
            DiffSingerEngine(),   # 优先级 1：v2.1 歌唱所需的引擎
            FallbackTTSEngine(),  # 优先级 2：始终可用
        ]
        self._active: Optional[TTSEngine] = None

    def get_engine(self, preferred: Optional[str] = None) -> TTSEngine:
        if preferred:
            for eng in self._tts_engines:
                if eng.name == preferred and eng.is_available():
                    return eng
        for eng in self._tts_engines:
            if eng.is_available():
                return eng
        return FallbackTTSEngine()

    def list_all_voices(self) -> list[dict[str, Any]]:
        all_voices = []
        for eng in self._tts_engines:
            if eng.is_available():
                for v in eng.list_voices():
                    v = dict(v)
                    v["engine"] = eng.name
                    all_voices.append(v)
        return all_voices

    def synthesize(self, request: TTSRequest, engine: Optional[str] = None) -> TTSResult:
        eng = self.get_engine(engine)
        return eng.synthesize(request)

    def get_singing_engine(self, preferred: Optional[str] = None) -> TTSEngine:
        """获取最佳可用的歌唱合成引擎。"""
        if preferred:
            for eng in self._singing_engines:
                if eng.name == preferred and eng.is_available():
                    return eng
        for eng in self._singing_engines:
            if eng.is_available():
                return eng
        return FallbackTTSEngine()

    def generate_singing(
        self,
        lyrics: str,
        voice_id: Optional[str] = None,
        language: str = "zh",
        params: Optional[dict[str, Any]] = None,
        output_path: Optional[str] = None,
        engine: Optional[str] = None,
    ) -> TTSResult:
        """从歌词和旋律（MIDI 或程序化）生成歌唱。"""
        request = TTSRequest(
            text=lyrics,
            voice_id=voice_id,
            language=language,
            params=params,
            output_path=output_path,
        )
        eng = self.get_singing_engine(engine)
        return eng.synthesize(request)


# 模块级单例
_manager: Optional[TTSManager] = None


def get_tts_manager() -> TTSManager:
    global _manager
    if _manager is None:
        _manager = TTSManager()
    return _manager


def synthesize_text(
    text: str,
    voice_id: Optional[str] = None,
    language: str = "zh",
    speed: float = 1.0,
    output_path: Optional[str] = None,
    engine: Optional[str] = None,
) -> TTSResult:
    """一次性合成的便捷函数。"""
    request = TTSRequest(
        text=text,
        voice_id=voice_id,
        language=language,
        speed=speed,
        output_path=output_path,
    )
    return get_tts_manager().synthesize(request, engine=engine)


__all__ = [
    "TTSRequest",
    "TTSResult",
    "TTSEngine",
    "FallbackTTSEngine",
    "MlxTTSEngine",
    "GgufTTSEngine",
    "MeloTTSEngine",
    "DiffSingerEngine",
    "TTSManager",
    "get_tts_manager",
    "synthesize_text",
]