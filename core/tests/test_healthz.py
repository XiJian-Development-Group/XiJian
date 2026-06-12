"""Tests for the unauthenticated ``/healthz`` probe."""

from __future__ import annotations


def test_healthz_returns_200_and_body(client):
    """``GET /healthz`` returns the handshake string with status 200."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.data == b"XIJIAN_OK_v1"


def test_healthz_is_text_plain(client):
    """The body is served as ``text/plain``."""
    response = client.get("/healthz")
    content_type = response.headers.get("Content-Type", "")
    assert content_type.startswith("text/plain")


def test_healthz_does_not_require_bearer(client):
    """``/healthz`` is reachable without an Authorization header."""
    # No Authorization header at all — must still succeed.
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.data == b"XIJIAN_OK_v1"


def test_healthz_ignores_wrong_bearer(client):
    """Even an invalid Bearer does not block the handshake."""
    response = client.get(
        "/healthz",
        headers={"Authorization": "Bearer not-the-real-token"},
    )
    assert response.status_code == 200


def test_healthz_stamps_api_version_header(client):
    """The standard ``X-XiJian-API-Version`` header is set on responses."""
    response = client.get("/healthz")
    assert response.headers.get("X-XiJian-API-Version") == "1.0.0"
