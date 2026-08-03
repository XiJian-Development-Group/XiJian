"""Chat completion + abort routes. / 聊天补全 + 中止路由。"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request, stream_with_context

from xijian_api import abort as abort_registry
from xijian_api.errors import ApiError
from xijian_api.stubs import chat as chat_stub
from xijian_api.streaming import build_stream_response
from xijian_api.utils.params import (
    parse_float,
    parse_int,
    parse_int_optional,
    safe_header_value,
)


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
    temperature = parse_float(payload.get("temperature"), "temperature", 0.7)
    top_p = parse_float(payload.get("top_p"), "top_p", 1.0)
    max_tokens = parse_int_optional(payload.get("max_tokens"), "max_tokens")
    stop = payload.get("stop")
    n = parse_int(payload.get("n"), "n", 1)
    user = payload.get("user")
    # A5.1 extension block must be an object; anything else is rejected
    # instead of crashing downstream ``(xijian or {}).get(...)`` with a 500.
    # A5.1 扩展块必须是对象；其他类型直接 400，避免下游
    # ``(xijian or {}).get(...)`` 崩溃成 500。
    xijian_raw = payload.get("xijian")
    if xijian_raw is not None and not isinstance(xijian_raw, dict):
        raise ApiError(
            400,
            "`xijian` must be a JSON object",
            "invalid_request_error",
            code="invalid_xijian_block",
            param="xijian",
        )
    xijian_ext = xijian_raw
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
        resp.headers["X-XiJian-Model-Id"] = safe_header_value(model)
        resp.headers["X-XiJian-Backend"] = safe_header_value(
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
    response.headers["X-XiJian-Model-Id"] = safe_header_value(model)
    response.headers["X-XiJian-Backend"] = safe_header_value(
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