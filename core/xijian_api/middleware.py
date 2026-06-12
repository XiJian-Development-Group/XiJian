"""Request middleware: request-id, trace-id, API-version, rate-limit headers
and idempotency.

Each piece is documented individually below.  The ``install_middleware``
function wires them all up on a Flask app.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from flask import Flask, g, jsonify, request

from xijian_api import auth
from xijian_api.config import (
    API_VERSION,
    IDEMPOTENCY_TTL_SECONDS,
    RATE_LIMIT_LIMIT_REQUESTS,
    RATE_LIMIT_REMAINING_REQUESTS,
)
from xijian_api.errors import ApiError
from xijian_api.utils.ids import gen_request_id, gen_trace_id
from xijian_api.utils.log import get_logger

_LOGGER = get_logger()

# ---------------------------------------------------------------------------
# Idempotency cache
# ---------------------------------------------------------------------------

#: In-memory idempotency cache keyed by ``Idempotency-Key`` header.
#: Each entry stores ``{"key_hash", "status", "headers", "body",
#: "expires_at"}`` where ``key_hash`` is the sha256 of the key + body
#: tuple (DESIGN §8).
_idem_cache: dict[str, dict] = {}
_idem_lock = threading.Lock()


def _mask_key(idem_key: str) -> str:
    """Return a masked form of ``idem_key`` suitable for log lines.

    Per DESIGN §8 we never log the raw key — only the first 4 chars
    followed by ``"***"``.
    """
    if not idem_key:
        return "***"
    return idem_key[:4] + "***"


def _compute_body_hash(body: bytes, idem_key: str) -> str:
    """Return a sha256 hex digest of ``idem_key + body``."""
    h = hashlib.sha256()
    h.update(idem_key.encode("utf-8"))
    h.update(b"\x00")
    h.update(body or b"")
    return h.hexdigest()


def _cleanup_expired() -> None:
    """Remove expired entries from the idempotency cache.

    Called lazily on every cache read so we don't need a background
    sweeper thread.
    """
    now = time.time()
    expired = [k for k, v in _idem_cache.items() if v["expires_at"] <= now]
    for key in expired:
        _idem_cache.pop(key, None)


def _cache_get(idem_key: str) -> dict | None:
    """Return the cache entry for ``idem_key`` if it exists and is fresh."""
    with _idem_lock:
        _cleanup_expired()
        entry = _idem_cache.get(idem_key)
        return entry


def _cache_put(
    idem_key: str,
    key_hash: str,
    status: int,
    headers: dict,
    body: Any,
) -> None:
    """Insert a new entry in the idempotency cache."""
    with _idem_lock:
        _idem_cache[idem_key] = {
            "key_hash": key_hash,
            "status": status,
            "headers": dict(headers),
            "body": body,
            "expires_at": time.time() + IDEMPOTENCY_TTL_SECONDS,
        }


def reset_idempotency_cache_for_testing() -> None:
    """Clear the idempotency cache (used by tests)."""
    with _idem_lock:
        _idem_cache.clear()


# ---------------------------------------------------------------------------
# Middleware installation
# ---------------------------------------------------------------------------


def _ensure_request_ids() -> None:
    """Populate ``g.request_id`` and ``g.trace_id`` if missing.

    Clients may supply their own via the corresponding ``X-XiJian-*``
    headers; otherwise we generate fresh ones.
    """
    request_id = request.headers.get("X-XiJian-Request-Id") or gen_request_id()
    trace_id = request.headers.get("X-XiJian-Trace-Id") or gen_trace_id()
    g.request_id = request_id
    g.trace_id = trace_id


def _add_common_headers(response):
    """Stamp the standard response headers on ``response``."""
    response.headers.setdefault("X-XiJian-API-Version", API_VERSION)
    # Echo back request / trace ids so clients can correlate.
    request_id = getattr(g, "request_id", None)
    if request_id:
        response.headers.setdefault("X-XiJian-Request-Id", request_id)
    trace_id = getattr(g, "trace_id", None)
    if trace_id:
        response.headers.setdefault("X-XiJian-Trace-Id", trace_id)
    # Rate-limit headers — DESIGN §5: "local default 0 限流 but keep
    # the headers".
    response.headers.setdefault(
        "X-RateLimit-Limit-Requests", str(RATE_LIMIT_LIMIT_REQUESTS)
    )
    response.headers.setdefault(
        "X-RateLimit-Remaining-Requests", str(RATE_LIMIT_REMAINING_REQUESTS)
    )
    response.headers.setdefault(
        "X-RateLimit-Limit-Tokens", str(RATE_LIMIT_LIMIT_REQUESTS)
    )
    response.headers.setdefault(
        "X-RateLimit-Remaining-Tokens", str(RATE_LIMIT_REMAINING_REQUESTS)
    )
    return response


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _maybe_replay_idempotent() -> Any | None:
    """Return a cached response if this is a replayed POST.

    Returns ``None`` if idempotency does not apply (no header, GET, or
    cache miss).  Raises :class:`ApiError` if the key was used with a
    different body.
    """
    if request.method != "POST":
        return None
    idem_key = request.headers.get("Idempotency-Key")
    if not idem_key:
        return None

    # Read the raw body so we can hash it.  We must buffer it because
    # Flask will need to read it again later for the actual view.
    raw_body = request.get_data(cache=True, as_text=False)
    body_hash = _compute_body_hash(raw_body, idem_key)
    log_key = _mask_key(idem_key)

    cached = _cache_get(idem_key)
    if cached is not None:
        if cached["key_hash"] != body_hash:
            _LOGGER.warning("idempotency key reuse with different body: %s", log_key)
            raise ApiError(
                status=409,
                message="Idempotency-Key reused with different body",
                type_="conflict",
                code="idempotency_key_conflict",
            )
        _LOGGER.info("idempotency replay: %s", log_key)
        replayed = jsonify(cached["body"])
        replayed.status_code = cached["status"]
        for name, value in cached["headers"].items():
            # Don't echo back internal headers.
            if name.lower() in {"content-length", "content-type"}:
                continue
            replayed.headers[name] = value
        replayed.headers["Idempotency-Replayed"] = "true"
        return replayed

    # Mark the request so :func:`_store_idempotent_response` knows what
    # to do after the view runs.
    g._idem_key = idem_key
    g._idem_body_hash = body_hash
    g._idem_log_key = log_key
    return None


def _store_idempotent_response(response) -> Any:
    """If a POST carried an Idempotency-Key, cache the response."""
    idem_key = getattr(g, "_idem_key", None)
    if not idem_key or request.method != "POST":
        return response

    # Don't cache streamed responses — Flask gives us a generator
    # body that isn't trivially re-emittable.
    if response.is_streamed:
        _LOGGER.info(
            "idempotency skipped (streamed response): %s",
            getattr(g, "_idem_log_key", "***"),
        )
        return response

    try:
        payload = response.get_json()
    except Exception:  # noqa: BLE001 — broad catch: anything non-JSON is fine
        payload = None
    if payload is None:
        # Non-JSON response (e.g. binary file content) — skip caching.
        return response

    headers = {k: v for k, v in response.headers.items()}
    _cache_put(
        idem_key,
        getattr(g, "_idem_body_hash", ""),
        response.status_code,
        headers,
        payload,
    )
    _LOGGER.info("idempotency stored: %s", getattr(g, "_idem_log_key", "***"))
    return response


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_middleware(app: Flask) -> None:
    """Wire all request middleware on ``app``.

    Order matters:

    1. ``before_request`` populates ``g.request_id`` / ``g.trace_id``.
    2. The auth check runs (raises :class:`AuthError` on failure).
    3. Idempotency replay is attempted before the view runs.
    4. ``after_request`` stamps the standard headers.
    5. The post-request hook stores idempotent responses.
    """
    _install_request_id(app)
    _install_auth(app)
    _install_idempotency(app)
    _install_after_request(app)


def _install_request_id(app: Flask) -> None:
    @app.before_request
    def _ensure_ids():  # type: ignore[no-redef]
        _ensure_request_ids()


def _install_auth(app: Flask) -> None:
    @app.before_request
    def _check_auth():  # type: ignore[no-redef]
        # Always stamp ids first (already done by the previous hook,
        # but keep this self-contained for tests that bypass ordering).
        _ensure_request_ids()
        auth.verify_bearer()


def _install_idempotency(app: Flask) -> None:
    @app.before_request
    def _idempotency_replay():  # type: ignore[no-redef]
        replay = _maybe_replay_idempotent()
        if replay is not None:
            # Returning a Response short-circuits the view function.
            return replay

    @app.after_request
    def _idempotency_store(response):  # type: ignore[no-redef]
        return _store_idempotent_response(response)


def _install_after_request(app: Flask) -> None:
    @app.after_request
    def _stamp_headers(response):  # type: ignore[no-redef]
        return _add_common_headers(response)


__all__ = [
    "install_middleware",
    "reset_idempotency_cache_for_testing",
]