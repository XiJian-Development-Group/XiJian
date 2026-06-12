"""Configuration constants and environment-driven settings.

The server is intentionally minimal: most knobs are read once at process
startup from environment variables.  This module exposes typed
helpers plus the :data:`Config` dataclass that bundles everything the
rest of the codebase needs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# API version exposed via the X-XiJian-API-Version response header.
API_VERSION = "1.0.0"

# Server bind host — local-only.  Per DESIGN §3.2 this is hard-coded.
DEFAULT_HOST = "127.0.0.1"

# Default rate-limit window counts (DESIGN §5).  The server does not
# actually enforce rate limits locally, but it still emits the headers.
RATE_LIMIT_LIMIT_REQUESTS = 100000
RATE_LIMIT_REMAINING_REQUESTS = 99999

# Idempotency cache TTL (seconds).
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60  # 24h

# Streaming defaults.
DEFAULT_STREAM_FORMAT = "sse"


@dataclass(frozen=True)
class Config:
    """Process-wide configuration values."""

    host: str = DEFAULT_HOST
    dev: bool = False
    keep_token_file: bool = False
    testing: bool = False

    @classmethod
    def from_env(cls, *, testing: bool = False) -> "Config":
        """Build a :class:`Config` from environment variables.

        Parameters
        ----------
        testing:
            When ``True`` the process is running under the test suite:
            no token-file I/O will be attempted by :func:`auth.setup_token`
            and the WSGI server will not be started.  Callers should
            pass this from :func:`create_app`.
        """
        return cls(
            host=DEFAULT_HOST,
            dev=_truthy(os.environ.get("XIJIAN_DEV")),
            keep_token_file=_truthy(os.environ.get("XIJIAN_DEV_TOKEN_FILE")),
            testing=testing,
        )


def _truthy(value: str | None) -> bool:
    """Return ``True`` if the value is a truthy string."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def token_file_path(pid: int | None = None) -> Path:
    """Return the canonical path for the Bearer token file.

    The path is ``/tmp/xijian-<pid>.token`` by default but is kept in
    one place so tests can override it.
    """
    if pid is None:
        pid = os.getpid()
    return Path("/tmp") / f"xijian-{pid}.token"


__all__ = [
    "API_VERSION",
    "DEFAULT_HOST",
    "RATE_LIMIT_LIMIT_REQUESTS",
    "RATE_LIMIT_REMAINING_REQUESTS",
    "IDEMPOTENCY_TTL_SECONDS",
    "DEFAULT_STREAM_FORMAT",
    "Config",
    "token_file_path",
]