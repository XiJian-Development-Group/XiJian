"""Tests for ``POST /v1/chat/abort``."""

from __future__ import annotations

from xijian_api import abort as abort_registry


def test_chat_abort_unknown_request_id_returns_200(client, auth_headers):
    """Aborting an unknown request is a 200 with ``aborted: false``."""
    response = client.post(
        "/v1/chat/abort",
        headers=auth_headers,
        json={"request_id": "req_does_not_exist"},
    )
    # Unknown id → 200 (we never had anything to cancel).
    assert response.status_code == 200
    body = response.get_json()
    assert body["aborted"] is False
    assert body["request_id"] == "req_does_not_exist"


def test_chat_abort_missing_request_id_returns_400(client, auth_headers):
    """A request without ``request_id`` is rejected with 400."""
    response = client.post(
        "/v1/chat/abort",
        headers=auth_headers,
        json={},
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"]["code"] == "missing_request_id"


def test_chat_abort_signals_active_stream(client, auth_headers):
    """Aborting a registered request_id flips the AbortSignal."""

    request_id = "req_test_abort_1234"
    signal = abort_registry.register(request_id)
    assert not signal.is_set()
    try:
        response = client.post(
            "/v1/chat/abort",
            headers=auth_headers,
            json={"request_id": request_id},
        )
        # 204 when we successfully signalled an active stream.
        assert response.status_code == 204
        assert signal.is_set()
    finally:
        abort_registry.cleanup(request_id)


def test_chat_abort_does_not_block_subsequent_streams(client, auth_headers):
    """After aborting, a fresh stream must complete normally."""
    # Burn an abort on a non-existent id so the server's abort map
    # path is exercised; the next real stream should be unaffected.
    client.post(
        "/v1/chat/abort",
        headers=auth_headers,
        json={"request_id": "req_burn_1"},
    )
    response = client.post(
        "/v1/chat/completions",
        headers={**auth_headers, "Accept": "text/event-stream"},
        json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    body = response.get_data(as_text=True)
    assert "[DONE]" in body
    assert '"finish_reason":"stop"' in body