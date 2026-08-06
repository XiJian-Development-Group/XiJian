"""DevKit AI 后端注册表 —— 改编自 core/xijian_api/ai/registry.py。

本模块为 MLX / GGUF 后端提供懒加载 + 回退逻辑。
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Callable

from devkit.ai.base import (
    BackendError,
    BackendUnavailable,
    ModelNotFound,
    ModelNotLoaded,
)
from devkit.ai.types import (
    ChatBackend,
    EmbeddingBackend,
    ImageGenBackend,
    STTBackend,
    TTSBackend,
    VideoGenBackend,
)

# ---------------------------------------------------------------------------
# 注册表（由装饰器填充）
# ---------------------------------------------------------------------------

_chat_backends: dict[str, type[ChatBackend]] = {}
_embedding_backends: dict[str, type[EmbeddingBackend]] = {}
_tts_backends: dict[str, type[TTSBackend]] = {}
_stt_backends: dict[str, type[STTBackend]] = {}
_image_backends: dict[str, type[ImageGenBackend]] = {}
_video_backends: dict[str, type[VideoGenBackend]] = {}


def register_chat(name: str) -> Callable[[type[ChatBackend]], type[ChatBackend]]:
    def deco(cls: type[ChatBackend]) -> type[ChatBackend]:
        _chat_backends[name] = cls
        return cls
    return deco


def register_embedding(name: str) -> Callable[[type[EmbeddingBackend]], type[EmbeddingBackend]]:
    def deco(cls: type[EmbeddingBackend]) -> type[EmbeddingBackend]:
        _embedding_backends[name] = cls
        return cls
    return deco


def register_tts(name: str) -> Callable[[type[TTSBackend]], type[TTSBackend]]:
    def deco(cls: type[TTSBackend]) -> type[TTSBackend]:
        _tts_backends[name] = cls
        return cls
    return deco


def register_stt(name: str) -> Callable[[type[STTBackend]], type[STTBackend]]:
    def deco(cls: type[STTBackend]) -> type[STTBackend]:
        _stt_backends[name] = cls
        return cls
    return deco


def register_image(name: str) -> Callable[[type[ImageGenBackend]], type[ImageGenBackend]]:
    def deco(cls: type[ImageGenBackend]) -> type[ImageGenBackend]:
        _image_backends[name] = cls
        return cls
    return deco


def register_video(name: str) -> Callable[[type[VideoGenBackend]], type[VideoGenBackend]]:
    def deco(cls: type[VideoGenBackend]) -> type[VideoGenBackend]:
        _video_backends[name] = cls
        return cls
    return deco


def available_backends() -> dict[str, list[str]]:
    """返回每个已注册且报告可用的后端的名称。"""
    out: dict[str, list[str]] = {}
    for kind, table in (
        ("chat", _chat_backends),
        ("embeddings", _embedding_backends),
        ("tts", _tts_backends),
        ("stt", _stt_backends),
        ("image", _image_backends),
        ("video", _video_backends),
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
# 懒加载 + 回退逻辑
# ---------------------------------------------------------------------------

_BUILTIN_IMPORTS: dict[str, dict[str, str]] = {
    "chat": {
        "mlx": "devkit.ai.backends.mlx.chat",
        "gguf": "devkit.ai.backends.gguf.chat",
        "mock": "devkit.ai.backends.mock.chat",
    },
    "embeddings": {
        "mlx": "devkit.ai.backends.mlx.embedding",
        "gguf": "devkit.ai.backends.gguf.embedding",
    },
    "tts": {
        "mlx": "devkit.ai.backends.mlx.tts",
        "gguf": "devkit.ai.backends.gguf.tts",
    },
    "stt": {
        "mlx": "devkit.ai.backends.mlx.stt",
        "gguf": "devkit.ai.backends.gguf.stt",
    },
    "image": {
        "mlx": "devkit.ai.backends.mlx.image",
        "gguf": "devkit.ai.backends.gguf.image",
    },
    "video": {
        "mlx": "devkit.ai.backends.mlx.video",
        "gguf": "devkit.ai.backends.gguf.video",
    },
}


def _ensure_loaded(task: str, name: str) -> None:
    """在首次使用时导入后端模块；如果已注册则不做任何操作。"""
    table = {
        "chat": _chat_backends,
        "embeddings": _embedding_backends,
        "tts": _tts_backends,
        "stt": _stt_backends,
        "image": _image_backends,
        "video": _video_backends,
    }[task]
    if name in table:
        return
    module_name = _BUILTIN_IMPORTS.get(task, {}).get(name)
    if module_name is None:
        return
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        sys.stderr.write(
            f"[devkit-ai] backend {task}:{name} unavailable: {exc}\n"
        )


def _pick(task: str, requested: str, fallbacks: tuple[str, ...]):
    """按顺序依次尝试每个后端名称；返回第一个可用实例。"""
    table = {
        "chat": _chat_backends,
        "embeddings": _embedding_backends,
        "tts": _tts_backends,
        "stt": _stt_backends,
        "image": _image_backends,
        "video": _video_backends,
    }[task]
    cls_type = {
        "chat": ChatBackend,
        "embeddings": EmbeddingBackend,
        "tts": TTSBackend,
        "stt": STTBackend,
        "image": ImageGenBackend,
        "video": VideoGenBackend,
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
# 公共辅助函数
# ---------------------------------------------------------------------------


def get_chat_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> ChatBackend:
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_CHAT", "mlx")
    return _pick("chat", requested, fallbacks)


def get_embedding_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> EmbeddingBackend:
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_EMBED", "mlx")
    return _pick("embeddings", requested, fallbacks)


def get_tts_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> TTSBackend:
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_TTS", "mlx")
    return _pick("tts", requested, fallbacks)


def get_stt_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> STTBackend:
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_STT", "mlx")
    return _pick("stt", requested, fallbacks)


def get_image_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> ImageGenBackend:
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_IMAGE", "mlx")
    return _pick("image", requested, fallbacks)


def get_video_backend(name: str | None = None, fallbacks: tuple[str, ...] = ()) -> VideoGenBackend:
    requested = name or os.environ.get("XIJIAN_AI_BACKEND_VIDEO", "mlx")
    return _pick("video", requested, fallbacks)


# 为公共辅助函数导入 os
import os

__all__ = [
    "register_chat", "register_embedding", "register_tts",
    "register_stt", "register_image", "register_video",
    "available_backends",
    "get_chat_backend", "get_embedding_backend", "get_tts_backend",
    "get_stt_backend", "get_image_backend", "get_video_backend",
]