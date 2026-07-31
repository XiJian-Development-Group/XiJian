"""OpenAI 兼容远程视频理解后端。

利用 OpenAI GPT-4o 的视频理解能力，通过 ``/chat/completions`` API
发送视频帧序列以理解视频内容。支持：

- 视频 URL 帧提取（通过 ffmpeg）
- 视频 DataFrame 描述理解（为什么/什么人在做什么）
- 关键画面分析
- 时间线问答

当 ``video_understanding`` 在配置的 ``[[models]].type`` 中设置为
``video_understanding`` 时，通过 ``get_video_understanding_backend()`` 选择。

OpenAI-compatible remote video understanding backend.

Leverages GPT-4o's video understanding capabilities by sending video
frame sequences through the ``/chat/completions`` API.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from xijian_api.ai.backends.openai._client import (
    RemoteConfig,
    resolve_config,
    _httpx_post_json,
)
from xijian_api.ai.base import (
    BackendError,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_video_understanding
from xijian_api.ai.types import (
    GenerationParams,
    VideoUnderstandingBackend,
)


def _extract_frames_ffmpeg(
    video_path: str,
    *,
    max_frames: int = 10,
    fps: float = 1.0,
) -> list[str]:
    """使用 ffmpeg 从视频中提取帧。Extract frames from video using ffmpeg."""
    import subprocess

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except Exception:
        return []

    frames: list[str] = []
    tmp_dir = tempfile.mkdtemp(prefix="xijian_video_understand_")

    if fps <= 0:
        fps = 1.0

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vf", f"fps={fps}",
             "-q:v", "2",
             os.path.join(tmp_dir, "frame_%04d.jpg")],
            capture_output=True, timeout=120, check=True,
        )
    except Exception:
        pass

    # Collect extracted frames, honor max_frames
    all_frames = sorted(
        [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir)
         if f.startswith("frame_") and f.endswith(".jpg")]
    )

    if not all_frames:
        # Fallback: extract one frame at key intervals
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

        if duration > 0:
            interval = max(1.0, duration / max_frames)
            for i in range(max_frames):
                ts = i * interval
                out_path = os.path.join(tmp_dir, f"frame_{i:04d}.jpg")
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
                         "-vframes", "1", "-q:v", "2", out_path],
                        capture_output=True, timeout=30, check=True,
                    )
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        all_frames.append(out_path)
                except Exception:
                    continue

    return all_frames[:max_frames]


@register_video_understanding("openai")
class OpenAIVideoUnderstandingBackend(VideoUnderstandingBackend):
    """OpenAI 兼容视频理解后端实现。

    通过将视频帧提取为图像序列并通过 GPT-4o 的视觉能力理解视频内容。
    支持视频 URL、本地文件路径和 base64 data URI。

    OpenAI-compatible video understanding backend implementation.

    Understands video content by extracting frames into image sequences
    and leveraging GPT-4o's vision capabilities.
    """
    name = "openai"

    def __init__(self) -> None:
        self._cfg: RemoteConfig | None = None
        self._model_path: Path | None = None
        self._loaded: bool = False

    # -- introspection / 内省 ------------------------------------------------------

    def is_available(self) -> bool:
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(self, model_path, **kwargs) -> None:
        section = kwargs.pop("_openai_section", None)
        cfg = resolve_config(kwargs, section=section)
        if not cfg.model_name:
            raise BackendError(
                "openai video understanding backend requires a model_name",
                code="backend_error",
            )
        self._cfg = cfg
        self._model_path = Path(model_path) if model_path else None
        self._loaded = True

    def unload(self) -> None:
        self._cfg = None
        self._model_path = None
        self._loaded = False

    # -- understanding / 理解 -------------------------------------------------------

    def understand(
        self,
        video: Any,
        *,
        prompt: str = "",
        fps: int = 1,
        max_frames: int = 10,
        abort_signal=None,
    ) -> str:
        """理解视频内容并返回文本描述。

        接受：
        - 字符串：``http(s)://`` URL、``file://`` 路径、裸路径、``data:`` URI
        - bytes：直接处理的视频字节
        - dict：``{"url": ...}`` 或 ``{"type": "video_url", "video_url": {...}}``

        Understand video content and return a text description.
        """
        if not self.is_loaded() or self._cfg is None:
            raise ModelNotLoaded("no openai video understanding model loaded")
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        # Resolve video to local path
        video_path = self._resolve_video(video)
        if video_path is None:
            raise BackendError(
                "unable to resolve video input",
                code="backend_error",
            )

        # Extract frames
        frames = _extract_frames_ffmpeg(
            video_path,
            max_frames=max_frames,
            fps=float(fps),
        )
        if not frames:
            raise BackendError(
                "unable to extract frames from video",
                code="backend_error",
            )

        # Build multimodal messages with frames
        system_prompt = (
            "You are a video understanding assistant. Analyze the provided video frames "
            "and answer the user's question about the video content. "
            "Describe what is happening, who is involved, and any relevant details. "
            "Be thorough and specific."
        )
        content_parts: list[dict] = [
            {"type": "text", "text": prompt or "Describe what is happening in this video."}
        ]
        for frame_path in frames:
            try:
                with open(frame_path, "rb") as fp:
                    raw = fp.read()
                b64 = base64.b64encode(raw).decode("ascii")
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
            except Exception:
                continue

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_parts},
        ]

        body: dict[str, Any] = {
            "model": self._cfg.model_name,
            "messages": messages,
            "max_tokens": 2048,
        }

        url = f"{self._cfg.base_url}/chat/completions"
        try:
            result = _httpx_post_json(
                url, headers=self._cfg.auth_header, json_body=body,
            )
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(
                f"video understanding request failed: {exc}",
                code="backend_error",
            ) from exc

        choices = result.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        content = message.get("content") or ""

        return str(content)

    # -- helpers / 辅助 ------------------------------------------------------------

    def _resolve_video(self, video: Any) -> str | None:
        """将各种格式的视频输入解析为本地文件系统路径。

        Resolve various video input formats to a local filesystem path.
        """
        if isinstance(video, str):
            if video.startswith("file://"):
                path = video[len("file://"):]
                return path if os.path.exists(path) else None
            if video.startswith("data:"):
                return self._resolve_data_uri(video)
            if video.startswith("http://") or video.startswith("https://"):
                return self._download_video(video)
            return video if os.path.exists(video) else None

        if isinstance(video, bytes):
            fd, tmp = tempfile.mkstemp(suffix=".mp4")
            with os.fdopen(fd, "wb") as f:
                f.write(video)
            return tmp

        if isinstance(video, dict):
            if "url" in video:
                return self._resolve_video(video["url"])
            for key in ("video_url", "file_url"):
                spec = video.get(key)
                if isinstance(spec, dict) and "url" in spec:
                    return self._resolve_video(spec["url"])
                if isinstance(spec, str):
                    return self._resolve_video(spec)

        return None

    def _resolve_data_uri(self, data_uri: str) -> str | None:
        """Decode a data URI to a temp file and return its path."""
        try:
            import base64
            header, b64 = data_uri.split(",", 1)
            raw = base64.b64decode(b64)
            ext = ".mp4"
            mime = header.split(":")[1].split(";")[0] if ":" in header else ""
            mime_ext_map = {
                "video/mp4": ".mp4",
                "video/webm": ".webm",
                "video/quicktime": ".mov",
                "video/x-msvideo": ".avi",
            }
            ext = mime_ext_map.get(mime, ".mp4")
            fd, tmp = tempfile.mkstemp(suffix=ext)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            return tmp
        except Exception:
            return None

    def _download_video(self, url: str) -> str | None:
        """Download video from URL to a temp file and return its path."""
        try:
            import httpx
            path_part = url.split("?")[0]
            ext = os.path.splitext(path_part)[1][:8].lower() or ".mp4"
            resp = httpx.get(url, timeout=120.0, follow_redirects=True)
            if resp.status_code >= 400:
                return None
            fd, tmp = tempfile.mkstemp(suffix=ext)
            with os.fdopen(fd, "wb") as f:
                f.write(resp.content)
            return tmp
        except Exception:
            return None


__all__ = ["OpenAIVideoUnderstandingBackend"]
