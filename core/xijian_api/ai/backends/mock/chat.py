"""Mock chat backend — used by the test suite and local development.

Goals
-----

* Always ``is_available()`` so ``tests`` and CI can run without
  ``mlx``/``llama_cpp`` installed and without a real checkpoint on
  disk.
* Accept any ``model_path`` in :meth:`load` (the path is recorded but
  never opened).  The registry's ``_resolve_backend_class`` will hand
  the same path to :meth:`ModelEntry.absolute_path`, which would
  otherwise fail when the directory doesn't exist.
* Emit a deterministic token sequence so tests can assert on output
  shape without depending on weights, prompt formatting, or platform
  backends.
* Support a minimal ``tool_call`` flow so the A1.2 forced-recall
  pipeline can be exercised end-to-end without a real model.  When
  the prompt contains a system instruction that references the
  ``recall_memory`` tool (the pipeline injects it), the mock emits a
  ``recall_memory`` tool call on the first turn; on the second turn
  it echoes the tool result back as the final answer.

Contract mirrors :class:`xijian_api.ai.types.ChatBackend`:

* :meth:`chat` returns an *iterable* of :class:`ChatChunk` instances
  in both blocking (``stream=False``) and streaming (``stream=True``)
  modes.
* An :class:`AbortSignal`, when supplied, is polled between emissions
  so a client-side ``POST .../abort`` halts the mock cleanly.  The
  final chunk's ``finish_reason`` is ``"abort"`` in that case.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator, Sequence

from xijian_api.ai.base import ModelNotLoaded
from xijian_api.ai.registry import register_chat
from xijian_api.ai.types import (
    ChatBackend,
    ChatChunk,
    ChatChoice,
    ChatMessage,
    ChatUsage,
    GenerationParams,
)
from xijian_api.errors import GenerationAborted as ApiGenerationAborted


# Default token budget when the caller doesn't pass ``max_tokens``.
# Kept small so an accidental call doesn't waste cycles, but big
# enough to exercise streaming.
_DEFAULT_MAX_TOKENS = 64

# Mock token sequence.  Each entry is the *new* suffix to append, so
# concatenating them reproduces the canonical mock text.  Tests that
# need a known output can join this list verbatim.
_MOCK_TOKENS: tuple[str, ...] = (
    "Mock", " response", " from", " the", " mock", " chat", " backend",
    ".", " This", " backend", " is", " intended", " for", " tests", " and",
    " local", " development", " only", ".", " It", " does", " not", " load",
    " any", " real", " model", " weights", ".",
)


def _now_ts() -> int:
    return int(time.time())


def _resolve_max_tokens(params: GenerationParams) -> int:
    """Resolve ``max_tokens`` honouring ``None`` / 0 as a default budget."""
    if params.max_tokens is None or params.max_tokens <= 0:
        return _DEFAULT_MAX_TOKENS
    return int(params.max_tokens)


def _build_chunk(
    *,
    chunk_id: str,
    model: str,
    delta: dict | None = None,
    finish_reason: str | None = None,
    usage: ChatUsage | None = None,
) -> ChatChunk:
    """Assemble a :class:`ChatChunk` from its OAI-style pieces."""
    return ChatChunk(
        id=chunk_id,
        model=model,
        created=_now_ts(),
        choices=[
            ChatChoice(
                index=0,
                delta=delta if delta is not None else {},
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
        backend="mock",
    )


def _last_user_text(messages: Sequence) -> str:
    """Return the most recent user message's text.  Empty string if none."""
    for m in reversed(messages):
        if isinstance(m, ChatMessage):
            if m.role == "user":
                return m.content or ""
        elif isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _system_has_recall_instruction(messages: Sequence) -> bool:
    """True when the system message mentions the recall_memory tool."""
    needle = "recall_memory"
    for m in messages:
        if isinstance(m, ChatMessage):
            content = m.content or ""
            role = m.role
        else:
            content = str((m or {}).get("content", ""))
            role = str((m or {}).get("role", ""))
        if role == "system" and needle in content:
            return True
    return False


def _latest_tool_result(messages: Sequence) -> dict | None:
    """Return the most recent ``role=tool`` message's parsed JSON content."""
    for m in reversed(messages):
        if isinstance(m, ChatMessage):
            role = m.role
            content = m.content or ""
        else:
            role = str((m or {}).get("role", ""))
            content = str((m or {}).get("content", ""))
        if role != "tool":
            continue
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


#: Marker injected by the MCP tools pipeline (A2) in the system prompt.
#: Mirrors the first line of ``_TOOLS_SYSTEM_PROMPT`` in chat_stub.py.
_MCP_TOOLS_MARKER = "你可以使用以下工具来完成用户的请求"


def _system_has_mcp_tools_instruction(messages: Sequence) -> bool:
    """True when the system message contains the MCP tools instruction."""
    for m in messages:
        if isinstance(m, ChatMessage):
            content = m.content or ""
            role = m.role
        else:
            content = str((m or {}).get("content", ""))
            role = str((m or {}).get("role", ""))
        if role == "system" and _MCP_TOOLS_MARKER in content:
            return True
    return False


def _extract_tool_names_from_system(messages: Sequence) -> list[str]:
    """Parse tool names from ``### name`` headers in the tools system prompt."""
    names: list[str] = []
    for m in messages:
        if isinstance(m, ChatMessage):
            content = m.content or ""
            role = m.role
        else:
            content = str((m or {}).get("content", ""))
            role = str((m or {}).get("role", ""))
        if role != "system":
            continue
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("### "):
                name = line[4:].strip()
                if name:
                    names.append(name)
    return names


def _latest_tool_text(messages: Sequence) -> str | None:
    """Return the most recent ``role=tool`` message's raw text, or ``None``.

    Unlike :func:`_latest_tool_result` this does not attempt JSON
    parsing — MCP tool results are plain strings, so this is the
    right helper for the MCP tools path.
    """
    for m in reversed(messages):
        if isinstance(m, ChatMessage):
            role = m.role
            content = m.content or ""
        else:
            role = str((m or {}).get("role", ""))
            content = str((m or {}).get("content", ""))
        if role == "tool":
            return content
    return None


def _build_echo_prefix(messages: Sequence) -> str:
    """Return a short ``[echo: ...]`` prefix from the last user message."""
    text = _last_user_text(messages).strip()
    if not text:
        return ""
    snippet = text[:120]
    return f"[echo: {snippet}] "


@register_chat("mock")
class MockChatBackend(ChatBackend):
    """Deterministic chat backend for tests + local development."""

    name = "mock"

    def __init__(self) -> None:
        self._model_path: Path | None = None
        self._context_length: int = 0
        self._loaded: bool = False

    # -- introspection ------------------------------------------------------

    def is_available(self) -> bool:
        # Always available — the whole point of the mock.
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    # -- lifecycle ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        """Record the path; never touch the filesystem.

        The registry resolves a path through
        :meth:`ModelEntry.absolute_path` and passes it here, but tests
        register models whose files don't exist.  Accepting the path
        blindly keeps the contract simple: mocks don't need files.
        Extra kwargs (the model's ``extra`` block + caller overrides)
        are silently ignored — the mock has no knobs to honour.
        """
        self._model_path = Path(model_path) if model_path is not None else None
        self._context_length = int(context_length) if context_length else 0
        self._loaded = True

    def unload(self) -> None:
        self._model_path = None
        self._context_length = 0
        self._loaded = False

    # -- generation ---------------------------------------------------------

    def chat(
        self,
        messages: Sequence,
        params: GenerationParams,
        *,
        stream: bool = False,
        abort_signal=None,
    ) -> Iterator[ChatChunk]:
        if not self.is_loaded():
            raise ModelNotLoaded("no mock chat model loaded")

        max_tokens = _resolve_max_tokens(params)
        chunk_id = f"chatcmpl-mock-{int(time.time() * 1000)}"
        model_id = str(self._model_path) if self._model_path else "mock"

        # MCP tools path (A2): when the pipeline injects the MCP tools
        # system instruction, the mock simulates a model that calls the
        # first available tool on turn 1, then echoes the tool result as
        # the final answer on turn 2.  This lets the tools pipeline be
        # exercised end-to-end without a real model.
        if _system_has_mcp_tools_instruction(messages):
            available = _extract_tool_names_from_system(messages)
            tool_text = _latest_tool_text(messages)
            if tool_text is None and available:
                # First turn — emit a tool call for the first tool.
                tool_name = available[0]
                if stream:
                    return self._streaming_mcp_tool_call(
                        tool_name=tool_name,
                        chunk_id=chunk_id,
                        model_id=model_id,
                        abort_signal=abort_signal,
                    )
                return self._blocking_mcp_tool_call(
                    tool_name=tool_name,
                    chunk_id=chunk_id,
                    model_id=model_id,
                    abort_signal=abort_signal,
                )
            # Second turn (or no tools available) — emit the final answer.
            full_content = self._mcp_final_turn(tool_text, messages)
            if stream:
                return self._streaming(
                    full_content=full_content,
                    chunk_id=chunk_id,
                    model_id=model_id,
                    abort_signal=abort_signal,
                )
            return self._blocking(
                full_content=full_content,
                chunk_id=chunk_id,
                model_id=model_id,
                abort_signal=abort_signal,
            )

        # Forced-recall path (A1.2): when the pipeline injects the
        # recall system instruction, the mock behaves like a real
        # model that dutifully follows it — first turn emits a
        # ``recall_memory`` tool call, second turn (with the tool
        # result attached) emits the final answer.
        if _system_has_recall_instruction(messages):
            tool_result = _latest_tool_result(messages)
            if tool_result is None:
                full_content = self._tool_call_turn(messages, chunk_id=chunk_id)
                kind = "tool_call"
            else:
                full_content = self._final_turn(tool_result, messages)
                kind = "final"
            if stream:
                if kind == "tool_call":
                    return self._streaming_tool_call(
                        chunk_id=chunk_id, model_id=model_id, abort_signal=abort_signal
                    )
                return self._streaming(
                    full_content=full_content,
                    chunk_id=chunk_id,
                    model_id=model_id,
                    abort_signal=abort_signal,
                )
            if kind == "tool_call":
                return self._blocking_tool_call(
                    chunk_id=chunk_id, model_id=model_id, abort_signal=abort_signal
                )
            return self._blocking(
                full_content=full_content,
                chunk_id=chunk_id,
                model_id=model_id,
                abort_signal=abort_signal,
            )

        # Build the full content up front; slice it for streaming.
        # The echo prefix reflects the last user message so callers
        # can verify "the request really reached the backend".
        tail_count = min(max_tokens, len(_MOCK_TOKENS))
        mock_tail = "".join(_MOCK_TOKENS[:tail_count])
        full_content = _build_echo_prefix(messages) + mock_tail

        if stream:
            return self._streaming(
                full_content=full_content,
                chunk_id=chunk_id,
                model_id=model_id,
                abort_signal=abort_signal,
            )
        return self._blocking(
            full_content=full_content,
            chunk_id=chunk_id,
            model_id=model_id,
            abort_signal=abort_signal,
        )

    # -- recall-pipeline helpers -----------------------------------------

    def _tool_call_turn(self, messages: Sequence, *, chunk_id: str) -> str:  # noqa: ARG002
        """Return the assistant's first-turn reply when recall is required.

        The mock doesn't actually *generate* a recall tool call — it
        always invokes ``recall_memory`` with the user's last message
        as the query (mirroring a perfectly obedient model that
        always recalls when asked).  The pipeline turns this into the
        chunk-level tool_call delta in :meth:`_blocking_tool_call`.
        """
        return ""

    def _final_turn(self, tool_result: dict, messages: Sequence) -> str:
        """Compose the second-turn reply using the tool's recall hits.

        Echoes the recalled entries so the response text is grounded
        in real memory (AC-3) and avoids hallucination (AC-4).  The
        snippet includes each entry's content with its id so the
        citation audit has a clear match to verify.
        """
        user_text = _last_user_text(messages)
        entry_ids = tool_result.get("entry_ids") or []
        hits = tool_result.get("hits") or []
        if not hits:
            return (
                f"[recall:no-hits] I checked memory for '{user_text}' but "
                "found no relevant entries."
            )
        parts = [f"[recall:hits={len(hits)}] For '{user_text}', I found:"]
        for h in hits:
            entry_id = h.get("entry_id", "")
            content = (h.get("content") or "").strip()
            parts.append(f"- ({entry_id}) {content}")
        return " ".join(parts)

    def _blocking_tool_call(
        self,
        *,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        tool_call_id = f"call_{chunk_id}"
        # Pull the query from the latest user message so the tool
        # arguments are non-empty and the pipeline's recall search
        # has something to match against.
        # We don't have access to messages here (chat() consumed
        # them), so embed a stable default — the pipeline reads the
        # arguments verbatim and runs recall against whatever the
        # query is, so an empty string is safe (no hits → no
        # citations → audit verdict = pass).
        arguments = json.dumps({"query": "memory", "top_k": 3}, ensure_ascii=False)
        usage = ChatUsage(
            prompt_tokens=0,
            completion_tokens=1,
            total_tokens=1,
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "index": 0,
                        "function": {"name": "recall_memory", "arguments": arguments},
                    }
                ],
            },
            finish_reason="tool_calls",
            usage=usage,
        )

    def _streaming_tool_call(
        self,
        *,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        tool_call_id = f"call_{chunk_id}"
        arguments = json.dumps({"query": "memory", "top_k": 3}, ensure_ascii=False)
        # Role chunk.
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant"},
        )
        # Tool-call delta chunk (split arguments across two chunks to
        # exercise the stream-assembler).
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "index": 0,
                        "function": {"name": "recall_memory", "arguments": arguments[:10]},
                    }
                ]
            },
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"arguments": arguments[10:]},
                    }
                ]
            },
        )
        # Final chunk.
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason="tool_calls",
            usage=ChatUsage(prompt_tokens=0, completion_tokens=1, total_tokens=1),
        )

    # -- mcp-tools-pipeline helpers ---------------------------------------

    def _mcp_final_turn(self, tool_text: str | None, messages: Sequence) -> str:
        """Compose the final reply using the MCP tool's result text.

        Echoes a snippet of the tool result so tests can verify the
        pipeline fed the result back correctly.  When no tool was
        called (``tool_text`` is ``None``) the mock emits a plain
        acknowledgement.
        """
        user_text = _last_user_text(messages)
        if tool_text is None:
            return f"[mcp:no-call] For '{user_text}', no tool was called."
        snippet = tool_text[:200]
        return f"[mcp:result] For '{user_text}', the tool returned: {snippet}"

    def _blocking_mcp_tool_call(
        self,
        *,
        tool_name: str,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        tool_call_id = f"call_{chunk_id}"
        # Empty arguments — the pipeline executes the tool and the
        # registry applies per-tool defaults.  Most MCP tools accept
        # an empty dict and return a sensible default (e.g. list_all).
        arguments = json.dumps({}, ensure_ascii=False)
        usage = ChatUsage(
            prompt_tokens=0,
            completion_tokens=1,
            total_tokens=1,
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "index": 0,
                        "function": {"name": tool_name, "arguments": arguments},
                    }
                ],
            },
            finish_reason="tool_calls",
            usage=usage,
        )

    def _streaming_mcp_tool_call(
        self,
        *,
        tool_name: str,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        tool_call_id = f"call_{chunk_id}"
        arguments = json.dumps({}, ensure_ascii=False)
        # Role chunk.
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant"},
        )
        # Tool-call delta chunk.
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "index": 0,
                        "function": {"name": tool_name, "arguments": arguments},
                    }
                ]
            },
        )
        # Final chunk.
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason="tool_calls",
            usage=ChatUsage(prompt_tokens=0, completion_tokens=1, total_tokens=1),
        )

    # -- internals ----------------------------------------------------------

    def _blocking(
        self,
        *,
        full_content: str,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        # The mock has no tokenizer; report word count as a stand-in
        # so callers that show a token counter get a non-zero value.
        words = len(full_content.split())
        usage = ChatUsage(
            prompt_tokens=0,
            completion_tokens=words,
            total_tokens=words,
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant", "content": full_content},
            finish_reason="stop",
            usage=usage,
        )

    def _streaming(
        self,
        *,
        full_content: str,
        chunk_id: str,
        model_id: str,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        # First chunk: role-only — OAI convention.
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant"},
        )

        # Per-character emission: small enough to look like a real
        # stream in tests, deterministic, and not worth batching.
        aborted = False
        emitted = 0
        try:
            for ch in full_content:
                if abort_signal is not None:
                    abort_signal.raise_if_aborted()
                yield _build_chunk(
                    chunk_id=chunk_id,
                    model=model_id,
                    delta={"content": ch},
                )
                emitted += 1
        except ApiGenerationAborted:
            aborted = True

        # Final chunk: finish_reason + usage.
        words = len(full_content.split()) if emitted else 0
        usage = ChatUsage(
            prompt_tokens=0,
            completion_tokens=words,
            total_tokens=words,
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason="abort" if aborted else "stop",
            usage=usage,
        )


__all__ = ["MockChatBackend"]
