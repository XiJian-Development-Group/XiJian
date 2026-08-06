"""Tests for the WSGI server-driver selection (P0 fix: /v1/ws under waitress).

(WSGI 服务器驱动选型的测试（P0 修复：waitress 下 /v1/ws 不可用）。)

Background / 背景:
- ``auto`` must resolve to ``werkzeug`` (threaded), because the
  WebSocket endpoint ``/v1/ws`` (feature list A6/A7) requires a WSGI
  environment that exposes the raw socket — waitress does not provide
  one, so the handshake 500s (``RuntimeError: Cannot obtain socket from
  WSGI environment``).
- ``auto`` 必须解析为 ``werkzeug``（多线程）：WebSocket 端点 ``/v1/ws``
  （功能清单 A6/A7）要求 WSGI 环境暴露原始 socket——waitress 不提供，
  握手会 500。
- Explicit ``waitress`` is honoured but logs a WARNING that ``/v1/ws``
  is unavailable.  We never bind a real waitress listener in CI; the
  waitress branch is exercised with a faked ``waitress`` module.
- 显式 ``waitress`` 会被采纳，但会记录 ``/v1/ws`` 不可用的 WARNING。
  测试中不会真的用 waitress 监听端口（CI 无法确认）；waitress 分支
  通过伪造 ``waitress`` 模块来验证。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import types

import pytest

from xijian_api import app as app_module
from xijian_api.app import parse_args, resolve_server_driver
from xijian_api.config import Config


# ---------------------------------------------------------------------------
# Driver resolution (pure function)
# 驱动解析（纯函数）
# ---------------------------------------------------------------------------


def test_resolve_auto_defaults_to_werkzeug():
    """``auto`` / no value at all must resolve to ``werkzeug`` (WS available).
    (``auto`` / 完全不指定必须解析为 ``werkzeug``（WebSocket 可用）。)
    """
    assert resolve_server_driver(None, None) == "werkzeug"
    assert resolve_server_driver("auto", None) == "werkzeug"
    assert resolve_server_driver(None, "auto") == "werkzeug"
    assert resolve_server_driver("auto", "auto") == "werkzeug"


def test_resolve_explicit_waitress_from_cli_or_config():
    """An explicit ``waitress`` from either source is honoured.
    (来自 CLI 或配置的显式 ``waitress`` 都会被采纳。)
    """
    assert resolve_server_driver("waitress", None) == "waitress"
    assert resolve_server_driver(None, "waitress") == "waitress"
    assert resolve_server_driver("waitress", "auto") == "waitress"


def test_resolve_cli_wins_over_config():
    """CLI value has priority over the config value.
    (CLI 值优先于配置值。)
    """
    assert resolve_server_driver("werkzeug", "waitress") == "werkzeug"
    assert resolve_server_driver("waitress", "werkzeug") == "waitress"
    assert resolve_server_driver("auto", "waitress") == "werkzeug"


def test_resolve_unknown_values_fall_back_to_werkzeug():
    """Unknown values degrade to ``auto`` → ``werkzeug``.
    (未知值降级为 ``auto`` → ``werkzeug``。)
    """
    assert resolve_server_driver("bogus", None) == "werkzeug"
    assert resolve_server_driver(None, "bogus") == "werkzeug"
    assert resolve_server_driver("", "  ") == "werkzeug"


# ---------------------------------------------------------------------------
# CLI + config plumbing
# CLI + 配置接线
# ---------------------------------------------------------------------------


def test_parse_args_server_flag():
    """``--server`` accepts the three drivers and defaults to ``None``.
    (``--server`` 接受三种驱动，默认 ``None``（→ config → auto）。)
    """
    assert parse_args([]).server is None
    assert parse_args(["--server", "auto"]).server == "auto"
    assert parse_args(["--server", "werkzeug"]).server == "werkzeug"
    assert parse_args(["--server", "waitress"]).server == "waitress"


def test_parse_args_server_flag_rejects_unknown(capsys):
    """``--server bogus`` is rejected by argparse choices.
    (``--server bogus`` 被 argparse choices 拒绝。)
    """
    with pytest.raises(SystemExit):
        parse_args(["--server", "bogus"])


def test_config_server_driver_from_dict():
    """``[server].driver`` maps to ``ServerConfig.server_driver``.
    (``[server].driver`` 映射到 ``ServerConfig.server_driver``。)
    """
    cfg = Config.from_dict({"server": {"driver": "waitress"}})
    assert cfg.server.server_driver == "waitress"
    cfg2 = Config.from_dict({"server": {"driver": "WERKZEUG"}})
    assert cfg2.server.server_driver == "werkzeug"
    assert Config.empty().server.server_driver == "auto"


def test_config_server_driver_invalid_raises():
    """An invalid ``[server].driver`` value raises ValueError.
    (非法的 ``[server].driver`` 值抛出 ValueError。)
    """
    with pytest.raises(ValueError, match=r"\[server\] driver must be auto\|werkzeug\|waitress"):
        Config.from_dict({"server": {"driver": "gunicorn"}})


# ---------------------------------------------------------------------------
# werkzeug driver — real /v1/ws handshake through _serve()
# werkzeug 驱动 — 通过 _serve() 的真实 /v1/ws 握手
# ---------------------------------------------------------------------------


def test_serve_werkzeug_ws_handshake_ok(app, token, monkeypatch):
    """Driving ``_serve(..., server="werkzeug")`` must serve ``/v1/ws``
    handshakes end to end (hello + auth.ok via Bearer subprotocol).
    (以 ``_serve(..., server="werkzeug")`` 启动必须端到端支持 ``/v1/ws``
    握手（通过 Bearer 子协议收到 hello + auth.ok）。)
    """
    from websocket import create_connection

    captured: dict = {}
    original_make_server = app_module._werkzeug_make_server

    def _fake_make_server(host, port, wsgi_app, threaded=True):
        server = original_make_server(host, port, wsgi_app, threaded=threaded)
        captured["server"] = server
        return server

    monkeypatch.setattr(app_module, "_werkzeug_make_server", _fake_make_server)

    # ``_serve`` resolves the driver itself; assert it picks werkzeug.
    # ``_serve`` 内部会解析驱动；确认它选中的是 werkzeug。
    assert resolve_server_driver("werkzeug", None) == "werkzeug"

    t = threading.Thread(
        target=app_module._serve,
        args=(app, "127.0.0.1", 0, "werkzeug"),
        daemon=True,
    )
    t.start()
    server = None
    try:
        for _ in range(100):
            server = captured.get("server")
            if server is not None:
                break
            time.sleep(0.05)
        assert server is not None, "_serve did not create the werkzeug server"
        port = server.server_port

        ws = create_connection(
            f"ws://127.0.0.1:{port}/v1/ws",
            subprotocols=["xijian.v1", f"bearer-{token}"],
            timeout=5,
        )
        try:
            hello = json.loads(ws.recv())
            assert hello["type"] == "hello"
            ok = json.loads(ws.recv())
            assert ok["type"] == "auth.ok"
        finally:
            ws.close()
    finally:
        if server is not None:
            server.shutdown()
        t.join(timeout=5)


# ---------------------------------------------------------------------------
# waitress branch — warning only, never a real listener
# waitress 分支 — 只验证警告，绝不启动真实监听
# ---------------------------------------------------------------------------


def test_serve_waitress_warns_ws_unavailable(app, monkeypatch, caplog):
    """Requesting ``waitress`` logs a WARNING that ``/v1/ws`` is unavailable
    and serves via the (faked) waitress module.
    (请求 ``waitress`` 会记录 ``/v1/ws`` 不可用的 WARNING，并通过
    （伪造的）waitress 模块提供服务。)
    """
    def _fake_serve(wsgi_app, host=None, port=None, ident=None):
        # Stop the server immediately; we only assert the startup log.
        # 立即停止服务；我们只断言启动日志。
        raise KeyboardInterrupt()

    monkeypatch.setitem(
        sys.modules,
        "waitress",
        types.SimpleNamespace(serve=_fake_serve),
    )
    with pytest.raises(KeyboardInterrupt):
        app_module._serve(app, "127.0.0.1", 0, "waitress")
    assert "waitress 不支持 WebSocket" in caplog.text
    assert "waitress 服务启动" in caplog.text


def test_serve_waitress_missing_falls_back_to_werkzeug(app, monkeypatch, caplog):
    """If ``waitress`` is requested but not installed, fall back to
    ``werkzeug`` (WS still available) instead of crashing.
    (请求 ``waitress`` 但未安装时，回退到 ``werkzeug``（WebSocket 仍可用），
    而不是崩溃。)
    """
    monkeypatch.setitem(sys.modules, "waitress", None)  # 此时 ``from waitress import serve`` 会抛出 ImportError

    class _FakeHttpd:
        def serve_forever(self):
            raise KeyboardInterrupt()

    monkeypatch.setattr(
        app_module,
        "_werkzeug_make_server",
        lambda host, port, wsgi_app, threaded=True: _FakeHttpd(),
    )
    with pytest.raises(KeyboardInterrupt):
        app_module._serve(app, "127.0.0.1", 0, "waitress")
    assert "waitress 未安装" in caplog.text
    assert "werkzeug 服务启动" in caplog.text
