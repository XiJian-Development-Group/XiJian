"""Stub image generation / edits / variations."""

from __future__ import annotations

import base64
import threading

from xijian_api.stubs import state
from xijian_api.utils.ids import gen_file_id
from xijian_api.utils.time import now_ts


# Minimal valid 1x1 transparent PNG (67 bytes).
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63000100000005000100"
    "0d0a2db40000000049454e44ae426082"
)


def _make_image_bytes() -> bytes:
    return _TINY_PNG


def generate(
    prompt: str,
    *,
    n: int = 1,
    size: str = "1024x1024",
    response_format: str = "b64_json",
    model: str = "stub-image",
) -> dict:
    """Return an OAI-style images.generations response body."""
    images = []
    for _ in range(max(1, n)):
        if response_format == "url":
            file_id = gen_file_id()
            state.files[file_id] = {
                "id": file_id,
                "bytes": _make_image_bytes(),
                "purpose": "vision",
                "filename": f"image_{file_id}.png",
            }
            images.append({"url": f"/v1/files/{file_id}/content"})
        else:
            images.append({"b64_json": base64.b64encode(_make_image_bytes()).decode("ascii")})
    return {
        "created": now_ts(),
        "data": images,
        "xijian": {"model": model, "size": size, "prompt_preview": prompt[:60]},
    }


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
    return generate(prompt, n=n, size=size, response_format=response_format, model=model)


def variation(
    image_bytes: bytes,
    *,
    n: int = 1,
    size: str = "1024x1024",
    response_format: str = "b64_json",
    model: str = "stub-image",
) -> dict:
    return generate("variation", n=n, size=size, response_format=response_format, model=model)


# ---- async helpers -----------------------------------------------------------


def schedule_videos_completion(video_id: str) -> None:
    """Background task — flip video status to ``completed`` after a short delay.

    Videos use a separate stub — see ``stubs.video``.
    """
    # Kept here to satisfy optional callers; actual video completion lives
    # in stubs.video.  This stub is a no-op to avoid double-scheduling.
    _ = video_id
    return None


__all__ = ["generate", "edit", "variation"]