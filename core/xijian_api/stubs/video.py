"""Video generation stub — submits to the configured video backend.
视频生成存根 — 提交到配置的视频后端。

The previous 64-byte zero-filled fake file has been removed.  When the
configured video backend is unavailable, :func:`submit` raises
:class:`xijian_api.errors.BackendError` (status 503).  Otherwise the
backend's ``poll`` is consulted in a background thread to flip the
queued record into ``completed`` (or ``failed``) state.

之前的 64 字节零填充伪文件已被移除。当配置的视频后端不可用时，
:func:`submit` 抛出 :class:`xijian_api.errors.BackendError` (状态码 503)。
否则，后台线程查阅后端的 ``poll`` 以将排队记录翻转为 ``completed``
（或 ``failed``）状态。
"""

from __future__ import annotations

import threading
import time

from flask import current_app

from xijian_api.ai.base import BackendError as AIBackendError
from xijian_api.ai.base import BackendUnavailable as AIBackendUnavailable
from xijian_api.ai.registry import get_video_backend
from xijian_api.ai.registry import get_video_understanding_backend
from xijian_api.config import Config
from xijian_api.errors import BackendError as ApiBackendError
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_file_id
from xijian_api.utils.time import now_ts


_POLL_INTERVAL_SECONDS = 1.5


def _resolve_config() -> Config | None:
    """Resolve the XiJian config from Flask app context.
    从 Flask 应用上下文解析 XiJian 配置。
    """
    try:
        return current_app.config.get("XIJIAN_CONFIG")
    except RuntimeError:
        return None


def _select_backend():
    """Select the appropriate video backend based on config.
    根据配置选择合适的视频后端。
    """
    config = _resolve_config()
    requested: str | None = None
    fallbacks: tuple[str, ...] = ()
    if config is not None:
        requested = config.backends.video.default or None
        fallbacks = config.backends.video.fallbacks or ()
    try:
        return get_video_backend(requested, fallbacks)
    except AIBackendUnavailable as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "no video backend available",
            type_="backend_unavailable",
            code="backend_unavailable",
        ) from exc


def _complete_record(
    video_id: str,
    *,
    backend_task_id: str | None = None,
) -> None:
    """Poll the backend in a background thread until the job finishes.
    在后台线程中轮询后端，直到任务完成。
    """
    backend = _select_backend()
    record = state.videos.get(video_id)
    if record is None:
        return

    def _poll():
        while True:
            time.sleep(_POLL_INTERVAL_SECONDS)
            current = state.videos.get(video_id)
            if current is None:
                return
            try:
                status = backend.poll(backend_task_id or video_id)
            except AIBackendError as exc:
                current["status"] = "failed"
                current["error"] = {
                    "code": getattr(exc, "code", "backend_error"),
                    "message": str(exc),
                }
                current["completed_at"] = now_ts()
                return
            state_value = str(status.get("status", "")).lower()
            if state_value in {"completed", "succeeded", "success"}:
                current["status"] = "completed"
                current["completed_at"] = now_ts()
                current["expires_at"] = now_ts() + 600
                # Backends should set ``url``; if not, create a stub
                # files entry so the OAI download URL still resolves.
                # 后端应设置 ``url``；否则创建存根文件条目使 OAI 下载 URL 仍可解析。
                if not current.get("url"):
                    file_id = gen_file_id()
                    payload = status.get("bytes") or b""
                    if not payload:
                        # No payload — record an empty file so the
                        # download endpoint doesn't 404.
                        # 无载荷——记录空文件，使下载端点不返回 404。
                        payload = b""
                    state.files[file_id] = {
                        "id": file_id,
                        "bytes": payload,
                        "purpose": "vision",
                        "filename": f"video_{video_id}.mp4",
                        "content_type": "video/mp4",
                    }
                    current["url"] = f"/v1/files/{file_id}/content"
                if status.get("url"):
                    current["url"] = status["url"]
                return
            if state_value in {"failed", "error", "cancelled"}:
                current["status"] = "failed" if state_value != "cancelled" else "cancelled"
                current["error"] = status.get("error") or {
                    "code": state_value,
                    "message": status.get("message", ""),
                }
                current["completed_at"] = now_ts()
                return

    threading.Thread(target=_poll, daemon=True).start()


def submit(
    prompt: str,
    *,
    model: str = "stub-video",
    input_reference: str | None = None,
    seconds: int = 4,
    size: str = "1280x720",
    fps: int = 24,
    seed: int | None = None,
    video_id: str,
    character_id: str | None = None,
) -> None:
    """Submit a video generation request to the backend.

    The route layer inserts the queued record into ``state.videos``
    first; this function hands the job to the backend and arranges for
    the polling thread to flip status when the job finishes.
    向后端提交视频生成请求。

    路由层先将排队记录插入 ``state.videos``；此函数将任务交给后端，
    并安排轮询线程在任务完成时翻转状态。

    ``character_id`` (A3.1 跨模态一致性): when provided and no
    explicit ``input_reference`` was given, the character's motion
    clip reference is used as the generation's input reference so
    the output video stays consistent with the character's motion
    library.
    """
    if not input_reference and character_id:
        from xijian_api.stubs.characters import get_generation_references
        refs = get_generation_references(character_id)
        input_reference = refs.get("motion_clip") or input_reference
    backend = _select_backend()
    try:
        backend_task_id = backend.submit(
            prompt,
            model_id=model,
            input_reference=input_reference,
            seconds=seconds,
            size=size,
            fps=fps,
            seed=seed,
        )
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "video backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc
    record = state.videos.get(video_id)
    if record is not None:
        record["backend_task_id"] = backend_task_id
    _complete_record(video_id, backend_task_id=backend_task_id)


def understand_video(
    video: Any,
    *,
    model: str = "stub-video-understanding",
    prompt: str = "",
    fps: int = 1,
    max_frames: int = 10,
    abort_signal=None,
) -> str:
    """Run video understanding through the configured backend.

    Accepts the same input shapes as the backend: ``str`` (URL / path /
    data URI), ``bytes``, or a ``dict`` with a ``url`` / ``video_url``
    / ``file_url`` key.  Returns the text description of the video.

    通过配置的后端执行视频理解。

    接受与后端相同的输入形式：``str``（URL / 路径 / data URI）、
    ``bytes`` 或带 ``url`` / ``video_url`` / ``file_url`` 键的
    ``dict``。返回视频的文本描述。
    """
    config = _resolve_config()
    requested: str | None = None
    fallbacks: tuple[str, ...] = ()
    if config is not None:
        requested = config.backends.video_understanding.default or None
        fallbacks = config.backends.video_understanding.fallbacks or ()

    backend = None
    # 显式指定的模型：优先通过注册表加载对应条目（如 stub-video-understanding → mock）。
    # Explicitly requested model: prefer loading the matching registry
    # entry (e.g. stub-video-understanding → mock) over the default chain.
    if model:
        try:
            from xijian_api.ai.model_registry import get_registry
            entry = config.model_by_id(model) if config is not None else None
            if entry is not None and entry.type == "video_understanding":
                loaded = get_registry().load(model, config=config)
                backend = loaded.instance
        except Exception:
            backend = None

    if backend is None:
        try:
            backend = get_video_understanding_backend(requested, fallbacks)
        except AIBackendUnavailable as exc:
            raise ApiBackendError(
                status=503,
                message=str(exc) or "no video understanding backend available",
                type_="backend_unavailable",
                code="backend_unavailable",
            ) from exc

    try:
        return backend.understand(
            video,
            prompt=prompt,
            fps=int(fps),
            max_frames=int(max_frames),
            abort_signal=abort_signal,
        )
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "video understanding backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc


__all__ = ["submit", "_complete_record", "understand_video"]
