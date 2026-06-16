"""Legacy ``POST /v1/completions`` (text completions)."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from xijian_api.errors import ApiError
from xijian_api.stubs import chat as chat_stub
from xijian_api.utils.ids import gen_chat_id
from xijian_api.utils.time import now_ts


bp = Blueprint("completions", __name__)


@bp.post("/v1/completions")
def completions():
    payload = request.get_json(silent=True) or {}
    if "prompt" not in payload:
        raise ApiError(
            400,
            "`prompt` is required",
            "invalid_request_error",
            code="missing_prompt",
            param="prompt",
        )
    model = payload.get("model", "stub-model")
    prompt = payload["prompt"]
    text = f"收到你的 prompt: {str(prompt)[:200]}"
    completion_id = gen_chat_id()
    response = {
        "id": completion_id,
        "object": "text_completion",
        "created": now_ts(),
        "model": model,
        "choices": [
            {
                "text": text,
                "index": 0,
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": len(str(prompt)) // 2,
            "completion_tokens": len(text) // 2,
            "total_tokens": (len(str(prompt)) + len(text)) // 2,
        },
        # Echo back any xijian extension fields for consistency.
        "xijian": payload.get("xijian", {}),
    }
    # Touch the chat stub so the import is intentional and the symbols
    # are reachable for future refactors.
    _ = chat_stub.complete
    return jsonify(response)


__all__ = ["bp"]