"""WebSocket route + broadcast helper.

Implements ``/v1/ws`` per DESIGN §11 and api.md §5.

Highlights:

* Auth via ``Sec-WebSocket-Protocol`` (``xijian.v1, bearer.<token>``) or
  a first-frame ``{"type": "auth", "token": "..."}`` envelope.
* Heartbeat (``ping`` / ``pong``) every 30 s.
* In-process pub/sub fan-out for the events the spec calls out
  (``character.*``, ``world.*``, ``memory.*``, ``protection.*``,
  ``generation.*``).
* Optional dev hook ``POST /v1/xijian/_test/emit`` calls
  :func:`publish_event`.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Iterable

from flask import Blueprint, current_app, request
from flask_sock import Sock

from xijian_api import auth
from xijian_api.utils.ids import gen_id
from xijian_api.utils.time import now_ts


# ---------------------------------------------------------------------------
# Blueprint + Sock setup
# ---------------------------------------------------------------------------


bp = Blueprint("ws_routes", __name__)
sock = Sock()

_PING_INTERVAL_SECONDS = 30
_HELLO_DELAY_SECONDS = 0  # immediate


# ---------------------------------------------------------------------------
# Connection registry — used by ``publish_event`` to fan out messages.
# ---------------------------------------------------------------------------


class _Subscriber:
    """A connected client waiting for events."""

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
    """Publish a server-side event to every connected subscriber.

    Used by dev tools (``POST /v1/xijian/_test/emit``) and by stub
    services that want to surface async progress.
    """
    payload = {
        "id": gen_id("evt_", 12),
        "type": event_type,
        "ts": now_ts(),
        "data": data or {},
    }
    _broadcast(payload)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _check_bearer_header() -> bool:
    """Return ``True`` if the request carries a valid Bearer subprotocol.

    Accepts both ``bearer-<token>`` (RFC-valid; RFC 6455 subprotocols
    use token syntax, no dots) and the dotted ``bearer.<token>`` form
    documented in api.md.  Both forms are accepted for compatibility
    with clients that haven't been updated to the dash form.
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
        if presented and presented == expected:
            return True
    return False


# ---------------------------------------------------------------------------
# Frame helpers
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
# Main entry point
# ---------------------------------------------------------------------------


@sock.route("/v1/ws", bp=bp)
def ws_endpoint(ws):
    """Handle a single WebSocket connection."""
    sub = _Subscriber(ws)
    _register(sub)

    try:
        # Parse the offered subprotocols once.
        offered = (request.headers.get("Sec-WebSocket-Protocol") or "").split(",")
        offered = [s.strip() for s in offered]
        has_xijian_v1 = "xijian.v1" in offered

        if not has_xijian_v1:
            # Refuse the upgrade: missing required subprotocol.
            _send(ws, _envelope("hello", {"server_version": "0.1.0"}))
            _send(ws, _envelope("auth.failed", {"reason": "missing_subprotocol"}))
            return

        # Greet.
        _send(ws, _envelope("hello", {"server_version": "0.1.0"}))

        # Try subprotocol-based auth first.
        if _check_bearer_header():
            sub.authed = True
            _send(ws, _envelope("auth.ok"))
        else:
            # No subprotocol auth → wait briefly for a first-frame
            # ``{"type": "auth", "token": "..."}`` envelope.
            try:
                first = ws.receive(timeout=2)
            except Exception:  # noqa: BLE001
                first = None
            if isinstance(first, str):
                try:
                    msg = json.loads(first)
                except json.JSONDecodeError:
                    msg = {}
                if msg.get("type") == "auth" and msg.get("token") == auth.get_token():
                    sub.authed = True
                    _send(ws, _envelope("auth.ok"))

        if not sub.authed:
            _send(ws, _envelope("auth.failed", {"reason": "invalid_token"}))
            return

        last_ping = time.time()
        # Schedule the dev proactive message.
        def _delayed_proactive():
            time.sleep(3)
            publish_event(
                "character.proactive_message",
                {
                    "character_id": "char_yuki",
                    "message": "你今天还好吗？",
                    "emotion": "concerned",
                },
            )
        threading.Thread(target=_delayed_proactive, daemon=True).start()

        while True:
            try:
                raw = ws.receive(timeout=_PING_INTERVAL_SECONDS)
            except Exception:  # noqa: BLE001 — likely a timeout
                raw = None

            if raw is None:
                # Heartbeat — only if the client hasn't pinged recently.
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
            # Other events are ignored on the server side.
    finally:
        sub.alive = False
        _unregister(sub)


def init_app(app) -> None:
    """Attach the Sock routes to ``app`` (called from register_routes)."""
    # Advertise ``xijian.v1`` as the subprotocol we accept.  Without
    # this, simple_websocket's ``choose_subprotocol`` returns ``None``
    # and the handshake response omits ``Sec-WebSocket-Protocol``,
    # which strict WS clients (e.g. ``websocket-client``) reject.
    app.config.setdefault("SOCK_SERVER_OPTIONS", {"subprotocols": ["xijian.v1"]})
    sock.init_app(app)


__all__ = ["bp", "init_app", "publish_event"]