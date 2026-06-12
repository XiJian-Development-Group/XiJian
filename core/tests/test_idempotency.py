"""Tests for the Idempotency-Key middleware (DESIGN §8)."""

from __future__ import annotations

import json

ECHO_URL = "/v1/__test__/echo"


def _post(client, auth_headers, idem_key, body):
    return client.post(
        ECHO_URL,
        headers={
            **auth_headers,
            "Idempotency-Key": idem_key,
            "Content-Type": "application/json",
        },
        data=json.dumps(body),
    )


def test_repeated_post_with_same_body_is_replayed(client, auth_headers):
    """Two POSTs with the same key and same body return the same payload,
    with ``Idempotency-Replayed: true`` on the second response."""
    body = {"messages": [{"role": "user", "content": "hello"}]}

    r1 = _post(client, auth_headers, "key-1", body)
    assert r1.status_code == 200
    assert r1.get_json()["echo"] == body
    # The first call has no ``Idempotency-Replayed`` header.
    assert r1.headers.get("Idempotency-Replayed") is None

    r2 = _post(client, auth_headers, "key-1", body)
    assert r2.status_code == 200
    assert r2.get_json() == r1.get_json()
    assert r2.headers.get("Idempotency-Replayed") == "true"


def test_same_key_different_body_returns_409(client, auth_headers):
    """Reusing a key with a different body raises 409 ``idempotency_key_conflict``."""
    body_a = {"messages": [{"role": "user", "content": "a"}]}
    body_b = {"messages": [{"role": "user", "content": "b"}]}

    r1 = _post(client, auth_headers, "key-2", body_a)
    assert r1.status_code == 200

    r2 = _post(client, auth_headers, "key-2", body_b)
    assert r2.status_code == 409
    payload = r2.get_json()
    assert payload["error"]["type"] == "conflict"
    assert payload["error"]["code"] == "idempotency_key_conflict"


def test_different_keys_do_not_collide(client, auth_headers):
    """Two POSTs with different keys are not treated as replays."""
    body = {"messages": [{"role": "user", "content": "hi"}]}

    r1 = _post(client, auth_headers, "key-a", body)
    r2 = _post(client, auth_headers, "key-b", body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Neither should be marked replayed.
    assert r1.headers.get("Idempotency-Replayed") is None
    assert r2.headers.get("Idempotency-Replayed") is None


def test_no_idempotency_key_means_no_replay(client, auth_headers):
    """POSTs without an ``Idempotency-Key`` header are not cached."""
    body = {"messages": [{"role": "user", "content": "no-cache"}]}
    headers = {**auth_headers, "Content-Type": "application/json"}

    r1 = client.post(ECHO_URL, headers=headers, data=json.dumps(body))
    r2 = client.post(ECHO_URL, headers=headers, data=json.dumps(body))
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.headers.get("Idempotency-Replayed") is None
    assert r2.headers.get("Idempotency-Replayed") is None


def test_idempotency_only_applies_to_post(client, auth_headers):
    """A GET with an ``Idempotency-Key`` header is not cached."""
    response = client.get(
        "/v1",
        headers={**auth_headers, "Idempotency-Key": "ignored-on-get"},
    )
    assert response.status_code == 200
    assert response.headers.get("Idempotency-Replayed") is None
