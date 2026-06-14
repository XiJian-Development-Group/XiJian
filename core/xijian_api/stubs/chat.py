"""Stub chat completions — synchronous + streaming."""

from __future__ import annotations

import time
from typing import Any, Iterator

from xijian_api.abort import AbortSignal
from xijian_api.utils.ids import gen_chat_id
from xijian_api.utils.time import now_ts


_ECHO_TEXT = "你好呀~ 这是 stub 响应。"
_CHUNK_DELAY_SECONDS = 0.03  # 30 ms per token — keeps tests fast, still feels streamed.


def _base_response(
    *,
    model: str,
    content: str,
    finish_reason: str = "stop",
    usage: dict | None = None,
) -> dict[str, Any]:
    completion_id = gen_chat_id()
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": now_ts(),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "usage": usage
        or {"prompt_tokens": 0, "completion_tokens": _estimate_tokens(content), "total_tokens": _estimate_tokens(content)},
        "xijian": {"backend": "stub", "guard_triggered": False, "memory_hits": 0},
    }


def _estimate_tokens(text: str) -> int:
    """Very rough heuristic — used only for stub usage numbers."""
    return max(1, len(text) // 2) if text else 0


def _last_user_text(messages: list[dict] | None) -> str:
    """Return the last user message's text content, or the default echo text."""
    if not messages:
        return _ECHO_TEXT
    for msg in reversed(messages):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if role == "user" and content:
            return str(content)
    return _ECHO_TEXT


def complete(
    messages: list[dict],
    *,
    model: str = "stub-model",
    temperature: float = 0.7,
    top_p: float = 1.0,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
    n: int = 1,
    user: str | None = None,
    xijian: dict | None = None,
) -> dict[str, Any]:
    """Return a non-streaming OAI chat completion payload."""
    last_user = _last_user_text(messages)
    # Stub echo: very lightly transform the user's last message so it
    # looks like a real reply (otherwise tests would always see the
    # exact same body for any input).
    content = f"收到你的消息: {last_user[:200]}" if last_user else _ECHO_TEXT
    response = _base_response(model=model, content=content)
    # Echo back the model id we received.
    response["model"] = model
    return response


def stream_chunks(
    messages: list[dict],
    *,
    model: str = "stub-model",
    temperature: float = 0.7,
    top_p: float = 1.0,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
    signal: AbortSignal | None = None,
    include_usage: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield OAI streaming chunks for ``messages``.

    Each chunk shares the same ``id`` (matches OAI behaviour).  The
    final chunk has ``finish_reason="stop"``; if ``signal`` is set and
    triggered, the final chunk has ``finish_reason="abort"``.
    """
    last_user = _last_user_text(messages)
    text = f"收到你的消息: {last_user[:200]}" if last_user else _ECHO_TEXT
    # Split into character-level tokens for visible streaming.
    tokens = list(text)
    completion_id = gen_chat_id()
    created = now_ts()

    # First chunk — role announcement + empty content.
    yield {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None,
            }
        ],
    }

    aborted = False
    for token in tokens:
        if signal is not None:
            signal.raise_if_aborted()
        time.sleep(_CHUNK_DELAY_SECONDS)
        yield {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": token},
                    "finish_reason": None,
                }
            ],
        }

    # Final chunk — finish_reason.
    if signal is not None and signal.is_set():
        aborted = True
    yield {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "abort" if aborted else "stop",
            }
        ],
    }

    if include_usage:
        yield {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [],
            "usage": {
                "prompt_tokens": _estimate_tokens(last_user),
                "completion_tokens": _estimate_tokens(text),
                "total_tokens": _estimate_tokens(last_user) + _estimate_tokens(text),
            },
        }


__all__ = ["complete", "stream_chunks"]