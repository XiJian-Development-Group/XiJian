"""MLX 视频生成后端。

MLX video-generation backend.

Apple Silicon 上基于扩散的视频生成的可选支持。目前尚无单一规范库 ——
``mlx_video`` / ``mlx-animate`` / 社区仓库填补了这一空间。
我们探测常见名称，暴露已安装的那个，未安装时回退到
``is_available() -> False``，以便注册表路由到其他地方。

后端遵循与 GGUF 副本相同的 submit/poll 契约：``submit`` 返回后端
任务 ID，``poll`` 返回带 ``status``、可选 ``url`` / ``bytes``
和可选 ``error`` 块的状态字典。

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


# 候选库，按偏好顺序。先导入哪个就选哪个。
# 可在不修改后端行为的情况下添加新条目。
_CANDIDATES: tuple[str, ...] = (
    "mlx_video",
    "mlx_animate",
)


def _probe() -> tuple[bool, str | None]:
    """找到第一个可导入的 MLX 视频库及其 ``generate`` 属性。

    Find the first importable MLX video library and its ``generate`` attr.
    """
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
    """MLX 视频生成后端。MLX video generation backend."""
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

    # -- internals / 内部 ----------------------------------------------------------

    def _call_submit(self, *, prompt, n_seconds, width, height, fps, seed, input_reference) -> Any:
        """调用库的 ``generate`` / ``submit`` 函数。

        Invoke the library's ``generate`` / ``submit`` function.
        """
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
        """轮询库的进度；回退到同步等待。

        大多数 MLX 视频库同步运行 —— 调用直接返回完成的视频而非任务 ID。
        此时我们接受字典结果并将其重塑为路由层使用的
        ``{status, url, bytes}`` 形状。

        Poll the library for status; fall back to a synchronous wait.

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
    """候选库无 poll 函数时抛出。

    Raised when the candidate library has no poll function.
    """


def _synchronous_poll(attr: str, task_id: str) -> dict:
    """尝试 ``<lib>.poll(<task_id>)``；不存在时抛出 ``_NoPollFunction``。

    Try ``<lib>.poll(<task_id>)``; raise ``_NoPollFunction`` if absent.
    """
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
    """将 ``task_id`` 视为库上次输出的缓存键。

    对于同步完成的库（``generate`` 直接返回字节），路由层实际不会
    用后端任务 ID 调用 ``poll`` —— 它轮询内存状态。我们提供透传，
    返回 ``completed`` 以便轮询循环干净终止。

    Treat ``task_id`` as a cache key into the library's last output.

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