"""OpenAI-compatible remote image-generation backend.

Calls ``POST /images/generations`` (OpenAI DALL-E API).  Returns image
bytes in the OAI ``b64_json`` / ``url`` shape used by the route layer.
"""

from __future__ import annotations

from pathlib import Path

from xijian_api.ai.backends.openai._client import (
    remote_image_generate,
    resolve_config,
)
from xijian_api.ai.base import (
    BackendError,
    ModelNotLoaded,
)
from xijian_api.ai.registry import register_image
from xijian_api.ai.types import ImageGenBackend


@register_image("openai")
class OpenAIImageBackend(ImageGenBackend):
    name = "openai"

    def __init__(self) -> None:
        self._cfg = None

    def is_available(self) -> bool:
        return True

    def is_loaded(self) -> bool:
        return self._cfg is not None

    def load(self, model_path, **kwargs) -> None:
        section = kwargs.pop("_openai_section", None)
        cfg = resolve_config(kwargs, section=section, default_model="dall-e-3")
        if not cfg.model_name:
            raise BackendError(
                "openai image backend requires a model_name",
                code="backend_error",
            )
        self._cfg = cfg

    def unload(self) -> None:
        self._cfg = None

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
        if not self.is_loaded() or self._cfg is None:
            raise ModelNotLoaded("no openai image model loaded")
        if abort_signal is not None:
            abort_signal.raise_if_aborted()
        # ``negative_prompt`` and ``seed`` are not part of the standard
        # OpenAI images API; we ignore them gracefully.  Some compatible
        # providers (e.g. Stable Diffusion wrappers) accept them, but
        # the canonical endpoint does not.
        result = remote_image_generate(
            self._cfg,
            prompt=prompt,
            n=max(1, int(n)),
            size=size,
            response_format="b64_json",
        )
        return _normalise(result)


def _normalise(result: dict) -> list[dict]:
    """Convert the OAI images response into the backend ``list[dict]`` shape."""
    import base64
    from io import BytesIO

    out: list[dict] = []
    data = result.get("data") or []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        b64 = entry.get("b64_json")
        if isinstance(b64, str) and b64:
            out.append({"bytes": base64.b64decode(b64)})
            continue
        url = entry.get("url")
        if isinstance(url, str) and url:
            # Download the image so the route layer gets bytes (matches
            # the MLX/GGUF backend contract).
            from xijian_api.ai.backends.openai._client import _httpx_get_bytes
            cfg_headers = {}  # URL images are typically public
            out.append({"bytes": _httpx_get_bytes(url, headers=cfg_headers)})
            continue
    return out


__all__ = ["OpenAIImageBackend"]
