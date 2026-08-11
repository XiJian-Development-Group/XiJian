"""Tests for the Idempotency-Key middleware (DESIGN §8).
(幂等性键中间件的测试 (DESIGN §8)。)
"""

from __future__ import annotations

import json

from xijian_api.middleware import reset_idempotency_cache_for_testing

ECHO_URL = "/v1/__test__/echo"


def _post(client, auth_headers, idem_key, body):
    """Helper to POST with idempotency key set.
    (使用设置好的幂等性键执行 POST 的辅助函数。)
    """
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
    with ``Idempotency-Replayed: true`` on the second response.
    (相同键和相同体的两个 POST 返回相同负载，
    第二个响应带有 ``Idempotency-Replayed: true``。)
    """
    body = {"messages": [{"role": "user", "content": "hello"}]}

    r1 = _post(client, auth_headers, "key-1", body)
    assert r1.status_code == 200
    assert r1.get_json()["echo"] == body
    # The first call has no ``Idempotency-Replayed`` header.
    # (第一次调用没有 ``Idempotency-Replayed`` 头部。)
    assert r1.headers.get("Idempotency-Replayed") is None

    r2 = _post(client, auth_headers, "key-1", body)
    assert r2.status_code == 200
    assert r2.get_json() == r1.get_json()
    assert r2.headers.get("Idempotency-Replayed") == "true"


def test_same_key_different_body_returns_409(client, auth_headers):
    """Reusing a key with a different body raises 409 ``idempotency_key_conflict``.
    (使用相同键但不同请求体复用导致 409 ``idempotency_key_conflict``。)
    """
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
    """Two POSTs with different keys are not treated as replays.
    (不同键的两个 POST 不会被视为重放。)
    """
    body = {"messages": [{"role": "user", "content": "hi"}]}

    r1 = _post(client, auth_headers, "key-a", body)
    r2 = _post(client, auth_headers, "key-b", body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Neither should be marked replayed.
    # (两者都不应标记为重放。)
    assert r1.headers.get("Idempotency-Replayed") is None
    assert r2.headers.get("Idempotency-Replayed") is None


def test_no_idempotency_key_means_no_replay(client, auth_headers):
    """POSTs without an ``Idempotency-Key`` header are not cached.
    (没有 ``Idempotency-Key`` 头部的 POST 不会被缓存。)
    """
    body = {"messages": [{"role": "user", "content": "no-cache"}]}
    headers = {**auth_headers, "Content-Type": "application/json"}

    r1 = client.post(ECHO_URL, headers=headers, data=json.dumps(body))
    r2 = client.post(ECHO_URL, headers=headers, data=json.dumps(body))
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.headers.get("Idempotency-Replayed") is None
    assert r2.headers.get("Idempotency-Replayed") is None


def test_idempotency_only_applies_to_post(client, auth_headers):
    """A GET with an ``Idempotency-Key`` header is not cached.
    (带有 ``Idempotency-Key`` 头部的 GET 不会被缓存。)
    """
    response = client.get(
        "/v1",
        headers={**auth_headers, "Idempotency-Key": "ignored-on-get"},
    )
    assert response.status_code == 200
    assert response.headers.get("Idempotency-Replayed") is None


# ---------------------------------------------------------------------------
# S8 — LRU-bounded idempotency cache
# S8 — 有 LRU 上限的幂等性缓存
# ---------------------------------------------------------------------------


def test_idempotency_cache_is_lru_bounded(monkeypatch):
    """When the cache exceeds its cap, the oldest entry is evicted (S8)."""
    from xijian_api import middleware as mw

    monkeypatch.setattr(mw, "_IDEM_CACHE_MAX", 3)
    with mw._idem_lock:
        mw._idem_cache.clear()

    # Fill the cache past the cap with unique keys.
    for i in range(5):
        mw._cache_put(f"lru-key-{i}", f"hash-{i}", 200, {}, {"n": i})

    with mw._idem_lock:
        keys = list(mw._idem_cache.keys())
        size = len(mw._idem_cache)
    assert size == 3
    # Oldest two entries were evicted; the newest three survive.
    assert "lru-key-0" not in keys and "lru-key-1" not in keys
    assert keys == ["lru-key-2", "lru-key-3", "lru-key-4"]


def test_idempotency_replay_after_eviction_is_fresh(client, auth_headers, monkeypatch):
    """An evicted key replays as a fresh request, not a cache hit (S8)."""
    from xijian_api import middleware as mw

    monkeypatch.setattr(mw, "_IDEM_CACHE_MAX", 3)
    reset_idempotency_cache_for_testing()

    body = {"messages": [{"role": "user", "content": "hello"}]}

    # 4 unique keys → the first is evicted.
    for i in range(4):
        r = _post(client, auth_headers, f"lru-api-{i}", body)
        assert r.status_code == 200

    # Replaying the evicted key must NOT be flagged as a replay.
    r_evicted = _post(client, auth_headers, "lru-api-0", body)
    assert r_evicted.headers.get("Idempotency-Replayed") is None

    # Replaying a still-cached key IS flagged.
    r_cached = _post(client, auth_headers, "lru-api-3", body)
    assert r_cached.headers.get("Idempotency-Replayed") == "true"
