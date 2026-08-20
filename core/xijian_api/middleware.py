"""Request middleware: request-id, trace-id, API-version, rate-limit headers
and idempotency.

请求中间件：请求 ID、追踪 ID、API 版本、速率限制标头和幂等性。

Each piece is documented individually below.  The ``install_middleware``
function wires them all up on a Flask app.

每个部分在下面单独说明。``install_middleware`` 函数将它们全部挂接到 Flask 应用上。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any

from flask import Flask, g, jsonify, request

from xijian_api import auth
from xijian_api.config import (
    API_VERSION,
    IDEMPOTENCY_TTL_SECONDS,
)
from xijian_api.errors import ApiError, render_error
from xijian_api.utils.ids import gen_request_id, gen_trace_id
from xijian_api.utils.log import get_logger

_LOGGER = get_logger()

# ---------------------------------------------------------------------------
# Idempotency cache
# 幂等性缓存
# ---------------------------------------------------------------------------

#: In-memory idempotency cache keyed by ``Idempotency-Key`` header.
#: Each entry stores ``{"key_hash", "status", "headers", "body",
#: "expires_at"}`` where ``key_hash`` is the sha256 of the key + body
#: tuple (DESIGN §8).  An :class:`~collections.OrderedDict` gives the
#: cache an LRU bound (S8): insertion order doubles as recency, the
#: most recently used key is moved to the end on write, and when the
#: cache exceeds ``_IDEM_CACHE_MAX`` entries the oldest (front) entry
#: is evicted.
#: 内存中的幂等性缓存，以 ``Idempotency-Key`` 标头为键。
#: 每个条目存储 ``{"key_hash", "status", "headers", "body", "expires_at"}``，
#: 其中 ``key_hash`` 是 key + body 元组的 sha256（DESIGN §8）。
#: 使用 :class:`~collections.OrderedDict` 为缓存加 LRU 上限 (S8)：
#: 插入顺序兼作新鲜度，最新使用的键写入时移到末尾，
#: 当缓存超过 ``_IDEM_CACHE_MAX`` 条时淘汰最旧（队首）条目。
_idem_cache: OrderedDict[str, dict] = OrderedDict()
_idem_lock = threading.Lock()

#: Hard cap on idempotency cache entries (S8).
#: 幂等性缓存条目的硬上限 (S8)。
_IDEM_CACHE_MAX = 4096


def _mask_key(idem_key: str) -> str:
    """Return a masked form of ``idem_key`` suitable for log lines.

    返回适合日志行的掩码形式的 ``idem_key``。

    Per DESIGN §8 we never log the raw key — only the first 4 chars
    followed by ``"***"``.

    根据 DESIGN §8，我们从不记录原始键——只记录前 4 个字符后跟 ``"***"``。
    """
    if not idem_key:
        return "***"
    return idem_key[:4] + "***"


def _compute_body_hash(body: bytes, idem_key: str) -> str:
    """Return a sha256 hex digest of ``idem_key + body``.

    返回 ``idem_key + body`` 的 sha256 十六进制摘要。
    """
    h = hashlib.sha256()
    h.update(idem_key.encode("utf-8"))
    h.update(b"\x00")
    h.update(body or b"")
    return h.hexdigest()


def _cleanup_expired() -> None:
    """Remove expired entries from the idempotency cache.

    从幂等性缓存中移除过期的条目。

    Called lazily on every cache read so we don't need a background
    sweeper thread.

    在每次缓存读取时惰性调用，因此我们不需要后台清理线程。
    """
    now = time.time()
    expired = [k for k, v in _idem_cache.items() if v["expires_at"] <= now]
    for key in expired:
        _idem_cache.pop(key, None)


def _cache_get(idem_key: str) -> dict | None:
    """Return the cache entry for ``idem_key`` if it exists and is fresh.

    如果存在且未过期，返回 ``idem_key`` 的缓存条目。
    """
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
    """Insert a new entry in the idempotency cache.

    在幂等性缓存中插入新条目。
    """
    with _idem_lock:
        _cleanup_expired()
        _idem_cache[idem_key] = {
            "key_hash": key_hash,
            "status": status,
            "headers": dict(headers),
            "body": body,
            "expires_at": time.time() + IDEMPOTENCY_TTL_SECONDS,
        }
        _idem_cache.move_to_end(idem_key)
        # S8 — LRU eviction: expired entries were just swept; if the
        # cache is still over the cap, drop the least-recently-used
        # (front) entry until it fits.
        # S8 — LRU 淘汰：过期条目刚被清除；若缓存仍超上限，
        # 持续丢弃最久未使用（队首）的条目直至容量合适。
        while len(_idem_cache) > _IDEM_CACHE_MAX:
            _idem_cache.popitem(last=False)


def reset_idempotency_cache_for_testing() -> None:
    """Clear the idempotency cache (used by tests).

    清除幂等性缓存（供测试使用）。
    """
    with _idem_lock:
        _idem_cache.clear()


# ---------------------------------------------------------------------------
# Middleware installation
# 中间件安装
# ---------------------------------------------------------------------------


def _ensure_request_ids() -> None:
    """Populate ``g.request_id`` and ``g.trace_id`` if missing.

    如果缺失，填充 ``g.request_id`` 和 ``g.trace_id``。

    Clients may supply their own via the corresponding ``X-XiJian-*``
    headers; otherwise we generate fresh ones.

    客户端可以通过相应的 ``X-XiJian-*`` 标头提供自己的 ID；否则我们生成新的。
    """
    request_id = request.headers.get("X-XiJian-Request-Id") or gen_request_id()
    trace_id = request.headers.get("X-XiJian-Trace-Id") or gen_trace_id()
    g.request_id = request_id
    g.trace_id = trace_id


def _add_common_headers(response):
    """Stamp the standard response headers on ``response``.

    在 ``response`` 上打上标准响应标头。
    """
    response.headers.setdefault("X-XiJian-API-Version", API_VERSION)
    # Echo back request / trace ids so clients can correlate.
    # 回显请求/追踪 ID，以便客户端可以关联。
    request_id = getattr(g, "request_id", None)
    if request_id:
        response.headers.setdefault("X-XiJian-Request-Id", request_id)
    trace_id = getattr(g, "trace_id", None)
    if trace_id:
        response.headers.setdefault("X-XiJian-Trace-Id", trace_id)
    return response


# ---------------------------------------------------------------------------
# Idempotency
# 幂等性
# ---------------------------------------------------------------------------


def _maybe_replay_idempotent() -> Any | None:
    """Return a cached response if this is a replayed POST.

    如果这是一个重放的 POST 请求，返回缓存的响应。

    Returns ``None`` if idempotency does not apply (no header, GET, or
    cache miss).  Raises :class:`ApiError` if the key was used with a
    different body.

    如果幂等性不适用（无标头、GET 或缓存未命中），返回 ``None``。
    如果该键与不同的请求体一起使用，抛出 :class:`ApiError`。
    """
    if request.method != "POST":
        return None
    idem_key = request.headers.get("Idempotency-Key")
    if not idem_key:
        return None

    # Read the raw body so we can hash it.  We must buffer it because
    # Flask will need to read it again later for the actual view.
    # 读取原始请求体以便计算哈希。我们必须缓冲它，因为 Flask 后续还需要为实际视图再次读取。
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
            # 不回显内部标头。
            if name.lower() in {"content-length", "content-type"}:
                continue
            replayed.headers[name] = value
        replayed.headers["Idempotency-Replayed"] = "true"
        return replayed

    # Mark the request so :func:`_store_idempotent_response` knows what
    # to do after the view runs.
    # 标记请求，以便 :func:`_store_idempotent_response` 知道视图运行后该做什么。
    g._idem_key = idem_key
    g._idem_body_hash = body_hash
    g._idem_log_key = log_key
    return None


def _store_idempotent_response(response) -> Any:
    """If a POST carried an Idempotency-Key, cache the response.

    如果 POST 请求带有 Idempotency-Key，缓存响应。
    """
    idem_key = getattr(g, "_idem_key", None)
    if not idem_key or request.method != "POST":
        return response

    # Don't cache streamed responses — Flask gives us a generator
    # body that isn't trivially re-emittable.
    # 不要缓存流式响应——Flask 给我们的生成器请求体不容易重新发出。
    if response.is_streamed:
        _LOGGER.info(
            "idempotency skipped (streamed response): %s",
            getattr(g, "_idem_log_key", "***"),
        )
        return response

    try:
        payload = response.get_json()
    except Exception:  # noqa: BLE001 — broad catch: anything non-JSON is fine
        # 宽泛捕获：任何非 JSON 的响应都没问题
        payload = None
    if payload is None:
        # Non-JSON response (e.g. binary file content) — skip caching.
        # 非 JSON 响应（例如二进制文件内容）— 跳过缓存。
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
# 公开 API
# ---------------------------------------------------------------------------


def install_middleware(app: Flask) -> None:
    """Wire all request middleware on ``app``.

    在 ``app`` 上挂接所有请求中间件。

    Order matters:

    顺序很重要：

    1. ``before_request`` populates ``g.request_id`` / ``g.trace_id``.
       ``before_request`` 填充 ``g.request_id`` / ``g.trace_id``。
    2. The auth check runs (raises :class:`AuthError` on failure).
       认证检查运行（失败时抛出 :class:`AuthError`）。
    3. Idempotency replay is attempted before the view runs.
       在视图运行前尝试幂等性重放。
    4. ``after_request`` stamps the standard headers.
       ``after_request`` 打上标准标头。
    5. The post-request hook stores idempotent responses.
       请求后钩子存储幂等响应。
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
        # 始终先打上 ID 标记（前一个钩子已经做了，
        # 但对于绕过顺序的测试保持自包含）。
        _ensure_request_ids()
        auth.verify_bearer()


def _install_idempotency(app: Flask) -> None:
    @app.before_request
    def _idempotency_replay():  # type: ignore[no-redef]
        replay = _maybe_replay_idempotent()
        if replay is not None:
            # Returning a Response short-circuits the view function.
            # 返回 Response 会短路视图函数。
            return replay

    @app.after_request
    def _idempotency_store(response):  # type: ignore[no-redef]
        return _store_idempotent_response(response)


def _install_after_request(app: Flask) -> None:
    @app.after_request
    def _stamp_headers(response):  # type: ignore[no-redef]
        return _add_common_headers(response)


# ---------------------------------------------------------------------------
# Rate Limiting (Token Bucket)
# 限流（令牌桶）
# ---------------------------------------------------------------------------
#
# Per-endpoint token bucket rate limiting with configurable capacity and
# refill rate.  Enabled by default in production (dev=false), controlled
# by config.features.rate_limit.
#
# 每端点令牌桶限流，可配置容量和填充速率。生产环境（dev=false）默认开启，
# 由 config.features.rate_limit 控制。
#
# Returns 429 with Retry-After header when limit exceeded.
# 超限时返回 429 并附带 Retry-After 标头。


_rate_limit_buckets: dict[str, dict] = {}
_rate_limit_lock = threading.Lock()
_DEFAULT_CAPACITY = 60  # requests per window
_DEFAULT_REFILL_RATE = 1.0  # tokens per second


def _get_rate_limit_config() -> tuple[int, float, bool]:
    """Get rate limit config from Flask app config.

    Returns (capacity, refill_rate, enabled).
    """
    try:
        from flask import current_app
        config = current_app.config.get("XIJIAN_CONFIG")
        if config is not None:
            # Check if rate limit is enabled (dev mode disables by default)
            dev_mode = getattr(config.server, "dev", False)
            enabled = config.features.rate_limit and not dev_mode
            # Allow customization via config (future: add rate_limit fields to config)
            capacity = getattr(config, "rate_limit_capacity", _DEFAULT_CAPACITY)
            refill_rate = getattr(config, "rate_limit_refill_rate", _DEFAULT_REFILL_RATE)
            return capacity, refill_rate, enabled
    except Exception:
        pass
    return _DEFAULT_CAPACITY, _DEFAULT_REFILL_RATE, False


def _rate_limit_key() -> str:
    """Generate a rate limit key for the current request.

    Uses client IP + endpoint path for per-endpoint limiting.
    """
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    path = request.path
    return f"{ip}:{path}"


def _check_rate_limit() -> tuple[bool, dict]:
    """Check and consume a token from the bucket.

    Returns (allowed, headers) where headers contains RateLimit-* headers
    for the response.
    """
    capacity, refill_rate, enabled = _get_rate_limit_config()
    if not enabled:
        return True, {}

    key = _rate_limit_key()
    now = time.time()

    with _rate_limit_lock:
        bucket = _rate_limit_buckets.get(key)
        if bucket is None:
            bucket = {
                "tokens": float(capacity),
                "last_refill": now,
            }
            _rate_limit_buckets[key] = bucket

        # Refill tokens based on elapsed time
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(capacity, bucket["tokens"] + elapsed * refill_rate)
        bucket["last_refill"] = now

        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            allowed = True
        else:
            allowed = False

        # Calculate headers
        retry_after = int((1.0 - bucket["tokens"]) / refill_rate) + 1 if not allowed else 0
        headers = {
            "X-RateLimit-Limit": str(capacity),
            "X-RateLimit-Remaining": str(max(0, int(bucket["tokens"]))),
            "X-RateLimit-Reset": str(int(now + retry_after)),
        }
        if not allowed:
            headers["Retry-After"] = str(retry_after)

        return allowed, headers


def _install_rate_limit(app: Flask) -> None:
    @app.before_request
    def _rate_limit_check():  # type: ignore[no-redef]
        allowed, headers = _check_rate_limit()
        if not allowed:
            # Store headers on g so after_request can add them
            g._rate_limit_headers = headers
            raise ApiError(
                status=429,
                message="rate limit exceeded",
                type_="rate_limit_error",
                code="rate_limit_exceeded",
            )
    @app.after_request
    def _rate_limit_headers(response):  # type: ignore[no-redef]
        rl_headers = getattr(g, "_rate_limit_headers", None)
        if rl_headers:
            for k, v in rl_headers.items():
                response.headers.setdefault(k, v)
        return response


def install_middleware(app: Flask) -> None:
    """Wire all request middleware on ``app``.

    在 ``app`` 上挂接所有请求中间件。

    Order matters:

    顺序很重要：

    1. ``before_request`` populates ``g.request_id`` / ``g.trace_id``.
       ``before_request`` 填充 ``g.request_id`` / ``g.trace_id``。
    2. The auth check runs (raises :class:`AuthError` on failure).
       认证检查运行（失败时抛出 :class:`AuthError`）。
    3. Rate limit check runs (returns 429 on limit exceeded).
       限流检查运行（超限时返回 429）。
    4. Idempotency replay is attempted before the view runs.
       在视图运行前尝试幂等性重放。
    5. ``after_request`` stamps the standard headers.
       ``after_request`` 打上标准标头。
    6. The post-request hook stores idempotent responses.
       请求后钩子存储幂等响应。
    """
    _install_request_id(app)
    _install_auth(app)
    _install_rate_limit(app)
    _install_idempotency(app)
    _install_after_request(app)


__all__ = [
    "install_middleware",
    "reset_idempotency_cache_for_testing",
    "_get_rate_limit_config",
    "_check_rate_limit",
]
