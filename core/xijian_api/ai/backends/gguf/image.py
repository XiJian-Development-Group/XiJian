"""GGUF image-generation backend.

Stable Diffusion GGUF models ship as ``.gguf`` files consumable by
``stable-diffusion.cpp`` and its Python binding
(``stable_diffusion_cpp``).  When installed this backend hands the
checkpoint to the binding and emits PNG bytes.

If the binding isn't installed this backend reports itself as
unavailable; operators can still produce images via a remote backend
by configuring ``[backends.image].default`` accordingly.
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
    """Return ``(available, class_attr)`` for the SD GGUF binding."""
    try:
        import stable_diffusion_cpp  # noqa: F401
    except Exception:
        return False, None
    for attr in ("StableDiffusion", "Pipeline"):
        if hasattr(stable_diffusion_cpp, attr):
            return True, attr
    return False, None


@register_image("gguf")
class GGUFImageBackend(ImageGenBackend):
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
                "stable_diffusion_cpp is not installed",
                code="backend_unavailable",
            )
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")
        try:
            cls = getattr(__import__("stable_diffusion_cpp", fromlist=[self._attr]), self._attr)
            self._pipeline = cls(model_path=str(path))
        except Exception as exc:
            raise BackendError(
                f"stable_diffusion_cpp init failed: {exc}",
                code="backend_error",
            ) from exc
        self._model_path = path

    def unload(self) -> None:
        self._pipeline = None
        self._model_path = None

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
        if not self.is_loaded():
            raise ModelNotLoaded("no GGUF image model loaded")
        width, height = _parse_size(size)
        try:
            images = self._pipeline.txt_to_img(
                prompt=prompt,
                negative_prompt=negative_prompt or "",
                width=width,
                height=height,
                sample_count=max(1, n),
                seed=int(seed) if seed is not None else -1,
            )
        except Exception as exc:
            raise BackendError(
                f"stable_diffusion_cpp.txt_to_img failed: {exc}",
                code="backend_error",
            ) from exc
        return _normalise(images)

    def edit(self, *args, **kwargs):  # pragma: no cover - delegated to stub
        raise BackendError(
            "GGUF image backend does not implement edit; fall back to generate",
            code="backend_error",
        )

    def variation(self, *args, **kwargs):  # pragma: no cover - delegated to stub
        raise BackendError(
            "GGUF image backend does not implement variation; fall back to generate",
            code="backend_error",
        )


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = size.lower().split("x", 1)
        return int(w), int(h)
    except Exception as exc:
        raise BackendError(
            f"invalid size '{size}' (expected WxH)",
            code="invalid_request_error",
        ) from exc


def _normalise(images) -> list[dict]:
    """Coerce ``stable_diffusion_cpp`` outputs into the OAI shape."""
    from io import BytesIO

    out: list[dict] = []
    for img in images or []:
        try:
            from PIL import Image
            if isinstance(img, Image.Image):
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
            f"unsupported stable_diffusion_cpp output: {type(img).__name__}",
            code="backend_error",
        )
    return out


__all__ = ["GGUFImageBackend"]
