"""MLX 聊天后端。

MLX chat backend.

将 ``mlx_lm.generate`` / ``mlx_lm.stream_generate`` 包装在
:class:`ChatBackend` 契约之后。当安装了 ``mlx_vlm`` 且加载的检查点是
视觉语言模型（VLM）时，后端透明地分派到 ``mlx_vlm`` 以支持多模态
内容（``image_url`` 片段）。

契约
--------

* :meth:`chat` 在阻塞（``stream=False``）和流式（``stream=True``）
  模式下都返回 :class:`ChatChunk` 实例的*可迭代对象*。
  路由层（``stubs/chat.py``）将每个 chunk 翻译为 OAI
  ``chat.completion.chunk`` 载荷，对非流式调用则将迭代器折叠为
  单个 OAI ``chat.completion``。

* :class:`AbortSignal`（当提供时）在每次 token 发射之间轮询，
  以便客户端 ``POST .../abort`` 能及时停止生成。我们将中止翻译为
  :class:`GenerationAborted`（匹配 :mod:`xijian_api.errors`）。

* 实现跨最近的 ``mlx_lm`` 版本具有防御性：参数名称已变化
  （``temp`` → ``temperature``，``max_tokens`` 稳定，``top_p`` 接受
  ``0.0`` 禁用）。我们先尝试新名称，再回退到旧名称，
  以支持更广泛的 ``mlx_lm`` 版本。

多模态处理
-------------------

* VLM 检查点（通过 ``config.json`` 架构或图像处理器存在检测）
  通过 ``mlx_vlm.load`` 加载，并通过 ``mlx_vlm.generate`` /
  ``mlx_vlm.stream_generate`` 生成。消息内容中的 ``image_url``
  片段被解析为本地文件路径（``file://``、``http(s)://`` 下载到
  临时文件、``data:image/...;base64,...`` 解码）并传递给 ``mlx_vlm``。

* 纯文本检查点（常见情况）继续使用 ``mlx_lm``。
  当纯文本模型收到多模态内容时，图像部分被替换为 ``[image]``
  占位符，仅文本部分被转发 —— 这匹配项目的 A2 接受标准
  （"模型不支持该模态 → 降级为占位符描述并记录失败"）。

Wraps ``mlx_lm.generate`` / ``mlx_lm.stream_generate`` behind the
:class:`ChatBackend` contract.  When ``mlx_vlm`` is installed and the
loaded checkpoint is a vision-language model (VLM), the backend
transparently dispatches to ``mlx_vlm`` so multimodal content
(``image_url`` parts) is honoured.

Contract
--------

* :meth:`chat` returns an *iterable* of :class:`ChatChunk` instances
  in both blocking (``stream=False``) and streaming (``stream=True``)
  modes.  The route layer (``stubs/chat.py``) translates each chunk
  into an OAI ``chat.completion.chunk`` payload and, for non-streaming
  calls, collapses the iterable into a single OAI ``chat.completion``.

* :class:`AbortSignal` (when supplied) is polled between token
  emissions so a client-side ``POST .../abort`` halts generation
  promptly.  We translate the abort into :class:`GenerationAborted`
  (matching :mod:`xijian_api.errors`).

* The implementation is defensive across recent ``mlx_lm`` versions:
  the parameter names changed (``temp`` → ``temperature``,
  ``max_tokens`` is stable, ``top_p`` accepts ``0.0`` to disable).
  We try the new names first and fall back to the older ones so a
  broader range of ``mlx_lm`` versions just works.

Multimodal handling
-------------------

* VLM checkpoints (detected via ``config.json`` architectures or the
  presence of an image processor) are loaded through ``mlx_vlm.load``
  and generate via ``mlx_vlm.generate`` / ``mlx_vlm.stream_generate``.
  ``image_url`` parts in the message content are resolved to local
  file paths (``file://``, ``http(s)://`` downloaded to a temp file,
  ``data:image/...;base64,...`` decoded) and passed to ``mlx_vlm``.

* Text-only checkpoints (the common case) keep using ``mlx_lm``.
  When a text-only model receives multimodal content the image parts
  are replaced with ``[image]`` placeholders and only the text parts
  are forwarded — this matches the project's A2 acceptance criterion
  ("model doesn't support the modality → degrade to placeholder
  description and log failure").
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any, Iterator, Sequence

from xijian_api.ai.base import (
    BackendError,
    GenerationAborted,
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


# 当 ``params.max_tokens`` 为 ``None`` 时的默认 token 预算。
# 保持适中，以免意外调用长时间运行。
# Token-budget default when ``params.max_tokens`` is ``None``.  Keep
# modest so an accidental call doesn't run away for minutes.
_DEFAULT_MAX_TOKENS = 1024

# 表明视觉语言模型的架构名称片段。
# 与 ``config.json`` 的 ``architectures`` 列表（以及后备的
# ``model_type`` 字段）进行不区分大小写的匹配。
# Architecture-name fragments that indicate a vision-language model.
# Matched case-insensitively against ``config.json``'s
# ``architectures`` list (and the ``model_type`` field as a fallback).
_VLM_ARCH_HINTS: tuple[str, ...] = (
    "vl", "vision", "llava", "image", "visual", "qwen2vl",
    "qwen2_vl", "paligemma", "idefics", "pixtral", "florence",
    "internvl", "deepseekvl", "smolvlm", "mllama",
)


def _now_ts() -> int:
    return int(time.time())


def _try_mlx_vlm_available() -> bool:
    """``mlx_vlm`` 可导入时返回 ``True``。

    Return ``True`` when ``mlx_vlm`` imports cleanly.
    """
    try:
        import mlx_vlm  # noqa: F401
        return True
    except Exception:
        return False


def _try_mlx_lm_available() -> bool:
    """``mlx_lm`` 可导入时返回 ``True``。

    Return ``True`` when ``mlx_lm`` imports cleanly.
    """
    try:
        import mlx.core  # noqa: F401
        import mlx_lm  # noqa: F401
        return True
    except Exception:
        return False


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
    choices = [
        ChatChoice(
            index=0,
            delta=delta if delta is not None else {},
            finish_reason=finish_reason,
        )
    ]
    return ChatChunk(
        id=chunk_id,
        model=model,
        created=_now_ts(),
        choices=choices,
        usage=usage,
        backend="mlx",
    )


def _resolve_max_tokens(params: GenerationParams) -> int:
    """返回 ``max_tokens``，``None`` 时使用 ``_DEFAULT_MAX_TOKENS``。

    Return ``max_tokens`` honouring ``None`` as ``_DEFAULT_MAX_TOKENS``.
    """
    if params.max_tokens is None or params.max_tokens <= 0:
        return _DEFAULT_MAX_TOKENS
    return int(params.max_tokens)


def _build_kwargs(
    params: GenerationParams,
    *,
    max_tokens: int,
) -> dict:
    """将 :class:`GenerationParams` 翻译为 ``mlx_lm`` 可接受的 kwargs。

    ``mlx_lm`` 在 0.18 和 0.20 之间将 ``temp`` 重命名为 ``temperature``；
    我们优先使用新名称，但若 ``mlx_lm.generate`` 拒绝 ``temperature``
    则回退到旧名称。``top_p`` 同理 —— 旧 API 使用 ``0.0`` 表示"使用默认值"，
    而新 API 接受字面值。

    Translate :class:`GenerationParams` into the kwargs ``mlx_lm`` accepts.

    ``mlx_lm`` renamed ``temp`` → ``temperature`` between 0.18 and
    0.20; we prefer the newer name but fall back to the older one if
    ``mlx_lm.generate`` rejects ``temperature``.  Same idea for
    ``top_p`` — the older API used ``0.0`` to mean "use default",
    whereas the newer one accepts the literal value.
    """
    kwargs: dict = {
        "max_tokens": max_tokens,
        "verbose": False,
    }
    temperature = float(params.temperature) if params.temperature is not None else 0.0
    top_p = float(params.top_p) if params.top_p is not None else 1.0
    if temperature != 0.0:
        kwargs["temperature"] = temperature
    if 0.0 < top_p < 1.0:
        kwargs["top_p"] = top_p
    stop = params.stop
    if stop:
        kwargs["stop"] = list(stop)
    return kwargs


def _resolve_generate_kwargs(
    mlx_generate,
    params: GenerationParams,
    *,
    max_tokens: int,
) -> dict:
    """根据安装的 ``mlx_lm`` 版本选择接受的参数名。

    返回绑定函数不会因 ``TypeError`` 而拒绝的 kwargs 字典。
    我们探查一次函数签名。

    Pick the parameter names accepted by the installed ``mlx_lm`` version.

    Returns a kwargs dict that the bound function will accept without
    raising ``TypeError``.  We probe the function signature once.
    """
    import inspect

    sig = inspect.signature(mlx_generate)
    accepts = set(sig.parameters.keys())

    base = _build_kwargs(params, max_tokens=max_tokens)

    # 新版 API: ``temperature``。旧版 API: ``temp``。
    # 不能总是同时有两者，优先使用函数暴露的名称。
    if "temperature" in base and "temperature" not in accepts and "temp" in accepts:
        base["temp"] = base.pop("temperature")

    # ``stop`` 在旧版本中为位置/关键字参数；仅在存在时保留。
    if "stop" in base and "stop" not in accepts:
        base.pop("stop")

    return base


def _extract_generation(response) -> str:
    """从 ``mlx_lm`` 生成响应中提取累计文本。

    不同 ``mlx_lm`` 版本将结果包装为：
      * 裸 ``str``（0.18 及更早的 ``generate``），
      * 带 ``.text`` 的数据类（0.20+ 的 ``generate``），
      * 带 ``.text`` 的数据类（0.20+ 的 ``stream_generate``）。

    Pull the cumulative text out of a ``mlx_lm`` generation response.

    Different ``mlx_lm`` versions wrap the result in either:
      * a bare ``str`` (0.18 and earlier ``generate``),
      * a dataclass with ``.text`` (0.20+ ``generate``),
      * a dataclass with ``.text`` (0.20+ ``stream_generate``).
    """
    if isinstance(response, str):
        return response
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    raise BackendError(
        f"unexpected mlx_lm response type: {type(response).__name__}",
        code="backend_error",
    )


def _extract_response_meta(response) -> dict:
    """从 ``mlx_lm`` 响应中提取可选的 prompt/completion token 计数。

    当字段未暴露（旧版本）时返回空字典。调用者在空时用 tokenizer
    估算自己的计数。

    Pull optional prompt/completion token counts out of a ``mlx_lm`` response.

    Returns an empty dict when the fields aren't exposed (older
    versions).  Callers fill in their own estimates from the tokenizer
    when this is empty.
    """
    meta: dict = {}
    for key in (
        "prompt_tokens",
        "generation_tokens",
        "completion_tokens",
    ):
        value = getattr(response, key, None)
        if isinstance(value, int):
            meta[key] = value
    finish_reason = getattr(response, "finish_reason", None)
    if isinstance(finish_reason, str):
        meta["finish_reason"] = finish_reason
    return meta


def _count_tokens(tokenizer, text: str) -> int:
    """通过 tokenizer 尽力计数 token；失败时返回 0。

    Best-effort token count via the tokenizer; 0 on failure.
    """
    try:
        return len(tokenizer.encode(text))
    except Exception:
        return 0


def _resolve_aborted(exc: BaseException) -> bool:
    """当 ``exc`` 是内部抛出的中止信号时返回 ``True``。

    Return ``True`` when ``exc`` is the abort signal raised internally.
    """
    return isinstance(exc, ApiGenerationAborted)


# ---------------------------------------------------------------------------
# VLM detection + multimodal helpers / VLM 检测与多模态辅助
# ---------------------------------------------------------------------------


def _detect_vlm(path: Path) -> bool:
    """启发式判断 ``path`` 处的检查点是否为 VLM。

    检查 ``config.json``（当 ``path`` 是目录时）中是否包含已知的
    VLM 架构名称。对单文件检查点（``.mlx`` / safetensors）返回
    ``False`` —— 它们绝大多数是纯文本模型，我们不希望误报将其
    路由到 ``mlx_vlm``。

    Heuristically decide whether the checkpoint at ``path`` is a VLM.

    Checks ``config.json`` (when ``path`` is a directory) for known
    VLM architecture names.  Returns ``False`` for single-file
    checkpoints (``.mlx`` / safetensors) — those are overwhelmingly
    text-only and we don't want a false positive that routes them
    through ``mlx_vlm``.
    """
    if not path.is_dir():
        return False
    config_path = path / "config.json"
    if not config_path.exists():
        return False
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
    if (path / "preprocessor_config.json").exists():
        return True
    return False


def _msg_content(m) -> Any:
    if isinstance(m, ChatMessage):
        return m.content
    if isinstance(m, dict):
        return m.get("content")
    return None


def _msg_role(m) -> str:
    if isinstance(m, ChatMessage):
        return m.role
    if isinstance(m, dict):
        return str(m.get("role", ""))
    return ""


def _has_multimodal_content(messages: Sequence) -> bool:
    """当任何消息携带列表式内容时返回 ``True``。

    Return ``True`` when any message carries list-of-parts content.
    """
    for m in messages:
        content = _msg_content(m)
        if isinstance(content, list):
            return True
    return False


def _degrade_multimodal_to_text(messages: Sequence) -> list:
    """将列表式内容展平为纯文本字符串。

    ``text`` 部分被拼接，``image_url``/``audio_url``/``video_url``
    部分变成 ``[image]``/``[audio]``/``[video]`` 占位符。
    纯字符串内容原样保留。返回的列表镜像输入类型
   （``ChatMessage`` 输入 → ``ChatMessage`` 输出，
    ``dict`` 输入 → ``dict`` 输出）。

    Flatten list-of-parts content into a text-only string.

    ``text`` parts are concatenated, ``image_url``/``audio_url``/
    ``video_url`` parts become ``[image]``/``[audio]``/``[video]``
    placeholders.  Plain-string content is preserved untouched.  The
    returned list mirrors the input types (``ChatMessage`` in →
    ``ChatMessage`` out, ``dict`` in → ``dict`` out).
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


def _resolve_image_to_path(url: str) -> str | None:
    """将 ``image_url`` 值解析为本地文件系统路径。

    支持：

    * ``file:///abs/path.png`` → ``/abs/path.png``
    * ``/abs/path.png`` → 原样返回
    * ``http(s)://...`` → 下载到临时文件
    * ``data:image/...;base64,...`` → 解码到临时文件

    当 URL 无法解析时返回 ``None``（调用者跳过该图像，让
    ``mlx_vlm`` 只看到有效的图像）。

    Resolve an ``image_url`` value to a local filesystem path.

    Supports:

    * ``file:///abs/path.png``  → ``/abs/path.png``
    * ``/abs/path.png`` → as-is
    * ``http(s)://...`` → downloaded to a temp file
    * ``data:image/...;base64,...`` → decoded to a temp file

    Returns ``None`` when the URL can't be resolved (the caller skips
    the image in that case and lets ``mlx_vlm`` see only the valid
    ones).
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


def _extract_images(messages: Sequence) -> list[str]:
    """从多模态消息内容中提取图像路径。

    遍历每条消息；对每个 ``image_url`` 部分将 URL 解析为本地路径
    （见 :func:`_resolve_image_to_path`）并追加到结果中。
    不可解析的图像被静默丢弃 —— ``mlx_vlm`` 只会看到磁盘上存在的图像。

    Pull image paths from multimodal message content.

    Walks every message; for each ``image_url`` part resolves the URL
    to a local path (see :func:`_resolve_image_to_path`) and appends
    it to the result.  Unresolvable images are silently dropped —
    ``mlx_vlm`` will only see the ones that exist on disk.
    """
    images: list[str] = []
    for m in messages:
        content = _msg_content(m)
        if not isinstance(content, list):
            continue
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") != "image_url":
                continue
            spec = p.get("image_url")
            if isinstance(spec, dict):
                url = spec.get("url", "")
            else:
                url = spec if isinstance(spec, str) else ""
            resolved = _resolve_image_to_path(url)
            if resolved:
                images.append(resolved)
    return images


def _extract_video_frames(messages: Sequence, max_frames: int = 5) -> list[str]:
    """从多模态消息内容中提取视频帧路径。

    遍历每条消息；对每个 ``video_url`` 部分下载/解析视频文件，
    使用 ffmpeg 提取关键帧，返回帧图像路径列表。
    需要 ffmpeg 可用。

    Extract video frame paths from multimodal message content.

    Walks every message; for each ``video_url`` part downloads/resolves
    the video file, extracts key frames via ffmpeg, returns frame image paths.
    Requires ffmpeg to be available.
    """
    frames: list[str] = []
    for m in messages:
        content = _msg_content(m)
        if not isinstance(content, list):
            continue
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") != "video_url":
                continue
            spec = p.get("video_url")
            if isinstance(spec, dict):
                url = spec.get("url", "")
            else:
                url = spec if isinstance(spec, str) else ""
            if not url:
                continue
            # 解析视频 URL 到本地路径
            video_path = _resolve_video_to_path(url)
            if not video_path:
                continue
            # 提取帧
            extracted = _extract_frames_from_video(video_path, max_frames)
            frames.extend(extracted)
    return frames


def _resolve_video_to_path(url: str) -> str | None:
    """将 ``video_url`` 值解析为本地文件系统路径。

    支持：
    * ``file:///abs/path.mp4`` → ``/abs/path.mp4``
    * ``/abs/path.mp4`` → 原样返回
    * ``http(s)://...`` → 下载到临时文件
    * ``data:video/...;base64,...`` → 解码到临时文件

    Resolve a ``video_url`` value to a local filesystem path.
    """
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("file://"):
        path = url[len("file://"):]
        return path if Path(path).exists() else None
    if url.startswith("data:"):
        try:
            header, b64 = url.split(",", 1)
            mime = header.split(":")[1].split(";")[0] if ":" in header else "video/mp4"
            ext = mime.split("/")[-1].split("-")[-1] or "mp4"
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
            ext = url.rsplit(".", 1)[-1].split("?")[0][:8].lower()
            if ext not in ("mp4", "mov", "avi", "mkv", "webm", "flv", "m4v"):
                ext = "mp4"
            resp = httpx.get(url, timeout=60.0, follow_redirects=True)
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


def _extract_frames_from_video(video_path: str, max_frames: int = 5) -> list[str]:
    """从视频文件中提取关键帧。

    使用 ffmpeg 均匀提取最多 max_frames 帧，返回帧图像路径列表。
    临时文件由调用者负责清理。

    Extract key frames from a video file.

    Uses ffmpeg to uniformly extract up to max_frames frames.
    Returns list of frame image paths. Temp files managed by caller.
    """
    frames: list[str] = []
    tmp_dir = tempfile.mkdtemp(prefix="mlx_vlm_video_frames_")

    try:
        # 获取视频时长
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             video_path],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(result.stdout.strip() or 0)
    except Exception:
        duration = 0

    try:
        if duration > 0:
            interval = max(1.0, duration / max_frames)
            for i in range(max_frames):
                ts = i * interval
                out_path = os.path.join(tmp_dir, f"frame_{i:04d}.jpg")
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
                         "-vframes", "1", "-q:v", "2", out_path],
                        capture_output=True, timeout=30, check=True,
                    )
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        frames.append(out_path)
                except Exception:
                    continue
        else:
            # 无法获取时长，提取第一帧
            out_path = os.path.join(tmp_dir, "frame_0001.jpg")
            subprocess.run(
                ["ffmpeg", "-y", "-i", video_path,
                 "-vframes", "1", "-q:v", "2", out_path],
                capture_output=True, timeout=30, check=True,
            )
            if os.path.exists(out_path):
                frames.append(out_path)
    except Exception:
        pass

    return frames[:max_frames]


def _extract_audio_paths(messages: Sequence) -> list[str]:
    """从多模态消息内容中提取音频文件路径。

    遍历每条消息；对每个 ``audio_url`` 部分将 URL 解析为本地路径。
    返回音频文件路径列表，供后续 STT 处理。

    Extract audio file paths from multimodal message content.

    Walks every message; for each ``audio_url`` part resolves the URL
    to a local path. Returns list of audio file paths for STT processing.
    """
    audio_paths: list[str] = []
    for m in messages:
        content = _msg_content(m)
        if not isinstance(content, list):
            continue
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") != "audio_url":
                continue
            spec = p.get("audio_url")
            if isinstance(spec, dict):
                url = spec.get("url", "")
            else:
                url = spec if isinstance(spec, str) else ""
            if not url:
                continue
            resolved = _resolve_audio_to_path(url)
            if resolved:
                audio_paths.append(resolved)
    return audio_paths


def _resolve_audio_to_path(url: str) -> str | None:
    """将 ``audio_url`` 值解析为本地文件系统路径。

    支持：
    * ``file:///abs/path.wav`` → ``/abs/path.wav``
    * ``/abs/path.wav`` → 原样返回
    * ``http(s)://...`` → 下载到临时文件
    * ``data:audio/...;base64,...`` → 解码到临时文件

    Resolve an ``audio_url`` value to a local filesystem path.
    """
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("file://"):
        path = url[len("file://"):]
        return path if Path(path).exists() else None
    if url.startswith("data:"):
        try:
            header, b64 = url.split(",", 1)
            mime = header.split(":")[1].split(";")[0] if ":" in header else "audio/wav"
            ext = mime.split("/")[-1].split("-")[-1] or "wav"
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
            ext = url.rsplit(".", 1)[-1].split("?")[0][:8].lower()
            if ext not in ("wav", "mp3", "flac", "ogg", "m4a", "aac", "opus", "webm"):
                ext = "wav"
            resp = httpx.get(url, timeout=60.0, follow_redirects=True)
            if resp.status_code >= 400:
                return None
            fd, tmp = tempfile.mkstemp(suffix=f".{ext}")
            with os.fdopen(fd, "wb") as fp:
                fp.write(resp.content)
            return tmp
        except Exception:
            return None
    return url if Path(url).exists() else None


def _messages_to_oai(messages: Sequence) -> list[dict]:
    """将 :class:`ChatMessage` / dict 序列转换为 OAI 字典。

    Convert :class:`ChatMessage` / dict sequence into OAI dicts.
    """
    out: list[dict] = []
    for m in messages:
        if isinstance(m, ChatMessage):
            out.append(m.to_dict())
        elif isinstance(m, dict):
            out.append(m)
        else:
            out.append({"role": "user", "content": str(m)})
    return out


@register_chat("mlx")
class MLXChatBackend(ChatBackend):
    """MLX 聊天后端。MLX chat backend."""
    name = "mlx"

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None           # mlx_lm 路径
        self._processor = None           # mlx_vlm 路径
        self._config: Any = None         # mlx_vlm 模型配置
        self._model_path: Path | None = None
        self._is_vlm: bool = False
        self._has_mlx_vlm = _try_mlx_vlm_available()
        self._has_mlx_lm = _try_mlx_lm_available()
        # 为远程/data: 图像创建的临时文件 — 在 unload 时清理。
        self._temp_image_paths: list[str] = []

    # -- introspection / 内省 ------------------------------------------------------

    def is_available(self) -> bool:
        # 安装了 mlx_lm（文本）或 mlx_vlm（视觉）时可用。
        # mlx_lm 是常见情况；单独的 mlx_vlm 也可以（它也能服务
        # 纯文本模型，但我们优先使用 mlx_lm）。
        return self._has_mlx_lm or self._has_mlx_vlm

    def is_loaded(self) -> bool:
        return self._model is not None and (
            self._tokenizer is not None or self._processor is not None
        )

    # -- lifecycle / 生命周期 ----------------------------------------------------------

    def load(self, model_path, *, context_length: int = 0, **kwargs) -> None:
        path = Path(model_path)
        if not path.exists():
            raise BackendError(
                f"model path does not exist: {path}",
                code="model_not_found",
            )

        # 决策 VLM 还是纯文本。运维者可通过 ``vlm = true`` extra 字段强制 VLM；
        # 否则我们从配置检测。
        force_vlm = bool(kwargs.get("vlm") or kwargs.get("is_vlm"))
        use_vlm = (force_vlm or _detect_vlm(path)) and self._has_mlx_vlm

        if use_vlm:
            try:
                from mlx_vlm import load as vlm_load
            except Exception as exc:
                raise BackendError(
                    f"mlx_vlm not importable: {exc}",
                    code="backend_unavailable",
                ) from exc
            try:
                self._model, self._processor = vlm_load(str(path))
            except Exception as exc:
                raise BackendError(
                    f"mlx_vlm.load failed: {exc}",
                    code="backend_error",
                ) from exc
            # 优先使用模型自身的 config 属性；回退到直接加载 config.json。
            self._config = getattr(self._model, "config", None)
            if self._config is None:
                try:
                    from mlx_vlm.utils import load_config
                    self._config = load_config(str(path))
                except Exception:
                    self._config = {}
            self._is_vlm = True
        else:
            if not self._has_mlx_lm:
                raise BackendError(
                    "neither mlx_lm nor mlx_vlm available to load this model",
                    code="backend_unavailable",
                )
            try:
                from mlx_lm import load as mlx_load
            except Exception as exc:
                raise BackendError(
                    f"mlx_lm not importable: {exc}",
                    code="backend_unavailable",
                ) from exc
            try:
                self._model, self._tokenizer = mlx_load(str(path))
            except Exception as exc:
                raise BackendError(
                    f"mlx_lm.load failed: {exc}",
                    code="backend_error",
                ) from exc
            self._is_vlm = False
        self._model_path = path

    def unload(self) -> None:
        # 清理为远程/data: 图像创建的所有临时文件。
        for tmp in self._temp_image_paths:
            try:
                os.unlink(tmp)
            except Exception:
                pass
        self._temp_image_paths.clear()

        self._model = None
        self._tokenizer = None
        self._processor = None
        self._config = None
        self._model_path = None
        self._is_vlm = False
        try:
            import mlx.core as mx
            mx.metal.clear_cache()
        except Exception:
            pass

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
            raise ModelNotLoaded("no MLX chat model loaded")

        max_tokens = _resolve_max_tokens(params)
        chunk_id = f"chatcmpl-mlx-{int(time.time() * 1000)}"
        model_id = str(self._model_path) if self._model_path else "mlx"

        if self._is_vlm:
            return self._chat_vlm(
                messages=messages,
                params=params,
                max_tokens=max_tokens,
                chunk_id=chunk_id,
                model_id=model_id,
                stream=stream,
                abort_signal=abort_signal,
            )

        # 纯文本路径。将多模态内容降级为文本，以免 tokenizer 的
        # chat template 在处理列表式 ``content`` 字段时出错。
        if _has_multimodal_content(messages):
            messages = _degrade_multimodal_to_text(messages)

        try:
            from mlx_lm import generate as mlx_generate
            from mlx_lm import stream_generate as mlx_stream
        except Exception as exc:
            raise BackendError(
                f"mlx_lm not importable: {exc}",
                code="backend_unavailable",
            ) from exc

        prompt = self._tokenizer.apply_chat_template(
            [m.to_dict() if isinstance(m, ChatMessage) else m for m in messages],
            tokenize=False,
            add_generation_prompt=True,
        )

        if stream:
            return self._streaming(
                prompt=prompt,
                params=params,
                max_tokens=max_tokens,
                chunk_id=chunk_id,
                model_id=model_id,
                mlx_stream=mlx_stream,
                abort_signal=abort_signal,
            )
        return self._blocking(
            prompt=prompt,
            params=params,
            max_tokens=max_tokens,
            chunk_id=chunk_id,
            model_id=model_id,
            mlx_generate=mlx_generate,
            abort_signal=abort_signal,
        )

    # -- VLM path / VLM 路径 -----------------------------------------------------------

    def _chat_vlm(
        self,
        *,
        messages: Sequence,
        params: GenerationParams,
        max_tokens: int,
        chunk_id: str,
        model_id: str,
        stream: bool,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        """通过 ``mlx_vlm`` 使用多模态输入（图像 + 视频 + 音频）生成。

        Generate via ``mlx_vlm`` with multimodal inputs (images + video + audio).

        - 图像（image_url）直接以文件路径传入 ``mlx_vlm``
        - 视频（video_url）通过 ffmpeg 提取帧，以图像序列形式传入
        - 音频（audio_url）通过 STT 转录为文本，注入 prompt（如果可用）

        - Images (image_url) passed directly as file paths to ``mlx_vlm``
        - Videos (video_url) frames extracted via ffmpeg, passed as image sequence
        - Audio (audio_url) transcribed via STT (if available) and injected as text
        """
        try:
            from mlx_vlm import (
                generate as vlm_generate,
                stream_generate as vlm_stream,
                apply_chat_template as vlm_apply_template,
            )
        except Exception as exc:
            raise BackendError(
                f"mlx_vlm not importable: {exc}",
                code="backend_unavailable",
            ) from exc

        oai_messages = _messages_to_oai(messages)

        # 1) 提取静态图像 / Extract static images
        images = _extract_images(oai_messages)

        # 2) 提取视频帧（如果 ffmpeg 可用） / Extract video frames (if ffmpeg available)
        video_frames = _extract_video_frames(oai_messages, max_frames=5)

        # 3) 尝试 STT 转录音频并注入为文本
        #    Try STT-transcribe audio and inject as text
        audio_paths = _extract_audio_paths(oai_messages)
        stt_texts: list[str] = []
        if audio_paths:
            # 惰性导入，避免文件级依赖
            # Lazy import to avoid file-level dependency
            try:
                from xijian_api.ai.registry import get_stt_backend
                stt = get_stt_backend("mlx", ("gguf",))
                if stt and stt.is_loaded():
                    for ap in audio_paths:
                        try:
                            with open(ap, "rb") as f:
                                audio_bytes = f.read()
                            result = stt.transcribe(audio_bytes, response_format="text")
                            text = result.get("text", "") if isinstance(result, dict) else str(result)
                            if text:
                                stt_texts.append(text)
                        except Exception:
                            pass
            except Exception:
                pass

        # 跟踪临时文件以便在 unload 时清理。
        all_media = images + video_frames + audio_paths
        for path in all_media:
            if path not in self._temp_image_paths and self._is_temp_path(path):
                self._temp_image_paths.append(path)

        # 注入转录文本到消息（作为额外的用户文本）
        # Inject transcribed text into messages (as additional user text)
        if stt_texts:
            transcript_combined = "\n\n[Audio transcription: " + " | ".join(
                t.strip() for t in stt_texts if t.strip()
            ) + "]"
            oai_messages.append({"role": "user", "content": transcript_combined})

        # 组合所有图像（静态图 + 视频帧）/ Combine all images (static + video frames)
        all_images = images + video_frames

        # 构建格式化的 prompt。``mlx_vlm.apply_chat_template``
        # 接受字符串或消息列表。
        try:
            prompt = vlm_apply_template(
                self._processor,
                self._config,
                oai_messages,
                add_generation_prompt=True,
                num_images=len(all_images),
            )
        except Exception as exc:
            raise BackendError(
                f"mlx_vlm.apply_chat_template failed: {exc}",
                code="backend_error",
            ) from exc

        # ``mlx_vlm`` 期望 ``image`` 为列表（或 None）。
        image_arg = all_images if all_images else None

        if stream:
            return self._streaming_vlm(
                prompt=prompt,
                image_arg=image_arg,
                params=params,
                max_tokens=max_tokens,
                chunk_id=chunk_id,
                model_id=model_id,
                vlm_stream=vlm_stream,
                abort_signal=abort_signal,
            )
        return self._blocking_vlm(
            prompt=prompt,
            image_arg=image_arg,
            params=params,
            max_tokens=max_tokens,
            chunk_id=chunk_id,
            model_id=model_id,
            vlm_generate=vlm_generate,
            abort_signal=abort_signal,
        )

    @staticmethod
    def _is_temp_path(path: str) -> bool:
        """检查路径是否在系统临时目录中。"""
        try:
            return path.startswith(tempfile.gettempdir())
        except Exception:
            return False

    def _blocking_vlm(
        self,
        *,
        prompt: str,
        image_arg,
        params: GenerationParams,
        max_tokens: int,
        chunk_id: str,
        model_id: str,
        vlm_generate,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        kwargs = _build_vlm_kwargs(params, max_tokens=max_tokens)
        try:
            result = vlm_generate(
                self._model, self._processor, prompt,
                image=image_arg, **kwargs,
            )
        except ApiGenerationAborted:
            raise
        except Exception as exc:
            raise BackendError(
                f"mlx_vlm.generate failed: {exc}",
                code="backend_error",
            ) from exc
        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        content = _extract_generation(result)
        prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(result, "generation_tokens", 0) or 0)
        if not prompt_tokens or not completion_tokens:
            # 通过处理器附加的 tokenizer 尽力估算。
            # 旧版 mlx_vlm 可能不暴露 token 计数。
            tok = getattr(self._processor, "tokenizer", None) or self._processor
            if not prompt_tokens:
                prompt_tokens = _count_tokens(tok, prompt)
            if not completion_tokens:
                completion_tokens = _count_tokens(tok, content)
        usage = ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        finish_reason = getattr(result, "finish_reason", None) or (
            "length" if completion_tokens >= max_tokens else "stop"
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant", "content": content},
            finish_reason=finish_reason,
            usage=usage,
        )

    def _streaming_vlm(
        self,
        *,
        prompt: str,
        image_arg,
        params: GenerationParams,
        max_tokens: int,
        chunk_id: str,
        model_id: str,
        vlm_stream,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        yield _build_chunk(
            chunk_id=chunk_id, model=model_id, delta={"role": "assistant"},
        )
        kwargs = _build_vlm_kwargs(params, max_tokens=max_tokens)
        seen_text = ""
        aborted = False
        prompt_tokens = 0
        last_result = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                for result in vlm_stream(
                    self._model, self._processor, prompt,
                    image=image_arg, **kwargs,
                ):
                    if abort_signal is not None:
                        abort_signal.raise_if_aborted()
                    last_result = result
                    if not prompt_tokens:
                        prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
                    cumulative = _extract_generation(result)
                    delta_text = cumulative[len(seen_text):]
                    if delta_text:
                        yield _build_chunk(
                            chunk_id=chunk_id,
                            model=model_id,
                            delta={"content": delta_text},
                        )
                    seen_text = cumulative
        except ApiGenerationAborted:
            aborted = True
        except Exception as exc:
            raise BackendError(
                f"mlx_vlm.stream_generate failed: {exc}",
                code="backend_error",
            ) from exc

        # 解析最终 usage + finish_reason。
        reported_completion = 0
        finish_reason = None
        if last_result is not None:
            reported_completion = int(getattr(last_result, "generation_tokens", 0) or 0)
            finish_reason = getattr(last_result, "finish_reason", None)
        if not reported_completion:
            tok = getattr(self._processor, "tokenizer", None) or self._processor
            reported_completion = _count_tokens(tok, seen_text)
        if not prompt_tokens:
            tok = getattr(self._processor, "tokenizer", None) or self._processor
            prompt_tokens = _count_tokens(tok, prompt)
        if finish_reason not in {"stop", "length", "abort"}:
            finish_reason = "length" if reported_completion >= max_tokens else "stop"
        if aborted:
            finish_reason = "abort"
        usage = ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=reported_completion,
            total_tokens=prompt_tokens + reported_completion,
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason=finish_reason,
            usage=usage,
        )

    # -- mlx_lm text-only path / mlx_lm 纯文本路径 ---------------------------------------------

    def _blocking(
        self,
        *,
        prompt: str,
        params: GenerationParams,
        max_tokens: int,
        chunk_id: str,
        model_id: str,
        mlx_generate,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        """生成一个完整响应并产生单个 :class:`ChatChunk`。

        Generate one full response and yield a single :class:`ChatChunk`.
        """
        kwargs = _resolve_generate_kwargs(
            mlx_generate, params, max_tokens=max_tokens
        )

        # mlx_lm.generate 是同步的；我们内联运行。
        # 这里的中止是尽力而为——mlx_lm 在非流模式不暴露逐 token 钩子。
        # 流式路径支持更精细的中止。
        try:
            text = mlx_generate(self._model, self._tokenizer, prompt, **kwargs)
        except ApiGenerationAborted:
            raise
        except Exception as exc:
            raise BackendError(f"mlx_lm.generate failed: {exc}", code="backend_error") from exc

        if abort_signal is not None:
            abort_signal.raise_if_aborted()

        # mlx_lm 可能返回裸字符串或带额外元数据的数据类；归一化两者。
        content = _extract_generation(text)

        prompt_tokens = _count_tokens(self._tokenizer, prompt)
        completion_tokens = _count_tokens(self._tokenizer, content)
        usage = ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        # 决定 finish_reason：达到预算时为 ``length``，否则 ``stop``。
        # ``mlx_lm`` 并不总是告诉我们，所以我们从 completion_tokens 推断。
        finish_reason = "length" if completion_tokens >= max_tokens else "stop"

        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={
                "role": "assistant",
                "content": content,
            },
            finish_reason=finish_reason,
            usage=usage,
        )

    def _streaming(
        self,
        *,
        prompt: str,
        params: GenerationParams,
        max_tokens: int,
        chunk_id: str,
        model_id: str,
        mlx_stream,
        abort_signal,
    ) -> Iterator[ChatChunk]:
        """随 token 到达产生递增的 :class:`ChatChunk` 实例。

        ``mlx_lm.stream_generate`` 产生 :class:`GenerationResponse` 对象，
        其 ``.text`` 是累积的生成文本。我们仅发出*新的*后缀作为
        OAI ``delta.content``，使客户端看到真正的逐 token 流。

        Yield incremental :class:`ChatChunk` instances as tokens arrive.

        ``mlx_lm.stream_generate`` yields :class:`GenerationResponse`
        objects whose ``.text`` is the cumulative generated text.  We
        emit only the *new* suffix as the OAI ``delta.content`` so the
        client sees a real token-by-token stream.
        """
        kwargs = _resolve_generate_kwargs(
            mlx_stream, params, max_tokens=max_tokens
        )

        # 首个 chunk 声明角色。OpenAI 的惯例是先发一个仅角色的 chunk，
        # 然后是纯内容增量，最后是带 ``finish_reason`` 的最终 chunk。
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={"role": "assistant"},
        )

        seen_text = ""
        prompt_tokens = _count_tokens(self._tokenizer, prompt)
        last_response = None
        aborted = False
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                for response in mlx_stream(
                    self._model, self._tokenizer, prompt, **kwargs
                ):
                    if abort_signal is not None:
                        abort_signal.raise_if_aborted()
                    last_response = response
                    cumulative = _extract_generation(response)
                    delta_text = cumulative[len(seen_text):]
                    if delta_text:
                        yield _build_chunk(
                            chunk_id=chunk_id,
                            model=model_id,
                            delta={"content": delta_text},
                        )
                    seen_text = cumulative
        except ApiGenerationAborted:
            aborted = True
        except Exception as exc:
            raise BackendError(
                f"mlx_lm.stream_generate failed: {exc}",
                code="backend_error",
            ) from exc

        # 最终 chunk 携带 finish_reason + usage。
        # 确定模型是自然结束（``stop``）还是达到
        # ``max_tokens`` （``length``）。
        if aborted:
            finish_reason = "abort"
        else:
            meta = _extract_response_meta(last_response) if last_response is not None else {}
            finish_reason = meta.get("finish_reason")
            if finish_reason not in {"stop", "length", "abort"}:
                completion_tokens = _count_tokens(self._tokenizer, seen_text)
                finish_reason = "length" if completion_tokens >= max_tokens else "stop"

        completion_tokens = _count_tokens(self._tokenizer, seen_text)
        if last_response is not None:
            meta = _extract_response_meta(last_response)
            reported = meta.get("generation_tokens")
            if isinstance(reported, int) and reported > 0:
                completion_tokens = reported
        usage = ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        yield _build_chunk(
            chunk_id=chunk_id,
            model=model_id,
            delta={},
            finish_reason=finish_reason,
            usage=usage,
        )


def _build_vlm_kwargs(params: GenerationParams, *, max_tokens: int) -> dict:
    """将 :class:`GenerationParams` 翻译为 ``mlx_vlm`` 的 kwargs。

    ``mlx_vlm`` 接受与 ``mlx_lm`` 0.20+ 相同的 ``max_tokens`` /
    ``temperature`` / ``top_p`` / ``stop`` 名称，所以这是薄透传。
    我们省略 ``verbose``（mlx_vlm 有自己的默认值）。

    Translate :class:`GenerationParams` into kwargs for ``mlx_vlm``.

    ``mlx_vlm`` accepts the same ``max_tokens`` / ``temperature`` /
    ``top_p`` / ``stop`` names as ``mlx_lm`` 0.20+, so this is a thin
    pass-through.  We omit ``verbose`` (mlx_vlm has its own default).
    """
    kwargs: dict = {"max_tokens": max_tokens}
    temperature = float(params.temperature) if params.temperature is not None else 0.0
    top_p = float(params.top_p) if params.top_p is not None else 1.0
    if temperature != 0.0:
        kwargs["temperature"] = temperature
    if 0.0 < top_p < 1.0:
        kwargs["top_p"] = top_p
    if params.stop:
        kwargs["stop"] = list(params.stop)
    return kwargs


__all__ = ["MLXChatBackend"]