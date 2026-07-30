"""Tests for streaming ``POST /v1/chat/completions`` over SSE.
(基于 SSE 的流式 ``POST /v1/chat/completions`` 测试。)
"""

from __future__ import annotations


def test_chat_stream_sse_returns_event_stream(client, auth_headers):
    """SSE streaming returns text/event-stream content type.
    (SSE 流式返回 text/event-stream 内容类型。)
    """
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
    # (每个 SSE 事件是 "data: <json>\n\n"，流以 "data: [DONE]\n\n" 结束。)
    assert "data:" in body
    assert "[DONE]" in body


def test_chat_stream_sse_emits_role_first_chunk(client, auth_headers):
    """First SSE chunk announces assistant role; last chunk has finish_reason.
    (首个 SSE 块声明 assistant 角色；最后一个块带有 finish_reason。)
    """
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
    # (第一个块应声明 assistant 角色。注意 SSE 帧是紧凑 JSON
    # (``separators=(",", ":")``)，因此键和值之间没有空格。)
    assert '"role":"assistant"' in body
    # The final chunk should have finish_reason=stop.
    # (最后一个块应具有 finish_reason=stop。)
    assert '"finish_reason":"stop"' in body


def test_chat_stream_sse_has_request_id_header(client, auth_headers):
    """SSE response includes X-XiJian-Request-Id header.
    (SSE 响应包含 X-XiJian-Request-Id 头部。)
    """
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
