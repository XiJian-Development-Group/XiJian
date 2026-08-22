"""Chat completion + abort routes. / 聊天补全 + 中止路由。"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request, stream_with_context

from xijian_api import abort as abort_registry
from xijian_api.errors import ApiError
from xijian_api.stubs import chat as chat_stub
from xijian_api.streaming import build_stream_response
from xijian_api.utils.log import get_logger
from xijian_api.utils.params import (
    parse_float,
    parse_int,
    parse_int_optional,
    safe_header_value,
)


bp = Blueprint("chat", __name__)

_LOGGER = get_logger()


# ---------------------------------------------------------------------------
# E2 — numeric parameter clamping
# E2 — 数值参数钳位
# ---------------------------------------------------------------------------

#: Accepted ranges for the core sampling parameters.  Values outside the
#: range are silently clamped (never rejected) so a hostile or sloppy
#: client cannot push the backend into an invalid sampling configuration.
#: 核心采样参数的允许范围。范围外的值被静默钳位（绝不拒绝），
#: 防止恶意或粗心的客户端把后端推入无效采样配置。
_TEMPERATURE_MIN, _TEMPERATURE_MAX = 0.0, 2.0
_TOP_P_MIN, _TOP_P_MAX = 0.0, 1.0
_MAX_TOKENS_MIN, _MAX_TOKENS_MAX = 1, 32768


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp ``value`` into ``[lo, hi]``.  将 ``value`` 钳位到 ``[lo, hi]``。"""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _clamp_float(value: float, param: str, lo: float, hi: float) -> float:
    """Clamp a parsed float, logging a warning when it changed.

    钳位已解析的浮点数，发生变化时记录警告。
    """
    clamped = _clamp(value, lo, hi)
    if clamped != value:
        _LOGGER.warning(
            "parameter `%s`=%r outside [%s, %s]; clamped to %r",
            param,
            value,
            lo,
            hi,
            clamped,
        )
    return clamped


def _clamp_optional_int(value: int | None, param: str, lo: int, hi: int) -> int | None:
    """Clamp an optional parsed int (``None`` stays ``None``), logging when changed.

    钳位可选已解析整数（``None`` 保持 ``None``），发生变化时记录日志。
    """
    if value is None:
        return None
    clamped = _clamp(value, lo, hi)
    if clamped != value:
        _LOGGER.warning(
            "parameter `%s`=%r outside [%s, %s]; clamped to %r",
            param,
            value,
            lo,
            hi,
            clamped,
        )
    return int(clamped)


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
    temperature = _clamp_float(
        parse_float(payload.get("temperature"), "temperature", 0.7),
        "temperature",
        _TEMPERATURE_MIN,
        _TEMPERATURE_MAX,
    )
    top_p = _clamp_float(
        parse_float(payload.get("top_p"), "top_p", 1.0),
        "top_p",
        _TOP_P_MIN,
        _TOP_P_MAX,
    )
    max_tokens = _clamp_optional_int(
        parse_int_optional(payload.get("max_tokens"), "max_tokens"),
        "max_tokens",
        _MAX_TOKENS_MIN,
        _MAX_TOKENS_MAX,
    )
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
        # Handle safety block response (tuple of (dict, status_code))
        if isinstance(response, tuple):
            resp = jsonify(response[0])
            resp.status_code = response[1]
        else:
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
    # Per api.md: 已注册的 request_id 返回 204（幂等取消）；未知返回 200 {"aborted": false}。
    if not signalled:
        # Return a tiny JSON body when there's no active stream.
        # 当没有活跃流时返回一个微小的 JSON 体。
        return jsonify({"aborted": False, "request_id": request_id}), 200
    return ("", 204)


__all__ = ["bp"]