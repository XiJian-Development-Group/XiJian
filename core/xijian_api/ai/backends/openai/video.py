"""OpenAI-compatible remote video-generation backend.

The standard OpenAI API does not have a video-generation endpoint, but
many OpenAI-compatible providers (e.g. Runway, Kling, MiniMax wrappers)
expose one at ``/video/generations`` or similar.  This backend probes
the configured ``base_url`` for a video endpoint at load time; if none
is configured it reports ``is_available() = False`` so the registry
falls through to local backends.

Configuration (in ``[[models]].extra`` or ``[backends.openai]``):

* ``video_endpoint`` — path appended to ``base_url`` (default:
  ``/video/generations``).  Set to empty string to disable.
* ``video_poll_interval`` — seconds between polls (default: 5).

The submit/poll contract mirrors :class:`VideoGenBackend`:
``submit`` returns a task id, ``poll`` returns a status dict.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from xijian_api.ai.backends.openai._client import (
    RemoteConfig,
    resolve_config,
    _httpx_post_json,
)
from xijian_api.ai.base import (
    BackendError,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_video
from xijian_api.ai.types import VideoGenBackend


@register_video("openai")
class OpenAIVideoBackend(VideoGenBackend):
    name = "openai"

    def __init__(self) -> None:
        self._cfg: RemoteConfig | None = None
        self._endpoint: str = "/video/generations"
        self._poll_interval: float = 5.0

    def is_available(self) -> bool:
        # Available only when an explicit video endpoint is configured.
        return self._cfg is not None and bool(self._endpoint)

    def is_loaded(self) -> bool:
        return self._cfg is not None

    def load(self, model_path, **kwargs) -> None:
        section = kwargs.pop("_openai_section", None)
        cfg = resolve_config(kwargs, section=section, default_model="")
        # ``video_endpoint`` controls whether video is enabled at all.
        # Per-model extra overrides the global section.
        endpoint = (
            kwargs.get("video_endpoint")
            or (section or {}).get("video_endpoint")
            or "/video/generations"
        )
        # Empty string → explicitly disabled.
        if endpoint == "":
            self._endpoint = ""
            self._cfg = cfg
            return
        self._endpoint = endpoint
        self._poll_interval = float(kwargs.get("video_poll_interval", 5.0) or 5.0)
        if not cfg.model_name:
            raise BackendError(
                "openai video backend requires a model_name",
                code="backend_error",
            )
        self._cfg = cfg

    def unload(self) -> None:
        self._cfg = None

    def submit(
        self,
        prompt: str,
        *,
        model_id: str,
        input_reference: str | None = None,
        seconds: int = 4,
        size: str = "1280x720",
        fps: int = 24,
        seed: int | None = None,
        progress_callback=None,
        abort_signal=None,
    ) -> str:
        if not self.is_loaded() or self._cfg is None:
            raise ModelNotLoaded("no openai video model loaded")
        if not self._endpoint:
            raise BackendError(
                "video endpoint not configured (set video_endpoint in model extra)",
                code="backend_error",
            )
        url = f"{self._cfg.base_url}{self._endpoint}"
        body: dict[str, Any] = {
            "model": self._cfg.model_name,
            "prompt": prompt,
            "seconds": max(1, int(seconds)),
            "size": size,
            "fps": int(fps),
        }
        if input_reference:
            body["input_reference"] = input_reference
        if seed is not None:
            body["seed"] = int(seed)
        result = _httpx_post_json(url, headers=self._cfg.auth_header, json_body=body)
        task_id = result.get("id") or result.get("task_id") or result.get("request_id")
        if not task_id:
            raise BackendError(
                f"video submit returned no task id: {result}",
                code="backend_error",
            )
        return str(task_id)

    def poll(self, task_id: str) -> dict:
        if not self.is_loaded() or self._cfg is None:
            raise ModelNotLoaded("no openai video model loaded")
        if not self._endpoint:
            raise BackendError(
                "video endpoint not configured",
                code="backend_error",
            )
        url = f"{self._cfg.base_url}{self._endpoint}/{task_id}"
        result = _httpx_post_json(url, headers=self._cfg.auth_header, json_body={})
        status = result.get("status", "unknown")
        out: dict[str, Any] = {
            "status": status,
            "task_id": task_id,
        }
        video_url = result.get("url") or result.get("video_url")
        if video_url:
            out["url"] = video_url
        error = result.get("error")
        if error:
            out["error"] = error
        return out


__all__ = ["OpenAIVideoBackend"]
