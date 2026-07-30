"""Streaming helpers — SSE, NDJSON and content-negotiation.

流式辅助函数 — SSE、NDJSON 和内容协商。

The chat completion route (``POST /v1/chat/completions`` with
``stream=True``) yields JSON objects; this module adapts them to
either Server-Sent Events or NDJSON frames depending on the client's
``Accept`` header.

聊天补全路由（``stream=True`` 的 ``POST /v1/chat/completions``）产生 JSON 对象；
此模块根据客户端的 ``Accept`` 标头将它们适配为 Server-Sent Events 或 NDJSON 帧。

Per ``DESIGN.md`` §9.2:

根据 ``DESIGN.md`` §9.2：

* :func:`sse_stream` — ``data: <json>\\n\\n`` per item, terminated by
  ``data: [DONE]\\n\\n``.
* :func:`ndjson_stream` — one JSON object per line (``\\n`` terminated).
  每行一个 JSON 对象（以 ``\\n`` 结尾）。
* :func:`negotiate_stream_format` — inspects ``Accept`` and returns
  either ``"sse"`` or ``"ndjson"`` (default ``"sse"``).
  检查 ``Accept`` 标头并返回 ``"sse"`` 或 ``"ndjson"``（默认 ``"sse"``）。
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

from flask import Response, request

# Stream content types.
# 流式内容类型。
SSE_CONTENT_TYPE = "text/event-stream; charset=utf-8"
NDJSON_CONTENT_TYPE = "application/x-ndjson; charset=utf-8"


def _to_json(item: Any) -> str:
    """Encode ``item`` as a JSON string.

    将 ``item`` 编码为 JSON 字符串。

    Strings are emitted as-is so callers can use sentinel values
    like ``"[DONE]"``.

    字符串原样发出，因此调用者可以使用像 ``"[DONE]"`` 这样的标记值。
    """
    if isinstance(item, str):
        return item
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def sse_stream(gen: Iterable[Any]) -> Iterator[bytes]:
    """Yield SSE frames for each item produced by ``gen``.

    对 ``gen`` 产生的每个项目生成 SSE 帧。

    The terminal ``"[DONE]"`` sentinel is emitted as a separate
    ``data: [DONE]\\n\\n`` frame after the iterator is exhausted.

    在迭代器耗尽后，终止标记 ``"[DONE]"`` 作为单独的 ``data: [DONE]\\n\\n`` 帧发出。
    """
    for item in gen:
        payload = _to_json(item)
        yield f"data: {payload}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


def ndjson_stream(gen: Iterable[Any]) -> Iterator[bytes]:
    """Yield NDJSON frames (one JSON object per line) for ``gen``.

    对 ``gen`` 生成 NDJSON 帧（每行一个 JSON 对象）。
    """
    for item in gen:
        payload = _to_json(item)
        yield f"{payload}\n".encode("utf-8")


def negotiate_stream_format() -> str:
    """Return ``"sse"`` or ``"ndjson"`` based on the request's ``Accept``.

    根据请求的 ``Accept`` 标头返回 ``"sse"`` 或 ``"ndjson"``。

    Default is ``"sse"``.  Recognised Accept values:

    默认为 ``"sse"``。可识别的 Accept 值：

    * ``text/event-stream`` → ``sse``
    * ``application/x-ndjson`` → ``ndjson``
    """
    accept = (request.headers.get("Accept") or "").lower()
    if "application/x-ndjson" in accept:
        return "ndjson"
    if "text/event-stream" in accept:
        return "sse"
    return "sse"


def build_stream_response(
    gen: Iterable[Any],
    *,
    fmt: str | None = None,
) -> Response:
    """Wrap ``gen`` in a Flask streaming :class:`Response`.

    将 ``gen`` 包装在 Flask 流式 :class:`Response` 中。

    Parameters
    ----------
    gen:
        The iterable that produces JSON-compatible dicts (or strings).
        生成 JSON 兼容字典（或字符串）的可迭代对象。
    fmt:
        Optional explicit format (``"sse"`` or ``"ndjson"``).  When
        omitted, :func:`negotiate_stream_format` is consulted.
        可选显式格式（``"sse"`` 或 ``"ndjson"``）。省略时由 :func:`negotiate_stream_format` 决定。
    """
    chosen = fmt or negotiate_stream_format()
    if chosen == "ndjson":
        return Response(
            ndjson_stream(gen),
            mimetype=NDJSON_CONTENT_TYPE,
        )
    return Response(
        sse_stream(gen),
        mimetype=SSE_CONTENT_TYPE,
    )


__all__ = [
    "SSE_CONTENT_TYPE",
    "NDJSON_CONTENT_TYPE",
    "sse_stream",
    "ndjson_stream",
    "negotiate_stream_format",
    "build_stream_response",
]
