"""MLX image-generation backend.

Optional support for diffusion models via the ``mlx_stable_diffusion``
community library (or ``diffusers`` with the MLX backend).  When no
MLX-capable image library is installed, :meth:`is_available` returns
``False`` and the registry falls through to GGUF.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xijian_api.ai.base import (
    BackendError,
    ModelNotFound,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_image
from xijian_api.ai.types import ImageGenBackend


def _probe() -> tuple[bool, str | None]:
    """Return ``(available, attribute)`` for the preferred MLX image library.

    ``mlx_stable_diffusion`` is the Apple-Silicon-native choice.  When
    it isn't installed we don't try to coerce ``diffusers`` (which is
    heavier and rarely MLX-only) — operators who want CPU/Metal
    Stable Diffusion can run a custom backend instead.
    """
    try:
        import mlx_stable_diffusion  # noqa: F401
    except Exception:
        return False, None
    if hasattr(mlx_stable_diffusion, "generate"):
        return True, "mlx_stable_diffusion.generate"
    if hasattr(mlx_stable_diffusion, "pipeline"):
        return True, "mlx_stable_diffusion.pipeline"
    return False, None


@register_image("mlx")
class MLXImageBackend(ImageGenBackend):
    name = "mlx"

    def __init__(self) -> None:
        self._available, self._attr = _probe()
        self._model_path: Path | None = None
        self._pipeline: Any = None

    def is_available(self) -> bool:
        return self._available

    def is_loaded(self) -> bool:
        return self._pipeline is not None

    def load(self, model_path, **kwargs) -> None:
        if not self._available:
            raise BackendError(
                "mlx_stable_diffusion is not installed; install it to enable MLX image",
                code="backend_unavailable",
            )
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")
        self._model_path = path
        # ``mlx_stable_diffusion`` is lazy: a call to ``generate`` loads
        # the pipeline on first use.  We mirror that to keep startup
        # fast and report issues at generation time.
        self._pipeline = None

    def unload(self) -> None:
        self._model_path = None
        self._pipeline = None
        try:
            import mlx.core as mx
            mx.metal.clear_cache()
        except Exception:
            pass

    def generate(
        self,
        prompt: str,
        *,
        model_id: str,
        n: int = 1,
        size: str = "1024x1024",
        negative_prompt: str | None = None,
        seed: int | None = None,
        abort_signal=None,
    ) -> list[dict]:
        if not self.is_loaded() and self._model_path is None:
            raise ModelNotLoaded("no MLX image model loaded")
        if not self._available:
            raise BackendError(
                "mlx_stable_diffusion is not installed",
                code="backend_unavailable",
            )
        width, height = _parse_size(size)
        try:
            images = self._call(
                prompt=prompt,
                n=n,
                width=width,
                height=height,
                negative_prompt=negative_prompt,
                seed=seed,
            )
        except Exception as exc:
            raise BackendError(
                f"mlx_stable_diffusion.generate failed: {exc}",
                code="backend_error",
            ) from exc
        return _normalise_outputs(images, n=n)

    def edit(self, *args, **kwargs):  # pragma: no cover - delegated to stub
        raise BackendError(
            "MLX image backend does not implement edit; fall back to generate",
            code="backend_error",
        )

    def variation(self, *args, **kwargs):  # pragma: no cover - delegated to stub
        raise BackendError(
            "MLX image backend does not implement variation; fall back to generate",
            code="backend_error",
        )

    # -- internals ----------------------------------------------------------

    def _call(self, *, prompt, n, width, height, negative_prompt, seed) -> list[Any]:
        """Invoke ``mlx_stable_diffusion.generate`` (or pipeline)."""
        import importlib

        parts = self._attr.split(".")
        module = importlib.import_module(".".join(parts[:-1]))
        fn = getattr(module, parts[-1])
        kwargs: dict[str, Any] = {
            "model_path": str(self._model_path),
            "prompt": prompt,
            "n_images": max(1, n),
            "width": width,
            "height": height,
        }
        if negative_prompt:
            kwargs["negative_prompt"] = negative_prompt
        if seed is not None:
            kwargs["seed"] = int(seed)
        return fn(**kwargs)


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = size.lower().split("x", 1)
        return int(w), int(h)
    except Exception as exc:
        raise BackendError(
            f"invalid size '{size}' (expected WxH)",
            code="invalid_request_error",
        ) from exc


def _normalise_outputs(images: list, *, n: int) -> list[dict]:
    """Convert the library's output into the OAI ``b64_json``/``url`` shape."""
    out: list[dict] = []
    for img in images[: max(1, n)]:
        # PIL.Image is the most common return type.
        try:
            from PIL import Image
            if isinstance(img, Image.Image):
                from io import BytesIO
                buf = BytesIO()
                img.save(buf, format="PNG")
                out.append({"bytes": buf.getvalue()})
                continue
        except Exception:
            pass
        if isinstance(img, (bytes, bytearray)):
            out.append({"bytes": bytes(img)})
            continue
        if isinstance(img, dict):
            out.append(img)
            continue
        raise BackendError(
            f"unsupported mlx_stable_diffusion output: {type(img).__name__}",
            code="backend_error",
        )
    return out


__all__ = ["MLXImageBackend"]
