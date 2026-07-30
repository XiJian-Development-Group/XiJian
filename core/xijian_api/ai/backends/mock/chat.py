"""模拟聊天后端 —— 测试套件和本地开发用。

目标
-----

* 始终 ``is_available()``，以便 ``tests`` 和 CI 可以在没有安装
  ``mlx``/``llama_cpp`` 且磁盘上没有真正检查点的情况下运行。
* 在 :meth:`load` 中接受任何 ``model_path``（路径会被记录但从不打开）。
  注册表的 ``_resolve_backend_class`` 会将相同路径传递给
  :meth:`ModelEntry.absolute_path`，否则当目录不存在时会失败。
* 输出确定的 token 序列，以便测试可以断言输出形状，而无需依赖权重、
  prompt 格式或平台后端。
* 支持最小的 ``tool_call`` 流，使 A1.2 强制召回流水线可以在没有真实
  模型的情况下端到端运行。当 prompt 包含引用了 ``recall_memory`` 工具
  （流水线注入的）的 system instruction 时，模拟器在第一轮发出
  ``recall_memory`` 工具调用；在第二轮将工具结果回显为最终答案。

契约镜像 :class:`xijian_api.ai.types.ChatBackend`：

* :meth:`chat` 在阻塞（``stream=False``）和流式（``stream=True``）
  模式下都返回 :class:`ChatChunk` 实例的*可迭代对象*。
* 当提供 :class:`AbortSignal` 时，在每次发射之间轮询，以便客户端
  ``POST .../abort`` 干净地停止模拟器。最终 chunk 的
  ``finish_reason`` 在这种情况下为 ``"abort"``。

Mock chat backend — used by the test suite and local development.

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


# 当调用者未传递 ``max_tokens`` 时的默认 token 预算。
# 保持较小以免意外调用浪费周期，但足够大以演练流式。
_DEFAULT_MAX_TOKENS = 64

# 模拟 token 序列。每个条目是*新*的后缀用于追加，拼接起来就重现
# 了规范的模拟文本。需要已知输出的测试可以直接按原样 join 此列表。
_MOCK_TOKENS: tuple[str, ...] = (
    "Mock", " response", " from", " the", " mock", " chat", " backend",
    ".", " This", " backend", " is", " intended", " for", " tests", " and",
    " local", " development", " only", ".", " It", " does", " not", " load",
    " any", " real", " model", " weights", ".",
)


def _now_ts() -> int:
    return int(time.time())


def _resolve_max_tokens(params: GenerationParams) -> int:
    """解析 ``max_tokens``，``None``/0 时使用默认预算。

    Resolve ``max_tokens`` honouring ``None`` / 0 as a default budget.
    """
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
    """从 OAI 风格的组件组装一个 :class:`ChatChunk`。

    Assemble a :class:`ChatChunk` from its OAI-style pieces.
    """
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


def _content_text(content) -> str:
    """从可能的多模态内容（str 或 list）中提取纯文本。

    Extract plain text from possibly-multimodal content (str or list).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                t = p.get("text", "")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return ""


def _msg_text(m) -> str:
    """从 ChatMessage 或 dict 中提取文本，处理多模态内容。

    Extract text from a ChatMessage or dict, handling multimodal content.
    """
    if isinstance(m, ChatMessage):
        return _content_text(m.content)
    if isinstance(m, dict):
        return _content_text(m.get("content"))
    return ""


def _msg_role(m) -> str:
    if isinstance(m, ChatMessage):
        return m.role
    if isinstance(m, dict):
        return str(m.get("role", ""))
    return ""


def _last_user_text(messages: Sequence) -> str:
    """返回最近一条用户消息的文本。没有则返回空字符串。

    Return the most recent user message's text.  Empty string if none.
    """
    for m in reversed(messages):
        if _msg_role(m) == "user":
            return _msg_text(m)
    return ""


def _system_has_recall_instruction(messages: Sequence) -> bool:
    """当 system 消息提及 recall_memory 工具时返回 True。

    True when the system message mentions the recall_memory tool.
    """
    needle = "recall_memory"
    for m in messages:
        if _msg_role(m) == "system" and needle in _msg_text(m):
            return True
    return False


def _latest_tool_result(messages: Sequence) -> dict | None:
    """返回最近一条 ``role=tool`` 消息的解析 JSON 内容。

    Return the most recent ``role=tool`` message's parsed JSON content.
    """
    for m in reversed(messages):
        if _msg_role(m) != "tool":
            continue
        content = _msg_text(m)
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


#: MCP 工具流水线（A2）在 system prompt 中注入的标记。
#: 镜像 chat_stub.py 中 ``_TOOLS_SYSTEM_PROMPT`` 的第一行。
_MCP_TOOLS_MARKER = "你可以使用以下工具来完成用户的请求"


def _system_has_mcp_tools_instruction(messages: Sequence) -> bool:
    """当 system 消息包含 MCP 工具指令时返回 True。

    True when the system message contains the MCP tools instruction.
    """
    for m in messages:
        if _msg_role(m) == "system" and _MCP_TOOLS_MARKER in _msg_text(m):
            return True
    return False


def _extract_tool_names_from_system(messages: Sequence) -> list[str]:
    """从工具 system prompt 中的 ``### name`` 头解析工具名称。

    Parse tool names from ``### name`` headers in the tools system prompt.
    """
    names: list[str] = []
    for m in messages:
        if _msg_role(m) != "system":
            continue
        content = _msg_text(m)
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("### "):
                name = line[4:].strip()
                if name:
                    names.append(name)
    return names


def _latest_tool_text(messages: Sequence) -> str | None:
    """返回最近一条 ``role=tool`` 消息的原始文本，或在没有时返回 ``None``。

    与 :func:`_latest_tool_result` 不同，此函数不尝试 JSON 解析 ——
    MCP 工具结果是纯字符串，因此这是 MCP 工具路径的正确辅助函数。

    Return the most recent ``role=tool`` message's raw text, or ``None``.

    Unlike :func:`_latest_tool_result` this does not attempt JSON
    parsing — MCP tool results are plain strings, so this is the
    right helper for the MCP tools path.
    """
    for m in reversed(messages):
        if _msg_role(m) == "tool":
            return _msg_text(m)
    return None


def _build_echo_prefix(messages: Sequence) -> str:
    """从最后一条用户消息返回短的 ``[echo: ...]`` 前缀。

    Return a short ``[echo: ...]`` prefix from the last user message.
    """
    text = _last_user_text(messages).strip()
    if not text:
        return ""
    snippet = text[:120]
    return f"[echo: {snippet}] "


@register_chat("mock")
class MockChatBackend(ChatBackend):
    """测试和本地开发用的确定性聊天后端。

    Deterministic chat backend for tests + local development.
    """

    name = "mock"

    def __init__(self) -> None:
        self._model_path: Path | None = None
        self._context_length: int = 0
        self._loaded: bool = False

    # -- introspection / 内省 ------------------------------------------------------

    def is_available(self) -> bool:
        # 始终可用 —— 这就是模拟器的全部意义。
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        """记录路径；从不接触文件系统。

        注册表通过 :meth:`ModelEntry.absolute_path` 解析路径并传入，
        但测试注册的模型文件并不存在。盲目接受路径使契约保持简单：
        模拟器不需要文件。额外的 kwargs（模型的 ``extra`` 块 + 调用者
        覆盖）被静默忽略 —— 模拟器没有可尊重的旋钮。

        Record the path; never touch the filesystem.

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

    # -- generation / 生成 ---------------------------------------------------------

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

        # MCP 工具路径（A2）：当流水线注入了 MCP 工具 system 指令时，
        # 模拟器模拟一个模型，它在第一轮调用第一个可用工具，然后在
        # 第二轮将工具结果回显为最终答案。这使工具流水线可以在没有
        # 真实模型的情况下端到端演练。
        if _system_has_mcp_tools_instruction(messages):
            available = _extract_tool_names_from_system(messages)
            tool_text = _latest_tool_text(messages)
            if tool_text is None and available:
                # 第一轮 —— 为第一个工具发出工具调用。
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
            # 第二轮（或无可用工具）—— 发出最终答案。
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

        # 强制召回路径（A1.2）：当流水线注入召回 system 指令时，
        # 模拟器表现得像一个忠实遵循它的真实模型 —— 第一轮发出
        # ``recall_memory`` 工具调用，第二轮（附加工具结果后）
        # 发出最终答案。
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

        # 预先构建完整内容；为流式切片。
        # echo 前缀反映最后一条用户消息，以便调用者可验证"请求确实到达了后端"。
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

    # -- recall-pipeline helpers / 召回流水线辅助 -----------------------------------------

    def _tool_call_turn(self, messages: Sequence, *, chunk_id: str) -> str:  # noqa: ARG002
        """在需要召回时返回助手的第一轮回复。

        模拟器实际上不*生成*召回工具调用 —— 它始终以用户最后一条消息
        作为查询调用 ``recall_memory``（镜像一个完全顺从的模型，
        当被要求时总是召回）。流水线在 :meth:`_blocking_tool_call` 中
        将其转换为 chunk 级 tool_call delta。

        Return the assistant's first-turn reply when recall is required.

        The mock doesn't actually *generate* a recall tool call — it
        always invokes ``recall_memory`` with the user's last message
        as the query (mirroring a perfectly obedient model that
        always recalls when asked).  The pipeline turns this into the
        chunk-level tool_call delta in :meth:`_blocking_tool_call`.
        """
        return ""

    def _final_turn(self, tool_result: dict, messages: Sequence) -> str:
        """使用工具的召回命中结果组成第二轮回复。

        回显召回条目，使回复文本基于真实记忆（AC-3）并避免幻觉（AC-4）。
        片段包括每条条目的内容和 id，以便引文审计能清晰匹配验证。

        Compose the second-turn reply using the tool's recall hits.

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
        # 从最新的用户消息拉取查询，使工具参数非空，
        # 流水线的召回搜索有东西可匹配。
        # 我们在这里没有对 messages 的访问（chat() 已消费它们），
        # 所以嵌入一个稳定的默认值 —— 流水线逐字读取参数并对任何查询
        # 运行召回，因此空字符串是安全的（无命中 → 无引文 → 审计判定 = 通过）。
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
        # Role chunk / 角色块。
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant"},
        )
        # Tool-call delta chunk / 工具调用增量块（将参数分两个 chunk 以演练流组装器）。
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
        # 最终 chunk。
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason="tool_calls",
            usage=ChatUsage(prompt_tokens=0, completion_tokens=1, total_tokens=1),
        )

    # -- mcp-tools-pipeline helpers / MCP 工具流水线辅助 ---------------------------------------

    def _mcp_final_turn(self, tool_text: str | None, messages: Sequence) -> str:
        """使用 MCP 工具的结果文本组成最终回复。

        回显工具结果的片段，以便测试可验证流水线正确传回了结果。
        当未调用工具（``tool_text`` 为 ``None``）时，模拟器发出
        一个简单的确认。

        Compose the final reply using the MCP tool's result text.

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
        # 空参数字典 —— 流水线执行工具，注册表应用逐工具默认值。
        # 大多数 MCP 工具接受空字典并返回合理的默认值（例如 list_all）。
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
        # Role chunk / 角色块。
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant"},
        )
        # Tool-call delta chunk / 工具调用增量块。
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
        # 最终 chunk。
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason="tool_calls",
            usage=ChatUsage(prompt_tokens=0, completion_tokens=1, total_tokens=1),
        )

    # -- internals / 内部 ----------------------------------------------------------

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
        # 模拟器没有 tokenizer；将单词数报告为替代品，
        # 使显示 token 计数器的调用者能获得非零值。
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

        # 首个 chunk：仅角色 —— OAI 惯例。
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant"},
        )

        # 逐字符发射：小到在测试中看起来像真实流，确定性，不值得批处理。
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

        # 最终 chunk：finish_reason + usage。
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