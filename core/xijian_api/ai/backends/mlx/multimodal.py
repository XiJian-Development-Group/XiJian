"""MLX 全模态理解后端（组合式实现）。

通过组合现有 MLX 后端能力实现全模态理解：

- 文本 + 图像 → 通过 ``mlx_vlm``（视觉语言模型）
- 音频 → 通过 ``mlx_audio`` / ``mlx_whisper`` STT 转录为文本
- 视频 → 通过 ffmpeg 提取帧，以图像序列通过 ``mlx_vlm`` 理解

这是一个"组合式"多模态后端——不依赖单个模型理解所有模态，
而是通过编排现有单模态模型协同工作。

MLX multimodal understanding backend (composite implementation).

Implements full multimodal understanding by composing existing MLX
backend capabilities:

- Text + image → via ``mlx_vlm`` (vision-language model)
- Audio → via ``mlx_audio`` / ``mlx_whisper`` STT transcription to text
- Video → via ffmpeg frame extraction + ``mlx_vlm`` understanding

This is a "composite" multimodal backend — it doesn't rely on a single
model understanding all modalities, but orchestrates existing single-
modality models to work together.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

import mimetypes

from xijian_api.ai.backends.mlx.chat import (
    MLXChatBackend,
    _detect_vlm,
    _try_mlx_lm_available,
    _try_mlx_vlm_available,
)
from xijian_api.ai.base import (
    BackendError,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_multimodal, get_stt_backend
from xijian_api.ai.types import (
    ChatChunk,
    ChatChoice,
    ChatUsage,
    GenerationParams,
    MultimodalBackend,
    resolve_part_content,
    resolve_part_to_path,
)


def _now_ts() -> int:
    return int(time.time())


def _build_chunk(
    *,
    chunk_id: str,
    model: str,
    delta: dict | None = None,
    finish_reason: str | None = None,
    usage: ChatUsage | None = None,
) -> ChatChunk:
    return ChatChunk(
        id=chunk_id,
        model=model,
        created=_now_ts(),
        choices=[
            ChatChoice(
                index=0,
                delta=delta if delta is not None else {},
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
        backend="mlx_multimodal",
    )


def _has_ffmpeg() -> bool:
    """检查 ffmpeg 是否可用。Check if ffmpeg is available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _extract_frames(
    video_path: str,
    max_frames: int = 5,
    *,
    quality: int = 2,  # ffmpeg -q:v 1~5, 1=best quality
    use_scene_detect: bool = True,
    max_width: int = 1024,
) -> list[str]:
    """从视频中提取高质量帧。

    支持常见视频格式（mp4, mov, avi, mkv, webm, flv, m4v）。
    默认使用场景检测提取关键帧比均匀采样更智能。
    缩放图像以平衡 VLM 质量和 token 消耗。

    Extract high-quality frames from video.

    Supports common formats (mp4, mov, avi, mkv, webm, flv, m4v).
    Uses scene detection by default for smarter keyframe extraction vs uniform sampling.
    Resizes images to balance VLM quality and token consumption.
    """
    frames: list[str] = []
    tmp_dir = tempfile.mkdtemp(prefix="mlx_video_frames_")

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             video_path],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(result.stdout.strip() or 0)
    except Exception:
        duration = 0

    # 尝试场景检测提取关键帧（更具代表性）/ Attempt scene-detect keyframe extraction
    if use_scene_detect and duration > 5:
        try:
            scene_result = subprocess.run(
                ["ffmpeg", "-i", video_path,
                 "-vf", "select='gt(scene,0.4)',showinfo",
                 "-vsync", "vfr",
                 "-f", "null",
                 "-"],
                capture_output=True, text=True, timeout=60,
            )
            scene_timestamps: list[float] = []
            for line in scene_result.stderr.split("\n"):
                if "pts_time:" in line:
                    try:
                        pts = float(line.split("pts_time:")[1].split()[0])
                        scene_timestamps.append(pts)
                    except (ValueError, IndexError):
                        pass
            if scene_timestamps and len(scene_timestamps) >= 1:
                scene_timestamps = scene_timestamps[:max_frames]
                for i, ts in enumerate(scene_timestamps):
                    out_path = os.path.join(tmp_dir, f"scene_{i:04d}.jpg")
                    try:
                        subprocess.run(
                            ["ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
                             "-vframes", "1",
                             "-q:v", str(quality),
                             "-vf", f"scale='min({max_width},iw)':min'(trunc(oh/a/2)*2)':force_original_aspect_ratio=decrease",
                             out_path],
                            capture_output=True, timeout=30, check=True,
                        )
                        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                            frames.append(out_path)
                    except Exception:
                        continue
                if frames:
                    return frames[:max_frames]
        except Exception:
            pass

    # 降级到均匀间隔采样 / Fall back to uniform interval sampling
    if duration > 0:
        interval = max(1.0, duration / max_frames)
        for i in range(max_frames):
            ts = i * interval
            out_path = os.path.join(tmp_dir, f"frame_{i:04d}.jpg")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
                     "-vframes", "1",
                     "-q:v", str(quality),
                     "-vf", f"scale='min({max_width},iw)':min'(trunc(oh/a/2)*2)':force_original_aspect_ratio=decrease",
                     out_path],
                    capture_output=True, timeout=30, check=True,
                )
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    frames.append(out_path)
            except Exception:
                continue
    else:
        out_path = os.path.join(tmp_dir, "frame_0001.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", video_path,
                 "-vframes", "1",
                 "-q:v", str(quality),
                 "-vf", f"scale='min({max_width},iw)':min'(trunc(oh/a/2)*2)':force_original_aspect_ratio=decrease",
                 out_path],
                capture_output=True, timeout=30, check=True,
            )
            if os.path.exists(out_path):
                frames.append(out_path)
        except Exception:
            pass

    return frames[:max_frames]


def _transcribe_audio(audio_data: bytes | str) -> str | None:
    """通过 MLX STT 后端转录音频。

    Transcribe audio via MLX STT backend.

    Returns the transcribed text, or ``None`` if STT is unavailable.
    """
    try:
        stt_backend = get_stt_backend("mlx", ("gguf",))
    except Exception:
        return None

    if not stt_backend.is_loaded():
        return None

    try:
        result = stt_backend.transcribe(
            audio_data if isinstance(audio_data, bytes) else audio_data.encode(),
            response_format="text",
        )
        if isinstance(result, dict):
            return result.get("text", "")
        return str(result)
    except Exception:
        return None


def _resolve_url_content(url: str) -> bytes | None:
    """从 URL 读取内容。Read content from URL."""
    if url.startswith("data:"):
        try:
            _, b64 = url.split(",", 1)
            return base64.b64decode(b64)
        except Exception:
            return None

    if url.startswith("http://") or url.startswith("https://"):
        try:
            import httpx
            resp = httpx.get(url, timeout=60.0, follow_redirects=True)
            if resp.status_code < 400:
                return resp.content
        except Exception:
            pass

    path = url
    if url.startswith("file://"):
        path = url[len("file://"):]
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None


@register_multimodal("mlx")
class MLXMultimodalBackend(MultimodalBackend):
    """MLX 全模态理解后端（组合式实现）。

    组合 MLX 视觉语言模型（VLM）和语音识别（STT）能力实现
    全模态理解。音频通过 MLX STT 转录后注入，视频通过 ffmpeg
    帧提取后以图像序列送入 VLM。

    对依赖于 ``mlx_vlm`` 和 ``mlx_audio`` / ``mlx_whisper``，
    两者必须至少安装一个才能使用相应的模态。

    MLX multimodal understanding backend (composite).

    Composes MLX VLM and STT capabilities. Audio is transcribed via
    MLX STT and injected as text. Video frames are extracted via
    ffmpeg and fed to the VLM as an image sequence.
    """
    name = "mlx"

    def __init__(self) -> None:
        self._chat_backend: MLXChatBackend | None = None
        self._model_path: Path | None = None
        self._has_vlm: bool = False
        self._has_stt: bool = False
        self._has_ffmpeg: bool = _has_ffmpeg()
        self._has_mlx_vlm = _try_mlx_vlm_available()
        self._has_mlx_lm = _try_mlx_lm_available()

        # Check STT availability lazily
        try:
            stt = get_stt_backend("mlx", ())
            self._has_stt = stt.is_available()
        except Exception:
            self._has_stt = False

    # -- introspection / 内省 ------------------------------------------------------

    def is_available(self) -> bool:
        return self._has_mlx_vlm or self._has_mlx_lm

    def is_loaded(self) -> bool:
        return self._chat_backend is not None and self._chat_backend.is_loaded()

    def modalities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": self._has_vlm,
            "audio": self._has_stt,
            "video": self._has_ffmpeg and self._has_vlm,
            "file": True,
        }

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        path = Path(model_path)
        if not path.exists():
            raise BackendError(
                f"model path does not exist: {path}",
                code="model_not_found",
            )

        # 复用 MLXChatBackend 的加载逻辑
        self._chat_backend = MLXChatBackend()
        self._chat_backend.load(path, context_length=context_length, **kwargs)
        self._model_path = path
        self._has_vlm = self._chat_backend._is_vlm if hasattr(self._chat_backend, "_is_vlm") else False

    def unload(self) -> None:
        if self._chat_backend is not None:
            try:
                self._chat_backend.unload()
            except Exception:
                pass
        self._chat_backend = None
        self._model_path = None
        self._has_vlm = False

    # -- multimodal understanding / 全模态理解 ---------------------------------------------

    def understand(
        self,
        messages: Sequence,
        params: GenerationParams,
        *,
        stream: bool = False,
        abort_signal=None,
    ) -> Iterator[ChatChunk]:
        """全模态理解入口。

        预处理流程：
        1. 扫描所有消息内容片段
        2. 音频片段 → STT 转录文本（替换）
        3. 视频片段 → ffmpeg 帧提取 → 图像片段（替换）
        4. 文件片段 → 文本提取（替换）
        5. 图像 + 文本 → 保留原始格式
        6. 发送到 MLX VLM 后端

        Multimodal understanding entry point.
        """
        if not self.is_loaded() or self._chat_backend is None:
            raise ModelNotLoaded("no MLX multimodal model loaded")
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        # 预处理消息：将非原生模态转换为后端可消化的格式
        processed = self._preprocess_messages(messages)

        # 委派给 MLX 聊天后端（其原生支持 VLM 图像）
        return self._chat_backend.chat(
            processed,
            params,
            stream=stream,
            abort_signal=abort_signal,
        )

    # -- preprocessing / 预处理 -------------------------------------------------------

    def _preprocess_messages(self, messages: Sequence) -> list:
        """预处理消息，转换非原生模态。Preprocess messages, convert non-native modalities."""
        out: list = []
        for m in messages:
            if isinstance(m, dict):
                m = dict(m)
                content = m.get("content", "")
                if isinstance(content, list):
                    m["content"] = self._preprocess_content_parts(content)
            elif hasattr(m, "content"):
                content = m.content
                if isinstance(content, list):
                    m.content = self._preprocess_content_parts(content)
            out.append(m)
        return out

    def _preprocess_content_parts(self, parts: list) -> list:
        """预处理内容片段列表。Preprocess a list of content parts."""
        new_parts: list = []
        for p in parts:
            if not isinstance(p, dict):
                new_parts.append(p)
                continue

            ptype = p.get("type", "")
            if ptype == "text":
                new_parts.append(p)
            elif ptype == "image_url":
                new_parts.append(p)  # MLX VLM 原生支持
            elif ptype == "audio_url":
                transformed = self._transform_audio_part(p)
                new_parts.extend(transformed)
            elif ptype == "video_url":
                transformed = self._transform_video_part(p)
                new_parts.extend(transformed)
            elif ptype == "file_url":
                transformed = self._transform_file_part(p)
                new_parts.extend(transformed)
            else:
                new_parts.append(p)

        return new_parts

    def _transform_audio_part(self, part: dict) -> list[dict]:
        """将音频内容片段转换为转录文本及可选语气分析。

        支持所有常见音频格式（wav, mp3, flac, ogg, m4a, aac, opus, webm）。
        除 STT 转录外，还尝试通过音频特征进行简单语气分析。
        如果 STT 不可用，降级为 ``[audio]`` 占位符。

        Transform an audio content part into transcribed text with optional tone analysis.

        Supports all common audio formats (wav, mp3, flac, ogg, m4a, aac, opus, webm).
        In addition to STT transcription, attempts simple tone analysis via audio features.
        Falls back to ``[audio]`` placeholder when STT is unavailable.
        """
        spec = part.get("audio_url", {})
        if isinstance(spec, dict):
            url = spec.get("url", "")
        elif isinstance(spec, str):
            url = spec
        else:
            return [{"type": "text", "text": "[audio: unsupported format]"}]

        if not url:
            return [{"type": "text", "text": "[audio]"}]

        # 确保 MIME 类型被正确识别 / Ensure MIME type is correctly detected
        audio_formats = ("wav", "mp3", "flac", "ogg", "m4a", "aac", "opus", "webm")
        if url.startswith("data:"):
            # data: URI 包含 MIME，让 _resolve_url_content 处理
            pass
        elif url.startswith("http://") or url.startswith("https://"):
            ext = url.rsplit(".", 1)[-1].split("?")[0][:8].lower()
            if ext not in audio_formats:
                # 可能无法识别，仍尝试 / Might not be recognized, still try
                pass

        audio_bytes = _resolve_url_content(url)
        if audio_bytes is None:
            return [{"type": "text", "text": "[audio: unable to load]"}]

        # -- STT 转录 / STT transcription --
        segments: list[str] = []
        if self._has_stt:
            transcript = _transcribe_audio(audio_bytes)
            if transcript:
                segments.append(f"transcript: {transcript}")

        # -- 尝试语气/情感分析 / Attempt tone/emotion analysis --
        # 使用音频信号的简单统计特征（RMS 能量、ZCR）估算语气的积极程度。
        # 这是一个轻量级启发式，不需要 ML 模型。
        # Uses simple statistical features (RMS energy, ZCR) from audio signal
        # to estimate tone positivity. This is a lightweight heuristic with no ML model.
        try:
            import struct
            import math

            # 尝试解析 WAV 头（最简单情况）/ Try parsing WAV header (simplest case)
            if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
                # 读取音频格式和样本 / Read format and samples
                num_channels = struct.unpack("<H", audio_bytes[22:24])[0]
                sample_rate = struct.unpack("<I", audio_bytes[24:28])[0]
                bits_per_sample = struct.unpack("<H", audio_bytes[34:36])[0]
                # 查找 data chunk / Find data chunk
                data_start = 44  # 标准 WAV 头 / Standard WAV header
                audio_data = audio_bytes[data_start:]
                if audio_data and bits_per_sample == 16 and num_channels > 0:
                    samples_fmt = f"<{len(audio_data) // 2}h"
                    samples = struct.unpack(samples_fmt, audio_data[:len(audio_data) - (len(audio_data) % 2)])
                    if samples:
                        # 计算 RMS 能量 / Compute RMS energy
                        rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0
                        # 计算过零率（帧级别信号变化）
                        # Compute zero-crossing rate (frame-level signal variation, lower = smoother)
                        zc = sum(1 for i in range(1, len(samples)) if
                                 (samples[i] >= 0) != (samples[i - 1] >= 0)) / len(samples)
                        # 基于能量和零交叉率的语气得分
                        # Tone score based on energy + zero-crossing rate
                        # 高能量 + 低 ZCR → 可能积极/愤怒
                        # High energy + low ZCR → likely positive/angry
                        # 低能量 + 高 ZCR → 可能犹豫/悲伤
                        # Low energy + high ZCR → likely hesitant/sad
                        if rms > 0.3 and zc < 0.05:
                            segments.append("tone: energetic / assertive")
                        elif rms > 0.2 and zc < 0.08:
                            segments.append("tone: positive / excited")
                        elif rms > 0.2:
                            segments.append("tone: moderate emphasis")
                        elif rms < 0.05 and zc > 0.15:
                            segments.append("tone: hesitant / uncertain")
                        elif rms < 0.03:
                            segments.append("tone: calm / subdued")
                        else:
                            segments.append("tone: neutral / balanced")
        except Exception:
            # 语气分析失败→静默忽略 / Tone analysis failed → silently ignore
            pass

        if not segments:
            return [{"type": "text", "text": "[audio: processing complete, no transcript]"}]

        return [{"type": "text", "text": f"[Audio analysis: {'; '.join(segments)}]"}]

    def _transform_video_part(self, part: dict) -> list[dict]:
        """将视频内容片段转换为图像帧序列。

        Transform a video content part into a sequence of image frames.
        """
        spec = part.get("video_url", {})
        if isinstance(spec, dict):
            url = spec.get("url", "")
        else:
            url = str(spec) if spec else ""

        if not url or not self._has_ffmpeg or not self._has_vlm:
            return [{"type": "text", "text": "[video]"}]

        video_path = resolve_part_to_path(part) or self._download_video(url)
        if video_path is None:
            return [{"type": "text", "text": "[video: unable to load]"}]

        frames = _extract_frames(video_path, max_frames=5)
        if not frames:
            return [{"type": "text", "text": "[video: unable to extract frames]"}]

        result: list[dict] = [{"type": "text", "text": "[Video frames for analysis:]"}]
        for f in frames:
            result.append({
                "type": "image_url",
                "image_url": {"url": f"file://{f}"},
            })
        return result

    def _transform_file_part(self, part: dict) -> list[dict]:
        """将文件内容片段转换为提取的文本。

        Transform a file content part into extracted text.
        """
        spec = part.get("file_url", {})
        if isinstance(spec, dict):
            url = spec.get("url", "")
            mime = spec.get("mime_type", "")
        else:
            url = str(spec) if spec else ""

        data = resolve_part_content(part)
        if data is None:
            return [{"type": "text", "text": "[file]"}] if not mime else \
                   [{"type": "text", "text": f"[file: {mime}]"}]

        # Try to extract text
        if isinstance(data, bytes):
            try:
                text = data.decode("utf-8")
                return [{"type": "text", "text": text}]
            except UnicodeDecodeError:
                return [{"type": "text", "text": f"[file: binary data, {len(data)} bytes, type: {mime or 'unknown'}]"}]

        if isinstance(data, str):
            return [{"type": "text", "text": data}]

        return [{"type": "text", "text": "[file: unable to extract content]"}]

    @staticmethod
    def _download_video(url: str) -> str | None:
        """Download video from URL."""
        try:
            import httpx
            ext = os.path.splitext(url.split("?")[0])[1][:8] or ".mp4"
            resp = httpx.get(url, timeout=120.0, follow_redirects=True)
            if resp.status_code >= 400:
                return None
            fd, tmp = tempfile.mkstemp(suffix=ext)
            with os.fdopen(fd, "wb") as f:
                f.write(resp.content)
            return tmp
        except Exception:
            return None


__all__ = ["MLXMultimodalBackend"]
