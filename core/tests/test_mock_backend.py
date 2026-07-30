"""Tests for the mock chat backend.
(模拟聊天后端的测试。)

The mock backend is the linchpin that lets the test suite + local
dev run without ``mlx`` or ``llama_cpp`` installed and without a real
checkpoint on disk.  These tests pin its contract:
(模拟后端是让测试套件和本地开发无需安装 ``mlx`` 或 ``llama_cpp``、
也无需磁盘上真实检查点的关键。这些测试固定其协议：)

* ``is_available()`` is always ``True``.
* ``load()`` accepts any path; ``is_loaded()`` reflects it.
* Blocking and streaming outputs both yield :class:`ChatChunk` with
  the OAI conventions: role-only first chunk, then ``content``
  deltas, then a final ``finish_reason`` + ``usage`` chunk.
* (阻塞和流式输出都产生遵循 OAI 约定的 :class:`ChatChunk`：
  仅角色的第一个块，然后是 ``content`` 增量，最后是
  ``finish_reason`` + ``usage`` 块。)
* :class:`AbortSignal` is honoured — aborts between emissions
  produce a final ``finish_reason="abort"`` chunk.
* (:class:`AbortSignal` 被尊重 — 发射之间的中止产生
  最终的 ``finish_reason="abort"`` 块。)
"""

from __future__ import annotations

import pytest

from xijian_api.ai.base import ModelNotLoaded
from xijian_api.ai.backends.mock.chat import MockChatBackend
from xijian_api.ai.types import ChatMessage, GenerationParams
from xijian_api.errors import GenerationAborted


def _msg(role: str, content: str) -> ChatMessage:
    """Helper to create a ChatMessage.
    (创建 ChatMessage 的辅助函数。)
    """
    return ChatMessage(role=role, content=content)


def test_is_available_always_true():
    """is_available() always returns True for the mock backend.
    (模拟后端的 is_available() 始终返回 True。)
    """
    backend = MockChatBackend()
    assert backend.is_available() is True


def test_load_marks_loaded_and_accepts_arbitrary_path():
    """load() accepts any path and is_loaded() reflects state.
    (load() 接受任意路径，is_loaded() 反映加载状态。)
    """
    backend = MockChatBackend()
    assert backend.is_loaded() is False
    backend.load("/totally/fake/path/Qwen-4bit", context_length=4096)
    assert backend.is_loaded() is True
    backend.unload()
    assert backend.is_loaded() is False


def test_chat_raises_when_not_loaded():
    """chat() raises ModelNotLoaded when the model is not loaded.
    (模型未加载时 chat() 抛出 ModelNotLoaded。)
    """
    backend = MockChatBackend()
    with pytest.raises(ModelNotLoaded):
        list(
            backend.chat(
                [_msg("user", "hi")],
                GenerationParams(),
                stream=False,
            )
        )


def _consume(gen) -> list:
    """Helper to consume a generator into a list.
    (将生成器消费为列表的辅助函数。)
    """
    return [chunk for chunk in gen]


def test_blocking_chat_yields_one_chunk_with_full_content():
    """Blocking chat yields single chunk with full content.
    (阻塞聊天产生单个包含完整内容的块。)
    """
    backend = MockChatBackend()
    backend.load("/fake/model")
    chunks = _consume(
        backend.chat(
            [_msg("user", "hello world")],
            GenerationParams(max_tokens=8),
            stream=False,
        )
    )
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.choices[0].delta["role"] == "assistant"
    assert "hello world" in chunk.choices[0].delta["content"]
    assert chunk.choices[0].finish_reason == "stop"
    assert chunk.usage is not None
    assert chunk.usage.completion_tokens > 0
    assert chunk.backend == "mock"


def test_streaming_chat_yields_role_then_content_then_finish():
    """Streaming chat yields role, then content deltas, then finish.
    (流式聊天产生角色、内容增量、然后完成块。)
    """
    backend = MockChatBackend()
    backend.load("/fake/model")
    chunks = _consume(
        backend.chat(
            [_msg("user", "ping")],
            GenerationParams(max_tokens=4),
            stream=True,
        )
    )
    # role chunk + N content chunks (per-character) + final chunk
    # (角色块 + N 个内容块（逐字符）+ 最终块)
    assert len(chunks) >= 3
    # First chunk is role-only.
    # (第一个块仅包含角色。)
    assert chunks[0].choices[0].delta == {"role": "assistant"}
    # Final chunk carries finish_reason and a non-None usage.
    # (最终块携带 finish_reason 和非 None 的 usage。)
    last = chunks[-1]
    assert last.choices[0].finish_reason == "stop"
    assert last.usage is not None
    # Middle chunks carry content deltas.
    # (中间块携带内容增量。)
    body = "".join(
        c.choices[0].delta.get("content", "")
        for c in chunks[1:-1]
        if c.choices[0].delta
    )
    assert "ping" in body or body  # either echoes the user msg or the mock tail
    # All chunks tag the backend.
    # (所有块都标记后端类型。)
    assert all(c.backend == "mock" for c in chunks)


def test_streaming_chat_respects_abort_signal():
    """Streaming chat honours AbortSignal mid-generation.
    (流式聊天在中途生成时尊重 AbortSignal。)
    """
    backend = MockChatBackend()
    backend.load("/fake/model")

    class _Signal:
        def __init__(self) -> None:
            self.calls = 0

        def raise_if_aborted(self) -> None:
            self.calls += 1
            if self.calls >= 3:
                raise GenerationAborted("client cancel")

    signal = _Signal()
    chunks = _consume(
        backend.chat(
            [_msg("user", "go")],
            GenerationParams(max_tokens=32),
            stream=True,
            abort_signal=signal,
        )
    )
    # Final chunk must mark the abort so the route can serialise a
    # proper finish_reason instead of an OAI ``stop``.
    # (最终块必须标记中止，以便路由可以序列化正确的 finish_reason
    # 而不是 OAI ``stop``。)
    final = chunks[-1]
    assert final.choices[0].finish_reason == "abort"


def test_mock_is_registered_as_chat_backend():
    """The registry helper resolves ``mock`` without touching the network.
    (注册助手解析 ``mock`` 而不触及网络。)
    """
    from xijian_api.ai.registry import get_chat_backend

    backend = get_chat_backend(name="mock", fallbacks=())
    assert isinstance(backend, MockChatBackend)
    assert backend.is_available() is True
