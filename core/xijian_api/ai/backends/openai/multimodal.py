"""OpenAI 兼容全模态理解后端。

利用 OpenAI GPT-4o、GPT-4o-audio-preview 等新一代多模态模型的
原生全模态能力。通过 OpenAI ``/chat/completions`` API 发送包含
文本、图像、音频、视频帧输入的消息，由模型原生理解所有模态。

与 ``OpenAIChatBackend`` 的关系：
- ``OpenAIChatBackend`` 通过标准的 chat 接口支持多模态输入透传
- ``OpenAIMultimodalBackend`` 在此之上提供模态感知的预处理和
  元数据报告（哪些模态被使用了、token 分模态统计等）

配置通过 :func:`xijian_api.ai.backends.openai._client.resolve_config`
按调用解析，与 OpenAIChatBackend 共享同一配置路径。

OpenAI-compatible unified multimodal backend.

Leverages native multimodal capabilities in models like GPT-4o and
GPT-4o-audio-preview.  Sends messages with text, image, audio, and
video frame inputs through the OpenAI ``/chat/completions`` API and
lets the model understand all modalities natively.

Relationship with ``OpenAIChatBackend``:
- ``OpenAIChatBackend`` already supports multimodal passthrough via chat
- ``OpenAIMultimodalBackend`` adds modality-aware metadata reporting and
  preprocessing on top
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

from xijian_api.ai.backends.openai._client import (
    RemoteConfig,
    resolve_config,
    _httpx_post_json,
    _httpx_post_stream,
)
from xijian_api.ai.base import (
    BackendError,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_multimodal
from xijian_api.ai.types import (
    ChatChunk,
    ChatChoice,
    ChatUsage,
    GenerationParams,
    MultimodalBackend,
    resolve_part_content,
    resolve_part_to_path,
)
from xijian_api.errors import GenerationAborted as ApiGenerationAborted


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
        backend="openai_multimodal",
    )


def _inventory_modalities(messages: Sequence) -> dict[str, int]:
    """统计消息列表中各模态的出现次数。

    Count occurrences of each modality in the message list.
    """
    counts: dict[str, int] = {}
    for m in messages:
        content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
        if isinstance(content, str):
            counts["text"] = counts.get("text", 0) + 1
        elif isinstance(content, list):
            for p in content:
                if isinstance(p, dict):
                    ptype = p.get("type", "unknown")
                    counts[ptype] = counts.get(ptype, 0) + 1
    return counts


def _convert_audio_for_gpt4o(part: dict) -> dict | None:
    """将 ``audio_url`` 内容片段转换为 GPT-4o-audio 原生的
    ``input_audio`` 格式。

    读取 base64 data URI 或下载远程文件，然后构建
    ``{"type": "input_audio", "input_audio": {"data": ..., "format": ...}}``。

    Convert an ``audio_url`` content part to the GPT-4o-audio native
    ``input_audio`` format.
    """
    spec = part.get("audio_url", {})
    if isinstance(spec, dict):
        url = spec.get("url", "")
        fmt = spec.get("format", "")
    else:
        url = str(spec) if spec else ""
        fmt = ""

    if not url:
        return None

    audio_bytes: bytes | None = None
    detected_fmt: str = ""

    if url.startswith("data:"):
        try:
            header, b64 = url.split(",", 1)
            audio_bytes = base64.b64decode(b64)
            mime = header.split(":")[1].split(";")[0] if ":" in header else ""
            detected_fmt = _mime_to_audio_format(mime)
        except Exception:
            pass
    elif url.startswith("http://") or url.startswith("https://"):
        try:
            import httpx
            resp = httpx.get(url, timeout=60.0, follow_redirects=True)
            if resp.status_code < 400:
                audio_bytes = resp.content
                # Try to infer format from URL extension
                path_part = url.split("?")[0]
                ext = os.path.splitext(path_part)[1].lower().lstrip(".")
                if ext in ("wav", "mp3", "opus", "flac", "aac", "pcm", "webm"):
                    detected_fmt = ext
        except Exception:
            pass

    if audio_bytes is None:
        path = resolve_part_to_path(part)
        if path:
            try:
                with open(path, "rb") as f:
                    audio_bytes = f.read()
                ext = os.path.splitext(path)[1].lower().lstrip(".")
                if ext in ("wav", "mp3", "opus", "flac", "aac", "pcm", "webm"):
                    detected_fmt = ext
            except Exception:
                pass

    if audio_bytes is None:
        return None

    if fmt and not detected_fmt:
        detected_fmt = fmt
    if not detected_fmt:
        detected_fmt = "wav"

    b64_data = base64.b64encode(audio_bytes).decode("ascii")
    return {
        "type": "input_audio",
        "input_audio": {
            "data": b64_data,
            "format": detected_fmt,
        },
    }


def _mime_to_audio_format(mime: str) -> str:
    mapping = {
        "audio/wav": "wav",
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/ogg": "opus",
        "audio/opus": "opus",
        "audio/flac": "flac",
        "audio/aac": "aac",
        "audio/pcm": "pcm",
        "audio/webm": "webm",
    }
    return mapping.get(mime.split(";")[0], "wav")


def _convert_video_for_gpt4o(part: dict) -> list[dict]:
    """将 ``video_url`` 内容片段转换为 GPT-4o 可理解的图像帧列表。

    通过 ffmpeg（如果可用）提取关键帧，返回图像帧内容片段列表。
    如果 ffmpeg 不可用，尝试最差情况：下载视频并尝试作为
    ``image_url`` 格式。

    Convert a ``video_url`` content part to a list of image frames
    that GPT-4o can understand.
    """
    spec = part.get("video_url", {})
    if isinstance(spec, dict):
        url = spec.get("url", "")
    else:
        url = str(spec) if spec else ""

    if not url:
        return []

    # Download video to temp file
    video_path = resolve_part_to_path(part) or _download_to_temp(url)
    if video_path is None:
        return []

    frames = _extract_frames_ffmpeg(video_path, max_frames=5)
    if frames:
        return [_frame_to_data_uri(f) for f in frames]

    # Fallback: try first frame only
    first_frame = _extract_frames_ffmpeg(video_path, max_frames=1)
    if first_frame:
        return [_frame_to_data_uri(first_frame[0])]

    return [{"type": "text", "text": "[video: unable to extract frames]"}]


def _frame_to_data_uri(frame_path: str) -> dict:
    """将本地帧文件转为 base64 data URI 内容片段。

    Remote endpoints (OpenAI-compatible) cannot access local ``file://``
    paths, so extracted frames must be embedded as base64 data URIs.
    远程端点（OpenAI 兼容）无法访问本地 ``file://`` 路径，
    因此提取的帧必须以 base64 data URI 形式嵌入。
    """
    try:
        with open(frame_path, "rb") as fp:
            raw = fp.read()
        b64 = base64.b64encode(raw).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        }
    except Exception:
        return {"type": "text", "text": "[video: frame read failed]"}


def _download_to_temp(url: str) -> str | None:
    """下载 URL 到临时文件并返回路径。Download URL to temp file and return path."""
    try:
        import httpx
        resp = httpx.get(url, timeout=60.0, follow_redirects=True)
        if resp.status_code >= 400:
            return None
        ext = os.path.splitext(url.split("?")[0])[1][:8] or ".bin"
        fd, tmp = tempfile.mkstemp(suffix=ext)
        with os.fdopen(fd, "wb") as f:
            f.write(resp.content)
        return tmp
    except Exception:
        return None


def _extract_frames_ffmpeg(video_path: str, max_frames: int = 5) -> list[str]:
    """使用 ffmpeg 从视频中提取帧。Extract frames from video using ffmpeg."""
    import subprocess

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except Exception:
        return []

    frames: list[str] = []
    tmp_dir = tempfile.mkdtemp(prefix="xijian_video_frames_")

    # Extract evenly-spaced frames
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(result.stdout.strip() or 0)
    except Exception:
        duration = 0

    if duration <= 0:
        # Can't probe; extract first frame only
        out_path = os.path.join(tmp_dir, "frame_0001.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", video_path,
                 "-vframes", "1", "-q:v", "2", out_path],
                capture_output=True, timeout=30, check=True,
            )
            frames.append(out_path)
        except Exception:
            pass
        return frames

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

    return frames


def _normalise_messages(messages: Sequence) -> list[dict]:
    """将消息序列规范化为 OAI 兼容字典列表，并进行模态转换。

    转换规则：
    - ``audio_url`` → ``input_audio``（GPT-4o-audio 原生格式）
    - ``video_url`` → 多帧 ``image_url``（帧提取）
    - ``file_url`` → 尝试提取文本内容注入
    - ``text`` / ``image_url`` → 原样透传

    Normalise messages to OAI-compatible dicts with modality conversion.
    """
    out: list[dict] = []
    for m in messages:
        if isinstance(m, dict):
            d = dict(m)
        else:
            d = {"role": "user", "content": str(m)}

        content = d.get("content", "")
        if isinstance(content, str):
            out.append(d)
            continue

        if isinstance(content, list):
            new_parts: list[dict] = []
            for part in content:
                if not isinstance(part, dict):
                    new_parts.append({"type": "text", "text": str(part)})
                    continue

                ptype = part.get("type", "")
                if ptype == "text":
                    new_parts.append(part)
                elif ptype == "image_url":
                    new_parts.append(part)
                elif ptype == "audio_url":
                    converted = _convert_audio_for_gpt4o(part)
                    if converted is not None:
                        new_parts.append(converted)
                    else:
                        new_parts.append({"type": "text", "text": "[audio: unable to process]"})
                elif ptype == "video_url":
                    frames = _convert_video_for_gpt4o(part)
                    new_parts.extend(frames)
                elif ptype == "file_url":
                    text = _extract_file_text(part)
                    if text:
                        new_parts.append({"type": "text", "text": text})
                    else:
                        new_parts.append({"type": "text", "text": "[file: unable to extract content]"})
                else:
                    new_parts.append(part)

            d["content"] = new_parts
        out.append(d)

    return out


def _extract_file_text(part: dict) -> str | None:
    """尝试从文件内容片段提取文本内容。

    Try to extract text content from a file content part.
    """
    from xijian_api.ai.types import detect_part_mime
    mime = detect_part_mime(part)

    if mime.startswith("text/"):
        data = resolve_part_content(part)
        if isinstance(data, bytes):
            try:
                return data.decode("utf-8")
            except Exception:
                return data.decode("utf-8", errors="replace")
        return str(data) if data else None

    if mime == "application/pdf":
        data = resolve_part_content(part)
        if data:
            try:
                import io
                import PyPDF2
                reader = PyPDF2.PdfReader(io.BytesIO(data if isinstance(data, bytes) else b""))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                return text if text.strip() else None
            except Exception:
                pass
            try:
                import pdfminer.high_level
                text = pdfminer.high_level.extract_text(io.BytesIO(data if isinstance(data, bytes) else b""))
                return text if text.strip() else None
            except Exception:
                pass
        return "[pdf: unable to extract text, try installing PyPDF2 or pdfminer]"

    return None


@register_multimodal("openai")
class OpenAIMultimodalBackend(MultimodalBackend):
    """OpenAI 全模态理解后端实现。

    利用 GPT-4o 系列模型的原生多模态能力。支持文本、图像、音频、
    视频输入的理解，通过 OpenAI ``/chat/completions`` API 发送。

    当模型同时支持多模态输入时（如 GPT-4o-audio-preview），
    音频内容以 ``input_audio`` 格式发送，由模型原生处理。
    视频内容通过 ffmpeg 提取关键帧后以图像序列发送。

    OpenAI multimodal understanding backend implementation.

    Leverages native multimodal capabilities of GPT-4o series models.
    Supports text, image, audio, and video input understanding through
    the OpenAI ``/chat/completions`` API.
    """
    name = "openai"

    def __init__(self) -> None:
        self._cfg: RemoteConfig | None = None
        self._model_path: Path | None = None
        self._loaded: bool = False
        self._supports_audio: bool = False
        self._supports_video: bool = False

    # -- introspection / 内省 ------------------------------------------------------

    def is_available(self) -> bool:
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    def modalities(self) -> dict[str, bool]:
        """返回此后端支持哪些模态。

        Return which modalities this backend supports.
        """
        return {
            "text": True,
            "image": True,
            "audio": self._supports_audio,
            "video": self._supports_video,
            "file": True,  # 文本文件可通过文本提取支持
        }

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        section = kwargs.pop("_openai_section", None)
        cfg = resolve_config(kwargs, section=section)
        if not cfg.model_name:
            raise BackendError(
                "openai multimodal backend requires a model_name (set in "
                "[[models]].extra.model_name or [backends.openai].default_model)",
                code="backend_error",
            )
        self._cfg = cfg
        self._model_path = Path(model_path) if model_path else None

        # 基于模型名称推断模态支持能力
        model_name = cfg.model_name.lower()
        self._supports_audio = "audio" in model_name
        self._supports_video = "video" in model_name or "vision" in model_name

        # 通过 xijian_backend 配置覆盖
        if kwargs.get("supports_audio") is not None:
            self._supports_audio = bool(kwargs.get("supports_audio"))
        if kwargs.get("supports_video") is not None:
            self._supports_video = bool(kwargs.get("supports_video"))

        self._loaded = True

    def unload(self) -> None:
        self._cfg = None
        self._model_path = None
        self._loaded = False
        self._supports_audio = False
        self._supports_video = False

    # -- multimodal understanding / 全模态理解 ---------------------------------------------

    def understand(
        self,
        messages: Sequence,
        params: GenerationParams,
        *,
        stream: bool = False,
        abort_signal=None,
    ) -> Iterator[ChatChunk]:
        """使用 GPT-4o 的全模态理解能力。

        接受包含文本、图像、音频、视频、文件的任意模态组合，
        通过归一化、模态转换后发送给后端，返回模型的理解结果。

        返回格式与 ``ChatBackend.chat()`` 相同（:class:`ChatChunk`
        迭代器），确保路由层可以统一处理。

        Leverage GPT-4o's native multimodal understanding.

        Accepts any combination of text, image, audio, video, file inputs,
        normalises them, converts unsupported modalities, sends to the
        backend, and returns the model's understanding result.
        """
        if not self.is_loaded() or self._cfg is None:
            raise ModelNotLoaded("no openai multimodal model loaded")

        # 模态感知的消息归一化
        oai_messages = _normalise_messages(messages)

        # 统计模态使用情况
        modality_counts = _inventory_modalities(messages)

        # 构建请求参数
        kwargs: dict[str, Any] = {}
        if params.temperature is not None:
            kwargs["temperature"] = float(params.temperature)
        if params.top_p is not None:
            kwargs["top_p"] = float(params.top_p)
        if params.max_tokens is not None and params.max_tokens > 0:
            kwargs["max_tokens"] = int(params.max_tokens)
        if params.stop:
            kwargs["stop"] = list(params.stop)

        chunk_id = f"multimodal-openai-{int(time.time() * 1000)}"
        model_id = self._cfg.model_name
        url = f"{self._cfg.base_url}/chat/completions"

        body: dict[str, Any] = {
            "model": model_id,
            "messages": oai_messages,
            **kwargs,
        }

        # 添加模态元数据（非标准字段，但兼容提供商可据此优化）
        body["xijian_modalities"] = modality_counts

        if stream:
            return self._streaming(
                url=url,
                body=body,
                chunk_id=chunk_id,
                model_id=model_id,
                abort_signal=abort_signal,
            )

        return self._blocking(
            url=url,
            body=body,
            chunk_id=chunk_id,
            model_id=model_id,
            abort_signal=abort_signal,
        )

    # -- internals / 内部 ----------------------------------------------------------

    def _blocking(
        self,
        *,
        url: str,
        body: dict,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        try:
            result = _httpx_post_json(
                url, headers=self._cfg.auth_header, json_body=body,
            )
        except ApiGenerationAborted:
            raise
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(
                f"openai multimodal request failed: {exc}",
                code="backend_error",
            ) from exc

        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        choices = result.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        content = message.get("content") or ""
        finish_reason = choice.get("finish_reason") or "stop"
        usage = self._usage(result.get("usage"))

        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant", "content": content},
            finish_reason=finish_reason,
            usage=usage,
        )

    def _streaming(
        self,
        *,
        url: str,
        body: dict,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        body_with_stream = {**body, "stream": True}

        yield _build_chunk(
            chunk_id=chunk_id, model=model_id, delta={"role": "assistant"},
        )

        aborted = False
        last_usage: ChatUsage | None = None
        try:
            for piece in _httpx_post_stream(
                url, headers=self._cfg.auth_header, json_body=body_with_stream,
            ):
                if abort_signal is not None:
                    abort_signal.raise_if_aborted()
                choices = piece.get("choices") or []
                choice = choices[0] if choices else {}
                delta = choice.get("delta") or {}
                finish_reason = choice.get("finish_reason")
                if "usage" in piece and piece["usage"]:
                    last_usage = self._usage(piece["usage"])
                content = delta.get("content")
                if content:
                    yield _build_chunk(
                        chunk_id=chunk_id,
                        model=model_id,
                        delta={"content": content},
                    )
                if finish_reason:
                    yield _build_chunk(
                        chunk_id=chunk_id,
                        model=model_id,
                        delta={},
                        finish_reason=finish_reason,
                        usage=last_usage,
                    )
                    return
        except ApiGenerationAborted:
            aborted = True

        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason="abort" if aborted else "stop",
            usage=last_usage,
        )

    @staticmethod
    def _usage(raw) -> ChatUsage | None:
        if not isinstance(raw, dict):
            return None
        return ChatUsage(
            prompt_tokens=int(raw.get("prompt_tokens", 0) or 0),
            completion_tokens=int(raw.get("completion_tokens", 0) or 0),
            total_tokens=int(raw.get("total_tokens", 0) or 0),
        )


__all__ = ["OpenAIMultimodalBackend"]
