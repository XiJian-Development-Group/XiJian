"""Tests for the mock chat backend.

The mock backend is the linchpin that lets the test suite + local
dev run without ``mlx`` or ``llama_cpp`` installed and without a real
checkpoint on disk.  These tests pin its contract:

* ``is_available()`` is always ``True``.
* ``load()`` accepts any path; ``is_loaded()`` reflects it.
* Blocking and streaming outputs both yield :class:`ChatChunk` with
  the OAI conventions: role-only first chunk, then ``content``
  deltas, then a final ``finish_reason`` + ``usage`` chunk.
* :class:`AbortSignal` is honoured — aborts between emissions
  produce a final ``finish_reason="abort"`` chunk.
"""

from __future__ import annotations

import pytest

from xijian_api.ai.base import ModelNotLoaded
from xijian_api.ai.backends.mock.chat import MockChatBackend
from xijian_api.ai.types import ChatMessage, GenerationParams
from xijian_api.errors import GenerationAborted


def _msg(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=role, content=content)


def test_is_available_always_true():
    backend = MockChatBackend()
    assert backend.is_available() is True


def test_load_marks_loaded_and_accepts_arbitrary_path():
    backend = MockChatBackend()
    assert backend.is_loaded() is False
    backend.load("/totally/fake/path/Qwen-4bit", context_length=4096)
    assert backend.is_loaded() is True
    backend.unload()
    assert backend.is_loaded() is False


def test_chat_raises_when_not_loaded():
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
    return [chunk for chunk in gen]


def test_blocking_chat_yields_one_chunk_with_full_content():
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
    assert len(chunks) >= 3
    # First chunk is role-only.
    assert chunks[0].choices[0].delta == {"role": "assistant"}
    # Final chunk carries finish_reason and a non-None usage.
    last = chunks[-1]
    assert last.choices[0].finish_reason == "stop"
    assert last.usage is not None
    # Middle chunks carry content deltas.
    body = "".join(
        c.choices[0].delta.get("content", "")
        for c in chunks[1:-1]
        if c.choices[0].delta
    )
    assert "ping" in body or body  # either echoes the user msg or the mock tail
    # All chunks tag the backend.
    assert all(c.backend == "mock" for c in chunks)


def test_streaming_chat_respects_abort_signal():
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
    final = chunks[-1]
    assert final.choices[0].finish_reason == "abort"


def test_mock_is_registered_as_chat_backend():
    """The registry helper resolves ``mock`` without touching the network."""
    from xijian_api.ai.registry import get_chat_backend

    backend = get_chat_backend(name="mock", fallbacks=())
    assert isinstance(backend, MockChatBackend)
    assert backend.is_available() is True
