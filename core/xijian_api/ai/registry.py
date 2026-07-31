"""后端注册表与选择逻辑。

每个后端模块（``xijian_api.ai.backends.<name>``）在导入时通过下方的
``register_*`` 助手自行注册。选择可通过 ``Config.backends.<task>``
按任务配置。

当请求的后端未安装（如 Linux 上的 ``mlx`` 或 macOS 上的 ``llama-cpp``）时，
加载器返回 ``None``，调用者回退到下一个配置选项。

Backend registry and selection logic.

Each backend module (``xijian_api.ai.backends.<name>``) registers
itself on import via the ``register_*`` helpers below.  Selection is
configurable per task through ``Config.backends.<task>``.

When a requested backend is not installed (e.g. ``mlx`` on Linux or
``llama-cpp`` on macOS), the loader returns ``None`` and the caller
falls back to the next configured option.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Callable

from xijian_api.ai.types import (
    ChatBackend,
    EmbeddingBackend,
    TTSBackend,
    STTBackend,
    ImageGenBackend,
    VideoGenBackend,
    VideoUnderstandingBackend,
    MultimodalBackend,
)
from xijian_api.ai.base import BackendUnavailable


# ---------------------------------------------------------------------------
# Internal registries / 内部注册表
# ---------------------------------------------------------------------------


_chat_backends: dict[str, type] = {}
_embedding_backends: dict[str, type] = {}
_tts_backends: dict[str, type] = {}
_stt_backends: dict[str, type] = {}
_image_backends: dict[str, type] = {}
_video_backends: dict[str, type] = {}
_video_understanding_backends: dict[str, type] = {}
_multimodal_backends: dict[str, type] = {}


def register_chat(name: str) -> Callable:
    """注册聊天后端装饰器。Register a chat backend decorator."""
    def deco(cls: type) -> type:
        _chat_backends[name] = cls
        return cls
    return deco


def register_embedding(name: str) -> Callable:
    """注册嵌入后端装饰器。Register an embedding backend decorator."""
    def deco(cls: type) -> type:
        _embedding_backends[name] = cls
        return cls
    return deco


def register_tts(name: str) -> Callable:
    """注册 TTS 后端装饰器。Register a TTS backend decorator."""
    def deco(cls: type) -> type:
        _tts_backends[name] = cls
        return cls
    return deco


def register_stt(name: str) -> Callable:
    """注册 STT 后端装饰器。Register an STT backend decorator."""
    def deco(cls: type) -> type:
        _stt_backends[name] = cls
        return cls
    return deco


def register_image(name: str) -> Callable:
    """注册图像后端装饰器。Register an image backend decorator."""
    def deco(cls: type) -> type:
        _image_backends[name] = cls
        return cls
    return deco


def register_video(name: str) -> Callable:
    """注册视频后端装饰器。Register a video backend decorator."""
    def deco(cls: type) -> type:
        _video_backends[name] = cls
        return cls
    return deco


def register_video_understanding(name: str) -> Callable:
    """注册视频理解后端装饰器。Register a video understanding backend decorator."""
    def deco(cls: type) -> type:
        _video_understanding_backends[name] = cls
        return cls
    return deco


def register_multimodal(name: str) -> Callable:
    """注册全模态后端装饰器。Register a multimodal backend decorator."""
    def deco(cls: type) -> type:
        _multimodal_backends[name] = cls
        return cls
    return deco


def available_backends() -> dict[str, list[str]]:
    """返回每个已注册并报告可用的后端名称。

    Return the names of every backend that has registered and reports available.
    """
    out: dict[str, list[str]] = {}
    for kind, table in (
        ("chat", _chat_backends),
        ("embeddings", _embedding_backends),
        ("tts", _tts_backends),
        ("stt", _stt_backends),
        ("image", _image_backends),
        ("video", _video_backends),
        ("video_understanding", _video_understanding_backends),
        ("multimodal", _multimodal_backends),
    ):
        names = []
        for name, cls in table.items():
            try:
                inst = cls()
                if inst.is_available():
                    names.append(name)
            except Exception:
                continue
        out[kind] = names
    return out


# ---------------------------------------------------------------------------
# Lazy import + fallback logic / 惰性导入与回退逻辑
# ---------------------------------------------------------------------------


_BUILTIN_IMPORTS: dict[str, dict[str, str]] = {
    "chat": {
        "mlx": "xijian_api.ai.backends.mlx.chat",
        "gguf": "xijian_api.ai.backends.gguf.chat",
        "openai": "xijian_api.ai.backends.openai.chat",
        # The mock backend is for tests and local development only; it
        # never loads real weights and is always ``is_available()``.
        # mock 后端仅用于测试和本地开发；它从不加载真实权重且始终 ``is_available()``。
        "mock": "xijian_api.ai.backends.mock.chat",
    },
    "embeddings": {
        "mlx": "xijian_api.ai.backends.mlx.embedding",
        "gguf": "xijian_api.ai.backends.gguf.embedding",
        "openai": "xijian_api.ai.backends.openai.embedding",
    },
    "tts": {
        "mlx": "xijian_api.ai.backends.mlx.tts",
        "gguf": "xijian_api.ai.backends.gguf.tts",
        "openai": "xijian_api.ai.backends.openai.tts",
    },
    "stt": {
        "mlx": "xijian_api.ai.backends.mlx.stt",
        "gguf": "xijian_api.ai.backends.gguf.stt",
        "openai": "xijian_api.ai.backends.openai.stt",
    },
    "image": {
        "mlx": "xijian_api.ai.backends.mlx.image",
        "gguf": "xijian_api.ai.backends.gguf.image",
        "openai": "xijian_api.ai.backends.openai.image",
    },
    "video": {
        "mlx": "xijian_api.ai.backends.mlx.video",
        "gguf": "xijian_api.ai.backends.gguf.video",
        "openai": "xijian_api.ai.backends.openai.video",
    },
    "video_understanding": {
        "openai": "xijian_api.ai.backends.openai.video_understanding",
        # The mock backend is for tests and local development only; it
        # never loads real weights and is always ``is_available()``.
        # mock 后端仅用于测试和本地开发；它从不加载真实权重且始终 ``is_available()``。
        "mock": "xijian_api.ai.backends.mock.multimodal",
    },
    "multimodal": {
        "openai": "xijian_api.ai.backends.openai.multimodal",
        "mlx": "xijian_api.ai.backends.mlx.multimodal",
        "gguf": "xijian_api.ai.backends.gguf.multimodal",
        # The mock backend is for tests and local development only; it
        # never loads real weights and is always ``is_available()``.
        # mock 后端仅用于测试和本地开发；它从不加载真实权重且始终 ``is_available()``。
        "mock": "xijian_api.ai.backends.mock.multimodal",
    },
}


def _ensure_loaded(task: str, name: str) -> None:
    """首次使用时导入后端模块；如已注册则无操作。

    从 ``_BUILTIN_IMPORTS`` 查找模块名并导入。导入失败时不抛出异常，
    而是将错误写入 stderr，让调用者能回退到下一选项。

    Import the backend module on first use; no-op if already registered.
    Falls back to stderr logging on failure so the caller can try the next option.
    """
    table = {
        "chat": _chat_backends,
        "embeddings": _embedding_backends,
        "tts": _tts_backends,
        "stt": _stt_backends,
        "image": _image_backends,
        "video": _video_backends,
        "video_understanding": _video_understanding_backends,
        "multimodal": _multimodal_backends,
    }[task]
    if name in table:
        return
    module_name = _BUILTIN_IMPORTS.get(task, {}).get(name)
    if module_name is None:
        return
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        # Backend missing / failing to import — leave registry empty so
        # the caller can fall back to the next option.
        # 后端缺失/导入失败 —— 让注册表保持空，调用者可回退到下一选项。
        sys.stderr.write(
            f"[xijian-api] backend {task}:{name} unavailable: {exc}\n"
        )


def _pick(task: str, requested: str, fallbacks: tuple[str, ...]):
    """按顺序尝试每个后端名称；返回第一个可用的实例。

    先尝试请求的后端，再试回退列表。若全不可用则抛出
    :class:`BackendUnavailable`。

    Try each backend name in order; return the first usable instance.
    Raises :class:`BackendUnavailable` when none is available.
    """
    table = {
        "chat": _chat_backends,
        "embeddings": _embedding_backends,
        "tts": _tts_backends,
        "stt": _stt_backends,
        "image": _image_backends,
        "video": _video_backends,
        "video_understanding": _video_understanding_backends,
        "multimodal": _multimodal_backends,
    }[task]
    cls_type = {
        "chat": ChatBackend,
        "embeddings": EmbeddingBackend,
        "tts": TTSBackend,
        "stt": STTBackend,
        "image": ImageGenBackend,
        "video": VideoGenBackend,
        "video_understanding": VideoUnderstandingBackend,
        "multimodal": MultimodalBackend,
    }[task]

    tried: list[str] = []
    for candidate in (requested, *fallbacks):
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        _ensure_loaded(task, candidate)
        cls = table.get(candidate)
        if cls is None:
            continue
        try:
            inst = cls()
            if not inst.is_available():
                continue
            inst.name = candidate
            return inst
        except Exception:
            continue
    raise BackendUnavailable(
        f"no usable backend for {task} (tried: {tried})",
        code="backend_unavailable",
    )


# ---------------------------------------------------------------------------
# Public helpers / 公共助手函数
# ---------------------------------------------------------------------------


def get_chat_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> ChatBackend:
    """获取聊天后端实例。优先使用环境变量 ``XIJIAN_AI_BACKEND_CHAT``。

    Get a chat backend instance. Prefers env var ``XIJIAN_AI_BACKEND_CHAT``.
    """
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_CHAT", "mlx")
    return _pick("chat", requested, fallbacks)


def get_embedding_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> EmbeddingBackend:
    """获取嵌入后端实例。优先使用环境变量 ``XIJIAN_AI_BACKEND_EMBED``。

    Get an embedding backend instance. Prefers env var ``XIJIAN_AI_BACKEND_EMBED``.
    """
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_EMBED", "mlx")
    return _pick("embeddings", requested, fallbacks)


def get_tts_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> TTSBackend:
    """获取 TTS 后端实例。优先使用环境变量 ``XIJIAN_AI_BACKEND_TTS``。

    Get a TTS backend instance. Prefers env var ``XIJIAN_AI_BACKEND_TTS``.
    """
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_TTS", "mlx")
    return _pick("tts", requested, fallbacks)


def get_stt_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> STTBackend:
    """获取 STT 后端实例。优先使用环境变量 ``XIJIAN_AI_BACKEND_STT``。

    Get an STT backend instance. Prefers env var ``XIJIAN_AI_BACKEND_STT``.
    """
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_STT", "mlx")
    return _pick("stt", requested, fallbacks)


def get_image_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> ImageGenBackend:
    """获取图像生成后端实例。优先使用环境变量 ``XIJIAN_AI_BACKEND_IMAGE``。

    Get an image generation backend instance. Prefers env var ``XIJIAN_AI_BACKEND_IMAGE``.
    """
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_IMAGE", "mlx")
    return _pick("image", requested, fallbacks)


def get_video_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> VideoGenBackend:
    """获取视频生成后端实例。优先使用环境变量 ``XIJIAN_AI_BACKEND_VIDEO``。

    Get a video generation backend instance. Prefers env var ``XIJIAN_AI_BACKEND_VIDEO``.
    """
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_VIDEO", "mlx")
    return _pick("video", requested, fallbacks)


def get_video_understanding_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> VideoUnderstandingBackend:
    """获取视频理解后端实例。优先使用环境变量 ``XIJIAN_AI_BACKEND_VIDEO_UNDERSTAND``。

    Get a video understanding backend instance. Prefers env var ``XIJIAN_AI_BACKEND_VIDEO_UNDERSTAND``.
    """
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_VIDEO_UNDERSTAND", "openai")
    return _pick("video_understanding", requested, fallbacks)


def get_multimodal_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> MultimodalBackend:
    """获取全模态理解后端实例。优先使用环境变量 ``XIJIAN_AI_BACKEND_MULTIMODAL``。

    Get a multimodal understanding backend instance. Prefers env var ``XIJIAN_AI_BACKEND_MULTIMODAL``.

    全模态后端是新一代的统一理解接口。对于支持多模态的模型（如 GPT-4o、Gemini 2.5），
    它提供原生的多模态输入输出。对于本地模型，它可能通过组合 VLM + STT 等方式实现。

    The multimodal backend is the next-generation unified understanding interface. For
    multimodal models (GPT-4o, Gemini 2.5), it provides native multimodal I/O. For local
    models, it may composite VLM + STT etc.
    """
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_MULTIMODAL", "openai")
    return _pick("multimodal", requested, fallbacks)


__all__ = [
    "register_chat", "register_embedding", "register_tts",
    "register_stt", "register_image", "register_video",
    "register_video_understanding", "register_multimodal",
    "available_backends",
    "get_chat_backend", "get_embedding_backend", "get_tts_backend",
    "get_stt_backend", "get_image_backend", "get_video_backend",
    "get_video_understanding_backend", "get_multimodal_backend",
]