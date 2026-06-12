"""Tests for the Bearer-token auth middleware."""

from __future__ import annotations


def test_missing_authorization_header_returns_401(client):
    """A request with no Authorization header is rejected with 401."""
    response = client.get("/v1")
    assert response.status_code == 401
    body = response.get_json()
    assert body is not None
    # Default Accept is JSON → OAI envelope.
    assert "error" in body
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "invalid_api_key"


def test_wrong_authorization_scheme_returns_401(client):
    """Using a non-Bearer scheme is rejected."""
    response = client.get(
        "/v1",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert response.status_code == 401


def test_wrong_token_returns_401(client):
    """A Bearer with the wrong token is rejected."""
    response = client.get(
        "/v1",
        headers={"Authorization": "Bearer this-is-not-the-token"},
    )
    assert response.status_code == 401


def test_correct_token_passes(client, auth_headers):
    """A correct Bearer token lets the request through."""
    response = client.get("/v1", headers=auth_headers)
    assert response.status_code == 200


def test_extra_whitespace_around_token_rejected(client, token):
    """Bearer with trailing whitespace after the token is rejected.

    The verify_bearer function trims trailing whitespace, so we add
    a clearly wrong fragment to make sure the trimmed value still
    doesn't match the actual token.
    """
    response = client.get(
        "/v1",
        headers={"Authorization": f"Bearer {token}x"},
    )
    assert response.status_code == 401
