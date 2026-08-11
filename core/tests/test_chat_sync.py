"""Tests for non-streaming ``POST /v1/chat/completions``.
(非流式 ``POST /v1/chat/completions`` 的测试。)
"""

from __future__ import annotations


def test_chat_sync_returns_oai_envelope(client, auth_headers):
    """A non-stream chat completion returns the standard OAI shape.
    (非流式聊天补全返回标准的 OAI 格式。)
    """
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "qwen2.5-7b-mlx-4bit",
            "messages": [{"role": "user", "content": "hi there"}],
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "qwen2.5-7b-mlx-4bit"
    choices = body["choices"]
    assert len(choices) == 1
    assert choices[0]["message"]["role"] == "assistant"
    assert "content" in choices[0]["message"]
    assert choices[0]["finish_reason"] in {"stop", "length"}
    assert "usage" in body
    assert "xijian" in body


def test_chat_sync_echoes_model_in_response_header(client, auth_headers):
    """The response header ``X-XiJian-Model-Id`` echoes the model.
    (响应头部 ``X-XiJian-Model-Id`` 回显模型名称。)
    """
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "qwen2.5-7b-mlx-4bit",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.headers.get("X-XiJian-Model-Id") == "qwen2.5-7b-mlx-4bit"


def test_chat_sync_missing_messages_returns_400(client, auth_headers):
    """A request without ``messages`` is rejected with 400.
    (没有 ``messages`` 的请求被拒绝，返回 400。)
    """
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={"model": "stub"},
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "missing_messages"
    assert body["error"]["param"] == "messages"


# ---------------------------------------------------------------------------
# E2 — numeric parameter clamping
# E2 — 数值参数钳位
# ---------------------------------------------------------------------------


def _post_capture(client, auth_headers, chat_stub, **body):
    """POST a chat request, capturing the kwargs chat_stub.complete saw."""
    captured: dict = {}
    original = chat_stub.complete

    def _fake_complete(messages, **kwargs):
        captured.update(kwargs)
        return original(messages, **kwargs)

    chat_stub.complete = _fake_complete
    try:
        payload = {
            "model": "qwen2.5-7b-mlx-4bit",
            "messages": [{"role": "user", "content": "hi"}],
        }
        payload.update(body)
        response = client.post("/v1/chat/completions", headers=auth_headers, json=payload)
    finally:
        chat_stub.complete = original
    return response, captured


def test_chat_sync_clamps_temperature_top_p_max_tokens(client, auth_headers, monkeypatch):
    """Out-of-range sampling params are clamped, never rejected (E2).

    temperature=1e308 → 2.0; top_p=5 → 1.0; max_tokens=-5 → 1.
    (超出范围的采样参数被钳位而非拒绝 (E2)。)
    """
    from xijian_api.stubs import chat as chat_stub

    response, captured = _post_capture(
        client,
        auth_headers,
        chat_stub,
        temperature=1e308,
        top_p=5,
        max_tokens=-5,
    )
    assert response.status_code == 200
    assert captured["temperature"] == 2.0
    assert captured["top_p"] == 1.0
    assert captured["max_tokens"] == 1


def test_chat_sync_clamps_low_temperature_and_max_tokens_ceiling(client, auth_headers):
    """temperature=-1 → 0.0; max_tokens=1_000_000 → 32768 (E2)."""
    from xijian_api.stubs import chat as chat_stub

    response, captured = _post_capture(
        client,
        auth_headers,
        chat_stub,
        temperature=-1,
        max_tokens=1_000_000,
    )
    assert response.status_code == 200
    assert captured["temperature"] == 0.0
    assert captured["max_tokens"] == 32768


def test_chat_sync_max_tokens_none_stays_none(client, auth_headers):
    """Absent max_tokens stays ``None`` after clamping (E2)."""
    from xijian_api.stubs import chat as chat_stub

    response, captured = _post_capture(client, auth_headers, chat_stub)
    assert response.status_code == 200
    assert captured["max_tokens"] is None
    assert captured["temperature"] == 0.7
    assert captured["top_p"] == 1.0


def test_chat_sync_clamped_values_still_stream(client, auth_headers):
    """Streaming path clamps identically — no crash on out-of-range values (E2)."""
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "qwen2.5-7b-mlx-4bit",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "temperature": 1e308,
            "max_tokens": -5,
        },
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.content_type
