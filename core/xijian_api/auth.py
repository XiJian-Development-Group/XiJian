"""Bearer token loading and verification.

Bearer 令牌加载与验证。

The token lives in a file under the unified temporary directory
(``~/Library/Application Support/XiJian/tmp/xijian-<pid>.token``).  In
production the parent process writes the file before launching us and
sets ``XIJIAN_DEV_TOKEN_FILE`` to a non-empty value if the file should
be kept around; otherwise we ``unlink`` it after reading so it cannot
leak.

令牌保存在统一临时目录下的 ``xijian-<pid>.token`` 文件中
（``~/Library/Application Support/XiJian/tmp/xijian-<pid>.token``）。
生产环境下父进程在启动我们之前写入该文件，并设置 ``XIJIAN_DEV_TOKEN_FILE``
为非空值以保留文件；否则我们在读取后 ``unlink`` 它以防止泄露。

In dev mode (``XIJIAN_DEV=1``) we generate a fresh 32-byte hex token,
write it to the canonical file with ``0600`` perms, and print it to
stderr — never to any HTTP response.

在开发模式 (``XIJIAN_DEV=1``) 下，我们会生成一个全新的 32 字节十六进制令牌，
以 ``0600`` 权限写入标准路径，并打印到 stderr——绝不会出现在任何 HTTP 响应中。

The verification function is :func:`verify_bearer`.  Per ``DESIGN.md``
§3.3 and §4.1 ``/healthz`` is exempt.

验证函数是 :func:`verify_bearer`。根据 ``DESIGN.md`` §3.3 和 §4.1，``/healthz`` 免于验证。
"""

from __future__ import annotations

import functools
import os
import secrets
from pathlib import Path
from typing import Callable

from flask import g, request

from xijian_api.config import Config, token_file_path
from xijian_api.errors import AuthError
from xijian_api.utils.log import get_logger

_LOGGER = get_logger()

# Module-level singleton (DESIGN §4.2).  Initialised by ``setup_token``.
# 模块级单例（DESIGN §4.2）。由 ``setup_token`` 初始化。
_TOKEN: str | None = None


def get_token() -> str | None:
    """Return the currently-loaded Bearer token, or ``None``.

    返回当前加载的 Bearer 令牌，或 ``None``。
    """
    return _TOKEN


def setup_token(config: Config, *, pid: int | None = None) -> str:
    """Initialise the in-memory token from disk (or generate one).

    从磁盘初始化内存中的令牌（或生成一个）。

    Returns the loaded (or freshly generated) token.

    返回加载的（或新生成的）令牌。

    Parameters
    ----------
    config:
        The :class:`xijian_api.config.Config` instance.
        :class:`xijian_api.config.Config` 实例。
    pid:
        Override the PID used to locate the token file (used by tests).
        覆盖用于定位令牌文件的 PID（供测试使用）。
    """
    global _TOKEN
    if _TOKEN is not None:
        return _TOKEN

    if config.testing:
        # Tests get a deterministic placeholder token.
        # 测试使用确定性的占位符令牌。
        _TOKEN = "test-token-do-not-use-in-prod"
        return _TOKEN

    path = token_file_path(pid)
    keep = config.keep_token_file

    if path.exists():
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            _LOGGER.error("failed to read token file %s: %s", path, exc)
            raise

        if not keep:
            try:
                path.unlink()
            except OSError as exc:
                _LOGGER.warning("token file %s could not be unlinked: %s", path, exc)
        else:
            try:
                os.chmod(path, 0o600)
            except OSError as exc:
                _LOGGER.warning("token file chmod failed for %s: %s", path, exc)

        if not token:
            raise RuntimeError(f"token file {path} is empty")
        _TOKEN = token
        _LOGGER.info("loaded bearer token from %s (kept=%s)", path, keep)
        return _TOKEN

    # No file present.
    # 文件不存在。
    if not config.dev:
        # Production: refuse to start without a pre-provisioned token.
        # 生产环境：拒绝在没有预置令牌的情况下启动。
        raise RuntimeError(
            f"token file {path} missing and XIJIAN_DEV not set; "
            "the API cannot start without a bearer token."
        )

    # Dev mode: generate a fresh token and write it to the canonical path.
    # 开发模式：生成新令牌并写入标准路径。
    token = secrets.token_hex(32)
    try:
        path.write_text(token, encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError as exc:
        _LOGGER.error("failed to write dev token file %s: %s", path, exc)
        raise

    _TOKEN = token
    # Print to stderr — never include in any HTTP response.
    # 输出到 stderr——绝不会包含在任何 HTTP 响应中。
    _LOGGER.info("dev token written to %s", path)
    print(f"[xijian-api] dev token: {token}", flush=True)
    return _TOKEN


def reset_for_testing() -> None:
    """Reset the module-level token (used by tests).

    重置模块级令牌（供测试使用）。
    """
    global _TOKEN
    _TOKEN = None


# ---------------------------------------------------------------------------
# Request-time verification
# 请求时验证
# ---------------------------------------------------------------------------


def _is_healthz() -> bool:
    """Return ``True`` if the current request is ``GET /healthz``.

    如果当前请求是 ``GET /healthz``，返回 ``True``。
    """
    return request.path == "/healthz" and request.method == "GET"


def _is_ws_endpoint() -> bool:
    """Return ``True`` if the current request targets ``/v1/ws``.

    如果当前请求目标是 ``/v1/ws``，返回 ``True``。

    The WebSocket endpoint has its own auth gate (subprotocol or first
    frame ``auth`` envelope) and must not be blocked by the HTTP
    bearer check before the protocol upgrade completes.

    WebSocket 端点有自己的认证门控（子协议或首帧 ``auth`` 信封），
    在协议升级完成前不能被 HTTP Bearer 检查阻止。
    """
    return request.path == "/v1/ws"


def verify_bearer() -> str:
    """Validate the request's ``Authorization`` header.

    验证请求的 ``Authorization`` 头。

    Returns the matched token.  Raises :class:`AuthError` if the
    header is missing or wrong.

    返回匹配的令牌。如果头部缺失或错误则抛出 :class:`AuthError`。

    The ``/healthz`` endpoint always passes — it is the handshake
    probe that runs before any token is available.

    ``/healthz`` 端点始终通过——它是在任何令牌可用之前运行的握手探测。
    """
    if _is_healthz() or _is_ws_endpoint():
        return _TOKEN or ""

    if _TOKEN is None:
        # Should never happen in production; we still want a clean
        # 401 instead of a 500 if a route forgets to call
        # ``setup_token``.
        # 生产环境中不应发生；但如果某个路由忘记调用 ``setup_token``，
        # 我们仍希望返回干净的 401 而非 500。
        raise AuthError("server token not initialised")

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthError("missing bearer token")
    presented = header[len("Bearer ") :].strip()
    if presented != _TOKEN:
        raise AuthError("invalid bearer token")
    # Stash the token on ``g`` so downstream code can reuse it.
    # 将令牌存储在 ``g`` 上，以便下游代码可以重复使用。
    g.bearer_token = _TOKEN
    return _TOKEN


def require_bearer(view: Callable) -> Callable:
    """Decorator that enforces Bearer auth on a Flask view.

    在 Flask 视图上强制 Bearer 认证的装饰器。

    Equivalent to wrapping the body in ``verify_bearer()`` but reads
    more naturally at the route declaration site.  Failures raise
    :class:`AuthError` which is converted to a 401 by the global
    error handler.

    相当于用 ``verify_bearer()`` 包裹函数体，但在路由声明处读起来更自然。
    失败时抛出 :class:`AuthError`，由全局错误处理器转换为 401。
    """

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        verify_bearer()
        return view(*args, **kwargs)

    return wrapper


__all__ = [
    "get_token",
    "setup_token",
    "reset_for_testing",
    "verify_bearer",
    "require_bearer",
]
