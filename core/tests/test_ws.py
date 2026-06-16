"""Tests for the WebSocket ``/v1/ws`` endpoint."""

from __future__ import annotations

import json


def test_ws_hello_then_auth_ok(app, auth_headers, token):
    """Connect with a valid Bearer subprotocol → receive ``hello``."""
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
            # Subprotocol auth → no separate auth frame.
            ws.send(json.dumps({"type": "ping"}))
            pong = json.loads(ws.recv())
            assert pong["type"] == "pong"
        finally:
            ws.close()
    finally:
        server.shutdown()


def test_ws_auth_failed_with_bad_token(app):
    """A wrong token in the subprotocol yields ``auth.failed``."""
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
    """A token sent in the first frame yields ``auth.ok``."""
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