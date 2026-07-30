"""MLX 图像生成后端。

MLX image-generation backend.

支持两条实现路径：

1. ``mlx_stable_diffusion`` — Apple Silicon 原生 SD 库
   （优先使用；不在 PyPI 上，需从源码构建）。
2. ``diffusers``（HuggingFace）+ MPS（Metal）后端 —
   始终可安装的回退方案。Diffusers 可在 Apple Silicon 上通过
   ``torch.device("mps")`` 运行 SD 1.5、SDXL、SD3 及许多其他
   扩散检查点。

当两者都未安装时，:meth:`is_available` 返回 ``False``，注册表
回退到下一个配置的后端（通常是 GGUF 或 OpenAI 远程）。

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
    """返回首选的图像库 ``(available, library)``。

    先尝试 ``mlx_stable_diffusion``，再试 ``diffusers``。

    Return ``(available, library)`` for the preferred image library.

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
    """选择最佳 torch 设备：Apple Silicon 用 MPS，否则用 CPU。

    Pick the best torch device: MPS on Apple Silicon, else CPU.
    """
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


@register_image("mlx")
class MLXImageBackend(ImageGenBackend):
    """MLX 图像生成后端。MLX image generation backend."""
    name = "mlx"

    def __init__(self) -> None:
        self._available, self._lib = _probe()
        self._model_path: Path | None = None
        self._pipeline: Any = None
        self._torch_device: str = ""

        # ``diffusers`` 缓存已加载的 pipeline；``mlx_stable_diffusion``
        # 用 ``None``（它惰性地在每次调用时重新加载）。
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
        # ``mlx_stable_diffusion`` 惰性加载 —— 推迟到生成时。

    def _load_diffusers(self, path: Path, **kwargs) -> None:
        """从 ``path`` 急切构建 ``diffusers`` pipeline。

        Eagerly build a ``diffusers`` pipeline from ``path``.
        """
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
            # ``path`` 可以是目录（HF 模型布局）或单个检查点文件。
            # Diffusers 的 ``from_pretrained`` 处理目录；文件则交
            # 由 ``FromSingleFileMixin`` 处理。
            if path.is_dir():
                self._diffusers_pipe = StableDiffusionPipeline.from_pretrained(
                    str(path), torch_dtype=torch_dtype,
                )
            else:
                # ``from_single_file`` 是 SD-WebUI 检查点路径。
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
            # 若 MPS 失败则 CPU 回退（某些检查点不支持半精度）。
            self._diffusers_pipe = self._diffusers_pipe.to("cpu")
            self._torch_device = "cpu"

    def unload(self) -> None:
        self._model_path = None
        self._pipeline = None
        self._diffusers_pipe = None
        self._torch_device = ""
        # 尽力缓存清理。
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

    def edit(self, *args, **kwargs):  # pragma: no cover - 委托给 stub
        raise BackendError(
            "MLX image backend does not implement edit; fall back to generate",
            code="backend_error",
        )

    def variation(self, *args, **kwargs):  # pragma: no cover - 委托给 stub
        raise BackendError(
            "MLX image backend does not implement variation; fall back to generate",
            code="backend_error",
        )

    # -- internals / 内部 ----------------------------------------------------------

    def _call_mlx_sd(
        self, *, prompt, n, width, height, negative_prompt, seed,
    ) -> list[Any]:
        """调用 ``mlx_stable_diffusion.generate``（或 pipeline）。

        Invoke ``mlx_stable_diffusion.generate`` (or pipeline).
        """
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
        """调用已加载的 ``diffusers`` pipeline。

        Invoke the loaded ``diffusers`` pipeline.
        """
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
        # ``StableDiffusionPipeline`` 返回 ``images`` 属性。
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
    """将库的输出转换为 OAI ``b64_json``/``url`` 形状。

    Convert the library's output into the OAI ``b64_json``/``url`` shape.
    """
    out: list[dict] = []
    for img in images[: max(1, n)]:
        # PIL.Image 是最常见的返回类型。
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