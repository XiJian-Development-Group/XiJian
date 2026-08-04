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
在调用之间保留，不保留状态，已注册，按需。

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
from xijian_api.errors import ApiError
from xijian_api.errors import BackendError as ApiBackendError
from xijian_api.stubs import citations as citations_stub
from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import safety as safety_stub
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
    已注册。

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
    # Free-form ids (e.g. ``stub-model``) don't carry a model entry,
    # so there's no checkpoint path to load into a real backend.
    # When the selected backend is available but not loaded (e.g.
    # mlx_lm is installed but no model has been loaded via
    # ``/v1/models/<id>/load``), fall through to mock so smoke
    # checks keep working without a real checkpoint on disk.
    if backend.name == "mock":
        if not backend.is_loaded():
            backend.load("/mock/default")
        return backend
    if not backend.is_loaded():
        # Try mock as a last resort for free-form ids.
        try:
            mock = get_chat_backend("mock", ())
        except AIBackendUnavailable:
            mock = None
        if mock is not None:
            if not mock.is_loaded():
                mock.load("/mock/default")
            return mock
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
    已注册。

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
    """Coerce raw dicts into :class:`ChatMessage` instances.

    Preserves multimodal content: when the original ``content`` is a list
    of content parts (OAI multimodal format), it is kept as-is rather than
    being forced to a string.
    保留多模态内容：当原始 ``content`` 是内容片段列表（OAI 多模态格式）时，
    将其原样保留，而不是强制转换为字符串。
    """
    out: list[ChatMessage] = []
    for m in messages:
        if isinstance(m, ChatMessage):
            out.append(m)
        else:
            raw_content = m.get("content", "")
            # Preserve list-type content (multimodal parts) as-is.
            # 保留列表类型的内容（多模态片段）不变。
            if isinstance(raw_content, list):
                content = raw_content
            else:
                content = str(raw_content) if raw_content is not None else ""
            out.append(
                ChatMessage(
                    role=str(m.get("role", "user")),
                    content=content,
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
    相同形状。

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
    character_id = xijian.get("character_id")
    if not character_id:
        return False
    recall = xijian.get("recall")
    if isinstance(recall, dict):
        if not bool(recall.get("enabled", False)):
            return False
    else:
        return False
    # Per-character memory_config.force_recall_on_history (A1.2).
    # When set to 0 (False) on the character config, recall is
    # skipped even if the global/request-level flag says enabled.
    from xijian_api.stubs import state
    mem_cfg = state.memory_configs.get(character_id, {})
    if mem_cfg.get("force_recall_on_history", 1) == 0:
        return False
    return True


# ---------------------------------------------------------------------------
# Character context injection (A2-01 / A3.2 / A4)
# ---------------------------------------------------------------------------

_CHARACTER_CONTEXT_TEMPLATE = """## 当前角色设定

### 角色基本信息
- 名称: {display_name}
- 描述: {description}
- 语言风格: {language_style}

### 角色人设
{persona_doc}

### 当前状态
{state_summary}

### 当前世界上下文
{world_context}


请严格遵循以上角色设定进行回复。"""


def _build_character_context(character_id: str) -> str | None:
    """Build a character context block from persona, state, and world.

    Reads from the core stubs (characters, character_state, worlds)
    and returns a Markdown-formatted string to inject into the system
    prompt.

    Returns ``None`` if the character is not found.
    未找到。

    """
    from xijian_api.stubs.characters import get as get_char
    from xijian_api.stubs.character_state import (
        get_or_init_state,
        get_active_behavior,
    )

    char = get_char(character_id)
    if char is None:
        return None

    display_name = char.get("display_name", char.get("name", "未知角色"))
    description = char.get("description", "")
    language_style = char.get("language_style", "")
    persona_doc = char.get("persona_doc", "")

    # Build state summary (A3.2)
    state_rec = get_or_init_state(character_id)
    if state_rec:
        state_summary = (
            f"心情: {state_rec.get('mood', 70):.0f}/100 | "
            f"饱食: {state_rec.get('hunger', 80):.0f}/100 | "
            f"饮水: {state_rec.get('thirst', 80):.0f}/100 | "
            f"健康: {state_rec.get('health', 100):.0f}/100 | "
            f"状态: {state_rec.get('status', 'Healthy')}"
        )
        behavior = get_active_behavior(character_id)
        if behavior:
            state_summary += f"\n当前行为倾向: {behavior}"
    else:
        state_summary = "（无状态数据）"

    # Build world context (A4)
    assigned_world = char.get("assigned_world", "")
    if assigned_world:
        from xijian_api.stubs.worlds import get as get_world
        from xijian_api.stubs.world_environment import get_env
        world = get_world(assigned_world)
        if world:
            env = get_env(assigned_world)
            world_context = f"世界: {world.get('name', assigned_world)}"
            if env:
                world_context += (
                    f"\n天气: {env.get('weather', '未知')} | "
                    f"时间: {env.get('time_of_day', 0)}分"
                )
        else:
            world_context = f"世界ID: {assigned_world}（未加载）"
    else:
        world_context = "（未绑定世界）"

    return _CHARACTER_CONTEXT_TEMPLATE.format(
        display_name=display_name,
        description=description,
        language_style=language_style or "默认",
        persona_doc=persona_doc or "（无人设文档）",
        state_summary=state_summary,
        world_context=world_context,
    )


def _inject_character_context(
    messages: list[dict],
    character_id: str,
) -> list[dict]:
    """Inject the character context block into the system prompt.

    Merges with any existing system message content rather than
    replacing it.
    """
    context_block = _build_character_context(character_id)
    if context_block is None:
        return messages

    out = list(messages)
    if not out or out[0].get("role") != "system":
        return [{"role": "system", "content": context_block}, *out]
    first = dict(out[0])
    first["content"] = (first.get("content") or "") + "\n\n" + context_block
    out[0] = first
    return out


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
    不修改输入，不修改。

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
    镜像。

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
        raw_content = m.get("content", "")
        # Preserve list-type content (multimodal parts) as-is.
        # 保留列表类型的内容（多模态片段）不变。
        if isinstance(raw_content, list):
            content = raw_content
        else:
            content = str(raw_content) if raw_content is not None else ""
        out.append(
            ChatMessage(
                role=role,
                content=content,
                name=m.get("name"),
                tool_call_id=m.get("tool_call_id"),
                tool_calls=m.get("tool_calls"),
            )
        )
    return out


#: Max regeneration attempts after an AC-4 ``block`` verdict.
#: The spec caps regeneration at 2 (1 original + up to 2 retries).
#: AC-4 拦截后的最大重生成次数。规范将重生成上限设为 2
#: (1 次原始生成 + 至多 2 次重试)。
_MAX_REGENERATE_ATTEMPTS = 2


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

    **AC-4 regeneration (spec §强制调用规则)**: when the citation
    audit returns ``block`` (the model fabricated history without a
    matching memory entry), the pipeline regenerates up to
    :data:`_MAX_REGENERATE_ATTEMPTS` more times.  Each retry injects
    an explicit system note telling the model to only cite real
    ``recall_memory`` results.  The final response's
    ``xijian.recall.regenerations`` field reports how many retries
    actually ran.

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

    base_prepared = _inject_memory_context(
        messages, context_envelope["system_message"]
    )
    base_prepared = _inject_recall_system(base_prepared)

    context_block = {
        "long_term_count": context_envelope["long_term_count"],
        "short_term_count": context_envelope["short_term_count"],
        "long_term_ids": list(context_envelope["long_term_ids"]),
        "short_term_ids": list(context_envelope["short_term_ids"]),
        "estimated_tokens": context_envelope["estimated_tokens"],
        "budget_tokens": context_envelope["budget_tokens"],
        "trimmed": context_envelope["trimmed"],
    }

    regenerations = 0
    last_response: dict[str, Any] | None = None
    last_audit: dict | None = None

    for attempt in range(_MAX_REGENERATE_ATTEMPTS + 1):
        prepared_messages = list(base_prepared)
        if attempt > 0:
            # AC-4 regeneration hint — appended to the first system
            # message so the model understands why it's being asked
            # to answer again.
            # AC-4 重生成提示 — 追加到第一条 system 消息，
            # 让模型明白为何被要求重新回答。
            hint = (
                "\n\n[系统重试] 你上一次的回复因引用审查被拦截："
                "你提到了历史信息，但没有引用任何 `recall_memory` 返回的 "
                "`entry_id`（或引用了不存在的条目）。请重新生成回复："
                "涉及过往信息时必须先调用 `recall_memory` 工具，并且只引用 "
                "工具实际返回的 `entry_id`。"
            )
            if not prepared_messages or prepared_messages[0].get("role") != "system":
                prepared_messages = [{"role": "system", "content": hint}, *prepared_messages]
            else:
                first = dict(prepared_messages[0])
                first["content"] = (first.get("content") or "") + hint
                prepared_messages[0] = first

        response, audit_result = _run_recall_attempt(
            backend,
            prepared_messages,
            model=model,
            character_id=character_id,
            params=params,
            audit_response=audit_response,
            context_block=context_block,
        )
        last_response = response
        last_audit = audit_result

        if audit_result is None or audit_result.get("verdict") != citations_stub.VERDICT_BLOCK:
            break
        if attempt < _MAX_REGENERATE_ATTEMPTS:
            regenerations += 1

    if last_response is None:
        # Defensive — an empty response should never happen, but keep
        # the envelope shape stable for callers.
        last_response = {
            "id": gen_chat_id(),
            "object": "chat.completion",
            "choices": [],
            "xijian": {},
        }
    last_response.setdefault("xijian", {})
    last_response["xijian"]["recall"]["regenerations"] = regenerations
    if last_audit is not None:
        last_response["xijian"]["audit"] = last_audit
    return last_response


def _run_recall_attempt(
    backend: ChatBackend,
    prepared_messages: list[Any],
    *,
    model: str,
    character_id: str | None,
    params: GenerationParams,
    audit_response: bool,
    context_block: dict[str, Any],
) -> tuple[dict[str, Any], dict | None]:
    """Run one recall-pipeline pass and return ``(response, audit)``.

    Extracted from :func:`_run_recall_pipeline` so the AC-4
    regeneration loop can re-invoke it with a fresh hint.
    """
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
        return response, audit_result

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
    return response, audit_result


# ---------------------------------------------------------------------------
# MCP tools pipeline (A2 — tool calling)
# ---------------------------------------------------------------------------


#: System instruction appended when MCP tools are enabled.  Tells the
#: model it has access to XiJian tools and should use them for real
#: actions rather than fabricating results.
_TOOLS_SYSTEM_PROMPT = (
    "你可以使用以下工具来完成用户的请求。工具调用规则：\n"
    "1. 当需要执行实际操作（创建角色、查询记忆、读写文件等）时，**必须**调用相应工具。\n"
    "2. 不要捏造工具调用的结果——必须等待工具返回后再继续回复。\n"
    "3. 工具可能被安全策略（A5.2）拒绝，此时请告知用户操作被拦截，不要尝试绕过。\n"
    "4. 对于查询类工具，调用后将结果整理为用户友好的格式再回复。\n"
    "5. 可以在一次回复中调用多个工具，但每个 tool_call 必须有独立的 id。"
)


def _mcp_tool_to_oai(tool_spec: dict[str, Any]) -> dict[str, Any]:
    """Convert an MCP tool spec to the OAI ``tools`` array entry shape."""
    return {
        "type": "function",
        "function": {
            "name": tool_spec["name"],
            "description": tool_spec.get("description", ""),
            "parameters": tool_spec.get("inputSchema", {"type": "object", "properties": {}}),
        },
    }


def _should_enable_tools(xijian: dict | None, user_tools: list | None) -> bool:
    """Return True if the MCP tools pipeline should run.

    Triggered by either:
    * ``xijian.tools.enabled = true``  → inject all MCP tools
    * ``tools`` field is a non-empty list → use provided tools, execute
      MCP tool names through the registry / A5.2 gate
    """
    if isinstance(user_tools, list) and len(user_tools) > 0:
        return True
    if isinstance(xijian, dict):
        tools_cfg = xijian.get("tools")
        if isinstance(tools_cfg, dict) and tools_cfg.get("enabled"):
            return True
    return False


def _build_oai_tools(
    xijian: dict | None,
    user_tools: list | None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Build the OAI tools list and return ``(tools, mcp_names)``.

    * If ``xijian.tools.enabled`` is true, every registered MCP tool
      is injected.
    * If ``user_tools`` (the OAI ``tools`` field) is provided, those
      entries are added as-is.
    * ``mcp_names`` is the set of tool names that should be executed
      through the MCP registry (i.e. names that exist in the MCP
      registry).  Non-MCP tools in ``user_tools`` are passed to the
      model but can't be executed by the server — the model would
      need to handle them client-side.
    已注册。

    """
    # Lazy import to avoid circular dependency at module load time.
    from xijian_api.mcp.registry import list_tool_names, list_tools

    oai_tools: list[dict[str, Any]] = []
    mcp_names: set[str] = set()

    # 1) Inject all MCP tools if xijian.tools.enabled is true.
    if isinstance(xijian, dict):
        tools_cfg = xijian.get("tools")
        if isinstance(tools_cfg, dict) and tools_cfg.get("enabled"):
            all_mcp = list_tools()
            include = tools_cfg.get("include")
            exclude = set(tools_cfg.get("exclude") or [])
            for spec in all_mcp:
                name = spec["name"]
                if name in exclude:
                    continue
                if include and name not in set(include):
                    continue
                oai_tools.append(_mcp_tool_to_oai(spec))
                mcp_names.add(name)

    # 2) Merge user-provided OAI tools.
    if isinstance(user_tools, list):
        registered = set(list_tool_names())
        for entry in user_tools:
            if not isinstance(entry, dict):
                continue
            # Standardise the entry shape.
            fn = entry.get("function") or entry
            name = fn.get("name")
            if name and name in registered:
                mcp_names.add(name)
                # Skip if already injected from MCP.
                if any(
                    t.get("function", {}).get("name") == name for t in oai_tools
                ):
                    continue
            oai_tools.append(entry)

    return oai_tools, mcp_names


def _mcp_gate_check(
    name: str,
    args: dict[str, Any],
    *,
    world_id: str | None,
) -> dict | None:
    """Run the A5.2 gate for a chat-dispatched MCP tool call (T0-1).

    Returns the gate result dict when the call must be **refused**
    (``verdict != "allowed"``), or ``None`` when the call is allowed.
    Tools that declare no ``action_kind`` (internal domain tools)
    bypass the gate — this mirrors the registry's own semantics in
    :func:`xijian_api.mcp.registry.call_tool`, where ``action_kind``
    is the switch that opts a tool into protection.

    The audit entry is written by :func:`xijian_api.stubs.mcp.check`
    itself, so a refusal is always queryable via ``mcp.count_audit``
    (AC-1: 黑名单动作 100% 拦截).
    """
    # Lazy import to avoid a module-load cycle: ``stubs/__init__``
    # imports ``chat`` before ``mcp``.
    # 惰性导入避免模块加载循环：``stubs/__init__`` 先导入 ``chat`` 再导入 ``mcp``。
    from xijian_api.mcp.registry import get_tool as mcp_get_tool
    from xijian_api.stubs import mcp as mcp_stub

    record = mcp_get_tool(name)
    if record is None:
        # Unknown tool — let the registry surface ``tool_not_found``.
        return None
    action_kind = record.get("action_kind")
    if action_kind is None:
        # Domain tool (no action_kind) — bypass, like the registry.
        return None
    gate = mcp_stub.check(
        action_kind=action_kind,
        args=args,
        world_id=world_id,
    )
    if gate.get("verdict") == mcp_stub.VERDICT_ALLOWED:
        return None
    return gate


def _execute_mcp_tool_call(
    name: str,
    arguments: dict[str, Any],
    *,
    world_id: str | None = None,
) -> dict[str, Any]:
    """Execute an MCP tool call through the registry (A5.2 gate).

    Returns a dict with ``{"content": str, "isError": bool, ...}``.
    """
    from xijian_api.mcp.registry import (
        ToolError as MCPToolError,
        ToolGateError,
        ToolNotFoundError,
        call_tool,
    )

    try:
        result = call_tool(name, arguments, world_id=world_id)
    except ToolNotFoundError:
        return {
            "content": "错误：工具 %s 不存在" % name,
            "isError": True,
            "error_type": "tool_not_found",
        }
    except ToolGateError as exc:
        return {
            "content": exc.message,
            "isError": True,
            "error_type": "gate_denied",
            "gate": exc.data,
        }
    except MCPToolError as exc:
        return {
            "content": exc.message,
            "isError": True,
            "error_type": "tool_error",
            "data": exc.data,
        }

    # Flatten the MCP content envelope into a plain string for the
    # tool message.  The OAI tool message content is a string.
    content_parts: list[str] = []
    for item in result.get("content", []):
        if isinstance(item, dict):
            content_parts.append(item.get("text", ""))
        elif isinstance(item, str):
            content_parts.append(item)
    return {
        "content": "\n".join(content_parts) or "(empty result)",
        "isError": result.get("isError", False),
        "_meta": result.get("_meta"),
    }


def _run_tools_pipeline(
    backend: ChatBackend,
    messages: list[Any],
    *,
    model: str,
    xijian: dict | None,
    user_tools: list | None,
    tool_choice: Any,
    params: GenerationParams,
) -> dict[str, Any]:
    """Drive the MCP tools pipeline and return the OAI envelope.

    The flow:

    1. Build the OAI tools list from MCP tools + user-provided tools.
    2. Inject the tools system instruction.
    3. First backend call with tools attached.
    4. If the model emits tool_calls, execute each one through the
       MCP registry (routes through A5.2 gate for desktop-control
       tools).  Feed results back as ``tool`` messages.
    5. Re-call the backend for the final answer.
    6. Record tool calls in ``xijian.tools`` block.

    The pipeline supports multiple rounds of tool calls: if the
    second backend response also contains tool_calls, we execute
    them and call again, up to ``_MAX_TOOL_ROUNDS`` iterations.
    """
    _MAX_TOOL_ROUNDS = 5

    world_id = (xijian or {}).get("world_id")
    oai_tools, mcp_names = _build_oai_tools(xijian, user_tools)
    if not oai_tools:
        # No tools to inject — fall through to a direct call.
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

    # Inject tool descriptions into the system prompt text (NOT as
    # backend kwargs) — the ChatBackend interface is a low-level
    # text-generation contract that doesn't accept tools/tool_choice.
    # This mirrors the recall pipeline's system-prompt approach.
    prepared_messages = _inject_tools_system(
        messages, oai_tools=oai_tools, tool_choice=tool_choice,
    )

    all_tool_calls_log: list[dict[str, Any]] = []
    current_messages = list(prepared_messages)

    for round_num in range(_MAX_TOOL_ROUNDS):
        try:
            iter_chunks = backend.chat(
                _chat_messages_for_backend(current_messages),
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

        chunks = list(iter_chunks)
        tool_calls = _extract_tool_calls(chunks)

        # Collect text content from this round.
        round_text_parts: list[str] = []
        for chunk in chunks:
            for choice in getattr(chunk, "choices", []) or []:
                delta = choice.delta if isinstance(choice.delta, dict) else {}
                text = _content_to_text(delta.get("content"))
                if text:
                    round_text_parts.append(text)

        if not tool_calls:
            # No more tool calls — this is the final answer.
            response = _to_oai_response(chunks, model=model)
            response.setdefault("xijian", {})
            response["xijian"]["tools"] = {
                "enabled": True,
                "rounds": round_num + 1,
                "tool_calls": all_tool_calls_log,
            }
            return response

        # Execute each tool call.
        tool_messages: list[dict[str, Any]] = []
        for tc in tool_calls:
            name = tc.get("function", {}).get("name", "")
            args_str = tc.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}

            # A5-05: Audit the tool call regardless of execution path.
            try:
                safety_stub.audit_tool_call(
                    tool_name=name,
                    arguments=args_str,
                    character_id=(xijian or {}).get("character_id"),
                    world_id=world_id,
                )
            except Exception:
                pass  # audit must never crash the pipeline

            if name in mcp_names:
                # T0-1: chat → MCP safety gate.  Every MCP tool call
                # must pass ``mcp.check()`` **before** dispatch; a
                # verdict != "allowed" refuses execution (the audit
                # entry is written by check() itself).
                # T0-1: chat → MCP 安全闸。每个 MCP 工具调用在派发前
                # 必须通过 ``mcp.check()``；verdict != "allowed" 时
                # 拒绝执行（审计条目由 check() 自身写入）。
                gate = _mcp_gate_check(name, args, world_id=world_id)
                if gate is not None:
                    exec_result = {
                        "content": (
                            "工具调用被安全策略拒绝"
                            "（verdict=%s, blocked=%s）"
                            % (gate.get("verdict"), gate.get("blocked"))
                        ),
                        "isError": True,
                        "error_type": "gate_denied",
                        "gate": gate,
                    }
                    all_tool_calls_log.append({
                        "tool_call_id": tc.get("id"),
                        "name": name,
                        "arguments": args_str,
                        "result": exec_result["content"],
                        "is_error": True,
                        "error_type": "gate_denied",
                        "gate": gate,
                    })
                    tool_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": name,
                        "content": exec_result["content"],
                    })
                    continue
                exec_result = _execute_mcp_tool_call(
                    name, args, world_id=world_id,
                )
                all_tool_calls_log.append({
                    "tool_call_id": tc.get("id"),
                    "name": name,
                    "arguments": args_str,
                    "result": exec_result["content"],
                    "is_error": exec_result.get("isError", False),
                    "error_type": exec_result.get("error_type"),
                })
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": name,
                    "content": exec_result["content"],
                })
            else:
                # Non-MCP tool — can't execute server-side.  Return
                # an error message so the model knows.
                all_tool_calls_log.append({
                    "tool_call_id": tc.get("id"),
                    "name": name,
                    "arguments": args_str,
                    "result": "server cannot execute non-MCP tool",
                    "is_error": True,
                    "error_type": "non_mcp_tool",
                })
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": name,
                    "content": "错误：服务端无法执行此工具（非 MCP 注册工具）。",
                })

        # Append the assistant's tool_calls message + tool results.
        current_messages = [
            *current_messages,
            {
                "role": "assistant",
                "content": "".join(round_text_parts) or "",
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

    # Exhausted rounds — return what we have with a note.
    try:
        final_iter = backend.chat(
            _chat_messages_for_backend(current_messages),
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

    final_chunks = list(final_iter)
    response = _to_oai_response(final_chunks, model=model)
    response.setdefault("xijian", {})
    response["xijian"]["tools"] = {
        "enabled": True,
        "rounds": _MAX_TOOL_ROUNDS,
        "tool_calls": all_tool_calls_log,
        "truncated": True,
        "note": "tool call rounds exhausted; returning last response",
    }
    return response


def _inject_tools_system(
    messages: list[dict],
    oai_tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
) -> list[dict]:
    """Prepend/merge the tools system instruction.

    When ``oai_tools`` is provided, the tool descriptions (name +
    description + parameter schema) are embedded as text in the
    system prompt so any text-generation backend (mlx / gguf / mock)
    can surface them to the model.  The model is expected to emit
    ``tool_calls`` in the OAI streaming delta format when it decides
    to call a tool — the pipeline parses those via
    :func:`_extract_tool_calls`.

    This mirrors the recall pipeline's approach (system-prompt
    injection rather than backend kwargs) because the
    :class:`ChatBackend` interface is a low-level text-generation
    contract that does not accept ``tools`` / ``tool_choice``.
    镜像。

    """
    parts = [_TOOLS_SYSTEM_PROMPT]
    if tool_choice in ("required", "tool"):
        parts.append("\n**本次请求要求必须调用至少一个工具。**")
    elif isinstance(tool_choice, dict):
        fn = tool_choice.get("function") or {}
        forced_name = fn.get("name")
        if forced_name:
            parts.append(f"\n**本次请求要求必须调用工具 `{forced_name}`。**")
    if oai_tools:
        parts.append("\n## 可用工具列表：")
        for t in oai_tools:
            fn = t.get("function") or t
            name = fn.get("name", "")
            desc = fn.get("description", "")
            schema = fn.get("parameters", {})
            parts.append(
                f"\n### {name}\n{desc}\n"
                f"参数 schema: {json.dumps(schema, ensure_ascii=False)}"
            )
        parts.append(
            "\n调用工具时，请在回复中使用 OAI tool_calls 格式：包含 "
            '"tool_calls" 数组，每个调用含独立 "id" 和 "function" '
            '（"name" + "arguments" JSON 字符串）。不需要调用工具时正常文本回复即可。'
        )
    instruction = "\n".join(parts)
    out = list(messages)
    if not out or out[0].get("role") != "system":
        return [{"role": "system", "content": instruction}, *out]
    first = dict(out[0])
    first["content"] = (first.get("content") or "") + "\n\n" + instruction
    out[0] = first
    return out


# ---------------------------------------------------------------------------
# Public entry points — called by the chat route
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Character dialogue guard (A3.2) + dialogue memory write-back (A1.2)
# ---------------------------------------------------------------------------

#: Error code used when a character's state forbids dialogue.
#: 角色状态禁止对话时使用的错误码。
ERR_CHARACTER_CANNOT_DIALOGUE = "character_cannot_dialogue"


def _guard_character_dialogue(xijian: dict | None) -> None:
    """Block chat when the character's state forbids dialogue.

    A3.2 boundary case: 健康 ≤ 0 → 角色不可对话.  The state stub's
    :func:`can_dialogue` returns ``False`` only for the Critical
    status; the chat pipeline never checked it before, so a dying
    character could keep chatting.  This guard raises a 400 so the
    caller gets a clear error instead.
    """
    if not isinstance(xijian, dict):
        return
    character_id = xijian.get("character_id")
    if not character_id:
        return
    from xijian_api.stubs import character_state as cs_stub
    if not cs_stub.can_dialogue(character_id):
        raise ApiError(
            400,
            f"角色 {character_id} 当前状态不允许对话（健康值 ≤ 0 / Critical）",
            "invalid_request_error",
            code=ERR_CHARACTER_CANNOT_DIALOGUE,
            param="character_id",
        )


#: Public alias — used by the chat route so streaming requests get the
#: guard eagerly (before the stream response is constructed).
#: 公共别名 — 聊天路由使用，使流式请求在构造流响应之前即获得门控。
guard_character_dialogue = _guard_character_dialogue


def _record_dialogue_memory(
    xijian: dict | None,
    messages: list[dict],
    response: dict[str, Any],
) -> None:
    """Write a short-term dialogue memory entry after a conversation.

    A1.2 §关键流程 final steps:

    * S->>M: 写入新产生的短期记忆 (type=short, source=dialogue)
    * M->>M: 更新 access_count / last_access_at / decay_score

    Best-effort: never raises, so a memory-write failure can't break
    the chat response.
    """
    if not isinstance(xijian, dict):
        return
    character_id = xijian.get("character_id")
    if not character_id:
        return
    last_user_msg = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    response_text = ""
    for choice in (response.get("choices") or []):
        msg = choice.get("message") or {}
        response_text = (msg.get("content") or "")
    if not response_text:
        return
    try:
        cited = list((response.get("xijian") or {}).get("recall", {}).get("citations") or [])
        memory_stub.record_dialogue(
            character_id,
            _content_to_text(last_user_msg),
            response_text,
        )
        if cited:
            memory_stub.bump_access(cited)
    except Exception:  # noqa: BLE001 — best effort, never raise
        pass


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
    tools: list | None = None,
    tool_choice: Any = None,
) -> dict[str, Any]:
    """Return a non-streaming OAI chat completion payload via the backend.

    When ``xijian`` carries ``character_id`` and ``recall.enabled``,
    the forced-recall pipeline (A1.2) intercepts the call: it injects
    the recall system instruction, attaches the ``recall_memory`` tool,
    executes any tool calls against the memory store, and audits the
    final response for citation faithfulness.

    When ``xijian.tools.enabled`` is true or the OAI ``tools`` field
    is provided, the MCP tools pipeline (A2) intercepts the call
    instead: it injects the MCP tool descriptions, lets the model
    decide which tools to call, executes them through the A5.2 gate,
    and feeds the results back for a final answer.

    A3.2 guard: when ``xijian.character_id`` is set and the character's
    state forbids dialogue (health ≤ 0 / Critical), a 400 is raised
    before any backend call.
    """
    _ = user  # accepted for OAI parity; backends consume the rest
    _guard_character_dialogue(xijian)
    n = max(1, int(n or 1))
    backend = _resolve_backend_for(model)
    params = GenerationParams(
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        stop=stop,
        n=n,
    )

    # MCP tools pipeline (A2) — checked before recall so that
    # xijian.tools.enabled takes precedence.  The tools pipeline
    # includes memory_recall as one of the MCP tools, so it
    # subsumes the recall pipeline when both are requested.
    if _should_enable_tools(xijian, tools):
        response = _run_tools_pipeline(
            backend,
            messages,
            model=model,
            xijian=xijian,
            user_tools=tools,
            tool_choice=tool_choice,
            params=params,
        )
        _record_dialogue_memory(xijian, messages, response)
        return response

    if _should_enable_recall(xijian):
        # Inject character context (A2-01) before the recall pipeline.
        character_id = (xijian or {}).get("character_id")
        if character_id:
            messages = _inject_character_context(messages, character_id)
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
        response = last_response or {"id": gen_chat_id(), "object": "chat.completion", "choices": []}
        # A5-03 safety guard: scan output on the response.
        last_user_msg = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        if last_user_msg:
            safety_stub.scan_input(
                text=last_user_msg,
                character_id=character_id,
            )
        response_text = ""
        for choice in (response.get("choices") or []):
            msg = choice.get("message") or {}
            response_text = (msg.get("content") or "")
        if response_text:
            scan_result = safety_stub.scan_output(
                text=response_text,
                character_id=character_id,
            )
            if scan_result.get("verdict") in ("block", "hard_block"):
                blocked = {
                    "role": "assistant",
                    "content": "I'm sorry, but my response has been blocked by the safety system.",
                }
                choices = response.get("choices", [])
                for choice in choices:
                    choice["message"] = blocked
        _record_dialogue_memory(xijian, messages, response)
        return response

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

    response = _to_oai_response(result, model=model)
    # A5-03 safety guard on the fallback path.
    char_id = (xijian or {}).get("character_id")
    last_user_msg = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    if last_user_msg:
        safety_stub.scan_input(
            text=last_user_msg,
            character_id=char_id,
        )
    response_text = ""
    for choice in (response.get("choices") or []):
        msg = choice.get("message") or {}
        response_text = (msg.get("content") or "")
    if response_text:
        scan_result = safety_stub.scan_output(
            text=response_text,
            character_id=char_id,
        )
        if scan_result.get("verdict") in ("block", "hard_block"):
            blocked = {
                "role": "assistant",
                "content": "I'm sorry, but my response has been blocked by the safety system.",
            }
            choices = response.get("choices", [])
            for choice in choices:
                choice["message"] = blocked
    _record_dialogue_memory(xijian, messages, response)
    return response


def _response_to_stream(
    response: dict[str, Any],
    *,
    model: str,
    include_usage: bool,
) -> Iterator[dict[str, Any]]:
    """Convert a non-streaming OAI completion into streaming chunks.

    The recall / tools pipelines require multiple round-trips (tool
    detection → execution → final answer), so they are inherently
    non-streaming.  This helper re-emits their final response as a
    sequence of OAI ``chat.completion.chunk`` dicts so the streaming
    route can consume them transparently.
    """
    completion_id = response.get("id") or gen_chat_id()
    created = response.get("created") or 0
    choices = response.get("choices") or []
    for choice in choices:
        idx = choice.get("index", 0)
        message = choice.get("message") or {}
        content = message.get("content") or ""
        # First chunk: announce role + deliver content.
        yield {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": idx,
                    "delta": {"role": "assistant", "content": content},
                    "finish_reason": None,
                }
            ],
        }
        # Trailing chunk: finish_reason only.
        yield {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": idx,
                    "delta": {},
                    "finish_reason": choice.get("finish_reason") or "stop",
                }
            ],
        }
    if include_usage:
        usage = response.get("usage") or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        yield {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [],
            "usage": usage,
        }


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
    xijian: dict | None = None,
    tools: list | None = None,
    tool_choice: Any = None,
) -> Iterator[dict[str, Any]]:
    """Yield OAI streaming chunks via the backend.

    The backend yields :class:`ChatChunk` instances; this function
    serialises them into OAI ``chat.completion.chunk`` JSON.  The
    ``signal`` is forwarded so client cancels abort generation.

    When ``xijian`` carries ``character_id`` and ``recall.enabled``,
    the forced-recall pipeline (A1.2) runs first (non-streaming:
    memory load → tool detection → execution → citation audit),
    then the final response is re-emitted as streaming chunks.
    When ``xijian.tools.enabled`` is true or the OAI ``tools`` field
    is provided, the MCP tools pipeline (A2) runs first instead.

    A3.2 guard: like :func:`complete`, a character whose state forbids
    dialogue raises a 400 before any streaming starts.
    """
    _guard_character_dialogue(xijian)
    # A1.2 recall / A2 tools pipelines — multiple round-trips, so
    # run non-streaming via complete() and re-emit as chunks.
    if _should_enable_tools(xijian, tools) or _should_enable_recall(xijian):
        response = complete(
            messages,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
            xijian=xijian,
            tools=tools,
            tool_choice=tool_choice,
        )
        yield from _response_to_stream(
            response, model=model, include_usage=include_usage
        )
        return

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