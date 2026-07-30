"""OpenAI 兼容远程视频生成后端。

标准 OpenAI API 没有视频生成端点，但许多兼容提供商（如 Runway、
Kling、MiniMax 包装器）在 ``/video/generations`` 或类似路径暴露了该功能。
本后端在加载时探测配置的 ``base_url`` 下的视频端点；若未配置则报告
``is_available() = False``，以便注册表回退到本地后端。

配置（在 ``[[models]].extra`` 或 ``[backends.openai]`` 中）：

* ``video_endpoint`` — 追加到 ``base_url`` 的路径（默认：``/video/generations``）。
  设为空字符串可禁用。
* ``video_poll_interval`` — 轮询间隔秒数（默认：5）。

提交/轮询契约与 :class:`VideoGenBackend` 一致：
``submit`` 返回任务 ID，``poll`` 返回状态字典。

OpenAI-compatible remote video-generation backend.

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
    """OpenAI 兼容视频生成后端实现。OpenAI-compatible video generation backend implementation."""
    name = "openai"

    def __init__(self) -> None:
        self._cfg: RemoteConfig | None = None
        self._endpoint: str = "/video/generations"
        self._poll_interval: float = 5.0

    def is_available(self) -> bool:
        # 仅当显式配置了视频端点时可用。
        # Available only when an explicit video endpoint is configured.
        return self._cfg is not None and bool(self._endpoint)

    def is_loaded(self) -> bool:
        return self._cfg is not None

    def load(self, model_path, **kwargs) -> None:
        section = kwargs.pop("_openai_section", None)
        cfg = resolve_config(kwargs, section=section, default_model="")
        # ``video_endpoint`` 控制视频功能是否启用。
        # 逐模型 extra 覆盖全局段。
        endpoint = (
            kwargs.get("video_endpoint")
            or (section or {}).get("video_endpoint")
            or "/video/generations"
        )
        # 空字符串 → 显式禁用。
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