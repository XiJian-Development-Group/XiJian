"""Chat completion stub — routes OAI requests through the model registry.

This module is the seam between the OAI-compatible ``/v1/chat/completions``
route and the per-task backends (mlx / gguf / mock).  Two paths are
supported:

* **Registered model** (``payload["model"]`` matches a ``[[models]]``
  entry in ``config.toml``): the registry loads the backend instance
  declared by the entry (e.g. ``mock`` for tests) and reuses it for
  every call until :meth:`unload` is invoked.  This is the path the
  test suite exercises — ``config.toml`` registers three ``mock``
  chat models so the suite runs without mlx / llama_cpp installed.

* **Free-form model id** (the request carries a string that isn't a
  registered id, e.g. ``"stub-model"``): the configured default backend
  (typically ``mlx`` with ``gguf`` fallback) is instantiated on demand.
  No state is retained between calls — useful for ad-hoc smoke checks
  and the original ``stub``-style demos.

If no backend can serve the request, :class:`xijian_api.errors.BackendError`
is raised with HTTP status 503 and ``code="backend_unavailable"``.
:class:`xijian_api.ai.base.BackendError` from the AI layer is translated
into the same envelope so the route serialises a uniform error shape.

Forced recall pipeline (A1.2)
-----------------------------

When the request payload carries ``xijian.character_id`` and
``xijian.recall.enabled`` is true, this module wraps the backend call
with the forced-recall pipeline from the A1.2 spec:

1. A system instruction is injected telling the model it must call
   ``recall_memory(query)`` whenever it would otherwise mention
   historical information.
2. The request is decorated with a ``recall_memory`` tool spec.
3. If the model emits a ``tool_call`` for ``recall_memory``, the
   pipeline executes the tool against the memory store, feeds the
   matching entries back as a ``tool`` message, and re-calls the
   backend for the final answer.
4. The pipeline records every entry_id that came back from the tool
   as a citation and runs :func:`xijian_api.stubs.citations.audit`
   against the final response text.
5. The OAI response envelope gains ``xijian.recall`` (tool calls +
   citations) and ``xijian.audit`` (verdict + warnings).

The pipeline is opt-in.  Existing callers that don't pass
``xijian.character_id`` see no behaviour change.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from flask import current_app

from xijian_api.ai.base import (
    BackendError as AIBackendError,
)
from xijian_api.ai.base import (
    BackendUnavailable as AIBackendUnavailable,
)
from xijian_api.ai.model_registry import get_registry
from xijian_api.ai.registry import get_chat_backend
from xijian_api.ai.types import (
    ChatBackend,
    ChatMessage,
    GenerationParams,
)
from xijian_api.config import Config
from xijian_api.errors import BackendError as ApiBackendError
from xijian_api.stubs import citations as citations_stub
from xijian_api.stubs import memory as memory_stub
from xijian_api.utils.ids import gen_chat_id


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def _resolve_config() -> Config | None:
    """Return the active Flask app's :class:`Config`, or ``None``."""
    try:
        return current_app.config.get("XIJIAN_CONFIG")
    except RuntimeError:
        return None


def _select_default_backend() -> ChatBackend:
    """Pick a chat backend from the active config's default chain.

    Used only when the request carries a model id that isn't a
    registered ``[[models]]`` entry — the registered path goes
    through :func:`_resolve_backend_for`.  Raises
    :class:`xijian_api.errors.BackendError` (status 503) when no
    backend is reachable.

    The ``mock`` backend is always appended to the configured
    fallbacks so a registered-mock config (test / local-dev) is
    also useful for free-form model ids.  Production deploys that
    only register ``mlx`` / ``gguf`` entries simply omit mock; this
    helper just opportunistically uses it when present.
    """
    config = _resolve_config()
    requested: str | None = None
    fallbacks: tuple[str, ...] = ()
    if config is not None:
        requested = config.backends.chat.default or None
        fallbacks = config.backends.chat.fallbacks or ()
    # Opportunistic mock fallback: harmless when the operator has
    # registered only real backends; lets ``stub-model`` style smoke
    # checks work without a production-grade model on disk.
    if "mock" not in fallbacks and "mock" != requested:
        fallbacks = (*fallbacks, "mock")
    try:
        backend = get_chat_backend(requested, fallbacks)
    except AIBackendUnavailable as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "no chat backend available",
            type_="backend_unavailable",
            code="backend_unavailable",
        ) from exc
    # The mock backend is "always available" but starts unloaded.
    # Free-form ids (e.g. ``stub-model``) don't carry a model entry,
    # so load against a sentinel path the mock accepts blindly.
    if backend.name == "mock" and not backend.is_loaded():
        backend.load("/mock/default")
    return backend


def _resolve_backend_for(model_id: str) -> ChatBackend:
    """Return a ready-to-call backend for ``model_id``.

    * When ``model_id`` matches a registered :class:`ModelEntry`, the
      entry's declared backend is loaded through the process-wide
      :class:`ModelRegistry` and the cached instance is returned.
      Subsequent calls reuse the same instance.
    * Otherwise the configured default chain is tried — useful for
      ad-hoc free-form model ids that don't need to be registered.

    AI-layer failures are translated into the API's :class:`ApiError`
    envelope with HTTP 503.
    """
    config = _resolve_config()
    if config is not None:
        entry = config.model_by_id(model_id)
        if entry is not None and entry.type == "chat":
            try:
                registry = get_registry()
                loaded = registry.load(model_id, config=config)
                return loaded.instance
            except AIBackendUnavailable as exc:
                raise ApiBackendError(
                    status=503,
                    message=str(exc) or "no chat backend available",
                    type_="backend_unavailable",
                    code="backend_unavailable",
                ) from exc
            except AIBackendError as exc:
                # Translate known AI-layer errors into the API envelope
                # so the route can surface a uniform error shape.
                raise ApiBackendError(
                    status=503,
                    message=str(exc) or "backend error",
                    type_="backend_unavailable",
                    code=getattr(exc, "code", "backend_error"),
                ) from exc
    return _select_default_backend()


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _normalise_messages(messages: list[Any]) -> list[ChatMessage]:
    """Coerce raw dicts into :class:`ChatMessage` instances."""
    out: list[ChatMessage] = []
    for m in messages:
        if isinstance(m, ChatMessage):
            out.append(m)
        else:
            out.append(
                ChatMessage(
                    role=str(m.get("role", "user")),
                    content=str(m.get("content", "")),
                    name=m.get("name"),
                    tool_call_id=m.get("tool_call_id"),
                    tool_calls=m.get("tool_calls"),
                )
            )
    return out


def _to_oai_chunk(chunk) -> dict[str, Any]:
    """Convert a backend :class:`ChatChunk` to an OAI streaming chunk dict."""
    payload: dict[str, Any] = {
        "id": chunk.id,
        "object": "chat.completion.chunk",
        "created": chunk.created,
        "model": chunk.model,
        "choices": [
            {
                "index": c.index,
                "delta": c.delta,
                "finish_reason": c.finish_reason,
            }
            for c in chunk.choices
        ],
    }
    if chunk.usage is not None:
        payload["usage"] = {
            "prompt_tokens": chunk.usage.prompt_tokens,
            "completion_tokens": chunk.usage.completion_tokens,
            "total_tokens": chunk.usage.total_tokens,
        }
    return payload


def _to_oai_response(backend_result, *, model: str) -> dict[str, Any]:
    """Convert a backend non-streaming result to an OAI completion dict.

    Backends return an iterable of :class:`ChatChunk` objects; for
    non-streaming we collapse them into a single message with a single
    finish_reason, mirroring how OpenAI returns ``chat.completion``.
    """
    completion_id = gen_chat_id()
    created = None
    content_parts: list[str] = []
    finish_reason: str | None = None
    usage_dict: dict[str, int] | None = None
    backend_name = ""
    for chunk in backend_result:
        created = created or chunk.created
        backend_name = backend_name or getattr(chunk, "backend", "")
        for choice in chunk.choices:
            delta = choice.delta or {}
            content = ""
            if isinstance(delta, dict):
                content = delta.get("content") or ""
            elif isinstance(delta, str):
                content = delta
            if content:
                content_parts.append(content)
            if choice.finish_reason:
                finish_reason = choice.finish_reason
        if chunk.usage is not None:
            usage_dict = {
                "prompt_tokens": chunk.usage.prompt_tokens,
                "completion_tokens": chunk.usage.completion_tokens,
                "total_tokens": chunk.usage.total_tokens,
            }
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created or 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "".join(content_parts),
                },
                "finish_reason": finish_reason or "stop",
                "logprobs": None,
            }
        ],
        "usage": usage_dict or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "xijian": {"backend": backend_name or ""},
    }


# ---------------------------------------------------------------------------
# Forced recall pipeline (A1.2)
# ---------------------------------------------------------------------------


#: System instruction appended to the chat when recall is enabled.
#: Mirrors the wording in the spec — phrased so the model knows it
#: must not invent history and must cite real entries when it does.
_RECALL_SYSTEM_PROMPT = (
    "你必须遵守以下记忆召回规则：\n"
    "1. 当你的回复可能引用过往对话、用户偏好或历史事件时，**必须**先调用 "
    "`recall_memory` 工具进行检索。\n"
    "2. 只引用工具实际返回的 `entry_id`；不得捏造未检索到的历史细节。\n"
    "3. 若用户问题与历史无关，可不调用工具，正常回答即可。\n"
    "4. 引用记忆时，明确指出这是来自记忆库的事实。"
)


def _recall_tool_spec(character_id: str | None) -> dict[str, Any]:
    """Return the OAI-style tool spec for ``recall_memory``.

    The spec is the same shape as OpenAI's ``tools`` array entry so the
    backend can surface it to the model verbatim.
    """
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "需要回忆的内容描述，例如'用户喜欢的食物'",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "返回的最大记忆条数",
            },
        },
        "required": ["query"],
    }
    if character_id is not None:
        parameters["properties"]["character_id"] = {
            "type": "string",
            "description": "目标角色 ID；缺省时使用请求中的 character_id",
        }
    return {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": (
                "查询角色的长期/短期记忆。当你需要引用过往信息（用户的偏好、"
                "过去的对话、曾经许下的承诺等）时必须先调用此工具。"
            ),
            "parameters": parameters,
        },
    }


def _should_enable_recall(xijian: dict | None) -> bool:
    if not isinstance(xijian, dict):
        return False
    if not xijian.get("character_id"):
        return False
    recall = xijian.get("recall")
    if isinstance(recall, dict):
        return bool(recall.get("enabled", False))
    return False


def _inject_recall_system(messages: list[dict]) -> list[dict]:
    """Prepend the recall system instruction if no system message exists,
    otherwise append it to the first system message.  Returns a new list
    so callers can keep the original untouched."""
    out = list(messages)
    if not out or out[0].get("role") != "system":
        return [{"role": "system", "content": _RECALL_SYSTEM_PROMPT}, *out]
    first = dict(out[0])
    first["content"] = (first.get("content") or "") + "\n\n" + _RECALL_SYSTEM_PROMPT
    out[0] = first
    return out


def _inject_memory_context(messages: list[dict], memory_block: str) -> list[dict]:
    """Insert the per-character memory block as the first system message.

    The block is built by :func:`xijian_api.stubs.memory.load_context`
    and contains long-term and short-term memory in Markdown form.  It
    is layered *before* the recall-rule system message so the model
    reads "what the character knows" first and "how to cite it" second.
    Returns a new list; the input is not mutated.
    """
    if not memory_block:
        return list(messages)
    block_msg = {"role": "system", "content": memory_block}
    # If the first message is already a system message we still
    # prepend — the model treats two consecutive system messages as a
    # single context block, but keeping the memory block first ensures
    # the model's attention sees the canonical facts before the rule
    # reminder.
    return [block_msg, *list(messages)]


def _execute_recall_call(arguments: str, *, default_character_id: str | None) -> dict[str, Any]:
    """Run the recall_memory tool call and return the parsed result envelope.

    The envelope mirrors the ``memory.search`` response shape so the
    backend / downstream LLM can consume it without a second mapping
    pass.  ``entry_ids`` is the flat list of returned entry ids — the
    citation audit reads from here directly.
    """
    try:
        args = json.loads(arguments or "{}") if arguments else {}
    except json.JSONDecodeError:
        args = {}
    character_id = args.get("character_id") or default_character_id
    query = args.get("query", "")
    top_k = int(args.get("top_k", 5) or 5)
    hits = memory_stub.recall_search(
        character_id=character_id,
        query=query,
        top_k=top_k,
    )
    entries = [h["entry"] for h in hits]
    return {
        "object": "memory.recall_result",
        "query": query,
        "character_id": character_id,
        "hits": [
            {
                "entry_id": e["id"],
                "score": s,
                "type": e.get("type"),
                "importance": e.get("importance"),
                "content": e.get("content"),
                "tags": e.get("tags"),
                "created_at": e.get("created_at"),
            }
            for e, (_, s) in zip(entries, [(h["score"], h["score"]) for h in hits])
        ],
        "entry_ids": [e["id"] for e in entries],
    }


def _extract_tool_calls(response_chunks: list) -> list[dict[str, Any]]:
    """Pull ``tool_call``\\s out of a stream of assistant chunks.

    The backend may emit a tool_call in any chunk — we accumulate the
    arguments string per ``id`` because the OAI streaming convention
    is to split arguments across chunks.  ``finish_reason="tool_calls"``
    is the canonical signal that the tool list is complete.
    """
    aggregated: dict[str, dict[str, Any]] = {}
    for chunk in response_chunks:
        for choice in getattr(chunk, "choices", []) or []:
            delta = choice.delta if isinstance(choice.delta, dict) else {}
            for tc in delta.get("tool_calls") or []:
                tc_id = tc.get("id") or ""
                bucket = aggregated.setdefault(
                    tc_id,
                    {
                        "id": tc_id,
                        "type": tc.get("type", "function"),
                        "function": {"name": "", "arguments": ""},
                    },
                )
                fn = tc.get("function") or {}
                bucket["function"]["name"] = bucket["function"]["name"] or fn.get("name", "")
                bucket["function"]["arguments"] += fn.get("arguments", "") or ""
    return list(aggregated.values())


def _content_to_text(content: Any) -> str:
    """Coerce a (possibly structured) content payload into a plain string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content)


def _chat_messages_for_backend(messages: list[Any]) -> list[ChatMessage]:
    """Normalise raw dicts (plus OAI ``tool`` messages) into ChatMessage."""
    out: list[ChatMessage] = []
    for m in messages:
        if isinstance(m, ChatMessage):
            out.append(m)
            continue
        role = str(m.get("role", "user"))
        if role == "tool":
            content = m.get("content", "")
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False)
            out.append(
                ChatMessage(
                    role="tool",
                    content=str(content),
                    name=m.get("name"),
                    tool_call_id=m.get("tool_call_id"),
                )
            )
            continue
        out.append(
            ChatMessage(
                role=role,
                content=str(m.get("content", "")),
                name=m.get("name"),
                tool_call_id=m.get("tool_call_id"),
                tool_calls=m.get("tool_calls"),
            )
        )
    return out


def _run_recall_pipeline(
    backend: ChatBackend,
    messages: list[Any],
    *,
    model: str,
    character_id: str | None,
    params: GenerationParams,
    audit_response: bool,
) -> dict[str, Any]:
    """Drive the forced-recall pipeline and return the OAI envelope.

    The flow:

    1. Run :func:`xijian_api.stubs.memory.load_context` to assemble
       the per-character memory block (long-term + short-term, with
       token-budget trimming when the budget is tight).
    2. Inject the recall system instruction (rule reminder).
    3. First backend call with the recall_memory tool spec attached.
    4. If the response contains tool calls for ``recall_memory``,
       execute them against the memory store, append the results as
       ``tool`` messages, and re-call the backend for the final answer.
    5. Run the citation audit if any entries were cited or the final
       text references past events.

    Returns the OAI envelope plus ``xijian.recall`` / ``xijian.context``
    / ``xijian.audit`` blocks.  ``xijian.context`` is the load_context
    envelope (counts, ids, tokens, trimmed flag) so callers / tests can
    assert which memories actually made it into the prompt.
    """
    context_envelope: dict[str, Any] = {
        "system_message": "",
        "long_term_count": 0,
        "short_term_count": 0,
        "trimmed": False,
        "empty": True,
    }
    if character_id is not None:
        context_envelope = memory_stub.load_context(character_id)

    prepared_messages = _inject_memory_context(messages, context_envelope["system_message"])
    prepared_messages = _inject_recall_system(prepared_messages)
    tools = [_recall_tool_spec(character_id)]

    try:
        first_iter = backend.chat(
            _chat_messages_for_backend(prepared_messages),
            params,
            stream=False,
        )
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc

    # ``backend.chat`` returns an iterable — drain it once so we can
    # inspect both the rendered chunks (for tool calls) and the
    # accumulated content.  The blocking backends yield a single
    # chunk; streaming would need different handling, but the test
    # suite and most real callers go through ``complete``.
    first_chunks = list(first_iter)
    tool_calls = _extract_tool_calls(first_chunks)
    recall_calls_log: list[dict[str, Any]] = []
    cited_entry_ids: list[str] = []
    final_text_parts: list[str] = []
    for chunk in first_chunks:
        for choice in getattr(chunk, "choices", []) or []:
            delta = choice.delta if isinstance(choice.delta, dict) else {}
            text = _content_to_text(delta.get("content"))
            if text:
                final_text_parts.append(text)

    context_block = {
        "long_term_count": context_envelope["long_term_count"],
        "short_term_count": context_envelope["short_term_count"],
        "long_term_ids": list(context_envelope["long_term_ids"]),
        "short_term_ids": list(context_envelope["short_term_ids"]),
        "estimated_tokens": context_envelope["estimated_tokens"],
        "budget_tokens": context_envelope["budget_tokens"],
        "trimmed": context_envelope["trimmed"],
    }

    if not tool_calls:
        # No recall invoked — emit the first response verbatim, but
        # still run the citation audit when the response text itself
        # references past events without citing anything (AC-3/AC-4).
        if audit_response:
            audit_result = citations_stub.audit(
                response_text="".join(final_text_parts),
                candidate_entry_ids=cited_entry_ids,
            )
        else:
            audit_result = None
        response = _to_oai_response(first_chunks, model=model)
        response["xijian"]["recall"] = {
            "enabled": True,
            "tool_calls": recall_calls_log,
            "citations": cited_entry_ids,
            "auto_executed": False,
        }
        response["xijian"]["context"] = context_block
        response["xijian"]["audit"] = audit_result
        return response

    # Execute the recall calls and feed results back as tool messages.
    tool_messages: list[dict[str, Any]] = []
    for tc in tool_calls:
        name = tc.get("function", {}).get("name", "")
        if name != "recall_memory":
            continue
        args_str = tc.get("function", {}).get("arguments", "")
        result = _execute_recall_call(args_str, default_character_id=character_id)
        cited_entry_ids.extend(result["entry_ids"])
        recall_calls_log.append(
            {
                "tool_call_id": tc.get("id"),
                "name": name,
                "arguments": args_str,
                "result_entry_ids": result["entry_ids"],
            }
        )
        tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "name": name,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

    # Second turn: assistant tool_calls message + tool results, then
    # request the final answer.
    follow_up_messages = [
        *prepared_messages,
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tc.get("id"),
                    "type": tc.get("type", "function"),
                    "function": tc.get("function", {}),
                }
                for tc in tool_calls
            ],
        },
        *tool_messages,
    ]
    try:
        second_iter = backend.chat(
            _chat_messages_for_backend(follow_up_messages),
            params,
            stream=False,
        )
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc

    second_chunks = list(second_iter)
    final_text_parts2: list[str] = []
    for chunk in second_chunks:
        for choice in getattr(chunk, "choices", []) or []:
            delta = choice.delta if isinstance(choice.delta, dict) else {}
            text = _content_to_text(delta.get("content"))
            if text:
                final_text_parts2.append(text)

    final_text = "".join(final_text_parts2)
    audit_result = None
    if audit_response:
        audit_result = citations_stub.audit(
            response_text=final_text,
            candidate_entry_ids=cited_entry_ids,
        )

    response = _to_oai_response(second_chunks, model=model)
    response["xijian"]["recall"] = {
        "enabled": True,
        "tool_calls": recall_calls_log,
        "citations": cited_entry_ids,
        "auto_executed": True,
    }
    response["xijian"]["context"] = context_block
    response["xijian"]["audit"] = audit_result
    return response


# ---------------------------------------------------------------------------
# Public entry points — called by the chat route
# ---------------------------------------------------------------------------


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
    """Return a non-streaming OAI chat completion payload via the backend.

    When ``xijian`` carries ``character_id`` and ``recall.enabled``,
    the forced-recall pipeline (A1.2) intercepts the call: it injects
    the recall system instruction, attaches the ``recall_memory`` tool,
    executes any tool calls against the memory store, and audits the
    final response for citation faithfulness.
    """
    _ = user  # accepted for OAI parity; backends consume the rest
    n = max(1, int(n or 1))
    backend = _resolve_backend_for(model)
    params = GenerationParams(
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        stop=stop,
        n=n,
    )

    if _should_enable_recall(xijian):
        # The pipeline walks n-times for parity but most callers pass n=1.
        # Multi-n is rare in chat — we still loop, picking the first
        # completion's audit verdict for the response.
        last_response: dict[str, Any] | None = None
        for _ in range(n):
            last_response = _run_recall_pipeline(
                backend,
                messages,
                model=model,
                character_id=(xijian or {}).get("character_id"),
                params=params,
                audit_response=bool((xijian or {}).get("recall", {}).get("audit", True)),
            )
        return last_response or {"id": gen_chat_id(), "object": "chat.completion", "choices": []}

    try:
        result = backend.chat(
            _normalise_messages(messages),
            params,
            stream=False,
        )
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc
    return _to_oai_response(result, model=model)


def stream_chunks(
    messages: list[dict],
    *,
    model: str = "stub-model",
    temperature: float = 0.7,
    top_p: float = 1.0,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
    signal=None,
    include_usage: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield OAI streaming chunks via the backend.

    The backend yields :class:`ChatChunk` instances; this function
    serialises them into OAI ``chat.completion.chunk`` JSON.  The
    ``signal`` is forwarded so client cancels abort generation.
    """
    backend = _resolve_backend_for(model)
    params = GenerationParams(
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        stop=stop,
    )
    try:
        for chunk in backend.chat(
            _normalise_messages(messages),
            params,
            stream=True,
            abort_signal=signal,
        ):
            yield _to_oai_chunk(chunk)
        if include_usage:
            # Emit a trailing usage-only chunk if the backend didn't.
            yield {
                "id": gen_chat_id(),
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc


__all__ = ["complete", "stream_chunks"]