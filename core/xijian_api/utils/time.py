"""Time helpers used across the XiJian API server."""

from __future__ import annotations

import datetime as _dt


def now_ts() -> int:
    """Return the current Unix timestamp (seconds since epoch)."""
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp())


def iso_now() -> str:
    """Return the current UTC time formatted as ISO-8601 with ``Z`` suffix."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["now_ts", "iso_now"]