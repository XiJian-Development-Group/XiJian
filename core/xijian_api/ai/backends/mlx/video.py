"""MLX video-generation backend.

Optional support for diffusion-based video generation on Apple Silicon.
There is no single canonical library yet — ``mlx_video`` /
``mlx-animate`` / community repos fill this space.  We probe the
common names and surface whichever one's installed, falling back to
``is_available() -> False`` so the registry can route elsewhere.

The backend follows the same submit/poll contract as the GGUF
counterpart: ``submit`` returns a backend task id, ``poll`` returns a
status dict with ``status``, optional ``url`` / ``bytes``, and an
optional ``error`` block.
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


# Candidate libraries, in preference order.  Whichever one imports
# wins.  New entries can be added without touching the backend's
# behaviour.
_CANDIDATES: tuple[str, ...] = (
    "mlx_video",
    "mlx_animate",
)


def _probe() -> tuple[bool, str | None]:
    """Find the first importable MLX video library and its ``generate`` attr."""
    for name in _CANDIDATES:
        try:
            module = __import__(name)
        except Exception:
            continue
        for attr in ("generate", "submit"):
            if hasattr(module, attr):
                return True, f"{name}.{attr}"
    return False, None


@register_video("mlx")
class MLXVideoBackend(VideoGenBackend):
    name = "mlx"

    def __init__(self) -> None:
        self._available, self._attr = _probe()
        self._model_path: Path | None = None

    def is_available(self) -> bool:
        return self._available

    def is_loaded(self) -> bool:
        return self._model_path is not None

    def load(self, model_path, **kwargs) -> None:
        if not self._available:
            raise BackendError(
                "no MLX video library installed (tried: %s)" % ", ".join(_CANDIDATES),
                code="backend_unavailable",
            )
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")
        self._model_path = path

    def unload(self) -> None:
        self._model_path = None
        try:
            import mlx.core as mx
            mx.metal.clear_cache()
        except Exception:
            pass

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
        if self._model_path is None:
            raise ModelNotLoaded("no MLX video model loaded")
        if not self._available:
            raise BackendError(
                "no MLX video library available",
                code="backend_unavailable",
            )
        width, height = _parse_size(size)
        try:
            task_id = self._call_submit(
                prompt=prompt,
                n_seconds=max(1, int(seconds)),
                width=width,
                height=height,
                fps=int(fps),
                seed=seed,
                input_reference=input_reference,
            )
        except Exception as exc:
            raise BackendError(
                f"MLX video submit failed: {exc}",
                code="backend_error",
            ) from exc
        return _stringify(task_id)

    def poll(self, task_id: str) -> dict:
        if not self._available:
            raise BackendError(
                "no MLX video library available",
                code="backend_unavailable",
            )
        try:
            status = self._call_poll(task_id)
        except Exception as exc:
            raise BackendError(
                f"MLX video poll failed: {exc}",
                code="backend_error",
            ) from exc
        if not isinstance(status, dict):
            raise BackendError(
                f"MLX video poll returned non-dict: {type(status).__name__}",
                code="backend_error",
            )
        return status

    # -- internals ----------------------------------------------------------

    def _call_submit(self, *, prompt, n_seconds, width, height, fps, seed, input_reference) -> Any:
        """Invoke the library's ``generate`` / ``submit`` function."""
        import importlib

        parts = self._attr.split(".")
        module = importlib.import_module(".".join(parts[:-1]))
        fn = getattr(module, parts[-1])
        kwargs: dict[str, Any] = {
            "model_path": str(self._model_path),
            "prompt": prompt,
            "seconds": n_seconds,
            "width": width,
            "height": height,
            "fps": fps,
        }
        if seed is not None:
            kwargs["seed"] = int(seed)
        if input_reference:
            kwargs["input_reference"] = input_reference
        return fn(**kwargs)

    def _call_poll(self, task_id: str) -> dict:
        """Poll the library for status; fall back to a synchronous wait.

        Most MLX video libraries run synchronously — the call returns
        the finished video rather than a task id.  In that case we
        accept the dict result and re-shape it into the
        ``{status, url, bytes}`` shape used by the route layer.
        """
        try:
            result = _synchronous_poll(self._attr, task_id)
        except _NoPollFunction:
            return _synchronous_generate_result(self._attr, task_id)
        return result


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


class _NoPollFunction(Exception):
    """Raised when the candidate library has no poll function."""


def _synchronous_poll(attr: str, task_id: str) -> dict:
    """Try ``<lib>.poll(<task_id>)``; raise ``_NoPollFunction`` if absent."""
    import importlib

    parts = attr.split(".")
    module_name = ".".join(parts[:-1])
    fn_name = parts[-1]
    try:
        module = importlib.import_module(module_name)
    except Exception:
        raise _NoPollFunction()
    poll_fn = getattr(module, "poll", None)
    if not callable(poll_fn):
        raise _NoPollFunction()
    return poll_fn(task_id)


def _synchronous_generate_result(attr: str, task_id: str) -> dict:
    """Treat ``task_id`` as a cache key into the library's last output.

    For libraries that complete synchronously (``generate`` returns the
    bytes directly), the route layer doesn't actually call ``poll``
    with a backend task id — it polls the in-memory state.  We provide
    a passthrough that returns ``completed`` so the poll loop
    terminates cleanly.
    """
    return {
        "status": "completed",
        "task_id": task_id,
        "synchronous": True,
    }


__all__ = ["MLXVideoBackend"]
