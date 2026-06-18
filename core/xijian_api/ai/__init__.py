"""AI abstraction layer.

* :mod:`xijian_api.ai.base` — error types raised by backends.
* :mod:`xijian_api.ai.types` — dataclasses shared by all backends.
* :mod:`xijian_api.ai.registry` — backend selection (``mlx``, ``gguf``).
* :mod:`xijian_api.ai.backends.mlx` — Apple Silicon MLX backend.
* :mod:`xijian_api.ai.backends.gguf` — GGUF (llama.cpp) backend.

The routes never import a backend module directly; they always go
through :func:`xijian_api.ai.registry.get_chat_backend` and friends.
"""

from xijian_api.ai import base, types
from xijian_api.ai.registry import (
    get_chat_backend,
    get_embedding_backend,
    get_tts_backend,
    get_stt_backend,
    get_image_backend,
    get_video_backend,
    register_chat,
    register_embedding,
    register_tts,
    register_stt,
    register_image,
    register_video,
)

__all__ = [
    "base", "types",
    "get_chat_backend", "get_embedding_backend", "get_tts_backend",
    "get_stt_backend", "get_image_backend", "get_video_backend",
    "register_chat", "register_embedding", "register_tts",
    "register_stt", "register_image", "register_video",
]
