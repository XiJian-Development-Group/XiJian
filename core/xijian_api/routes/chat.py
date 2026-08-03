"""Chat completion + abort routes. / 聊天补全 + 中止路由。"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request, stream_with_context

from xijian_api import abort as abort_registry
from xijian_api.errors import ApiError
from xijian_api.stubs import chat as chat_stub
from xijian_api.streaming import build_stream_response


def _parse_float(value: object, param: str) -> float:
    """Parse a float, raising ApiError(400) on failure.
    解析 float，失败时抛出 ApiError(400)。"""
    if value is None:
        return 0.0
    # Reject bools explicitly (bool is subclass of int in Python).
    # 显式拒绝 bool（Python 中 bool 是 int 的子类）。
    if isinstance(value, bool):
        raise ApiError(
            400,
            f"`{param}` must be a valid number",
            "invalid_request_error",
            code="invalid_numeric_value",
            param=param,
        )
    try:
        # Reject NaN / Infinity explicitly (JSON doesn't have them natively
        # but some clients may send them via Python's json module).
        # 显式拒绝 NaN / Infinity（JSON 原生无此类值，但某些客户端可能
        # 通过 Python json 模块发送）。
        f = float(value)
        if f != f or f in (float("inf"), float("-inf")):  # NaN or Infinity
            raise ValueError
        return f
    except (TypeError, ValueError):
        raise ApiError(
            400,
            f"`{param}` must be a valid number",
            "invalid_request_error",
            code="invalid_numeric_value",
            param=param,
        )


def _parse_int(value: object, param: str) -> int:
    """Parse an int, raising ApiError(400) on failure.
    解析 int，失败时抛出 ApiError(400)。"""
    if value is None:
        return 0
    try:
        # Reject bools explicitly (bool is subclass of int in Python).
        # 显式拒绝 bool（Python 中 bool 是 int 的子类）。
        if isinstance(value, bool):
            raise TypeError
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(
            400,
            f"`{param}` must be a valid integer",
            "invalid_request_error",
            code="invalid_numeric_value",
            param=param,
        )


def _parse_int_optional(value: object, param: str) -> int | None:
    """Parse an optional int, returning None if missing/empty.
    解析可选 int，缺失/空值返回 None。"""
    if value is None or value == "":
        return None
    return _parse_int(value, param)


def _safe_header_value(value: object) -> str:
    """Strip CR/LF and control characters from a response-header value.

    A hostile ``model`` / ``user`` string may contain newlines which
    raise ``ValueError`` in Werkzeug when assigned to a header; scrub
    them so the request still completes with 200 instead of 500.

    从响应头值中去除 CR/LF 和控制字符。恶意的 ``model``/``user`` 字符串
    可能包含换行符，在赋给响应头时会导致 Werkzeug 抛 ``ValueError``；
    这里将其清除，使请求仍以 200 完成而不是 500。
    """
    return "".join(ch for ch in str(value or "") if ch not in "\r\n" and ord(ch) >= 0x20)


bp = Blueprint("chat", __name__)


@bp.post("/v1/chat/completions")
def chat_completions():
    """Chat completion endpoint (sync or streaming). / 聊天补全端点（同步或流式）。"""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError(
            400,
            "Request body must be a JSON object",
            "invalid_request_error",
            code="invalid_request_body",
            param="body",
        )
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ApiError(
            400,
            "`messages` is required and must be a non-empty list",
            "invalid_request_error",
            code="missing_messages",
            param="messages",
        )
    if not all(isinstance(m, dict) for m in messages):
        raise ApiError(
            400,
            "each item in `messages` must be a JSON object",
            "invalid_request_error",
            code="invalid_messages",
            param="messages",
        )
    model = payload.get("model", "stub-model")
    temperature = _parse_float(payload.get("temperature", 0.7), "temperature")
    top_p = _parse_float(payload.get("top_p", 1.0), "top_p")
    max_tokens = _parse_int_optional(payload.get("max_tokens"), "max_tokens")
    stop = payload.get("stop")
    n = _parse_int(payload.get("n", 1), "n")
    user = payload.get("user")
    xijian_ext = payload.get("xijian")
    tools = payload.get("tools")
    tool_choice = payload.get("tool_choice")

    stream = bool(payload.get("stream", False))
    stream_options = payload.get("stream_options") or {}
    include_usage = bool(stream_options.get("include_usage", False))

    # A3.2 guard — raise eagerly (before the stream response is
    # constructed) so a Critical character gets a clean 400 for both
    # sync and streaming requests.  ``complete()`` repeats the guard
    # internally for direct stub callers.
    # A3.2 门控 — 在构造流式响应之前立即抛出，使 Critical 角色在
    # 同步和流式请求下都得到干净的 400。``complete()`` 内部会为
    # 直接调用存根的调用者重复执行该门控。
    chat_stub.guard_character_dialogue(xijian_ext)

    if not stream:
        response = chat_stub.complete(
            messages,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
            n=n,
            user=user,
            xijian=xijian_ext,
            tools=tools,
            tool_choice=tool_choice,
        )
        resp = jsonify(response)
        resp.headers["X-XiJian-Model-Id"] = _safe_header_value(model)
        resp.headers["X-XiJian-Backend"] = _safe_header_value(
            (xijian_ext or {}).get("backend", "stub")
        )
        return resp

    request_id = getattr(g, "request_id", None) or "req_unknown"
    signal = abort_registry.register(request_id)

    def _gen():
        """Generator that yields SSE chunks and respects abort signals.
        / 生成器，产出 SSE 数据块并尊重中止信号。"""
        try:
            for chunk in chat_stub.stream_chunks(
                messages,
                model=model,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                stop=stop,
                signal=signal,
                include_usage=include_usage,
                xijian=xijian_ext,
                tools=tools,
                tool_choice=tool_choice,
            ):
                signal.raise_if_aborted()
                yield chunk
        finally:
            abort_registry.cleanup(request_id)

    response = build_stream_response(stream_with_context(_gen()))
    response.headers["X-XiJian-Model-Id"] = _safe_header_value(model)
    response.headers["X-XiJian-Backend"] = _safe_header_value(
        (xijian_ext or {}).get("backend", "stub")
    )
    return response


@bp.post("/v1/chat/abort")
def chat_abort():
    """Abort a streaming request by request_id. / 通过 request_id 中止流式请求。"""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError(
            400,
            "Request body must be a JSON object",
            "invalid_request_error",
            code="invalid_request_body",
            param="body",
        )
    request_id = payload.get("request_id", "")
    if not request_id:
        raise ApiError(
            400,
            "`request_id` is required",
            "invalid_request_error",
            code="missing_request_id",
            param="request_id",
        )
    signalled = abort_registry.abort(request_id)
    # Per api.md, 204 even if no signal existed (idempotent cancel).
    # 按 api.md，即使没有活跃的信号也返回 204（幂等取消）。
    response = jsonify({"aborted": signalled, "request_id": request_id})
    response.status_code = 204 if signalled else 200
    if not signalled:
        # Return a tiny JSON body when there's no active stream.
        # 当没有活跃流时返回一个微小的 JSON 体。
        return jsonify({"aborted": False, "request_id": request_id}), 200
    return ("", 204)


__all__ = ["bp"]