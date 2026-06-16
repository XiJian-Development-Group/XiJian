"""Tests for streaming ``POST /v1/chat/completions`` over SSE."""

from __future__ import annotations


def test_chat_stream_sse_returns_event_stream(client, auth_headers):
    response = client.post(
        "/v1/chat/completions",
        headers={**auth_headers, "Accept": "text/event-stream"},
        json={
            "model": "qwen2.5-7b-mlx-4bit",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    ctype = response.headers.get("Content-Type", "")
    assert ctype.startswith("text/event-stream")
    body = response.get_data(as_text=True)
    # Each SSE event is "data: <json>\n\n" and the stream ends with
    # "data: [DONE]\n\n".
    assert "data:" in body
    assert "[DONE]" in body


def test_chat_stream_sse_emits_role_first_chunk(client, auth_headers):
    response = client.post(
        "/v1/chat/completions",
        headers={**auth_headers, "Accept": "text/event-stream"},
        json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )
    body = response.get_data(as_text=True)
    # The first chunk should announce the assistant role.  Note the
    # SSE frame is compact JSON (``separators=(",", ":")``) so there
    # is no space between key and value.
    assert '"role":"assistant"' in body
    # The final chunk should have finish_reason=stop.
    assert '"finish_reason":"stop"' in body


def test_chat_stream_sse_has_request_id_header(client, auth_headers):
    response = client.post(
        "/v1/chat/completions",
        headers={**auth_headers, "Accept": "text/event-stream"},
        json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )
    assert response.headers.get("X-XiJian-Request-Id", "").startswith("req_")