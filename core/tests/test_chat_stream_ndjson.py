"""Tests for streaming ``POST /v1/chat/completions`` over NDJSON.
(在 NDJSON 上流式 ``POST /v1/chat/completions`` 的测试。)
"""

from __future__ import annotations

import json


def test_chat_stream_ndjson_returns_ndjson_content_type(client, auth_headers):
    """Streaming with NDJSON Accept returns NDJSON content type.
    (使用 NDJSON Accept 的流式返回 NDJSON 内容类型。)
    """
    response = client.post(
        "/v1/chat/completions",
        headers={**auth_headers, "Accept": "application/x-ndjson"},
        json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    ctype = response.headers.get("Content-Type", "")
    assert ctype.startswith("application/x-ndjson")


def test_chat_stream_ndjson_each_line_is_json(client, auth_headers):
    """Every line of NDJSON output must be a valid JSON object.
    (NDJSON 输出的每一行都必须是有效的 JSON 对象。)
    """
    response = client.post(
        "/v1/chat/completions",
        headers={**auth_headers, "Accept": "application/x-ndjson"},
        json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    body = response.get_data(as_text=True).strip()
    lines = [ln for ln in body.split("\n") if ln]
    assert len(lines) >= 1
    # Every line must be a parseable JSON object.
    # (每一行都必须是可解析的 JSON 对象。)
    for line in lines:
        parsed = json.loads(line)
        assert parsed.get("object") == "chat.completion.chunk"
