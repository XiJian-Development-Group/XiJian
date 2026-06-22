"""GGUF video-generation backend.

Video diffusion models in GGUF format are still emerging.  The
community ``stable-diffusion.cpp`` fork extended with video
generation is the most common shape (``stable-diffusion.cpp-video``
or third-party builds).  When installed this backend hands the
checkpoint to that binding and emits MP4 bytes via the
``submit``/``poll`` contract used by the route layer.

If no GGUF video binding is installed this backend reports itself
as unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xijian_api.ai.base import (
    BackendError,
    ModelNotFound,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_video
from xijian_api.ai.types import VideoGenBackend


def _probe() -> tuple[bool, str | None]:
    """Return ``(available, class_attr)`` for the GGUF video binding.

    Both the upstream ``stable_diffusion_cpp`` package and the
    community fork have added video classes over time.  We try
    several known names; whichever one wins, that's what we use.
    """
    candidates: tuple[str, ...] = (
        "stable_diffusion_cpp_video",
        "stable_diffusion_cpp",
    )
    for module_name in candidates:
        try:
            module = __import__(module_name, fromlist=["*"])
        except Exception:
            continue
        for attr in ("StableVideoDiffusion", "VideoPipeline", "StableDiffusionVideo"):
            if hasattr(module, attr):
                return True, attr
    return False, None


@register_video("gguf")
class GGUFVideoBackend(VideoGenBackend):
    name = "gguf"

    def __init__(self) -> None:
        self._available, self._attr = _probe()
        self._pipeline: Any = None
        self._model_path: Path | None = None

    def is_available(self) -> bool:
        return self._available

    def is_loaded(self) -> bool:
        return self._pipeline is not None

    def load(self, model_path, **kwargs) -> None:
        if not self._available or self._attr is None:
            raise BackendError(
                "no GGUF video binding installed (tried stable_diffusion_cpp[_video])",
                code="backend_unavailable",
            )
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")
        try:
            cls = getattr(__import__("stable_diffusion_cpp_video", fromlist=[self._attr])
                          if self._attr and False else  # noqa: SIM222 - keep structure
                          __import__("stable_diffusion_cpp", fromlist=[self._attr]),
                          self._attr)
            self._pipeline = cls(model_path=str(path))
        except Exception as exc:
            raise BackendError(
                f"GGUF video init failed: {exc}",
                code="backend_error",
            ) from exc
        self._model_path = path

    def unload(self) -> None:
        self._pipeline = None
        self._model_path = None

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
        if not self.is_loaded():
            raise ModelNotLoaded("no GGUF video model loaded")
        width, height = _parse_size(size)
        try:
            task_id = self._pipeline.submit(
                prompt=prompt,
                input_reference=input_reference,
                seconds=max(1, int(seconds)),
                width=width,
                height=height,
                fps=int(fps),
                seed=int(seed) if seed is not None else -1,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            raise BackendError(
                f"GGUF video submit failed: {exc}",
                code="backend_error",
            ) from exc
        return _stringify(task_id)

    def poll(self, task_id: str) -> dict:
        if not self.is_loaded():
            raise ModelNotLoaded("no GGUF video model loaded")
        try:
            status = self._pipeline.poll(task_id)
        except Exception as exc:
            raise BackendError(
                f"GGUF video poll failed: {exc}",
                code="backend_error",
            ) from exc
        if not isinstance(status, dict):
            raise BackendError(
                f"GGUF video poll returned non-dict: {type(status).__name__}",
                code="backend_error",
            )
        return status


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = size.lower().split("x", 1)
        return int(w), int(h)
    except Exception as exc:
        raise BackendError(
            f"invalid size '{size}' (expected WxH)",
            code="invalid_request_error",
        ) from exc


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(value)


__all__ = ["GGUFVideoBackend"]
