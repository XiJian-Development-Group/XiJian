"""Image generation stub — dispatches to the configured image backend.

The previous 1x1 transparent PNG fallback has been removed.  When the
configured image backend is unavailable the call raises
:class:`xijian_api.errors.BackendError` (status 503) so clients see a
real OAI error envelope rather than a fake image.

Image generation can be expensive, so the call is synchronous (the
caller is expected to put it in a worker if needed).  For
``response_format == "url"`` the backend is expected to return URLs
already; we pass them through unchanged.
"""

from __future__ import annotations

import base64

from flask import current_app

from xijian_api.ai.base import BackendError as AIBackendError
from xijian_api.ai.base import BackendUnavailable as AIBackendUnavailable
from xijian_api.ai.registry import get_image_backend
from xijian_api.config import Config
from xijian_api.errors import BackendError as ApiBackendError
from xijian_api.utils.time import now_ts


def _resolve_config() -> Config | None:
    try:
        return current_app.config.get("XIJIAN_CONFIG")
    except RuntimeError:
        return None


def _select_backend():
    config = _resolve_config()
    requested: str | None = None
    fallbacks: tuple[str, ...] = ()
    if config is not None:
        requested = config.backends.image.default or None
        fallbacks = config.backends.image.fallbacks or ()
    try:
        return get_image_backend(requested, fallbacks)
    except AIBackendUnavailable as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "no image backend available",
            type_="backend_unavailable",
            code="backend_unavailable",
        ) from exc


def _envelope(items: list[dict], *, prompt: str, model: str, size: str) -> dict:
    return {
        "created": now_ts(),
        "data": items,
        "xijian": {"model": model, "size": size, "prompt_preview": prompt[:60]},
    }


def _ensure_b64(item: dict) -> dict:
    """Coerce a backend item into the OAI ``b64_json`` / ``url`` shape."""
    if "b64_json" in item or "url" in item:
        return item
    if "bytes" in item and isinstance(item["bytes"], (bytes, bytearray)):
        return {"b64_json": base64.b64encode(item["bytes"]).decode("ascii")}
    if isinstance(item.get("image"), (bytes, bytearray)):
        return {"b64_json": base64.b64encode(item["image"]).decode("ascii")}
    return item


def generate(
    prompt: str,
    *,
    n: int = 1,
    size: str = "1024x1024",
    response_format: str = "b64_json",
    model: str = "stub-image",
    negative_prompt: str | None = None,
    seed: int | None = None,
) -> dict:
    """Return an OAI-style images.generations response body via the backend."""
    backend = _select_backend()
    try:
        results = backend.generate(
            prompt,
            model_id=model,
            n=max(1, n),
            size=size,
            negative_prompt=negative_prompt,
            seed=seed,
        )
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "image backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc
    items = [_ensure_b64(r) for r in results]
    return _envelope(items, prompt=prompt, model=model, size=size)


def edit(
    image_bytes: bytes,
    prompt: str,
    *,
    n: int = 1,
    size: str = "1024x1024",
    response_format: str = "b64_json",
    mask: bytes | None = None,
    model: str = "stub-image",
) -> dict:
    """Return an OAI-style images.edits response body via the backend.

    Falls back to :func:`generate` when the configured backend has no
    explicit ``edit`` method (e.g. diffusion-only implementations).
    """
    backend = _select_backend()
    edit_fn = getattr(backend, "edit", None)
    try:
        if callable(edit_fn):
            results = edit_fn(
                image_bytes,
                prompt,
                model_id=model,
                n=max(1, n),
                size=size,
                mask=mask,
            )
        else:
            # Best-effort: concat the source image into the prompt and
            # regenerate.  Production backends should override ``edit``.
            results = backend.generate(
                f"{prompt} [edit of provided image]",
                model_id=model,
                n=max(1, n),
                size=size,
            )
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "image backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc
    items = [_ensure_b64(r) for r in results]
    return _envelope(items, prompt=prompt, model=model, size=size)


def variation(
    image_bytes: bytes,
    *,
    n: int = 1,
    size: str = "1024x1024",
    response_format: str = "b64_json",
    model: str = "stub-image",
) -> dict:
    """Return an OAI-style images.variations response body via the backend."""
    backend = _select_backend()
    variation_fn = getattr(backend, "variation", None)
    try:
        if callable(variation_fn):
            results = variation_fn(
                image_bytes,
                model_id=model,
                n=max(1, n),
                size=size,
            )
        else:
            results = backend.generate(
                "variation of provided image",
                model_id=model,
                n=max(1, n),
                size=size,
            )
    except AIBackendError as exc:
        raise ApiBackendError(
            status=503,
            message=str(exc) or "image backend error",
            type_="backend_unavailable",
            code=getattr(exc, "code", "backend_error"),
        ) from exc
    items = [_ensure_b64(r) for r in results]
    return _envelope(items, prompt="variation", model=model, size=size)


# Kept for callers that import ``schedule_videos_completion``; the
# actual video completion logic lives in :mod:`xijian_api.stubs.video`.
def schedule_videos_completion(video_id: str) -> None:  # pragma: no cover - shim
    from xijian_api.stubs import video as video_stub

    video_stub._complete_record(video_id)


__all__ = ["generate", "edit", "variation"]