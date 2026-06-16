"""Tests for non-streaming ``POST /v1/chat/completions``."""

from __future__ import annotations


def test_chat_sync_returns_oai_envelope(client, auth_headers):
    """A non-stream chat completion returns the standard OAI shape."""
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
    """The response header ``X-XiJian-Model-Id`` echoes the model."""
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
    """A request without ``messages`` is rejected with 400."""
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