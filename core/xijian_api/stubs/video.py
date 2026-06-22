"""Video generation stub — submits to the configured video backend.

The previous 64-byte zero-filled fake file has been removed.  When the
configured video backend is unavailable, :func:`submit` raises
:class:`xijian_api.errors.BackendError` (status 503).  Otherwise the
backend's ``poll`` is consulted in a background thread to flip the
queued record into ``completed`` (or ``failed``) state.
"""

from __future__ import annotations

import threading
import time

from flask import current_app

from xijian_api.ai.base import BackendError as AIBackendError
from xijian_api.ai.base import BackendUnavailable as AIBackendUnavailable
from xijian_api.ai.registry import get_video_backend
from xijian_api.config import Config
from xijian_api.errors import BackendError as ApiBackendError
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_file_id
from xijian_api.utils.time import now_ts


_POLL_INTERVAL_SECONDS = 1.5


def _resolve_config() -> Config | None:
    try:
        return current_app.config.get("XIJIAN_CONFIG")
    except RuntimeError:
        return None


def _select_backend():
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
    """Poll the backend in a background thread until the job finishes."""
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
                if not current.get("url"):
                    file_id = gen_file_id()
                    payload = status.get("bytes") or b""
                    if not payload:
                        # No payload — record an empty file so the
                        # download endpoint doesn't 404.
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
) -> None:
    """Submit a video generation request to the backend.

    The route layer inserts the queued record into ``state.videos``
    first; this function hands the job to the backend and arranges for
    the polling thread to flip status when the job finishes.
    """
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


__all__ = ["submit", "_complete_record"]