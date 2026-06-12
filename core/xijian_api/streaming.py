"""Streaming helpers — SSE, NDJSON and content-negotiation.

The chat completion route (``POST /v1/chat/completions`` with
``stream=True``) yields JSON objects; this module adapts them to
either Server-Sent Events or NDJSON frames depending on the client's
``Accept`` header.

Per ``DESIGN.md`` §9.2:

* :func:`sse_stream` — ``data: <json>\\n\\n`` per item, terminated by
  ``data: [DONE]\\n\\n``.
* :func:`ndjson_stream` — one JSON object per line (``\\n`` terminated).
* :func:`negotiate_stream_format` — inspects ``Accept`` and returns
  either ``"sse"`` or ``"ndjson"`` (default ``"sse"``).
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

from flask import Response, request

# Stream content types.
SSE_CONTENT_TYPE = "text/event-stream; charset=utf-8"
NDJSON_CONTENT_TYPE = "application/x-ndjson; charset=utf-8"


def _to_json(item: Any) -> str:
    """Encode ``item`` as a JSON string.

    Strings are emitted as-is so callers can use sentinel values
    like ``"[DONE]"``.
    """
    if isinstance(item, str):
        return item
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def sse_stream(gen: Iterable[Any]) -> Iterator[bytes]:
    """Yield SSE frames for each item produced by ``gen``.

    The terminal ``"[DONE]"`` sentinel is emitted as a separate
    ``data: [DONE]\\n\\n`` frame after the iterator is exhausted.
    """
    for item in gen:
        payload = _to_json(item)
        yield f"data: {payload}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


def ndjson_stream(gen: Iterable[Any]) -> Iterator[bytes]:
    """Yield NDJSON frames (one JSON object per line) for ``gen``."""
    for item in gen:
        payload = _to_json(item)
        yield f"{payload}\n".encode("utf-8")


def negotiate_stream_format() -> str:
    """Return ``"sse"`` or ``"ndjson"`` based on the request's ``Accept``.

    Default is ``"sse"``.  Recognised Accept values:

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

    Parameters
    ----------
    gen:
        The iterable that produces JSON-compatible dicts (or strings).
    fmt:
        Optional explicit format (``"sse"`` or ``"ndjson"``).  When
        omitted, :func:`negotiate_stream_format` is consulted.
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