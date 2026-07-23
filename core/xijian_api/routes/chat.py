"""Chat completion + abort routes."""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request, stream_with_context

from xijian_api import abort as abort_registry
from xijian_api.errors import ApiError
from xijian_api.stubs import chat as chat_stub
from xijian_api.streaming import build_stream_response


bp = Blueprint("chat", __name__)


@bp.post("/v1/chat/completions")
def chat_completions():
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
    response = jsonify({"aborted": signalled, "request_id": request_id})
    response.status_code = 204 if signalled else 200
    if not signalled:
        # Return a tiny JSON body when there's no active stream.
        return jsonify({"aborted": False, "request_id": request_id}), 200
    return ("", 204)


__all__ = ["bp"]