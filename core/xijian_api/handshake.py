"""Handshake primitives: ``/healthz`` and port-token file helpers.

The :func:`register_healthz` function installs the unauthenticated
``/healthz`` route on a Flask app.  Per ``DESIGN.md`` §3.3 it returns
``XIJIAN_OK_v1`` as ``text/plain``.

Port-token file handling lives in :mod:`xijian_api.auth`; we keep
this module focused on the HTTP probe.
"""

from __future__ import annotations

from flask import Flask, Response


HEALTHZ_BODY = "XIJIAN_OK_v1"


def register_healthz(app: Flask) -> None:
    """Install ``GET /healthz`` on ``app``.

    The route bypasses authentication (the auth middleware checks for
    ``request.path == "/healthz"`` explicitly) but we still want the
    response to carry the standard headers (request-id echo, etc.),
    so we leave the middleware in place.
    """

    @app.get("/healthz")
    def healthz() -> Response:  # type: ignore[no-redef]
        return Response(HEALTHZ_BODY, status=200, mimetype="text/plain")


__all__ = ["register_healthz", "HEALTHZ_BODY"]