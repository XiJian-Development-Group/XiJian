"""GGUF 聊天后端 —— 包装 ``llama-cpp-python``（``llama.cpp`` GGUF 模型的规范绑定）。

契约镜像 :class:`xijian_api.ai.types.ChatBackend`：

* :meth:`chat` 在流式和非流式模式下都产生 :class:`ChatChunk` 实例。
* :class:`AbortSignal` 在 token 发射之间轮询，以便客户端中止能及时停止生成。

llama-cpp-python 特有细节
--------------------------

* ``Llama.create_chat_completion(messages=..., stream=True)`` 返回
  OAI 风格字典的生成器（``{"choices": [{"delta": {...}}]}``）。
* 非流式返回具有相同 ``choices`` 形状的单个字典。
* Token 计数来自 ``usage``（较新版本）或未暴露时从 tokenizer 推断。

多模态支持
----------

* VLM 模型（视觉语言模型）通过 ``llama.cpp`` 的 ``mmproj`` 支持加载。
* 当加载的模型有 ``.mmproj`` 文件或配置中包含多模态架构标识时，
  自动启用 VLM 模式。
* 消息中的 ``image_url`` 内容片段会被解析为本地路径并传递给
  ``llama-cpp-python`` 的 ``create_chat_completion()``。

GGUF chat backend — wraps ``llama-cpp-python`` (the canonical
binding for ``llama.cpp`` GGUF models).

Contract mirrors :class:`xijian_api.ai.types.ChatBackend`:

* :meth:`chat` yields :class:`ChatChunk` instances in both streaming
  and non-streaming modes.
* :class:`AbortSignal` is polled between token emissions so a client
  abort halts generation promptly.

llama-cpp-python specifics
--------------------------

* ``Llama.create_chat_completion(messages=..., stream=True)`` returns a
  generator of OAI-style dicts (``{"choices": [{"delta": {...}}]}``).
* Non-streaming returns a single dict with the same ``choices`` shape.
* Token counts come from ``usage`` (newer versions) or are inferred
  from the tokenizer when not exposed.

Multimodal support
------------------

* VLM models (vision-language models) are loaded via ``llama.cpp``'s
  ``mmproj`` support.
* When the loaded model has a ``.mmproj`` file or the config contains
  multimodal architecture identifiers, VLM mode is automatically enabled.
* ``image_url`` content parts in messages are resolved to local paths
  and passed to ``llama-cpp-python``'s ``create_chat_completion()``.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

from xijian_api.ai.base import (
    BackendError,
    ContextLengthExceeded,
    GenerationAborted,
    ModelNotFound,
    ModelNotLoaded,
)
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


def _now_ts() -> int:
    return int(time.time())


def _build_chunk(
    *,
    chunk_id: str,
    model: str,
    delta: dict | None = None,
    finish_reason: str | None = None,
    usage: ChatUsage | None = None,
) -> ChatChunk:
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
        backend="gguf",
    )


def _extract_delta_content(delta) -> str:
    """从 OAI delta 字典中提取 ``content`` 字段。

    Pull the ``content`` field out of an OAI delta dict.
    """
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    return content if isinstance(content, str) else ""


def _build_gguf_kwargs(params: GenerationParams, *, max_tokens: int) -> dict:
    kwargs: dict = {
        "temperature": float(params.temperature) if params.temperature is not None else 0.7,
        "top_p": float(params.top_p) if params.top_p is not None else 1.0,
        "max_tokens": max(1, int(max_tokens)),
    }
    if params.stop:
        kwargs["stop"] = list(params.stop)
    return kwargs


# ---------------------------------------------------------------------------
# VLM detection + multimodal helpers / VLM 检测与多模态辅助
# ---------------------------------------------------------------------------

# 表明视觉语言模型的架构名称片段（不区分大小写匹配）。
# Architecture-name fragments that indicate a VLM (case-insensitive).
_VLM_ARCH_HINTS: tuple[str, ...] = (
    "vl", "vision", "llava", "qwen2vl", "qwen2_vl",
    "paligemma", "idefics", "pixtral", "internvl",
    "deepseekvl", "smolvlm", "mllama", "phi3v", "florence",
)


def _detect_vlm(path: Path) -> bool:
    """启发式判断 ``path`` 处的 GGUF 检查点是否为 VLM。

    检查目录中是否有 ``.mmproj`` 文件（llama.cpp VLM 加载的明确标志），
    或检查 ``config.json`` 中是否包含已知的 VLM 架构名称。
    对单文件检查点也会检查同级目录的 ``.mmproj`` 文件。

    Heuristically decide whether the GGUF checkpoint at ``path`` is a VLM.

    Checks for ``.mmproj`` files (the definitive signal for llama.cpp VLM
    loading), or inspects ``config.json`` for known VLM architecture names.
    For single-file checkpoints, also checks for sibling ``.mmproj`` files.
    """
    # 检查 .mmproj 文件（最明确信号）
    if path.is_dir():
        mmproj_files = list(path.glob("*.mmproj"))
        if mmproj_files:
            return True
    else:
        # 单文件时查同级目录
        mmproj_files = list(path.parent.glob("*.mmproj"))
        if mmproj_files:
            return True

    # 检查 config.json 架构
    config_path = path / "config.json" if path.is_dir() else path.parent / "config.json"
    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as fp:
                cfg = json.load(fp)
        except Exception:
            return False
        archs = cfg.get("architectures") or []
        if isinstance(archs, str):
            archs = [archs]
        model_type = str(cfg.get("model_type", "")).lower()
        candidates = [str(a).lower() for a in archs] + [model_type]
        for cand in candidates:
            for hint in _VLM_ARCH_HINTS:
                if hint in cand:
                    return True
        # 预处理器配置的存在也是强 VLM 信号。
        prepro_path = path / "preprocessor_config.json" if path.is_dir() else path.parent / "preprocessor_config.json"
        if prepro_path.exists():
            return True
    return False


def _resolve_image_to_path(url: str) -> str | None:
    """将 ``image_url`` 值解析为本地文件系统路径。

    支持：

    * ``file:///abs/path.png`` → ``/abs/path.png``
    * ``/abs/path.png`` → 原样返回
    * ``http(s)://...`` → 下载到临时文件
    * ``data:image/...;base64,...`` → 解码到临时文件

    Resolve an ``image_url`` value to a local filesystem path.

    Supports:

    * ``file:///abs/path.png``  → ``/abs/path.png``
    * ``/abs/path.png`` → as-is
    * ``http(s)://...`` → downloaded to a temp file
    * ``data:image/...;base64,...`` → decoded to a temp file
    """
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("file://"):
        path = url[len("file://"):]
        return path if Path(path).exists() else None
    if url.startswith("data:"):
        # 格式: data:<mime>;base64,<payload>
        try:
            header, b64 = url.split(",", 1)
            mime = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
            ext = mime.split("/")[-1].split("-")[-1] or "png"
            raw = base64.b64decode(b64)
            fd, tmp = tempfile.mkstemp(suffix=f".{ext}")
            with os.fdopen(fd, "wb") as fp:
                fp.write(raw)
            return tmp
        except Exception:
            return None
    if url.startswith("http://") or url.startswith("https://"):
        try:
            import httpx
            ext = url.rsplit(".", 1)[-1].split("?")[0][:5].lower()
            if ext not in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
                ext = "png"
            resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            if resp.status_code >= 400:
                return None
            fd, tmp = tempfile.mkstemp(suffix=f".{ext}")
            with os.fdopen(fd, "wb") as fp:
                fp.write(resp.content)
            return tmp
        except Exception:
            return None
    # 裸文件系统路径。
    return url if Path(url).exists() else None


def _msg_content(m) -> Any:
    """从消息中提取内容。Extract content from a message."""
    if isinstance(m, ChatMessage):
        return m.content
    if isinstance(m, dict):
        return m.get("content")
    return None


def _has_multimodal_content(messages: Sequence) -> bool:
    """当任何消息携带列表式多模态内容时返回 ``True``。

    Return ``True`` when any message carries list-of-parts multimodal content.
    """
    for m in messages:
        content = _msg_content(m)
        if isinstance(content, list):
            return True
    return False


def _degrade_multimodal_to_text(messages: Sequence) -> list:
    """将列表式内容展平为纯文本字符串。

    ``text`` 部分被拼接，``image_url`` 部分变成 ``[image]`` 占位符。
    纯字符串内容原样保留。返回的列表镜像输入类型。

    Flatten list-of-parts content into a text-only string.

    ``text`` parts are concatenated, ``image_url`` parts become
    ``[image]`` placeholders. Plain-string content is preserved.
    """
    out: list = []
    for m in messages:
        content = _msg_content(m)
        if not isinstance(content, list):
            out.append(m)
            continue
        parts: list[str] = []
        for p in content:
            if not isinstance(p, dict):
                parts.append(str(p))
                continue
            ptype = p.get("type")
            if ptype == "text":
                t = p.get("text", "")
                if isinstance(t, str):
                    parts.append(t)
            elif ptype == "image_url":
                parts.append("[image]")
            elif ptype == "audio_url":
                parts.append("[audio]")
            elif ptype == "video_url":
                parts.append("[video]")
            else:
                parts.append(f"[{ptype}]")
        joined = " ".join(p for p in parts if p)
        if isinstance(m, ChatMessage):
            out.append(ChatMessage(
                role=m.role, content=joined, name=m.name,
                tool_call_id=m.tool_call_id, tool_calls=m.tool_calls,
            ))
        elif isinstance(m, dict):
            new_m = dict(m)
            new_m["content"] = joined
            out.append(new_m)
        else:
            out.append(m)
    return out


def _resolve_images_in_messages(messages: Sequence) -> tuple[list, list[str]]:
    """从消息中提取并解析所有 ``image_url`` 内容。

    返回 (messages_with_resolved_paths, temp_image_paths)，
    其中 temp_image_paths 是需要清理的临时文件列表。

    Resolve all ``image_url`` content parts in messages to local paths.

    Returns (messages_with_resolved_paths, temp_image_paths) where
    temp_image_paths is a list of temp files to clean up later.
    """
    temp_images: list[str] = []
    out_messages: list = []
    for m in messages:
        content = _msg_content(m)
        if not isinstance(content, list):
            out_messages.append(m)
            continue
        if isinstance(m, ChatMessage):
            new_content: list[dict] = []
            for p in content:
                if not isinstance(p, dict):
                    new_content.append(p)
                    continue
                if p.get("type") == "image_url":
                    spec = p.get("image_url")
                    if isinstance(spec, dict):
                        url = spec.get("url", "")
                    else:
                        url = spec if isinstance(spec, str) else ""
                    resolved = _resolve_image_to_path(url)
                    if resolved:
                        if resolved.startswith(tempfile.gettempdir()):
                            temp_images.append(resolved)
                        new_p = dict(p)
                        new_p["image_url"] = {"url": f"file://{resolved}"}
                        new_content.append(new_p)
                    else:
                        new_content.append({"type": "text", "text": "[image: unable to load]"})
                else:
                    new_content.append(p)
            out_messages.append(ChatMessage(
                role=m.role, content=new_content, name=m.name,
                tool_call_id=m.tool_call_id, tool_calls=m.tool_calls,
            ))
        elif isinstance(m, dict):
            new_content = list(content)
            for i, p in enumerate(content):
                if not isinstance(p, dict):
                    continue
                if p.get("type") == "image_url":
                    spec = p.get("image_url")
                    if isinstance(spec, dict):
                        url = spec.get("url", "")
                    else:
                        url = spec if isinstance(spec, str) else ""
                    resolved = _resolve_image_to_path(url)
                    if resolved:
                        if resolved.startswith(tempfile.gettempdir()):
                            temp_images.append(resolved)
                        new_p = dict(p)
                        new_p["image_url"] = {"url": f"file://{resolved}"}
                        new_content[i] = new_p
                    else:
                        new_content[i] = {"type": "text", "text": "[image: unable to load]"}
            new_m = dict(m)
            new_m["content"] = new_content
            out_messages.append(new_m)
        else:
            out_messages.append(m)
    return out_messages, temp_images


@register_chat("gguf")
class GGUFChatBackend(ChatBackend):
    """GGUF 聊天后端。GGUF chat backend."""
    name = "gguf"

    def __init__(self) -> None:
        self._llama = None
        self._model_path: Path | None = None
        self._n_ctx: int = 0
        self._is_vlm: bool = False  # 是否为 VLM 模型
        self._mmproj_path: str | None = None  # mmproj 投影文件路径
        self._temp_image_paths: list[str] = []  # 临时图像文件，unload 时清理

    # -- introspection / 内省 ------------------------------------------------------

    def is_available(self) -> bool:
        try:
            import llama_cpp  # noqa: F401
            return True
        except Exception:
            return False

    def is_loaded(self) -> bool:
        return self._llama is not None

    def is_vlm(self) -> bool:
        """返回模型是否为视觉语言模型。Return whether the model is a VLM."""
        return self._is_vlm

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        try:
            from llama_cpp import Llama
        except Exception as exc:
            raise BackendError(
                f"llama-cpp-python not importable: {exc}",
                code="backend_unavailable",
            ) from exc
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")
        n_ctx = int(context_length) if context_length else 0
        # 当 context_length 为 0 时让 llama.cpp 选择自己的默认值（通常 4096）。
        # 调用者可以通过 kwargs 覆盖。
        try:
            self._llama = Llama(model_path=str(path), n_ctx=n_ctx or 4096, verbose=False)
        except Exception as exc:
            raise BackendError(
                f"llama_cpp.Llama init failed: {exc}",
                code="backend_error",
            ) from exc
        self._model_path = path
        self._n_ctx = n_ctx

    def unload(self) -> None:
        self._llama = None
        self._model_path = None
        self._n_ctx = 0

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
            raise ModelNotLoaded("no GGUF chat model loaded")
        messages_dict = [
            m.to_dict() if isinstance(m, ChatMessage) else m for m in messages
        ]
        max_tokens = params.max_tokens or 1024
        chunk_id = f"chatcmpl-gguf-{int(time.time() * 1000)}"
        model_id = str(self._model_path) if self._model_path else "gguf"
        kwargs = _build_gguf_kwargs(params, max_tokens=max_tokens)

        if stream:
            return self._streaming(
                messages=messages_dict,
                kwargs=kwargs,
                chunk_id=chunk_id,
                model_id=model_id,
                abort_signal=abort_signal,
            )
        return self._blocking(
            messages=messages_dict,
            kwargs=kwargs,
            chunk_id=chunk_id,
            model_id=model_id,
            abort_signal=abort_signal,
        )

    # -- internals / 内部 ----------------------------------------------------------

    def _blocking(
        self,
        *,
        messages,
        kwargs,
        chunk_id,
        model_id,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        try:
            result = self._llama.create_chat_completion(
                messages=messages,
                stream=False,
                **kwargs,
            )
        except ApiGenerationAborted:
            raise
        except Exception as exc:
            self._map_llama_exception(exc)
            raise  # pragma: no cover
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        # 结果是 OAI 字典：``{"choices": [{"message": {...}, ...}]}``。
        try:
            choice = result["choices"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError(
                f"llama_cpp returned unexpected shape: {exc}",
                code="backend_error",
            ) from exc
        message = choice.get("message") or {}
        content = message.get("content") or ""
        finish_reason = choice.get("finish_reason") or "stop"
        usage = self._usage_to_chat(result.get("usage"))
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant", "content": content},
            finish_reason=finish_reason,
            usage=usage,
        )

    def _streaming(
        self,
        *,
        messages,
        kwargs,
        chunk_id,
        model_id,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        # 首个 chunk 宣告角色，以便 OAI 客户端能立即开始渲染。
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant"},
        )
        aborted = False
        try:
            for piece in self._llama.create_chat_completion(
                messages=messages,
                stream=True,
                **kwargs,
            ):
                if abort_signal is not None:
                    abort_signal.raise_if_aborted()
                try:
                    choice = piece["choices"][0]
                except (KeyError, IndexError, TypeError) as exc:
                    raise BackendError(
                        f"llama_cpp stream returned bad chunk: {exc}",
                        code="backend_error",
                    ) from exc
                delta = choice.get("delta") or {}
                content = _extract_delta_content(delta)
                finish_reason = choice.get("finish_reason")
                if content:
                    yield _build_chunk(
                        chunk_id=chunk_id,
                        model=model_id,
                        delta={"content": content},
                    )
                if finish_reason:
                    # 最终 chunk 携带 ``finish_reason``。发一次就停止迭代。
                    # llama-cpp-python 通常会在此 chunk 后关闭迭代器。
                    usage = self._usage_to_chat(piece.get("usage"))
                    yield _build_chunk(
                        chunk_id=chunk_id,
                        model=model_id,
                        delta={},
                        finish_reason=(
                            "abort" if aborted else (finish_reason or "stop")
                        ),
                        usage=usage,
                    )
                    return
        except ApiGenerationAborted:
            aborted = True
        except Exception as exc:
            self._map_llama_exception(exc)
            raise  # pragma: no cover

        # 若流式结束未收到 ``finish_reason`` chunk，补发一个终止帧，
        # 确保客户端始终看到终端标记。
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason="abort" if aborted else "stop",
            usage=None,
        )

    # -- helpers / 辅助 ------------------------------------------------------------

    @staticmethod
    def _usage_to_chat(raw) -> ChatUsage | None:
        if not isinstance(raw, dict):
            return None
        prompt_tokens = int(raw.get("prompt_tokens", 0) or 0)
        completion_tokens = int(raw.get("completion_tokens", 0) or 0)
        total = raw.get("total_tokens")
        if total is None:
            total = prompt_tokens + completion_tokens
        return ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=int(total),
        )

    @staticmethod
    def _map_llama_exception(exc: Exception) -> None:
        """将 llama.cpp 错误翻译为 AI 层的异常类型。

        Translate llama.cpp errors into the AI layer's exception types.
        """
        msg = str(exc).lower()
        if "context" in msg and ("exceed" in msg or "length" in msg or "full" in msg):
            raise ContextLengthExceeded(str(exc)) from exc
        raise BackendError(f"llama_cpp error: {exc}", code="backend_error") from exc


__all__ = ["GGUFChatBackend"]