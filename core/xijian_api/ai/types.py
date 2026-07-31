"""
数据类型定义模块：聊天消息、生成参数、各类后端抽象基类等。
Data type definitions: chat messages, generation parameters, backend abstract bases, etc.
"""
from dataclasses import dataclass, field
from typing import Sequence, Union


#: 多模态内容：纯文本字符串或 OpenAI 兼容的内容片段列表（文本/图片/音频等）。
#: Multimodal content: either a plain string (text-only) or a list of OAI content parts
#: (e.g., {"type": "text", "text": ...} / {"type": "image_url", "image_url": {"url": ...}} etc.).
Content = Union[str, list]


# ---------------------------------------------------------------------------
# 多模态内容片段类型 / Multimodal Content Part Helpers
# ---------------------------------------------------------------------------


def make_text_part(text: str) -> dict:
    """创建一个文本内容片段。Create a text content part."""
    return {"type": "text", "text": text}


def make_image_part(url: str) -> dict:
    """创建一个图片内容片段。Create an image content part.

    ``url`` 支持 ``data:`` base64 编码、``http(s)://`` 远程链接、
    ``file://`` 本地路径和裸文件系统路径。
    """
    return {"type": "image_url", "image_url": {"url": url}}


def make_audio_part(url: str, *, format: str | None = None) -> dict:
    """创建一个音频内容片段。Create an audio content part.

    ``url`` 支持 ``data:`` base64 编码、``http(s)://`` 远程链接、
    和 ``file://`` 本地路径。
    ``format`` 为音频格式提示（如 ``"wav"``、``"mp3"``、``"opus"``）。
    """
    part: dict = {"type": "audio_url", "audio_url": {"url": url}}
    if format:
        part["audio_url"]["format"] = format
    return part


def make_video_part(url: str, *, format: str | None = None) -> dict:
    """创建一个视频内容片段。Create a video content part.

    ``url`` 支持 ``data:`` base64 编码、``http(s)://`` 远程链接、
    和 ``file://`` 本地路径。
    """
    part: dict = {"type": "video_url", "video_url": {"url": url}}
    if format:
        part["video_url"]["format"] = format
    return part


def make_file_part(url: str, *, mime_type: str = "application/octet-stream") -> dict:
    """创建一个文件内容片段。Create a file content part.

    ``url`` 支持 ``data:`` base64 编码、``http(s)://`` 远程链接、
    和 ``file://`` 本地路径。
    ``mime_type`` 为文件 MIME 类型提示。
    """
    return {"type": "file_url", "file_url": {"url": url, "mime_type": mime_type}}


def resolve_part_to_path(part: dict) -> str | None:
    """将内容片段中的 URL 解析为本地文件系统路径，返回路径或 None。

    支持的 URL 格式：
    * ``file:///abs/path``
    * ``data:<mime>;base64,<payload>``
    * ``http(s)://...``
    * 裸本地路径（原样返回）

    Resolve the URL in a content part to a local filesystem path.
    """
    import base64, os, tempfile

    # 确定 URL 字段名
    url_field = part.get("type", "").replace("_url", "") + "_url"
    spec = part.get(url_field, {})
    if isinstance(spec, dict):
        url = spec.get("url", "")
    elif isinstance(spec, str):
        url = spec
    else:
        return None

    if not isinstance(url, str) or not url:
        return None

    if url.startswith("file://"):
        path = url[len("file://"):]
        return path if os.path.exists(path) else None

    if url.startswith("data:"):
        try:
            header, b64 = url.split(",", 1)
            mime = header.split(":")[1].split(";")[0] if ":" in header else ""
            ext_map = {
                "image/png": ".png", "image/jpeg": ".jpg",
                "image/gif": ".gif", "image/webp": ".webp",
                "audio/wav": ".wav", "audio/mpeg": ".mp3",
                "audio/ogg": ".ogg", "audio/opus": ".opus",
                "video/mp4": ".mp4", "video/webm": ".webm",
                "application/pdf": ".pdf",
            }
            ext = ext_map.get(mime, ".bin")
            raw = base64.b64decode(b64)
            fd, tmp = tempfile.mkstemp(suffix=ext)
            with os.fdopen(fd, "wb") as fp:
                fp.write(raw)
            return tmp
        except Exception:
            return None

    if url.startswith("http://") or url.startswith("https://"):
        try:
            import httpx
            # 从扩展名推断类型
            path_part = url.split("?")[0] if "?" in url else url
            ext = os.path.splitext(path_part)[1][:8].lower() or ".bin"
            if not ext.startswith("."):
                ext = "." + ext
            resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            if resp.status_code >= 400:
                return None
            fd, tmp = tempfile.mkstemp(suffix=ext)
            with os.fdopen(fd, "wb") as fp:
                fp.write(resp.content)
            return tmp
        except Exception:
            return None

    return url if os.path.exists(url) else None


def resolve_part_content(part: dict) -> bytes | str | None:
    """解析内容片段的内容。

    返回解码后的字节数据（图像、音频、视频、文件）或文本字符串（文本片段），
    无法解析时返回 None。

    Resolve the content of a content part.
    Returns decoded bytes (image/audio/video/file) or text string (text part).
    """
    ptype = part.get("type", "")
    if ptype == "text":
        text = part.get("text", "")
        return text if isinstance(text, str) else None

    # image_url, audio_url, video_url, file_url
    url_field = ptype.replace("_url", "") + "_url" if ptype.endswith("_url") else ptype
    spec = part.get(url_field, {})
    if isinstance(spec, dict):
        url = spec.get("url", "")
    else:
        url = str(spec) if spec else ""

    if not url:
        return None

    # data: URI → direct decode
    if isinstance(url, str) and url.startswith("data:"):
        try:
            import base64
            _, b64 = url.split(",", 1)
            return base64.b64decode(b64)
        except Exception:
            return None

    # http(s):// → download
    if isinstance(url, str) and url.startswith("http"):
        try:
            import httpx
            resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            if resp.status_code < 400:
                return resp.content
        except Exception:
            pass

    # file:// or bare path
    path = resolve_part_to_path(part)
    if path:
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            pass

    return None


def detect_part_mime(part: dict) -> str:
    """从内容片段推断 MIME 类型。Infer MIME type from a content part."""
    ptype = part.get("type", "")
    if ptype == "text":
        return "text/plain"
    if ptype == "image_url":
        spec = part.get("image_url", {})
        if isinstance(spec, dict):
            url = spec.get("url", "")
            if isinstance(url, str) and url.startswith("data:"):
                mime = url.split(":")[1].split(";")[0] if ":" in url else "image/png"
                return mime
        return "image/png"
    if ptype == "audio_url":
        spec = part.get("audio_url", {})
        if isinstance(spec, dict):
            fmt = spec.get("format")
            if fmt:
                return f"audio/{fmt}"
        return "audio/wav"
    if ptype == "video_url":
        return "video/mp4"
    if ptype == "file_url":
        spec = part.get("file_url", {})
        if isinstance(spec, dict):
            mt = spec.get("mime_type")
            if mt:
                return mt
        return "application/octet-stream"
    return "application/octet-stream"


@dataclass
class ChatMessage:
    """聊天消息数据结构。Chat message data structure."""
    role: str
    content: Content
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list | None = None

    def to_dict(self) -> dict:
        """转换为 OpenAI 兼容的字典格式。Convert to OpenAI-compatible dict."""
        out: dict = {"role": self.role, "content": self.content}
        if self.name:
            out["name"] = self.name
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            out["tool_calls"] = self.tool_calls
        return out

    @property
    def text_content(self) -> str:
        """尽力从可能的多模态内容中提取纯文本。

        当 ``content`` 为字符串时直接返回；为列表时拼接所有 ``type == "text"`` 的片段；
        非文本片段会被忽略。

        Best-effort extraction of plain text from possibly-multimodal content.

        Returns the string as-is when ``content`` is a string; when it's a list of parts,
        concatenates the ``text`` fields of every ``{"type": "text"}`` part. Non-text parts are skipped.
        """
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts: list[str] = []
            for p in self.content:
                if isinstance(p, dict) and p.get("type") == "text":
                    t = p.get("text", "")
                    if isinstance(t, str):
                        parts.append(t)
            return "".join(parts)
        return ""


@dataclass
class GenerationParams:
    """生成参数。Generation parameters."""
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int | None = None
    stop: Sequence[str] | None = None
    n: int = 1


@dataclass
class ChatChoice:
    """聊天完成的单个选项。Single choice in a chat completion."""
    index: int = 0
    message: object = None
    delta: object = None
    finish_reason: str | None = None


@dataclass
class ChatUsage:
    """Token 使用统计。Token usage statistics."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatChunk:
    """流式聊天的单个增量块。Single streaming chat chunk."""
    id: str
    model: str
    created: int
    choices: list = field(default_factory=list)
    usage: ChatUsage | None = None
    backend: str = ""


class ChatBackend:
    """聊天后端抽象基类。Abstract base for chat backends."""
    name: str = ""
    def is_available(self) -> bool: return True
    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None: ...
    def unload(self) -> None: ...
    def is_loaded(self) -> bool: return False
    def chat(self, messages, params, *, stream: bool = False, abort_signal=None): ...


class EmbeddingBackend:
    """嵌入向量后端抽象基类。Abstract base for embedding backends."""
    name: str = ""
    def is_available(self) -> bool: return True
    def load(self, model_path, **kwargs) -> None: ...
    def embed(self, texts, *, model_id: str | None = None) -> list: ...


class TTSBackend:
    """语音合成后端抽象基类。Abstract base for TTS backends."""
    name: str = ""
    def is_available(self) -> bool: return True
    def load(self, model_path, **kwargs) -> None: ...
    def synth(self, text, *, voice, response_format: str = "mp3",
              speed: float = 1.0, emotion=None, voice_clone_ref=None,
              abort_signal=None) -> bytes: ...


class STTBackend:
    """语音识别后端抽象基类。Abstract base for STT backends."""
    name: str = ""
    def is_available(self) -> bool: return True
    def transcribe(self, audio, *, language=None, prompt=None,
                   response_format: str = "json"): ...


class ImageGenBackend:
    """图像生成后端抽象基类。Abstract base for image generation backends."""
    name: str = ""
    def is_available(self) -> bool: return True
    def generate(self, prompt, *, model_id, n: int = 1,
                 size: str = "1024x1024", negative_prompt=None,
                 seed=None, abort_signal=None) -> list: ...


class VideoGenBackend:
    """视频生成后端抽象基类。Abstract base for video generation backends."""
    name: str = ""
    def is_available(self) -> bool: return True
    def submit(self, prompt, *, model_id, input_reference=None,
               seconds: int = 4, size: str = "1280x720", fps: int = 24,
               seed=None, progress_callback=None, abort_signal=None) -> str: ...
    def poll(self, task_id: str) -> dict: ...


class VideoUnderstandingBackend:
    """视频理解后端抽象基类。Abstract base for video understanding backends.

    支持视频帧提取、关键画面分析、时序理解等。用于聊天中通过 ``video_url``
    内容片段理解视频内容。

    Supports video frame extraction, keyframe analysis, temporal understanding.
    Used for understanding video content via ``video_url`` content parts in chat.
    """
    name: str = ""
    def is_available(self) -> bool: return True
    def load(self, model_path, **kwargs) -> None: ...
    def unload(self) -> None: ...
    def is_loaded(self) -> bool: return False
    def understand(self, video, *, prompt: str = "",
                   fps: int = 1, max_frames: int = 10,
                   abort_signal=None) -> str: ...


class MultimodalBackend:
    """全模态理解后端抽象基类。

    统一的全模态理解后端，能够接受并处理文本、图像、音频、视频、文件等
    任意模态的输入，输出多模态理解结果。这是新一代多模态模型的原生接口
    （如 GPT-4o、Gemini 2.5、Claude 3.5 等），它们通过单一模型
    理解所有输入模态。

    与 ``ChatBackend`` 的关系：
    - ``ChatBackend`` 以聊天形式处理输入，仍然保留为兼容现有接口
    - ``MultimodalBackend`` 是新一代接口，直接支持多模态输入
    - 实现者可以选择实现其中一个或两个

    Unified multimodal understanding backend abstract base.

    Accepts and processes text, image, audio, video, file inputs of any modality
    and outputs multimodal understanding results. This is the native interface for
    next-generation multimodal models (e.g. GPT-4o, Gemini 2.5, Claude 3.5) that
    understand all input modalities through a single model.
    """
    name: str = ""
    def is_available(self) -> bool: return True
    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None: ...
    def unload(self) -> None: ...
    def is_loaded(self) -> bool: return False

    def understand(
        self,
        messages: Sequence,
        params,
        *,
        stream: bool = False,
        abort_signal=None,
    ):
        """接受任意模态的消息列表并返回理解结果。

        ``messages`` 可以是包含任何内容片段类型的列表，使用 OAI 兼容格式：
        ``{"type": "text", "text": ...}``,
        ``{"type": "image_url", "image_url": {"url": ...}}``,
        ``{"type": "audio_url", "audio_url": {"url": ...}}``,
        ``{"type": "video_url", "video_url": {"url": ...}}``,
        ``{"type": "file_url", "file_url": {"url": ..., "mime_type": ...}}``。

        Accept messages with any content part type and return understanding results.
        """
        ...

    # 以下可选方法供支持生成模态的后端覆盖
    # Optional methods for backends that also support generation modalities

    def generate_image(
        self, prompt: str, *, n: int = 1, size: str = "1024x1024",
        negative_prompt: str | None = None, seed: int | None = None,
        abort_signal=None,
    ) -> list[dict]:
        raise NotImplementedError

    def generate_audio(
        self, text: str, *, voice: str = "default",
        response_format: str = "mp3", speed: float = 1.0,
        abort_signal=None,
    ) -> bytes:
        raise NotImplementedError