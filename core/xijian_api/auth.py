"""Bearer token loading and verification.

The token lives in a file under ``/tmp/xijian-<pid>.token``.  In
production the parent process writes the file before launching us and
sets ``XIJIAN_DEV_TOKEN_FILE`` to a non-empty value if the file should
be kept around; otherwise we ``unlink`` it after reading so it cannot
leak.

In dev mode (``XIJIAN_DEV=1``) we generate a fresh 32-byte hex token,
write it to the canonical file with ``0600`` perms, and print it to
stderr — never to any HTTP response.

The verification function is :func:`verify_bearer`.  Per ``DESIGN.md``
§3.3 and §4.1 ``/healthz`` is exempt.
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
_TOKEN: str | None = None


def get_token() -> str | None:
    """Return the currently-loaded Bearer token, or ``None``."""
    return _TOKEN


def setup_token(config: Config, *, pid: int | None = None) -> str:
    """Initialise the in-memory token from disk (or generate one).

    Returns the loaded (or freshly generated) token.

    Parameters
    ----------
    config:
        The :class:`xijian_api.config.Config` instance.
    pid:
        Override the PID used to locate the token file (used by tests).
    """
    global _TOKEN
    if _TOKEN is not None:
        return _TOKEN

    if config.testing:
        # Tests get a deterministic placeholder token.
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
    if not config.dev:
        # Production: refuse to start without a pre-provisioned token.
        raise RuntimeError(
            f"token file {path} missing and XIJIAN_DEV not set; "
            "the API cannot start without a bearer token."
        )

    # Dev mode: generate a fresh token and write it to the canonical path.
    token = secrets.token_hex(32)
    try:
        path.write_text(token, encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError as exc:
        _LOGGER.error("failed to write dev token file %s: %s", path, exc)
        raise

    _TOKEN = token
    # Print to stderr — never include in any HTTP response.
    _LOGGER.info("dev token written to %s", path)
    print(f"[xijian-api] dev token: {token}", flush=True)
    return _TOKEN


def reset_for_testing() -> None:
    """Reset the module-level token (used by tests)."""
    global _TOKEN
    _TOKEN = None


# ---------------------------------------------------------------------------
# Request-time verification
# ---------------------------------------------------------------------------


def _is_healthz() -> bool:
    """Return ``True`` if the current request is ``GET /healthz``."""
    return request.path == "/healthz" and request.method == "GET"


def verify_bearer() -> str:
    """Validate the request's ``Authorization`` header.

    Returns the matched token.  Raises :class:`AuthError` if the
    header is missing or wrong.

    The ``/healthz`` endpoint always passes — it is the handshake
    probe that runs before any token is available.
    """
    if _is_healthz():
        return _TOKEN or ""

    if _TOKEN is None:
        # Should never happen in production; we still want a clean
        # 401 instead of a 500 if a route forgets to call
        # ``setup_token``.
        raise AuthError("server token not initialised")

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthError("missing bearer token")
    presented = header[len("Bearer ") :].strip()
    if presented != _TOKEN:
        raise AuthError("invalid bearer token")
    # Stash the token on ``g`` so downstream code can reuse it.
    g.bearer_token = _TOKEN
    return _TOKEN


def require_bearer(view: Callable) -> Callable:
    """Decorator that enforces Bearer auth on a Flask view.

    Equivalent to wrapping the body in ``verify_bearer()`` but reads
    more naturally at the route declaration site.  Failures raise
    :class:`AuthError` which is converted to a 401 by the global
    error handler.
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