"""Time helpers used across the XiJian API server.
隙间 API 服务器通用的时间辅助函数。"""

from __future__ import annotations

import datetime as _dt


def now_ts() -> int:
    """Return the current Unix timestamp (seconds since epoch).
    返回当前 Unix 时间戳 (自纪元以来的秒数)。"""
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp())


def iso_now() -> str:
    """Return the current UTC time formatted as ISO-8601 with ``Z`` suffix.
    返回当前 UTC 时间的 ISO-8601 格式，以 ``Z`` 结尾。"""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = ["now_ts", "iso_now"]