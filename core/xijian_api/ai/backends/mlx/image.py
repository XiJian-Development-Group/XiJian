"""MLX image-generation backend.

Supports two implementation paths:

1. ``mlx_stable_diffusion`` — the Apple-Silicon-native SD library
   (preferred when installed; not on PyPI, must be built from source).
2. ``diffusers`` (HuggingFace) with the MPS (Metal) backend — the
   always-installable fallback.  Diffusers can run SD 1.5, SDXL, SD3
   and many other diffusion checkpoints on Apple Silicon via
   ``torch.device("mps")``.

When neither is installed, :meth:`is_available` returns ``False`` and
the registry falls through to the next configured backend (typically
GGUF or OpenAI remote).
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


def _probe() -> tuple[bool, str]:
    """Return ``(available, library)`` for the preferred image library.

    Tries ``mlx_stable_diffusion`` first, then ``diffusers``.
    """
    try:
        import mlx_stable_diffusion  # noqa: F401
        if hasattr(mlx_stable_diffusion, "generate") or hasattr(
            mlx_stable_diffusion, "pipeline"
        ):
            return True, "mlx_stable_diffusion"
    except Exception:
        pass
    try:
        import diffusers  # noqa: F401
        import torch  # noqa: F401
        return True, "diffusers"
    except Exception:
        pass
    return False, ""


def _torch_device() -> str:
    """Pick the best torch device: MPS on Apple Silicon, else CPU."""
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


@register_image("mlx")
class MLXImageBackend(ImageGenBackend):
    name = "mlx"

    def __init__(self) -> None:
        self._available, self._lib = _probe()
        self._model_path: Path | None = None
        self._pipeline: Any = None
        self._torch_device: str = ""

        # ``diffusers`` cache for the loaded pipeline; ``None`` for
        # ``mlx_stable_diffusion`` (which is lazy and re-loads per call).
        self._diffusers_pipe: Any = None

    def is_available(self) -> bool:
        return self._available

    def is_loaded(self) -> bool:
        if self._lib == "diffusers":
            return self._diffusers_pipe is not None
        return self._pipeline is not None

    def load(self, model_path, **kwargs) -> None:
        if not self._available:
            raise BackendError(
                "neither mlx_stable_diffusion nor diffusers is installed; "
                "install one of them to enable MLX image generation",
                code="backend_unavailable",
            )
        path = Path(model_path)
        if not path.exists():
            raise ModelNotFound(f"model path does not exist: {path}")
        self._model_path = path

        if self._lib == "diffusers":
            self._load_diffusers(path, **kwargs)
        # ``mlx_stable_diffusion`` is lazy — defer to generate time.

    def _load_diffusers(self, path: Path, **kwargs) -> None:
        """Eagerly build a ``diffusers`` pipeline from ``path``."""
        try:
            import torch
            from diffusers import StableDiffusionPipeline
        except Exception as exc:
            raise BackendError(
                f"diffusers/torch not importable: {exc}",
                code="backend_unavailable",
            ) from exc
        self._torch_device = _torch_device()
        torch_dtype = torch.float16 if self._torch_device == "mps" else torch.float32
        try:
            # ``path`` may be a directory (HF model layout) or a single
            # checkpoint file.  Diffusers' ``from_pretrained`` handles
            # directories; for files we hand off to
            # ``FromSingleFileMixin``.
            if path.is_dir():
                self._diffusers_pipe = StableDiffusionPipeline.from_pretrained(
                    str(path), torch_dtype=torch_dtype,
                )
            else:
                # ``from_single_file`` is the SD-WebUI-checkpoint path.
                try:
                    self._diffusers_pipe = StableDiffusionPipeline.from_single_file(
                        str(path), torch_dtype=torch_dtype,
                    )
                except AttributeError:
                    raise BackendError(
                        f"loaded diffusers version lacks from_single_file; "
                        f"pass a HF model directory instead of {path}",
                        code="backend_error",
                    )
        except Exception as exc:
            raise BackendError(
                f"diffusers pipeline init failed: {exc}",
                code="backend_error",
            ) from exc
        try:
            self._diffusers_pipe = self._diffusers_pipe.to(self._torch_device)
        except Exception:
            # CPU fallback if MPS fails (some checkpoints don't support half).
            self._diffusers_pipe = self._diffusers_pipe.to("cpu")
            self._torch_device = "cpu"

    def unload(self) -> None:
        self._model_path = None
        self._pipeline = None
        self._diffusers_pipe = None
        self._torch_device = ""
        # Best-effort cache clear.
        try:
            import mlx.core as mx
            mx.metal.clear_cache()
        except Exception:
            pass
        try:
            import torch
            if self._torch_device == "mps":
                torch.mps.empty_cache()
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
                "image library not installed",
                code="backend_unavailable",
            )
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        width, height = _parse_size(size)
        try:
            if self._lib == "diffusers":
                images = self._call_diffusers(
                    prompt=prompt, n=n, width=width, height=height,
                    negative_prompt=negative_prompt, seed=seed,
                )
            else:
                images = self._call_mlx_sd(
                    prompt=prompt, n=n, width=width, height=height,
                    negative_prompt=negative_prompt, seed=seed,
                )
        except Exception as exc:
            raise BackendError(
                f"image generation failed: {exc}",
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

    def _call_mlx_sd(
        self, *, prompt, n, width, height, negative_prompt, seed,
    ) -> list[Any]:
        """Invoke ``mlx_stable_diffusion.generate`` (or pipeline)."""
        import importlib

        try:
            module = importlib.import_module("mlx_stable_diffusion")
        except Exception as exc:
            raise BackendError(
                f"mlx_stable_diffusion not importable: {exc}",
                code="backend_unavailable",
            ) from exc
        fn = getattr(module, "generate", None) or getattr(module, "pipeline", None)
        if fn is None:
            raise BackendError(
                "mlx_stable_diffusion has no generate/pipeline entry point",
                code="backend_error",
            )
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
        result = fn(**kwargs)
        if isinstance(result, list):
            return result
        return [result]

    def _call_diffusers(
        self, *, prompt, n, width, height, negative_prompt, seed,
    ) -> list[Any]:
        """Invoke the loaded ``diffusers`` pipeline."""
        if self._diffusers_pipe is None:
            raise ModelNotLoaded("diffusers pipeline not loaded")
        import torch

        gen_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "num_images_per_prompt": max(1, n),
            "width": width,
            "height": height,
        }
        if negative_prompt:
            gen_kwargs["negative_prompt"] = negative_prompt
        if seed is not None:
            generator = torch.Generator(device=self._torch_device or "cpu")
            generator = generator.manual_seed(int(seed))
            gen_kwargs["generator"] = generator
        output = self._diffusers_pipe(**gen_kwargs)
        # ``StableDiffusionPipeline`` returns an ``images`` attribute.
        images = getattr(output, "images", None)
        if images is None and isinstance(output, dict):
            images = output.get("images")
        if images is None:
            images = list(output)
        return list(images)


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
            f"unsupported image output: {type(img).__name__}",
            code="backend_error",
        )
    return out


__all__ = ["MLXImageBackend"]
