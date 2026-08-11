"""WebSocket 路由 + 广播辅助。

按 DESIGN §11 与 api.md §5 实现 ``/v1/ws``。

要点：

* 通过 ``Sec-WebSocket-Protocol``（``xijian.v1, bearer.<token>``）或
  首帧 ``{"type": "auth", "token": "..."}`` 信封认证。
* 每 30 秒心跳（``ping`` / ``pong``）。
* 针对规范提及的事件（``character.*``、``world.*``、``memory.*``、``safety.*``、
  ``generation.*``）进行进程内 pub/sub 广播。
* 可选 dev 钩子 ``POST /v1/xijian/_test/emit`` 调用 :func:`publish_event`。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Iterable

from flask import Blueprint, current_app, jsonify, request
from flask_sock import Sock

from xijian_api import auth
from xijian_api._version import CORE_VERSION_NORMALIZED
from xijian_api.errors import ApiError
from xijian_api.utils.ids import gen_id
from xijian_api.utils.time import now_ts


# ---------------------------------------------------------------------------
# Blueprint + Sock 设置
# ---------------------------------------------------------------------------


bp = Blueprint("ws_routes", __name__)
sock = Sock()

_PING_INTERVAL_SECONDS = 30
_HELLO_DELAY_SECONDS = 0  # immediate


# ---------------------------------------------------------------------------
# 连接注册表 — 供 ``publish_event`` 广播消息使用。
# ---------------------------------------------------------------------------


class _Subscriber:
    """等待事件的已连接客户端。"""

    __slots__ = ("ws", "send_lock", "authed", "alive")

    def __init__(self, ws):
        self.ws = ws
        self.send_lock = threading.Lock()
        self.authed = False
        self.alive = True


_subscribers: list[_Subscriber] = []
_subs_lock = threading.Lock()


def _register(sub: _Subscriber) -> None:
    with _subs_lock:
        _subscribers.append(sub)


def _unregister(sub: _Subscriber) -> None:
    with _subs_lock:
        try:
            _subscribers.remove(sub)
        except ValueError:
            pass


def _broadcast(payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _subs_lock:
        subs = list(_subscribers)
    for sub in subs:
        if not sub.alive:
            continue
        with sub.send_lock:
            try:
                sub.ws.send(body)
            except Exception:  # noqa: BLE001 — connection probably closed
                sub.alive = False


def publish_event(event_type: str, data: dict | None = None) -> None:
    """向每个已连接订阅者发布服务端事件。

    供开发工具（``POST /v1/xijian/_test/emit``）以及希望暴露异步进度的
    存根服务使用。
    """
    payload = {
        "id": gen_id("evt_", 12),
        "type": event_type,
        "ts": now_ts(),
        "data": data or {},
    }
    _broadcast(payload)


# ---------------------------------------------------------------------------
# 认证辅助
# ---------------------------------------------------------------------------


def _check_bearer_header() -> bool:
    """请求携带有效 Bearer 子协议时返回 ``True``。

    同时接受 ``bearer-<token>``（RFC 合法；RFC 6455 子协议使用 token 语法，无点号）
    与 api.md 中记载的点号形式 ``bearer.<token>``。两种形式都被接受，
    以兼容尚未更新为短横线形式的客户端。
    """
    subprotocols = (request.headers.get("Sec-WebSocket-Protocol") or "").split(",")
    subprotocols = [s.strip() for s in subprotocols]
    if "xijian.v1" not in subprotocols:
        return False
    expected = auth.get_token() or ""
    for proto in subprotocols:
        presented = None
        if proto.startswith("bearer-"):
            presented = proto[len("bearer-"):]
        elif proto.startswith("bearer."):
            presented = proto[len("bearer."):]
        if presented and auth.constant_time_eq(presented, expected):
            return True
    return False


# ---------------------------------------------------------------------------
# 帧辅助
# ---------------------------------------------------------------------------


def _envelope(event_type: str, data: dict | None = None) -> dict:
    return {
        "id": gen_id("evt_", 12),
        "type": event_type,
        "ts": now_ts(),
        "data": data or {},
    }


def _send(ws, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    ws.send(body)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


@sock.route("/v1/ws", bp=bp)
def ws_endpoint(ws):
    """处理单个 WebSocket 连接。"""
    sub = _Subscriber(ws)
    _register(sub)

    try:
        # 解析一次客户端提供的子协议。
        offered = (request.headers.get("Sec-WebSocket-Protocol") or "").split(",")
        offered = [s.strip() for s in offered]
        has_xijian_v1 = "xijian.v1" in offered

        if not has_xijian_v1:
            # 拒绝升级：缺少必需的子协议。
            _send(ws, _envelope("hello", {"server_version": CORE_VERSION_NORMALIZED}))
            _send(ws, _envelope("auth.failed", {"reason": "missing_subprotocol"}))
            return

        # 打招呼。
        _send(ws, _envelope("hello", {"server_version": CORE_VERSION_NORMALIZED}))

        # 先尝试基于子协议的认证。
        if _check_bearer_header():
            sub.authed = True
            _send(ws, _envelope("auth.ok"))
        else:
            # 无子协议认证 → 短暂等待首帧
            # ``{"type": "auth", "token": "..."}`` 信封。
            try:
                first = ws.receive(timeout=2)
            except Exception:  # noqa: BLE001
                first = None
            if isinstance(first, str):
                try:
                    msg = json.loads(first)
                except json.JSONDecodeError:
                    msg = {}
                if msg.get("type") == "auth" and msg.get("token") is not None \
                        and auth.constant_time_eq(str(msg.get("token")), auth.get_token() or ""):
                    sub.authed = True
                    _send(ws, _envelope("auth.ok"))

        if not sub.authed:
            _send(ws, _envelope("auth.failed", {"reason": "invalid_token"}))
            return

        last_ping = time.time()
        # 2026-08-01: the hardcoded dev demo broadcast
        # (``character.proactive_message`` 3s after every authenticated
        # connect) has been REMOVED — it leaked into other tests'
        # unauthenticated connections in the same process (test_ws
        # isolation failure).  Real proactive messaging now flows
        # through A7 (:mod:`xijian_api.stubs.character_initiated_actions`
        # + its tick thread), which broadcasts
        # ``character.initiated_action`` only when an action is actually
        # created — see routes/xijian_initiated.py.
        # 2026-08-01：硬编码的 dev 演示广播 (连接认证成功后 3 秒广播
        # ``character.proactive_message``) 已移除 —— 它会泄漏到同进程
        # 其他测试的未认证连接 (test_ws 隔离失败)。真实的主动消息现在
        # 走 A7 (character_initiated_actions + tick 线程)，仅在真正
        # 创建动作时广播 ``character.initiated_action``。

        while True:
            try:
                raw = ws.receive(timeout=_PING_INTERVAL_SECONDS)
            except Exception:  # noqa: BLE001 — likely a timeout
                raw = None

            if raw is None:
                # 心跳 — 仅当客户端最近未 ping 时发送。
                if time.time() - last_ping >= _PING_INTERVAL_SECONDS:
                    last_ping = time.time()
                    try:
                        _send(ws, _envelope("ping"))
                    except Exception:  # noqa: BLE001
                        break
                continue

            if not isinstance(raw, str):
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event_type = msg.get("type", "")
            if event_type == "ping":
                _send(ws, _envelope("pong", msg.get("data")))
                last_ping = time.time()
            elif event_type == "pong":
                last_ping = time.time()
            elif event_type == "client.cancel_request":
                from xijian_api import abort as abort_registry
                data = msg.get("data") or {}
                request_id = data.get("request_id", "")
                if request_id:
                    abort_registry.abort(request_id)
                    _send(ws, _envelope("client.cancel_request.ack", {"request_id": request_id}))
            elif event_type == "desktop_pet.emergency_pause":
                _send(ws, _envelope("desktop_pet.paused"))
            elif event_type == "desktop_pet.command":
                _send(ws, _envelope("desktop_pet.command.echo", msg.get("data")))
            # 服务端忽略其他事件。
    finally:
        sub.alive = False
        _unregister(sub)


def init_app(app) -> None:
    """将 Sock 路由附加到 ``app``（由 register_routes 调用）。"""
    # 通告 ``xijian.v1`` 为我们接受的子协议。没有它，
    # simple_websocket 的 ``choose_subprotocol`` 返回 ``None``，
    # 握手响应将省略 ``Sec-WebSocket-Protocol``，
    # 严格的 WS 客户端（如 ``websocket-client``）会拒绝。
    app.config.setdefault("SOCK_SERVER_OPTIONS", {"subprotocols": ["xijian.v1"]})
    sock.init_app(app)


# ---------------------------------------------------------------------------
# 仅限开发环境的 WS 事件注入器
# ---------------------------------------------------------------------------


@bp.post("/v1/xijian/_test/emit")
def dev_emit():
    """仅限开发环境的端点，用于发布假的 WS 事件。

    由 ``XIJIAN_DEV=1`` 保护，确保永远不会部署到生产环境。
    """
    if os.environ.get("XIJIAN_DEV") != "1":
        raise ApiError(404, "not found", "not_found_error", code="route_not_found")
    payload = request.get_json(silent=True) or {}
    event_type = payload.get("type", "ping")
    data = payload.get("data", {})
    publish_event(event_type, data)
    return jsonify({"published": True, "type": event_type})


__all__ = ["bp", "init_app", "publish_event"]