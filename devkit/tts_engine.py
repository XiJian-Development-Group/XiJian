"""TTS Engine abstraction with MLX / GGUF / MeloTTS / Fallback backends.

The DevKit stays lightweight by default (no heavy ML deps).  Real TTS is
enabled when the user installs optional extras:
    pip install "xijian-api[devkit-mlx]"     # Apple Silicon MLX backend
    pip install "xijian-api[devkit-gguf]"    # GGUF / llama.cpp backend
    pip install "xijian-api[devkit-melo]"    # MeloTTS backend (recommended for v2.1)
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
    """Abstract TTS engine interface."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Engine identifier (e.g. 'mlx', 'gguf', 'fallback')."""

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return True if this engine can run in the current environment."""

    @abc.abstractmethod
    def synthesize(self, request: TTSRequest) -> TTSResult:
        """Generate speech from text.  Returns path to WAV file."""

    @abc.abstractmethod
    def list_voices(self) -> list[dict[str, Any]]:
        """Return available voices for this engine."""


class FallbackTTSEngine(TTSEngine):
    """Pure-Python sine-wave fallback — always available.

    Produces a simple frequency-modulated tone that mimics speech cadence.
    Not real speech, but useful for testing and as a last resort.
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
        duration = max(1.0, len(request.text) * 0.08)  # rough heuristic
        n_samples = int(sample_rate * duration)

        voice = request.voice_id or "fallback_zh_female"
        base_freq = self._voice_base_freq(voice)

        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)

            # Simple FM synthesis with amplitude envelope shaped by text length
            for i in range(n_samples):
                t = i / sample_rate
                # Carrier frequency modulated by a slow sine (prosody)
                mod = math.sin(2 * math.pi * 0.5 * t) * 0.15
                freq = base_freq * (1.0 + mod)
                # Amplitude envelope: attack-sustain-release
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
    """Apple Silicon MLX backend via mlx-audio.

    Requires: pip install mlx-audio
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
                # mlx-audio TTS doesn't expose a voice list API; we return known models
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
            # mlx-audio TTS API: tts(text, voice=..., output_path=...)
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
    """GGUF / llama.cpp backend for TTS.

    Requires: pip install llama-cpp-python
    Model: any GGUF TTS model (e.g. bark-gguf, piper-gguf, whisper.cpp TTS variants).
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
        """Load a GGUF TTS model.  Returns True on success."""
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
            # llama-cpp-python doesn't have a built-in TTS API; we call the model
            # as a text-to-speech generator via a prompt template.  This is a
            # placeholder — real integration depends on the specific GGUF model.
            prompt = f"[TTS] {request.text}"
            output = self._llama(prompt, max_tokens=512, temperature=0.7)
            generated = output["choices"][0]["text"]
            # The above is just text generation.  Actual TTS GGUF models would
            # return audio tokens that need a vocoder.  For now, fall back.
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
    """MeloTTS Engine — implements the v2.1 required MeloTTS for dialogue TTS.

    MeloTTS is a high-quality multi-lingual TTS model from MyShell.
    This engine downloads the model from Hugging Face (with mirror support)
    and uses it for synthesis.

    Model: myshell-ai/MeloTTS-Chinese (or myshell-ai/MeloTTS-English)
    """

    # Default model repositories on Hugging Face
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
        """Check if MeloTTS is available (model downloaded and dependencies installed)."""
        try:
            import melo  # noqa: F401
            return self._model_path is not None and os.path.isdir(self._model_path)
        except ImportError:
            return False

    def _get_cache_dir(self) -> str:
        """Get the cache directory for MeloTTS models."""
        base = os.environ.get("XIJIAN_DEV_WORK_DIR", os.path.expanduser("~/Library/Application Support/XiJian/DevKit"))
        return os.path.join(base, "models", "melo")

    def _get_mirror_url(self, original_url: str) -> str:
        """Convert Hugging Face URL to mirror if configured."""
        mirror = os.environ.get("HF_MIRROR", "https://hf-mirror.com")
        if "huggingface.co" in original_url:
            return original_url.replace("https://huggingface.co", mirror)
        return original_url

    def _download_model(self, language: str = "zh") -> bool:
        """Download MeloTTS model from Hugging Face with mirror support."""
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
            # Use mirror if configured
            original_repo = model_repo
            if os.environ.get("HF_MIRROR"):
                # We'll use the mirror by setting the endpoint
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
        """Ensure the model is downloaded and ready. Returns True if successful."""
        if self.is_available() and self._language == language:
            return True
        return self._download_model(language)

    def list_voices(self) -> list[dict[str, Any]]:
        if not self.is_available():
            return []
        if not self._voices_cache:
            # MeloTTS typically has multiple speakers
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
            # Try to auto-download
            lang = request.language or "zh"
            if not self.ensure_model(lang):
                return TTSResult(success=False, error="MeloTTS model not available. Please download first.", engine=self.name)

        out_path = request.output_path or self._default_path(request.voice_id)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        try:
            from melo.api import TTS as MeloTTS

            # Initialize TTS if not done
            if self._model is None:
                self._model = MeloTTS(language=self._language, device="auto")

            voice = request.voice_id or f"melo_{self._language}_female_0"
            # Extract speaker_id from voice_id if possible
            speaker_id = 0
            if "speaker_id" in voice:
                try:
                    speaker_id = int(voice.split("_")[-1])
                except (ValueError, IndexError):
                    pass

            # Use MeloTTS to synthesize
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
        """Map voice_id to MeloTTS speaker."""
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
    """DiffSinger Engine — implements the v2.1 required DiffSinger for singing synthesis.

    DiffSinger is a singing voice synthesis system from OpenVPI.
    This engine downloads the model from Hugging Face (with mirror support)
    and uses it for singing synthesis.

    Model: openvpi/DiffSinger (or specific acoustic/vocoder models)
    """

    # Default model repositories on Hugging Face
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
        """Check if DiffSinger is available (model downloaded and dependencies installed)."""
        try:
            import diffsinger  # noqa: F401
            import torch  # noqa: F401
            return self._model_path is not None and os.path.isdir(self._model_path)
        except ImportError:
            return False

    def _get_cache_dir(self) -> str:
        """Get the cache directory for DiffSinger models."""
        base = os.environ.get("XIJIAN_DEV_WORK_DIR", os.path.expanduser("~/Library/Application Support/XiJian/DevKit"))
        return os.path.join(base, "models", "diffsinger")

    def _get_mirror_url(self, original_url: str) -> str:
        """Convert Hugging Face URL to mirror if configured."""
        mirror = os.environ.get("HF_MIRROR", "https://hf-mirror.com")
        if "huggingface.co" in original_url:
            return original_url.replace("https://huggingface.co", mirror)
        return original_url

    def ensure_model(self, language: str = "zh") -> bool:
        """Ensure the DiffSinger model for the given language is downloaded.

        Returns True if model is ready, False otherwise.
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

        # Download from Hugging Face
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
            # Return expected voices even if not available (for UI)
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
        """Synthesize singing from lyrics and melody.

        For DiffSinger, the request should contain:
        - text: lyrics (with optional pitch/duration annotations)
        - params: dict with 'midi_path' or 'melody' (list of {note, duration})
        """
        if not self.is_available():
            # Try to auto-download
            lang = request.language or "zh"
            if not self.ensure_model(lang):
                return TTSResult(success=False, error="DiffSinger model not available. Please download first.", engine=self.name)

        out_path = request.output_path or self._default_path(request.voice_id)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        try:
            import torch
            from diffsinger.infer import DiffSingerInfer

            # Initialize inference if not done
            if self._model is None:
                self._model = DiffSingerInfer(self._model_path, device="auto")

            voice = request.voice_id or f"diffsinger_{self._language}_singer_0"

            # Get melody from params
            params = request.params or {}
            midi_path = params.get("midi_path")
            melody = params.get("melody")  # List of {note, duration}

            if midi_path and os.path.isfile(midi_path):
                # Use MIDI file for melody
                self._model.sing_from_midi(
                    lyrics=request.text,
                    midi_path=midi_path,
                    speaker=voice,
                    output_path=out_path,
                )
            elif melody:
                # Use programmatic melody
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
    """Singleton manager that selects the best available engine.

    Priority order per v2.1 spec:
    - For dialogue TTS: MeloTTS > MLX > GGUF > Fallback
    - For singing: DiffSinger > Fallback
    """

    def __init__(self):
        # Dialogue TTS engines (used by synthesize_text)
        self._tts_engines: list[TTSEngine] = [
            MeloTTSEngine(),      # Priority 1: v2.1 required engine for dialogue
            MlxTTSEngine(),       # Priority 2: Apple Silicon native
            GgufTTSEngine(),      # Priority 3: If user loaded GGUF model
            FallbackTTSEngine(),  # Priority 4: Always available
        ]
        # Singing synthesis engines (used by generate_singing)
        self._singing_engines: list[TTSEngine] = [
            DiffSingerEngine(),   # Priority 1: v2.1 required engine for singing
            FallbackTTSEngine(),  # Priority 2: Always available
        ]
        self._active: Optional[TTSEngine] = None

    def get_engine(self, preferred: Optional[str] = None) -> TTSEngine:
        if preferred:
            for eng in self._engines:
                if eng.name == preferred and eng.is_available():
                    return eng
        for eng in self._engines:
            if eng.is_available():
                return eng
        return FallbackTTSEngine()

    def list_all_voices(self) -> list[dict[str, Any]]:
        all_voices = []
        for eng in self._engines:
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
        """Get the best available singing synthesis engine."""
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
        """Generate singing from lyrics and melody (MIDI or programmatic)."""
        request = TTSRequest(
            text=lyrics,
            voice_id=voice_id,
            language=language,
            params=params,
            output_path=output_path,
        )
        eng = self.get_singing_engine(engine)
        return eng.synthesize(request)


# Module-level singleton
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
    """Convenience function for one-shot synthesis."""
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