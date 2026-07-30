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