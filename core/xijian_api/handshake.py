"""Handshake primitives: ``/healthz`` and port-token file helpers.

握手原语：``/healthz`` 和端口令牌文件辅助函数。

The :func:`register_healthz` function installs the unauthenticated
``/healthz`` route on a Flask app.  Per ``DESIGN.md`` §3.3 it returns
``XIJIAN_OK_v1`` as ``text/plain``.

:func:`register_healthz` 函数在 Flask 应用上安装无需认证的 ``/healthz`` 路由。
根据 ``DESIGN.md`` §3.3，它返回 ``text/plain`` 格式的 ``XIJIAN_OK_v1``。

Port-token file handling lives in :mod:`xijian_api.auth`; we keep
this module focused on the HTTP probe.

端口令牌文件处理在 :mod:`xijian_api.auth` 中；我们将此模块专注于 HTTP 探测。
"""

from __future__ import annotations

from flask import Flask, Response


HEALTHZ_BODY = "XIJIAN_OK_v1"


def register_healthz(app: Flask) -> None:
    """Install ``GET /healthz`` on ``app``.

    在 ``app`` 上安装 ``GET /healthz`` 路由。

    The route bypasses authentication (the auth middleware checks for
    ``request.path == "/healthz"`` explicitly) but we still want the
    response to carry the standard headers (request-id echo, etc.),
    so we leave the middleware in place.

    该路由绕过认证（认证中间件显式检查 ``request.path == "/healthz"``），
    但我们仍希望响应携带标准标头（请求 ID 回显等），因此我们保留中间件。
    """

    @app.get("/healthz")
    def healthz() -> Response:  # type: ignore[no-redef]
        return Response(HEALTHZ_BODY, status=200, mimetype="text/plain")


__all__ = ["register_healthz", "HEALTHZ_BODY"]
