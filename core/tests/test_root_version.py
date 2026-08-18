"""Tests for the root / version / capabilities routes.
(根路由 / 版本 / 能力声明的测试。)
"""

from __future__ import annotations


def test_root_returns_server_identity(client, auth_headers):
    """``GET /`` returns a small JSON envelope with the server name.
    (``GET /`` 返回包含服务器名称的小型 JSON 信封。)
    """
    response = client.get("/", headers=auth_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["name"] == "xijian-api"
    assert body["status"] == "ok"
    assert body["api_version"] == "1.1.0"
    assert "server_version" in body


def test_v1_returns_capabilities(client, auth_headers):
    """``GET /v1`` returns the API version + capabilities list.
    (``GET /v1`` 返回 API 版本 + 能力声明列表。)
    """
    response = client.get("/v1", headers=auth_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["api_version"] == "1.1.0"
    assert "server_version" in body
    caps = body["capabilities"]
    assert isinstance(caps, list)
    # A few well-known capabilities must be advertised.
    # (一些已知的能力必须被声明。)
    assert "chat.completions" in caps
    assert "xijian.characters" in caps
    assert "websocket" in caps


def test_root_requires_auth(client):
    """``GET /`` and ``GET /v1`` both require Bearer auth.
    (``GET /`` 和 ``GET /v1`` 都需要 Bearer 认证。)
    """
    for path in ("/", "/v1"):
        response = client.get(path)
        assert response.status_code == 401, f"path={path}"


def test_root_echoes_request_id_header(client, auth_headers):
    """The server stamps the request id on the response.
    (服务器在响应上标记请求 id。)
    """
    response = client.get(
        "/v1",
        headers={**auth_headers, "X-XiJian-Request-Id": "req_clientsupplied"},
    )
    assert response.status_code == 200
    assert response.headers.get("X-XiJian-Request-Id") == "req_clientsupplied"


def test_root_generates_request_id_when_missing(client, auth_headers):
    """If the client doesn't supply one, the server generates a fresh id.
    (如果客户端未提供，服务器生成一个新的 id。)
    """
    response = client.get("/v1", headers=auth_headers)
    request_id = response.headers.get("X-XiJian-Request-Id")
    assert request_id is not None
    assert request_id.startswith("req_")
    assert len(request_id) == len("req_") + 12  # 12 hex chars (12 个十六进制字符)


def test_root_stamps_trace_id_header(client, auth_headers):
    """The server stamps a trace id on the response (generated or echoed).
    (服务器在响应上标记 trace id（生成或回显）。)
    """
    response = client.get(
        "/v1",
        headers={**auth_headers, "X-XiJian-Trace-Id": "trace_abcdef123456"},
    )
    assert response.headers.get("X-XiJian-Trace-Id") == "trace_abcdef123456"
