"""AI 抽象层。

* :mod:`xijian_api.ai.base` — 后端抛出的错误类型。
* :mod:`xijian_api.ai.types` — 所有后端共享的数据类。
* :mod:`xijian_api.ai.registry` — 后端选择逻辑（``mlx``, ``gguf``, ``openai``, ``mock``）。
* :mod:`xijian_api.ai.backends.mlx` — Apple Silicon MLX 后端。
* :mod:`xijian_api.ai.backends.gguf` — GGUF（llama.cpp）后端。
* :mod:`xijian_api.ai.backends.openai` — OpenAI 兼容远程后端。
* :mod:`xijian_api.ai.backends.mock` — 测试用模拟后端。

路由代码从不直接导入后端模块，总是通过
:func:`xijian_api.ai.registry.get_chat_backend` 及其兄弟函数来获取。

AI abstraction layer.

* :mod:`xijian_api.ai.base` — error types raised by backends.
* :mod:`xijian_api.ai.types` — dataclasses shared by all backends.
* :mod:`xijian_api.ai.registry` — backend selection (``mlx``, ``gguf``, ``openai``, ``mock``).
* :mod:`xijian_api.ai.backends.mlx` — Apple Silicon MLX backend.
* :mod:`xijian_api.ai.backends.gguf` — GGUF (llama.cpp) backend.
* :mod:`xijian_api.ai.backends.openai` — OpenAI-compatible remote backend.
* :mod:`xijian_api.ai.backends.mock` — mock backend for tests.

The routes never import a backend module directly; they always go
through :func:`xijian_api.ai.registry.get_chat_backend` and friends.
"""

from xijian_api.ai import base, types
from xijian_api.ai.model_registry import (
    LoadedModel,
    ModelRegistry,
    get_registry,
)
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
    "LoadedModel", "ModelRegistry", "get_registry",
    "get_chat_backend", "get_embedding_backend", "get_tts_backend",
    "get_stt_backend", "get_image_backend", "get_video_backend",
    "register_chat", "register_embedding", "register_tts",
    "register_stt", "register_image", "register_video",
]