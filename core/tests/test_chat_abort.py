"""Tests for ``POST /v1/chat/abort``.
(``POST /v1/chat/abort`` 的测试。)
"""

from __future__ import annotations

from xijian_api import abort as abort_registry


def test_chat_abort_unknown_request_id_returns_200(client, auth_headers):
    """Aborting an unknown request is a 200 with ``aborted: false``.
    (中止未知请求返回 200 且 ``aborted: false``。)
    """
    response = client.post(
        "/v1/chat/abort",
        headers=auth_headers,
        json={"request_id": "req_does_not_exist"},
    )
    # Unknown id → 200 (we never had anything to cancel).
    # (未知 id → 200 (我们从未有任何东西可取消)。)
    assert response.status_code == 200
    body = response.get_json()
    assert body["aborted"] is False
    assert body["request_id"] == "req_does_not_exist"


def test_chat_abort_missing_request_id_returns_400(client, auth_headers):
    """A request without ``request_id`` is rejected with 400.
    (没有 ``request_id`` 的请求被拒绝，返回 400。)
    """
    response = client.post(
        "/v1/chat/abort",
        headers=auth_headers,
        json={},
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"]["code"] == "missing_request_id"


def test_chat_abort_signals_active_stream(client, auth_headers):
    """Aborting a registered request_id flips the AbortSignal.
    (中止已注册的 request_id 会翻转 AbortSignal。)
    """

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
        # (成功向活跃流发出信号时返回 204。)
        assert response.status_code == 204
        assert signal.is_set()
    finally:
        abort_registry.cleanup(request_id)


def test_chat_abort_does_not_block_subsequent_streams(client, auth_headers):
    """After aborting, a fresh stream must complete normally.
    (中止后，新的流必须正常完成。)
    """
    # Burn an abort on a non-existent id so the server's abort map
    # path is exercised; the next real stream should be unaffected.
    # (对一个不存在的 id 执行中止，以锻炼服务器的中止映射路径；
    # 下一个真实流应不受影响。)
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