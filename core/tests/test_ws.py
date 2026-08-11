"""Tests for the WebSocket ``/v1/ws`` endpoint.
(WebSocket ``/v1/ws`` 端点的测试。)
"""

from __future__ import annotations

import json


def test_ws_hello_then_auth_ok(app, auth_headers, token):
    """Connect with a valid Bearer subprotocol → receive ``hello`` then ``auth.ok``.
    (使用有效 Bearer 子协议连接 → 接收 ``hello`` 然后 ``auth.ok``。)
    """
    import threading

    from werkzeug.serving import make_server
    from websocket import create_connection

    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        ws = create_connection(
            f"ws://127.0.0.1:{port}/v1/ws",
            subprotocols=["xijian.v1", f"bearer-{token}"],
            timeout=5,
        )
        try:
            hello = json.loads(ws.recv())
            assert hello["type"] == "hello"
            auth_ok = json.loads(ws.recv())
            assert auth_ok["type"] == "auth.ok"
            # After auth, ping → pong.
            # (认证后，ping → pong。)
            ws.send(json.dumps({"type": "ping"}))
            pong = json.loads(ws.recv())
            assert pong["type"] == "pong"
        finally:
            ws.close()
    finally:
        server.shutdown()


def test_ws_auth_failed_with_bad_token(app):
    """A wrong token in the subprotocol yields ``auth.failed``.
    (子协议中的错误 token 产生 ``auth.failed``。)
    """
    import threading

    from werkzeug.serving import make_server
    from websocket import create_connection

    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        ws = create_connection(
            f"ws://127.0.0.1:{port}/v1/ws",
            subprotocols=["xijian.v1", "bearer-wrong-token"],
            timeout=5,
        )
        try:
            hello = json.loads(ws.recv())
            assert hello["type"] == "hello"
            failed = json.loads(ws.recv())
            assert failed["type"] == "auth.failed"
        finally:
            ws.close()
    finally:
        server.shutdown()


def test_ws_first_frame_auth(app, token):
    """A token sent in the first frame yields ``auth.ok``.
    (在第一帧中发送的 token 产生 ``auth.ok``。)
    """
    import threading

    from werkzeug.serving import make_server
    from websocket import create_connection

    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        ws = create_connection(
            f"ws://127.0.0.1:{port}/v1/ws",
            subprotocols=["xijian.v1"],
            timeout=5,
        )
        try:
            hello = json.loads(ws.recv())
            assert hello["type"] == "hello"
            ws.send(json.dumps({"type": "auth", "token": token}))
            ok = json.loads(ws.recv())
            assert ok["type"] == "auth.ok"
        finally:
            ws.close()
    finally:
        server.shutdown()


def test_ws_hello_server_version_matches_root(app, token):
    """The WS ``hello`` envelope advertises the same server version as the
    root route (B1) — no more hardcoded ``0.1.0``.
    (WS ``hello`` 信封通告与根路由相同的服务版本 (B1)——不再硬编码 ``0.1.0``。)
    """
    import threading

    from werkzeug.serving import make_server
    from websocket import create_connection

    from xijian_api._version import CORE_VERSION_NORMALIZED

    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        ws = create_connection(
            f"ws://127.0.0.1:{port}/v1/ws",
            subprotocols=["xijian.v1", f"bearer-{token}"],
            timeout=5,
        )
        try:
            hello = json.loads(ws.recv())
            assert hello["type"] == "hello"
            assert hello["data"]["server_version"] == CORE_VERSION_NORMALIZED
        finally:
            ws.close()
    finally:
        server.shutdown()


def test_ws_hello_missing_subprotocol_uses_same_version(app):
    """The reject path (missing subprotocol) sends the same versioned
    hello.  A real client cannot complete this handshake (websocket-client
    refuses a server that echoes no matching subprotocol) and flask_sock's
    ``route`` decorator does not expose the handler, so we assert both
    structurally: the module source no longer hardcodes ``0.1.0`` (B1).
    (拒绝路径（缺少子协议）发送相同版本的 hello。真实客户端无法完成
    该握手（websocket-client 会拒绝不回应匹配子协议的服务器），
    flask_sock 的 ``route`` 装饰器也不暴露处理器，因此做结构性断言：
    模块源码不再硬编码 ``0.1.0`` (B1)。)
    """
    import inspect

    from xijian_api.routes import ws_routes

    source = inspect.getsource(ws_routes)
    assert "0.1.0" not in source
    # The version constant is wired into both hello envelopes.
    assert source.count('"server_version": CORE_VERSION_NORMALIZED') >= 2
