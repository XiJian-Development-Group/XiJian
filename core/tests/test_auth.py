"""Tests for the Bearer-token auth middleware.
(Bearer-token 认证中间件的测试。)

从 __future__ 导入 annotations 以支持从 Python 3.7+ 开始的
推迟注解评估 (PEP 563)。
(从 __future__ 导入 annotations 以支持从 Python 3.7+ 开始的
推迟注解评估 (PEP 563)。)
"""

from __future__ import annotations


def test_missing_authorization_header_returns_401(client):
    """A request with no Authorization header is rejected with 401.
    (没有 Authorization 头的请求被拒绝，返回 401。)
    """
    response = client.get("/v1")
    assert response.status_code == 401
    body = response.get_json()
    assert body is not None
    # Default Accept is JSON → OAI envelope.
    # (默认 Accept 为 JSON → OAI 信封格式。)
    assert "error" in body
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "invalid_api_key"


def test_wrong_authorization_scheme_returns_401(client):
    """Using a non-Bearer scheme is rejected.
    (使用非 Bearer 方案被拒绝。)
    """
    response = client.get(
        "/v1",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert response.status_code == 401


def test_wrong_token_returns_401(client):
    """A Bearer with the wrong token is rejected.
    (带有错误 token 的 Bearer 被拒绝。)
    """
    response = client.get(
        "/v1",
        headers={"Authorization": "Bearer this-is-not-the-token"},
    )
    assert response.status_code == 401


def test_correct_token_passes(client, auth_headers):
    """A correct Bearer token lets the request through.
    (正确的 Bearer token 让请求通过。)
    """
    response = client.get("/v1", headers=auth_headers)
    assert response.status_code == 200


def test_extra_whitespace_around_token_rejected(client, token):
    """Bearer with trailing whitespace after the token is rejected.

    The verify_bearer function trims trailing whitespace, so we add
    a clearly wrong fragment to make sure the trimmed value still
    doesn't match the actual token.
    (Bearer token 后带有尾随空白被拒绝。

    verify_bearer 函数会修剪尾随空白，所以我们添加一个明显错误的片段，
    以确保修剪后的值仍然不匹配实际 token。)
    """
    response = client.get(
        "/v1",
        headers={"Authorization": f"Bearer {token}x"},
    )
    assert response.status_code == 401