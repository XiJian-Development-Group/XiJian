"""GGUF 全模态理解后端（组合式实现）。

通过组合现有 GGUF 后端能力实现全模态理解：
- 图像理解 → llama.cpp VLM（通过 chat backend）
- 音频理解 → GGUF STT backend（pywhispercpp）
- 视频理解 → ffmpeg 帧提取 → VLM
- 文件理解 → 文本/二进制提取
- 文本理解 → GGUF chat backend

GGUF multimodal understanding backend (composite implementation).

Implements full multimodal understanding by composing existing GGUF
backend capabilities:

- Image understanding → llama.cpp VLM (via chat backend)
- Audio understanding → GGUF STT backend (pywhispercpp)
- Video understanding → ffmpeg frame extraction → VLM
- File understanding → text/binary extraction
- Text understanding → GGUF chat backend

This is a "composite" multimodal backend — it doesn't rely on a single
model understanding all modalities, but orchestrates existing single-
modality models to work together.
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

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
        backend="gguf_multimodal",
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


def _extract_frames(video_path: str, max_frames: int = 5) -> list[str]:
    """从视频中提取帧。Extract frames from video."""
    frames: list[str] = []
    tmp_dir = tempfile.mkdtemp(prefix="gguf_video_frames_")

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
                    frames.append(out_path)
            except Exception:
                continue
    else:
        out_path = os.path.join(tmp_dir, "frame_0001.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", video_path,
                 "-vframes", "1", "-q:v", "2", out_path],
                capture_output=True, timeout=30, check=True,
            )
            if os.path.exists(out_path):
                frames.append(out_path)
        except Exception:
            pass

    return frames[:max_frames]


def _transcribe_audio(audio_data: bytes | str) -> str | None:
    """通过 GGUF STT 后端转录音频。

    Transcribe audio via GGUF STT backend.

    Returns the transcribed text, or ``None`` if STT is unavailable.
    """
    try:
        stt_backend = get_stt_backend("gguf")
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


def _extract_images_from_messages(messages: Sequence) -> list[str]:
    """从消息中提取图像 URL。Extract image URLs from messages."""
    images: list[str] = []
    for m in messages:
        content = m.content if hasattr(m, "content") else m.get("content", "")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    spec = part.get("image_url")
                    if isinstance(spec, dict):
                        url = spec.get("url", "")
                    else:
                        url = spec if isinstance(spec, str) else ""
                    if url:
                        images.append(url)
    return images


def _extract_audio_from_messages(messages: Sequence) -> list[bytes]:
    """从消息中提取音频数据。Extract audio data from messages."""
    audios: list[bytes] = []
    for m in messages:
        content = m.content if hasattr(m, "content") else m.get("content", "")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "audio_url":
                    spec = part.get("audio_url")
                    if isinstance(spec, dict):
                        url = spec.get("url", "")
                    else:
                        url = spec if isinstance(spec, str) else ""
                    if url:
                        data = _resolve_url_content(url)
                        if data:
                            audios.append(data)
    return audios


def _extract_video_from_messages(messages: Sequence) -> list[str]:
    """从消息中提取视频 URL。Extract video URLs from messages."""
    videos: list[str] = []
    for m in messages:
        content = m.content if hasattr(m, "content") else m.get("content", "")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "video_url":
                    spec = part.get("video_url")
                    if isinstance(spec, dict):
                        url = spec.get("url", "")
                    else:
                        url = spec if isinstance(spec, str) else ""
                    if url:
                        videos.append(url)
    return videos


def _extract_files_from_messages(messages: Sequence) -> list[tuple[str, bytes]]:
    """从消息中提取文件内容。Extract file contents from messages."""
    files: list[tuple[str, bytes]] = []
    for m in messages:
        content = m.content if hasattr(m, "content") else m.get("content", "")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "file":
                    spec = part.get("file")
                    if isinstance(spec, dict):
                        url = spec.get("url", "")
                        name = spec.get("name", "file")
                    else:
                        url = spec if isinstance(spec, str) else ""
                        name = "file"
                    if url:
                        data = _resolve_url_content(url)
                        if data:
                            files.append((name, data))
    return files


def _extract_text_from_file(name: str, data: bytes) -> str:
    """从文件中提取文本内容。Extract text content from file."""
    ext = Path(name).suffix.lower()
    try:
        if ext in (".txt", ".md", ".py", ".js", ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".csv", ".log"):
            return data.decode("utf-8", errors="ignore")
        elif ext == ".pdf":
            try:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(data)) as pdf:
                    return "\n".join(page.extract_text() or "" for page in pdf.pages)
            except Exception:
                return f"[PDF file: {name}, size: {len(data)} bytes]"
        elif ext in (".docx", ".doc"):
            try:
                import docx
                doc = docx.Document(io.BytesIO(data))
                return "\n".join(p.text for p in doc.paragraphs)
            except Exception:
                return f"[Word document: {name}, size: {len(data)} bytes]"
        else:
            return f"[Binary file: {name}, size: {len(data)} bytes]"
    except Exception:
        return f"[File: {name}, size: {len(data)} bytes]"


def _preprocess_multimodal_messages(messages: Sequence) -> Sequence:
    """预处理多模态消息，将音频/视频/文件转换为文本/图像。

    Preprocess multimodal messages: transcribe audio, extract video frames,
    extract file text, keep images as-is for VLM.
    """
    # 检查是否有非文本内容
    has_multimodal = False
    for m in messages:
        content = m.content if hasattr(m, "content") else m.get("content", "")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") != "text":
                    has_multimodal = True
                    break
            if has_multimodal:
                break

    if not has_multimodal:
        return messages

    # 导入 ChatMessage 用于构建新消息
    from xijian_api.ai.types import ChatMessage

    new_messages = []
    for m in messages:
        role = m.role if hasattr(m, "role") else m.get("role", "user")
        content = m.content if hasattr(m, "content") else m.get("content", "")
        name = m.name if hasattr(m, "name") else m.get("name", None)

        if isinstance(content, str):
            new_messages.append(ChatMessage(role=role, content=content, name=name))
            continue

        # 处理列表式内容
        new_content_parts: list[dict] = []
        for part in content:
            if not isinstance(part, dict):
                new_content_parts.append({"type": "text", "text": str(part)})
                continue

            ptype = part.get("type")
            if ptype == "text":
                new_content_parts.append(part)
            elif ptype == "image_url":
                # 保留图像，传给 VLM
                new_content_parts.append(part)
            elif ptype == "audio_url":
                # 音频转文字
                spec = part.get("audio_url")
                if isinstance(spec, dict):
                    url = spec.get("url", "")
                else:
                    url = spec if isinstance(spec, str) else ""
                if url:
                    audio_data = _resolve_url_content(url)
                    if audio_data:
                        text = _transcribe_audio(audio_data)
                        if text:
                            new_content_parts.append({"type": "text", "text": f"[Audio transcription]: {text}"})
                        else:
                            new_content_parts.append({"type": "text", "text": "[Audio: transcription unavailable]"})
            elif ptype == "video_url":
                # 视频提取帧转图像
                spec = part.get("video_url")
                if isinstance(spec, dict):
                    url = spec.get("url", "")
                else:
                    url = spec if isinstance(spec, str) else ""
                if url and _has_ffmpeg():
                    video_data = _resolve_url_content(url)
                    if video_data:
                        tmp_path = tempfile.mktemp(suffix=".mp4")
                        try:
                            with open(tmp_path, "wb") as f:
                                f.write(video_data)
                            frames = _extract_frames(tmp_path, max_frames=5)
                            if frames:
                                new_content_parts.append({"type": "text", "text": f"[Video: extracted {len(frames)} frames for analysis]"})
                                for frame in frames:
                                    new_content_parts.append({"type": "image_url", "image_url": {"url": f"file://{frame}"}})
                            else:
                                new_content_parts.append({"type": "text", "text": "[Video: frame extraction failed]"})
                        finally:
                            try:
                                os.unlink(tmp_path)
                            except Exception:
                                pass
                    else:
                        new_content_parts.append({"type": "text", "text": "[Video: unable to download]"})
                else:
                    new_content_parts.append({"type": "text", "text": "[Video: ffmpeg not available]"})
            elif ptype == "file":
                # 文件提取文本
                spec = part.get("file")
                if isinstance(spec, dict):
                    url = spec.get("url", "")
                    name = spec.get("name", "file")
                else:
                    url = spec if isinstance(spec, str) else ""
                    name = "file"
                if url:
                    file_data = _resolve_url_content(url)
                    if file_data:
                        text = _extract_text_from_file(name, file_data)
                        new_content_parts.append({"type": "text", "text": f"[File: {name}]\n{text}"})
                    else:
                        new_content_parts.append({"type": "text", "text": f"[File: {name} - unable to read]"})
            else:
                new_content_parts.append({"type": "text", "text": f"[{ptype}]"})

        new_messages.append(ChatMessage(role=role, content=new_content_parts, name=name))

    return new_messages


@register_multimodal("gguf")
class GGUFMultimodalBackend(MultimodalBackend):
    """GGUF 全模态理解后端。GGUF multimodal understanding backend."""

    name = "gguf"

    def __init__(self) -> None:
        self._chat_backend = None
        self._has_llama_cpp = False
        self._has_pywhispercpp = False
        self._has_ffmpeg = False
        self._model_path: Path | None = None
        self._is_vlm: bool = False
        self._temp_frame_paths: list[str] = []

    # -- introspection / 内省 ------------------------------------------------------

    def is_available(self) -> bool:
        # 检查 llama-cpp-python 是否可用
        try:
            import llama_cpp  # noqa: F401
            self._has_llama_cpp = True
        except Exception:
            self._has_llama_cpp = False

        # 检查 pywhispercpp 是否可用
        try:
            import pywhispercpp  # noqa: F401
            self._has_pywhispercpp = True
        except Exception:
            self._has_pywhispercpp = False

        # 检查 ffmpeg
        self._has_ffmpeg = _has_ffmpeg()

        return self._has_llama_cpp

    def is_loaded(self) -> bool:
        return self._chat_backend is not None and self._chat_backend.is_loaded()

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        path = Path(model_path)
        if not path.exists():
            raise BackendError(
                f"model path does not exist: {path}",
                code="model_not_found",
            )

        # 检测是否为 VLM 模型
        force_vlm = bool(kwargs.get("vlm") or kwargs.get("is_vlm"))
        self._is_vlm = force_vlm or self._detect_vlm(path)

        from xijian_api.ai.backends.gguf.chat import GGUFChatBackend
        self._chat_backend = GGUFChatBackend()
        n_ctx = int(context_length) if context_length else 0
        self._chat_backend.load(model_path, context_length=n_ctx, **kwargs)
        self._model_path = path

    def unload(self) -> None:
        # 清理临时帧文件
        for tmp in self._temp_frame_paths:
            try:
                os.unlink(tmp)
            except Exception:
                pass
        self._temp_frame_paths.clear()

        if self._chat_backend:
            self._chat_backend.unload()
        self._chat_backend = None
        self._model_path = None
        self._is_vlm = False

    # -- capabilities / 能力 ----------------------------------------------------------

    def modalities(self) -> dict[str, bool]:
        """返回支持的模态。Return supported modalities."""
        return {
            "text": True,
            "image": self._is_vlm and self._has_llama_cpp,
            "audio": self._has_pywhispercpp,
            "video": self._has_ffmpeg and self._is_vlm and self._has_llama_cpp,
            "file": True,
        }

    # -- core multimodal understanding / 核心全模态理解 ----------------------------------

    def understand(
        self,
        messages: Sequence,
        params: GenerationParams,
        *,
        stream: bool = False,
        abort_signal=None,
    ) -> Iterator[ChatChunk]:
        """核心全模态理解方法。

        Core multimodal understanding method.

        预处理步骤：
        1. 扫描消息中的所有内容片段
        2. 音频片段 → STT 转录 → 替换为文本
        3. 视频片段 → ffmpeg 帧提取 → 替换为图像片段列表
        4. 图像 + 文本 → 保留原始格式，传给 VLM
        5. 文件片段 → 尝试提取文本内容
        6. 调用 chat 后端（VLM 模式）处理
        """
        if not self.is_loaded():
            raise ModelNotLoaded("no GGUF multimodal model loaded")

        if not self._chat_backend:
            raise BackendError("chat backend not initialized", code="backend_error")

        # 预处理多模态消息
        processed_messages = _preprocess_multimodal_messages(messages)

        # 委托给 chat backend（VLM 模式）
        chunk_id = f"chatcmpl-gguf-multi-{int(time.time() * 1000)}"
        model_id = str(self._model_path) if self._model_path else "gguf"

        return self._chat_backend.chat(
            messages=processed_messages,
            params=params,
            stream=stream,
            abort_signal=abort_signal,
        )

    # -- helpers / 辅助 ------------------------------------------------------------

    def _detect_vlm(self, path: Path) -> bool:
        """启发式判断是否为 VLM 模型。

        Heuristically decide whether the model at path is a VLM.
        """
        # llama-cpp-python 的 VLM 支持需要 mmproj 文件
        # 检查目录下是否有 *.mmproj 文件
        if path.is_dir():
            mmproj_files = list(path.glob("*.mmproj"))
            if mmproj_files:
                return True
            # 检查 config.json 中的架构
            config_path = path / "config.json"
            if config_path.exists():
                try:
                    import json
                    with config_path.open("r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    archs = cfg.get("architectures") or []
                    if isinstance(archs, str):
                        archs = [archs]
                    vlm_hints = ("vl", "vision", "llava", "qwen2vl", "qwen2_vl",
                                 "paligemma", "idefics", "pixtral", "internvl",
                                 "deepseekvl", "smolvlm", "mllama", "phi3v")
                    for arch in archs:
                        arch_lower = str(arch).lower()
                        for hint in vlm_hints:
                            if hint in arch_lower:
                                return True
                    if "model_type" in cfg:
                        model_type = str(cfg.get("model_type", "")).lower()
                        for hint in vlm_hints:
                            if hint in model_type:
                                return True
                except Exception:
                    pass
        return False


__all__ = ["GGUFMultimodalBackend"]