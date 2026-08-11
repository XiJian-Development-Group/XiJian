"""Shared request-parameter parsing helpers (robustness layer).

共享的请求参数解析辅助函数（健壮性层）。

These helpers convert hostile / malformed values (non-dict bodies,
bool-as-number, NaN/Infinity, non-numeric strings) into clean 400
``invalid_request_error`` responses instead of letting a
``ValueError``/``TypeError`` bubble up as a 500.

这些辅助函数将恶意/畸形值（非字典请求体、布尔当数字、NaN/Infinity、
非数字字符串）转换为干净的 400 ``invalid_request_error`` 响应，
而不是让 ``ValueError``/``TypeError`` 冒泡成 500。
"""

from __future__ import annotations

import math
from typing import Any

from xijian_api.errors import ApiError


def parse_float(value: Any, param: str, default: float) -> float:
    """Parse a float field, returning ``default`` when absent.

    解析浮点字段，缺失时返回 ``default``。

    Rejects bools (bool is a subclass of int), NaN and ±Infinity
    (invalid JSON numbers) and non-numeric strings with a clean 400.

    拒绝布尔值（bool 是 int 的子类）、NaN 和 ±Infinity（非法 JSON 数字）
    以及非数字字符串，返回干净的 400。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        raise ApiError(
            400,
            f"`{param}` must be a valid number",
            "invalid_request_error",
            code="invalid_numeric_value",
            param=param,
        )
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ApiError(
            400,
            f"`{param}` must be a valid number",
            "invalid_request_error",
            code="invalid_numeric_value",
            param=param,
        ) from None
    if math.isnan(f) or math.isinf(f):
        raise ApiError(
            400,
            f"`{param}` must be a valid number",
            "invalid_request_error",
            code="invalid_numeric_value",
            param=param,
        )
    return f


def parse_int(value: Any, param: str, default: int) -> int:
    """Parse an integer field, returning ``default`` when absent.

    解析整数字段，缺失时返回 ``default``。

    Rejects bools and non-numeric values with a clean 400.  Floats are
    truncated via ``int()`` (matching Python's previous lenient
    behaviour) but NaN/Infinity are rejected.

    拒绝布尔值和非数字值，返回干净的 400。浮点数通过 ``int()`` 截断
    （与 Python 之前宽松行为一致），但 NaN/Infinity 会被拒绝。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        raise ApiError(
            400,
            f"`{param}` must be a valid integer",
            "invalid_request_error",
            code="invalid_numeric_value",
            param=param,
        )
    try:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise ValueError
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise ApiError(
            400,
            f"`{param}` must be a valid integer",
            "invalid_request_error",
            code="invalid_numeric_value",
            param=param,
        ) from None


def parse_int_optional(value: Any, param: str) -> int | None:
    """Parse an optional integer, returning ``None`` when absent/empty.

    解析可选整数字段，缺失/空值时返回 ``None``。
    """
    if value is None or value == "":
        return None
    return parse_int(value, param, default=0)


def parse_int_range(
    value: Any,
    param: str,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    """Parse an integer field and enforce a ``[min_value, max_value]`` range.

    解析整数字段并强制 ``[min_value, max_value]`` 范围。

    Missing/empty values resolve to ``default`` (which must itself be
    inside the range).  Out-of-range values raise a clean 400
    ``invalid_request_error`` with the ``param`` field set, matching
    the existing error format::

        ApiError(400, "`fps` must be between 1 and 120",
                 "invalid_request_error", code="invalid_numeric_value",
                 param="fps")

    缺失/空值解析为 ``default``（其自身必须在范围内）。越界值抛出
    干净的 400 ``invalid_request_error`` 并携带 ``param`` 字段，
    与现有错误格式一致。
    """
    if value is None or value == "":
        value = default
    parsed = parse_int(value, param, default)
    if parsed < min_value or parsed > max_value:
        raise ApiError(
            400,
            f"`{param}` must be between {min_value} and {max_value}",
            "invalid_request_error",
            code="invalid_numeric_value",
            param=param,
        )
    return parsed


def safe_header_value(value: Any) -> str:
    """Strip CR/LF and control characters from a response-header value.

    从响应头值中去除 CR/LF 和控制字符。

    A hostile ``model`` / ``user`` string may contain newlines which
    raise ``ValueError`` in Werkzeug when assigned to a header; scrub
    them so the request still completes with 200 instead of 500.

    恶意的 ``model``/``user`` 字符串可能包含换行符，赋给响应头时会导致
    Werkzeug 抛出 ``ValueError``；清除后请求仍以 200 完成而不是 500。
    """
    return "".join(ch for ch in str(value or "") if ch not in "\r\n" and ord(ch) >= 0x20)


__all__ = [
    "parse_float",
    "parse_int",
    "parse_int_optional",
    "safe_header_value",
]
