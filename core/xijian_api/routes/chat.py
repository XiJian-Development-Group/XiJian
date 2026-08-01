"""Chat completion + abort routes. / 聊天补全 + 中止路由。"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request, stream_with_context

from xijian_api import abort as abort_registry
from xijian_api.errors import ApiError
from xijian_api.stubs import chat as chat_stub
from xijian_api.streaming import build_stream_response


bp = Blueprint("chat", __name__)


@bp.post("/v1/chat/completions")
def chat_completions():
    """Chat completion endpoint (sync or streaming). / 聊天补全端点（同步或流式）。"""
    payload = request.get_json(silent=True) or {}
    if not payload.get("messages"):
        raise ApiError(
            400,
            "`messages` is required and must be a non-empty list",
            "invalid_request_error",
            code="missing_messages",
            param="messages",
        )
    model = payload.get("model", "stub-model")
    temperature = float(payload.get("temperature", 0.7))
    top_p = float(payload.get("top_p", 1.0))
    max_tokens = payload.get("max_tokens")
    stop = payload.get("stop")
    n = int(payload.get("n", 1))
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
            payload["messages"],
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
        resp.headers["X-XiJian-Model-Id"] = model
        resp.headers["X-XiJian-Backend"] = (xijian_ext or {}).get("backend", "stub")
        return resp

    request_id = getattr(g, "request_id", None) or "req_unknown"
    signal = abort_registry.register(request_id)

    def _gen():
        """Generator that yields SSE chunks and respects abort signals.
        / 生成器，产出 SSE 数据块并尊重中止信号。"""
        try:
            for chunk in chat_stub.stream_chunks(
                payload["messages"],
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
    response.headers["X-XiJian-Model-Id"] = model
    response.headers["X-XiJian-Backend"] = (xijian_ext or {}).get("backend", "stub")
    return response


@bp.post("/v1/chat/abort")
def chat_abort():
    """Abort a streaming request by request_id. / 通过 request_id 中止流式请求。"""
    payload = request.get_json(silent=True) or {}
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