"""AI types for the DevKit — copied & adapted from core/xijian_api/ai/types.py."""

from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class ChatMessage:
    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list | None = None

    def to_dict(self) -> dict:
        out: dict = {"role": self.role, "content": self.content}
        if self.name:
            out["name"] = self.name
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            out["tool_calls"] = self.tool_calls
        return out


@dataclass
class GenerationParams:
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int | None = None
    stop: Sequence[str] | None = None
    n: int = 1


@dataclass
class ChatChoice:
    index: int = 0
    message: object = None
    delta: object = None
    finish_reason: str | None = None


@dataclass
class ChatUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatChunk:
    id: str
    model: str
    created: int
    choices: list = field(default_factory=list)
    usage: ChatUsage | None = None
    backend: str = ""


class ChatBackend:
    name: str = ""
    def is_available(self) -> bool: return True
    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None: ...
    def unload(self) -> None: ...
    def is_loaded(self) -> bool: return False
    def chat(self, messages, params, *, stream: bool = False, abort_signal=None): ...


class EmbeddingBackend:
    name: str = ""
    def is_available(self) -> bool: return True
    def load(self, model_path, **kwargs) -> None: ...
    def embed(self, texts, *, model_id: str | None = None) -> list: ...


class TTSBackend:
    name: str = ""
    def is_available(self) -> bool: return True
    def load(self, model_path, **kwargs) -> None: ...
    def synth(self, text, *, voice, response_format: str = "mp3",
              speed: float = 1.0, emotion=None, voice_clone_ref=None,
              abort_signal=None) -> bytes: ...


class STTBackend:
    name: str = ""
    def is_available(self) -> bool: return True
    def transcribe(self, audio, *, language=None, prompt=None,
                   response_format: str = "json"): ...


class ImageGenBackend:
    name: str = ""
    def is_available(self) -> bool: return True
    def generate(self, prompt, *, model_id, n: int = 1,
                 size: str = "1024x1024", negative_prompt=None,
                 seed=None, abort_signal=None) -> list: ...


class VideoGenBackend:
    name: str = ""
    def is_available(self) -> bool: return True
    def submit(self, prompt, *, model_id, input_reference=None,
               seconds: int = 4, size: str = "1280x720", fps: int = 24,
               seed=None, progress_callback=None, abort_signal=None) -> str: ...
    def poll(self, task_id: str) -> dict: ...