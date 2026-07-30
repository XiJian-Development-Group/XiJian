"""Tests for the OpenAI-compatible remote backend.
(OpenAI 兼容远程后端的测试。)

These tests pin the contract of ``OpenAIChatBackend`` and the shared
HTTP client (``_client.py``) without making real network calls.  All
HTTP traffic is mocked via monkeypatching the transport functions.
(这些测试固定 ``OpenAIChatBackend`` 和共享 HTTP 客户端 (``_client.py``)
的契约，而不进行真实的网络调用。所有 HTTP 流量通过 monkeypatch 传输函数来模拟。)

Coverage:
(覆盖范围：)

* :func:`resolve_config` — per-model ``extra`` vs global section vs env
* ``OpenAIChatBackend`` lifecycle (load / is_loaded / unload)
* Blocking chat — single-chunk yield with content + finish_reason
* Streaming chat — role-first, content deltas, finish_reason, usage
* Multimodal content passthrough (image_url parts forwarded as-is)
* Error translation (HTTP 4xx → :class:`BackendError`)
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from xijian_api.ai.backends.openai._client import (
    RemoteConfig,
    resolve_config,
)
from xijian_api.ai.backends.openai.chat import OpenAIChatBackend
from xijian_api.ai.base import BackendError, ModelNotLoaded
from xijian_api.ai.types import ChatMessage, GenerationParams


# ---------------------------------------------------------------------------
# resolve_config
# ---------------------------------------------------------------------------
# (配置解析)


def test_resolve_config_defaults():
    """With no overrides, should use hardcoded defaults.
    (没有重写时，应使用硬编码的默认值。)
    """
    os.environ.pop("OPENAI_BASE_URL", None)
    os.environ.pop("OPENAI_API_KEY", None)
    cfg = resolve_config(None, section=None, default_model="fallback")
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.api_key == ""
    assert cfg.model_name == "fallback"
    assert cfg.transport == "httpx"


def test_resolve_config_env_vars():
    """Env vars fill in when no explicit config is given.
    (没有显式配置时，环境变量填补配置。)
    """
    os.environ["OPENAI_BASE_URL"] = "https://custom.example.com/v1"
    os.environ["OPENAI_API_KEY"] = "sk-test-env"
    try:
        cfg = resolve_config(None, section=None, default_model="m")
        assert cfg.base_url == "https://custom.example.com/v1"
        assert cfg.api_key == "sk-test-env"
    finally:
        os.environ.pop("OPENAI_BASE_URL")
        os.environ.pop("OPENAI_API_KEY")


def test_resolve_config_section_overrides_env():
    """Global [backends.openai] section takes priority over env.
    (全局 [backends.openai] 配置段优先级高于环境变量。)
    """
    os.environ["OPENAI_BASE_URL"] = "https://env.example.com/v1"
    try:
        cfg = resolve_config(
            None,
            section={"base_url": "https://section.example.com/v1",
                     "api_key": "sk-section",
                     "default_model": "gpt-4o",
                     "transport": "httpx"},
            default_model="fallback",
        )
        assert cfg.base_url == "https://section.example.com/v1"
        assert cfg.api_key == "sk-section"
        assert cfg.model_name == "gpt-4o"
    finally:
        os.environ.pop("OPENAI_BASE_URL")


def test_resolve_config_model_extra_wins():
    """Per-model extra fields override everything else.
    (每个模型的额外字段覆盖所有其他来源。)
    """
    os.environ["OPENAI_BASE_URL"] = "https://env.example.com/v1"
    try:
        cfg = resolve_config(
            {"base_url": "https://model.example.com/v1",
             "api_key": "sk-model",
             "model_name": "gpt-4o-mini",
             "transport": "httpx"},
            section={"base_url": "https://section.example.com/v1",
                     "api_key": "sk-section"},
            default_model="fallback",
        )
        assert cfg.base_url == "https://model.example.com/v1"
        assert cfg.api_key == "sk-model"
        assert cfg.model_name == "gpt-4o-mini"
    finally:
        os.environ.pop("OPENAI_BASE_URL")


def test_resolve_config_strips_trailing_slash():
    """Trailing slash is stripped from base_url.
    (尾随斜杠从 base_url 中被去除。)
    """
    cfg = resolve_config(
        {"base_url": "https://api.example.com/v1/"},
        section=None,
    )
    assert cfg.base_url == "https://api.example.com/v1"


def test_resolve_config_auth_header():
    """auth_header includes Authorization and extra headers.
    (auth_header 包含 Authorization 和额外头部。)
    """
    cfg = RemoteConfig(
        base_url="https://x", api_key="sk-1",
        model_name="m", transport="httpx",
        extra_headers={"X-Custom": "yes"},
    )
    h = cfg.auth_header
    assert h["Authorization"] == "Bearer sk-1"
    assert h["X-Custom"] == "yes"


def test_resolve_config_auth_header_no_key():
    """When api_key is empty, no Authorization header is set.
    (api_key 为空时，不设置 Authorization 头部。)
    """
    cfg = RemoteConfig(
        base_url="https://x", api_key="",
        model_name="m", transport="httpx",
        extra_headers={},
    )
    assert "Authorization" not in cfg.auth_header


# ---------------------------------------------------------------------------
# OpenAIChatBackend lifecycle
# ---------------------------------------------------------------------------
# (OpenAIChatBackend 生命周期)


def _make_backend(**load_kwargs) -> OpenAIChatBackend:
    """Create and load a backend with the given kwargs.
    (使用给定的关键字参数创建并加载后端。)
    """
    backend = OpenAIChatBackend()
    backend.load("", **load_kwargs)
    return backend


def test_backend_is_available():
    """is_available() always returns True.
    (is_available() 始终返回 True。)
    """
    assert OpenAIChatBackend().is_available() is True


def test_backend_load_requires_model_name():
    """Loading without model_name raises BackendError.
    (没有 model_name 的加载会抛出 BackendError。)
    """
    backend = OpenAIChatBackend()
    with pytest.raises(BackendError, match="model_name"):
        backend.load("", base_url="https://api.example.com/v1")


def test_backend_load_marks_loaded():
    """Successfully loaded backend is marked as loaded.
    (成功加载的后端被标记为已加载。)
    """
    backend = _make_backend(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model_name="gpt-4o",
    )
    assert backend.is_loaded() is True
    assert backend._cfg.model_name == "gpt-4o"


def test_backend_unload():
    """Unloaded backend is marked as unloaded and config cleared.
    (卸载的后端被标记为未加载，配置被清除。)
    """
    backend = _make_backend(
        model_name="gpt-4o",
        base_url="https://api.example.com/v1",
    )
    backend.unload()
    assert backend.is_loaded() is False
    assert backend._cfg is None


def test_backend_load_with_openai_section():
    """The _openai_section kwarg is consumed and merged.
    (_openai_section 参数被消费和合并。)
    """
    backend = OpenAIChatBackend()
    backend.load(
        "",
        model_name="gpt-4o",
        _openai_section={
            "base_url": "https://section.example.com/v1",
            "api_key": "sk-section",
            "default_model": "fallback",
            "transport": "httpx",
            "headers": {},
            "video_endpoint": "/video/generations",
        },
    )
    assert backend.is_loaded()
    assert backend._cfg.base_url == "https://section.example.com/v1"
    assert backend._cfg.api_key == "sk-section"


def test_chat_raises_when_not_loaded():
    """chat() raises ModelNotLoaded when the model is not loaded.
    (模型未加载时 chat() 抛出 ModelNotLoaded。)
    """
    backend = OpenAIChatBackend()
    with pytest.raises(ModelNotLoaded):
        list(backend.chat(
            [ChatMessage(role="user", content="hi")],
            GenerationParams(),
        ))


# ---------------------------------------------------------------------------
# Blocking chat
# ---------------------------------------------------------------------------
# (阻塞聊天)


def test_blocking_chat_yields_single_chunk():
    """Non-streaming call returns one chunk with full content + usage.
    (非流式调用返回一个包含完整内容和用量的块。)
    """
    mock_response = {
        "id": "chatcmpl-123",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "Hello!"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }
    backend = _make_backend(
        model_name="gpt-4o",
        base_url="https://api.example.com/v1",
        api_key="sk-test",
    )
    with patch(
        "xijian_api.ai.backends.openai.chat.remote_chat_completion",
        return_value=mock_response,
    ):
        chunks = list(backend.chat(
            [ChatMessage(role="user", content="hi")],
            GenerationParams(),
            stream=False,
        ))
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.choices[0].delta["content"] == "Hello!"
    assert chunk.choices[0].delta["role"] == "assistant"
    assert chunk.choices[0].finish_reason == "stop"
    assert chunk.usage.total_tokens == 7
    assert chunk.backend == "openai"


def test_blocking_chat_forwards_params():
    """GenerationParams are translated into OAI kwargs.
    (GenerationParams 被转换为 OAI 关键字参数。)
    """
    captured = {}

    def fake_remote(cfg, *, messages, stream, **kwargs):
        captured["kwargs"] = kwargs
        captured["messages"] = messages
        captured["stream"] = stream
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    backend = _make_backend(
        model_name="gpt-4o",
        base_url="https://api.example.com/v1",
    )
    with patch(
        "xijian_api.ai.backends.openai.chat.remote_chat_completion",
        side_effect=fake_remote,
    ):
        list(backend.chat(
            [ChatMessage(role="user", content="test")],
            GenerationParams(temperature=0.5, top_p=0.9, max_tokens=100, stop=["END"]),
            stream=False,
        ))
    assert captured["kwargs"]["temperature"] == 0.5
    assert captured["kwargs"]["top_p"] == 0.9
    assert captured["kwargs"]["max_tokens"] == 100
    assert captured["kwargs"]["stop"] == ["END"]
    assert captured["stream"] is False


# ---------------------------------------------------------------------------
# Streaming chat
# ---------------------------------------------------------------------------
# (流式聊天)


def _fake_sse_chunks():
    """Simulate OAI SSE stream: role delta, content deltas, finish.
    (模拟 OAI SSE 流：角色增量、内容增量、完成。)
    """
    yield {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}
    yield {"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]}
    yield {"choices": [{"delta": {"content": " world"}, "finish_reason": None}]}
    # OAI sends usage in the same chunk as finish_reason (or the chunk
    # just before it).  We include it here so the backend captures it.
    # (OAI 在与 finish_reason 相同的块中发送 usage（或在前一个块）。
    # 我们在此包含它以便后端捕获。)
    yield {
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def test_streaming_chat_yields_role_first():
    """Streaming chat yields role delta first, then content.
    (流式聊天先产生角色增量，然后是内容。)
    """
    backend = _make_backend(
        model_name="gpt-4o",
        base_url="https://api.example.com/v1",
    )
    with patch(
        "xijian_api.ai.backends.openai.chat.remote_chat_completion",
        return_value=_fake_sse_chunks(),
    ):
        chunks = list(backend.chat(
            [ChatMessage(role="user", content="hi")],
            GenerationParams(),
            stream=True,
        ))
    # First chunk should announce the role.
    # (第一个块应声明角色。)
    assert chunks[0].choices[0].delta.get("role") == "assistant"
    # Content should be split across subsequent chunks.
    # (内容应在后续块中分割。)
    content = "".join(
        c.choices[0].delta.get("content", "")
        for c in chunks
        if c.choices[0].delta.get("content")
    )
    assert content == "Hello world"
    # Last chunk should carry finish_reason.
    # (最后一个块应携带 finish_reason。)
    last = chunks[-1]
    assert last.choices[0].finish_reason == "stop"


def test_streaming_chat_carries_usage():
    """Streaming chat carries usage info in the last chunk.
    (流式聊天在最后一个块中携带用量信息。)
    """
    backend = _make_backend(
        model_name="gpt-4o",
        base_url="https://api.example.com/v1",
    )
    with patch(
        "xijian_api.ai.backends.openai.chat.remote_chat_completion",
        return_value=_fake_sse_chunks(),
    ):
        chunks = list(backend.chat(
            [ChatMessage(role="user", content="hi")],
            GenerationParams(),
            stream=True,
        ))
    last = chunks[-1]
    assert last.usage is not None
    assert last.usage.total_tokens == 5


# ---------------------------------------------------------------------------
# Multimodal passthrough
# ---------------------------------------------------------------------------
# (多模态内容透传)


def test_multimodal_content_passthrough():
    """image_url parts should be forwarded to the remote API as-is.
    (image_url 部分应按原样转发给远程 API。)
    """
    captured = {}

    def fake_remote(cfg, *, messages, stream, **kwargs):
        captured["messages"] = messages
        return {"choices": [{"message": {"content": "I see an image"}, "finish_reason": "stop"}]}

    backend = _make_backend(
        model_name="gpt-4o",
        base_url="https://api.example.com/v1",
    )
    multimodal_content = [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
    ]
    with patch(
        "xijian_api.ai.backends.openai.chat.remote_chat_completion",
        side_effect=fake_remote,
    ):
        list(backend.chat(
            [ChatMessage(role="user", content=multimodal_content)],
            GenerationParams(),
            stream=False,
        ))
    # The list-of-parts content should be forwarded as-is.
    # (多部件列表内容应按原样转发。)
    msg = captured["messages"][0]
    assert msg["role"] == "user"
    assert isinstance(msg["content"], list)
    assert msg["content"][0]["type"] == "text"
    assert msg["content"][1]["type"] == "image_url"
    assert msg["content"][1]["image_url"]["url"] == "https://example.com/cat.png"


def test_multimodal_content_text_extraction():
    """ChatMessage.text_content extracts text from list-of-parts.
    (ChatMessage.text_content 从多部件列表中提取文本。)
    """
    msg = ChatMessage(role="user", content=[
        {"type": "text", "text": "Hello "},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        {"type": "text", "text": "world"},
    ])
    assert msg.text_content == "Hello world"


def test_plain_string_content_text_extraction():
    """Plain string content is returned as-is from text_content.
    (纯字符串内容从 text_content 原样返回。)
    """
    msg = ChatMessage(role="user", content="plain text")
    assert msg.text_content == "plain text"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------
# (错误处理)


def test_backend_error_on_http_4xx():
    """HTTP errors from the remote are translated to BackendError.
    (来自远程的 HTTP 错误被转换为 BackendError。)
    """
    backend = _make_backend(
        model_name="gpt-4o",
        base_url="https://api.example.com/v1",
    )

    class FakeResp:
        status_code = 401
        def json(self):
            return {"error": {"message": "Invalid API key"}}
        @property
        def text(self):
            return '{"error": {"message": "Invalid API key"}}'

    with patch("httpx.post", return_value=FakeResp()):
        with pytest.raises(BackendError, match="401"):
            list(backend.chat(
                [ChatMessage(role="user", content="hi")],
                GenerationParams(),
                stream=False,
            ))


def test_backend_error_on_connection_failure():
    """Connection errors are wrapped in BackendError.
    (连接错误被包装在 BackendError 中。)
    """
    backend = _make_backend(
        model_name="gpt-4o",
        base_url="https://api.example.com/v1",
    )
    with patch("httpx.post", side_effect=ConnectionError("Connection refused")):
        with pytest.raises(BackendError, match="remote request failed"):
            list(backend.chat(
                [ChatMessage(role="user", content="hi")],
                GenerationParams(),
                stream=False,
            ))
