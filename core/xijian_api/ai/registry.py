"""Backend registry and selection logic.

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
)
from xijian_api.ai.base import BackendUnavailable


# ---------------------------------------------------------------------------
# Internal registries
# ---------------------------------------------------------------------------


_chat_backends: dict[str, type] = {}
_embedding_backends: dict[str, type] = {}
_tts_backends: dict[str, type] = {}
_stt_backends: dict[str, type] = {}
_image_backends: dict[str, type] = {}
_video_backends: dict[str, type] = {}


def register_chat(name: str) -> Callable:
    def deco(cls: type) -> type:
        _chat_backends[name] = cls
        return cls
    return deco


def register_embedding(name: str) -> Callable:
    def deco(cls: type) -> type:
        _embedding_backends[name] = cls
        return cls
    return deco


def register_tts(name: str) -> Callable:
    def deco(cls: type) -> type:
        _tts_backends[name] = cls
        return cls
    return deco


def register_stt(name: str) -> Callable:
    def deco(cls: type) -> type:
        _stt_backends[name] = cls
        return cls
    return deco


def register_image(name: str) -> Callable:
    def deco(cls: type) -> type:
        _image_backends[name] = cls
        return cls
    return deco


def register_video(name: str) -> Callable:
    def deco(cls: type) -> type:
        _video_backends[name] = cls
        return cls
    return deco


def available_backends() -> dict[str, list[str]]:
    """Return the names of every backend that has registered and reports available."""
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
# Lazy import + fallback logic
# ---------------------------------------------------------------------------


_BUILTIN_IMPORTS: dict[str, dict[str, str]] = {
    "chat": {
        "mlx": "xijian_api.ai.backends.mlx.chat",
        "gguf": "xijian_api.ai.backends.gguf.chat",
        # The mock backend is for tests and local development only; it
        # never loads real weights and is always ``is_available()``.
        "mock": "xijian_api.ai.backends.mock.chat",
    },
    "embeddings": {
        "mlx": "xijian_api.ai.backends.mlx.embedding",
        "gguf": "xijian_api.ai.backends.gguf.embedding",
    },
    "tts": {
        "mlx": "xijian_api.ai.backends.mlx.tts",
        "gguf": "xijian_api.ai.backends.gguf.tts",
    },
    "stt": {
        "mlx": "xijian_api.ai.backends.mlx.stt",
        "gguf": "xijian_api.ai.backends.gguf.stt",
    },
    "image": {
        "mlx": "xijian_api.ai.backends.mlx.image",
        "gguf": "xijian_api.ai.backends.gguf.image",
    },
    "video": {
        "mlx": "xijian_api.ai.backends.mlx.video",
        "gguf": "xijian_api.ai.backends.gguf.video",
    },
}


def _ensure_loaded(task: str, name: str) -> None:
    """Import the backend module on first use; no-op if already registered."""
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
        # Backend missing / failing to import — leave registry empty so
        # the caller can fall back to the next option.
        sys.stderr.write(
            f"[xijian-api] backend {task}:{name} unavailable: {exc}\n"
        )


def _pick(task: str, requested: str, fallbacks: tuple[str, ...]):
    """Try each backend name in order; return the first usable instance."""
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
# Public helpers
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


__all__ = [
    "register_chat", "register_embedding", "register_tts",
    "register_stt", "register_image", "register_video",
    "available_backends",
    "get_chat_backend", "get_embedding_backend", "get_tts_backend",
    "get_stt_backend", "get_image_backend", "get_video_backend",
]
