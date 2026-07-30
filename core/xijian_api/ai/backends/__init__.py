"""AI 后端实现包。

所有后端模块在此包下根据引擎名称按子包组织。每个后端通过
:mod:`xijian_api.ai.registry` 中的 ``register_*`` 装饰器注册自身，
实现 :mod:`xijian_api.ai.types` 中的抽象基类。

支持的后端：
* ``mlx`` — Apple Silicon MLX（本地推理）
* ``gguf`` — GGUF / llama.cpp（本地推理）
* ``openai`` — OpenAI 兼容远程 API
* ``mock`` — 模拟后端（测试用）

AI backend implementation package.

All backend modules live under this package, organized by engine name
in sub-packages.  Each backend registers itself via the ``register_*``
decorators from :mod:`xijian_api.ai.registry` and implements the
abstract base classes from :mod:`xijian_api.ai.types`.

Supported backends:
* ``mlx`` — Apple Silicon MLX (local inference)
* ``gguf`` — GGUF / llama.cpp (local inference)
* ``openai`` — OpenAI-compatible remote API
* ``mock`` — mock backend (for tests)
"""
