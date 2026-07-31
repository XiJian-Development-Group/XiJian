"""Multimodal understanding routes — accept any modality input through a unified API.

全模态理解路由 — 通过统一 API 接受任意模态输入。

提供两个端点：

* ``POST /v1/multimodal/completions`` — 非流式全模态理解
* ``POST /v1/multimodal/completions?stream=true`` — 流式全模态理解

输入格式：与 ``/v1/chat/completions`` 相同的 OAI 兼容消息格式，
但 ``content`` 字段可以包含 ``audio_url``、``video_url``、``file_url``
等任意 OAI 内容片段类型。

全模态后端会智能地处理非原生模态:
* OpenAI 后端：音频 → ``input_audio`` 原生格式，视频 → 帧提取
* MLX 后端：音频 → STT 转录，视频 → 帧提取 → VLM
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request, stream_with_context

from xijian_api import abort as abort_registry
from xijian_api.errors import ApiError
from xijian_api.stubs import multimodal as multimodal_stub
from xijian_api.streaming import build_stream_response


bp = Blueprint("multimodal", __name__)


@bp.post("/v1/multimodal/completions")
def multimodal_completions():
    """Multimodal understanding endpoint (sync or streaming).

    全模态理解端点（同步或流式）。

    Accepts messages with any combination of content parts:
    ``text``, ``image_url``, ``audio_url``, ``video_url``, ``file_url``.

    接受包含任意组合内容片段的消息：
    ``text``, ``image_url``, ``audio_url``, ``video_url``, ``file_url``。
    """
    payload = request.get_json(silent=True) or {}
    if not payload.get("messages"):
        raise ApiError(
            400,
            "`messages` is required and must be a non-empty list",
            "invalid_request_error",
            code="missing_messages",
            param="messages",
        )
    model = payload.get("model", "stub-multimodal")
    temperature = float(payload.get("temperature", 0.7))
    top_p = float(payload.get("top_p", 1.0))
    max_tokens = payload.get("max_tokens")
    stop = payload.get("stop")

    stream = bool(payload.get("stream", False))
    stream_options = payload.get("stream_options") or {}
    include_usage = bool(stream_options.get("include_usage", False))

    if not stream:
        response = multimodal_stub.understand(
            payload["messages"],
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
        )
        resp = jsonify(response)
        resp.headers["X-XiJian-Model-Id"] = model
        return resp

    request_id = getattr(g, "request_id", None) or "req_unknown"
    signal = abort_registry.register(request_id)

    # Eagerly resolve the backend *before* the streaming response starts,
    # so unknown/unavailable models return 503 instead of 500 mid-stream.
    # 在流式响应开始*之前*急切解析后端，
    # 这样未知/不可用的模型返回 503 而不是流式中途 500。
    try:
        multimodal_stub.resolve_backend(model)
    except Exception:
        abort_registry.cleanup(request_id)
        raise

    def _gen():
        """Generator that yields SSE chunks and respects abort signals.
        生成器，产出 SSE 数据块并尊重中止信号。"""
        try:
            for chunk in multimodal_stub.understand_stream(
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
    return response


@bp.get("/v1/multimodal/models")
def list_multimodal_models():
    """List available multimodal models.

    列出可用的全模态模型。

    Returns models registered with ``type = "multimodal"`` in config.
    """
    from xijian_api.stubs import state

    models = getattr(state, "models", {})
    # The seeded records carry the model type under ``xijian.type``
    # (rendered by :meth:`ModelEntry.to_oai_metadata`); filter on that
    # rather than a top-level ``capabilities`` key which the seeded
    # records never have.
    # 已播种的记录在 ``xijian.type`` 下携带模型类型（由
    # :meth:`ModelEntry.to_oai_metadata` 渲染）；按其过滤，而不是依赖
    # 已播种记录从未包含的顶层 ``capabilities`` 键。
    multimodal_models = [
        m for m in models.values()
        if isinstance(m, dict) and m.get("xijian", {}).get("type") == "multimodal"
    ]
    return jsonify({
        "object": "list",
        "data": multimodal_models,
    })


@bp.post("/v1/multimodal/abort")
def multimodal_abort():
    """Abort a streaming multimodal request by request_id.

    通过 request_id 中止流式全模态请求。
    """
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
    if not signalled:
        return jsonify({"aborted": False, "request_id": request_id}), 200
    return ("", 204)


__all__ = ["bp"]
